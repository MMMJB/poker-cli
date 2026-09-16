"""The application loop.

Async top to bottom.  The alternative -- a sync app calling ``asyncio.run()``
per decision -- tears down the event loop *and the HTTP connection pool* every
time, adding a TLS handshake to each of the ~7 opponent decisions in a hand.
That is most of the latency budget spent on nothing.

Two bounded sync islands remain: rich's own refresh thread, and the termios
switch at startup and shutdown.
"""

from __future__ import annotations

import asyncio
import random
import time

from rich.console import Console
from rich.live import Live

from poker.agents.client import AgentClient
from poker.agents.decide import Decision, OpponentAgent, Source, forced_action
from poker.agents.fallback import decision_rng, local_decision
from poker.agents.personas import (
    assign_seats, choose_table_size, leak_denylist,
)
from poker.app.commands import QUIT, TOGGLE_REVIEW
from poker.app.input import ActionPrompt
from poker.app.review import ReviewCoach
from poker.app.session import HandRecord, SessionStore
from poker.app.view import build_view, street_tag, win_labels_for
from poker.config import TIERS, Config
from poker.engine.actions import Action
from poker.engine.state import GameConfig, Street
from poker.engine.table import Table
from poker.ui.cards import probe_glyph_width
from poker.ui import keys as K
from poker.ui.keys import KeyReader
from poker.ui.model import ActionBar, InputLine, ReviewView, TableView
from poker.ui.table import TableRenderable, money
from poker.ui.theme import THEME

HERO_NAME = "You"


class App:
    def __init__(self, config: Config, console: Console | None = None) -> None:
        self.cfg = config
        self.console = console or Console(theme=THEME)
        self._view = TableView()
        self._started = time.monotonic()
        self.quit = False

        self.store = SessionStore(config)
        resumed = self.store.load()

        self.rng = random.Random(resumed.session_seed if resumed else None)
        self.client: AgentClient | None = None if config.offline else AgentClient()
        self.denylist = tuple(w.lower() for w in leak_denylist())

        # The hero's bankroll follows them between tables; the opponents do not.
        self.hero_stack = config.buy_in
        self.hero_bought_in = config.buy_in
        self.hands_played = 0
        self.hand_number = 0
        self.table_number = 0
        self.hands_at_table = 0

        if resumed:
            hero = config.hero_seat
            if hero < len(resumed.stacks):
                self.hero_stack = resumed.stacks[hero]
            self.hero_bought_in = resumed.bought_in.get(hero, config.buy_in)
            self.hands_played = resumed.hands_played
            self.hand_number = resumed.hand_number
            self.table_number = getattr(resumed, "table_number", 0)

        self.seat_size = config.num_seats
        self.personas: dict = {}
        self.table: Table
        self.agents: dict = {}
        self._seat_table(config.num_seats)

        self.coach = ReviewCoach(self.client, config, self.denylist)
        self.keys = KeyReader()
        self.prompt = ActionPrompt(self.keys, self.publish)
        self._log_lines: list[tuple[str, str]] = []
        self._street_parts: list[str] = []
        self._street: Street | None = None
        self.offline_reason = ""
        """Why live play is unavailable, if it is.  Shown in the banner and again on exit."""

    # ------------------------------------------------------------ the table

    def _seat_table(self, num_seats: int) -> None:
        """Sit down at a new table: fresh opponents, fresh names, same bankroll."""
        self.seat_size = num_seats
        self.table_number += 1
        self.hands_at_table = 0

        game = GameConfig(
            small_blind=self.cfg.small_blind,
            big_blind=self.cfg.big_blind,
            num_seats=num_seats,
            buy_in=self.cfg.buy_in,
            rebuy_threshold=self.cfg.rebuy_threshold,
            topup_to=self.cfg.buy_in,
        )
        hero = self.cfg.hero_seat
        previous = {s.name for s in self.personas.values()}
        self.personas = assign_seats(self.rng, hero, num_seats,
                                     avoid_names=previous)

        names = [HERO_NAME] * num_seats
        for seat, seated in self.personas.items():
            names[seat] = seated.name

        stacks = [self.cfg.buy_in] * num_seats
        stacks[hero] = max(self.hero_stack, self.cfg.big_blind)

        self.table = Table(
            game, names,
            human_seat=hero,
            session_seed=self.rng.getrandbits(64),
            stacks=stacks,
            hand_number=self.hand_number,
        )
        # Carry the hero's cost basis so session P/L stays continuous.
        self.table.seats[hero].total_bought_in = self.hero_bought_in
        self.table.seats[hero].hands_played = self.hands_played

        self.agents = {
            seat: OpponentAgent(seated, seat, self.client, self.cfg, self.denylist)
            for seat, seated in self.personas.items()
        }

    def _remember_hero(self) -> None:
        hero = self.table.human
        self.hero_stack = hero.stack
        self.hero_bought_in = hero.total_bought_in
        self.hands_played = hero.hands_played
        self.hand_number = self.table.hand_number

    async def change_table(self, reason: str = "") -> None:
        """Move to a new table, keeping the bankroll and nothing else."""
        self._remember_hero()
        size = choose_table_size(self.rng, self.cfg.table_sizes,
                                 self.cfg.table_size_weights)
        self._seat_table(size)
        self.publish(seats=(), banner=self._view.banner)
        note = reason or "New table."
        self._note(f"{note}  Seat {self.cfg.hero_seat} at a {size}-handed table.",
                   "subtle")
        await self._pause(self.cfg.street_pause)

    # ------------------------------------------------------------ publishing

    def publish(self, **kw) -> None:
        """Replace the whole view object -- never mutate it.

        rich refreshes from a background thread, so an in-place edit could be
        read half-applied.  Rebinding an immutable object is atomic.
        """
        self._view = self._view.with_(**kw)

    def refresh_table(self, **kw) -> None:
        self.publish(**{
            **_view_fields(build_view(self.engine, self.table, self._view, **kw)),
        })

    def _tick(self) -> float:
        return time.monotonic() - self._started

    # ------------------------------------------------------------------ main

    async def run(self) -> None:
        probe_glyph_width()

        banner = ""
        if self.cfg.offline:
            banner = "OFFLINE"
        elif self.client is not None:
            models = [TIERS[p.tier].model for p in self.personas.values()]
            if self.coach.enabled:
                models += [self.cfg.analyst_model, self.cfg.translator_model]
            error = await self.client.preflight(models)
            if error:
                # The cause goes in the banner, not just the log: the log is
                # cleared at the start of every hand, so a note written here
                # is gone before the first frame the player actually reads.
                self.offline_reason = error
                banner = offline_banner(error)
                for agent in self.agents.values():
                    agent.degraded = True
                self.coach.degraded = True
        self.publish(banner=banner, review_on=self.coach.enabled)

        renderable = TableRenderable(lambda: self._view, self._tick)
        async with self.keys:
            with Live(
                renderable,
                console=self.console,
                screen=True,
                auto_refresh=True,
                refresh_per_second=self.cfg.refresh_per_second,
                transient=False,
            ):
                try:
                    played = 0
                    while not self.quit:
                        await self.play_hand()
                        played += 1
                        if self.quit:
                            break
                        await self.show_review()
                        await self.between_hands()
                        await self.wait_for_next_hand()
                        if self.quit:
                            break
                        if (self.cfg.hands_per_table
                                and self.hands_at_table >= self.cfg.hands_per_table):
                            await self.change_table(
                                f"{self.hands_at_table} hands here \u2014 "
                                "the room is moving you."
                            )
                        if self.cfg.demo_hands and played >= self.cfg.demo_hands:
                            break
                finally:
                    self.store.save(self.table)
                    if self.client is not None:
                        await self.client.aclose()

        self._print_farewell()

    # ------------------------------------------------------------- one hand

    async def play_hand(self) -> None:
        self.engine = self.table.start_hand()
        self._log_lines = []
        self._street_parts = []
        self._street = Street.PREFLOP
        self.publish(
            review=ReviewView(), talk="", talk_speaker="",
            action_bar=ActionBar(), input_line=InputLine(),
            log_lines=(),
        )
        self.decisions: list[dict] = []
        self.prompt.clear_draft()

        self.refresh_table()
        await self._animate_deal()

        while not self.engine.is_complete():
            seat = self.engine.current_actor()
            if seat is None:
                break
            legal = self.engine.legal_actions()

            if seat == self.cfg.hero_seat:
                if self.cfg.demo_hands:
                    action = self._demo_hero(seat, legal)
                    self.refresh_table(acting=seat)
                    await self._pause(self.cfg.deal_pause)
                else:
                    while True:
                        action = await self._hero_acts(legal)
                        if action is QUIT:
                            self.quit = True
                            return
                        if action is TOGGLE_REVIEW:
                            self._toggle_review()
                            continue
                        break
                self._record(seat, action, Source.HUMAN)
            else:
                decision = await self._seat_acts(seat, legal)
                action = decision.action
                self._record(seat, action, decision.source, decision)

            street_before = self.engine.state.street
            pot_before = self.engine.state.pot
            self.engine.apply(action)
            self._log_action(seat, action, pot_before)
            self.refresh_table()

            # The dwell above is spent *before* a seat acts. Without a beat
            # afterwards, the result is wiped by the next seat starting to
            # think and the hand is unreadable.
            if seat != self.cfg.hero_seat:
                await self._pause_think(self.cfg.action_hold)

            if self.engine.state.street is not street_before:
                await self._animate_street_change()

        await self._animate_showdown()

    def _demo_hero(self, seat: int, legal) -> Action:
        """Autoplay the hero, for the headless demo."""
        from poker.agents.personas import RAJ

        rng = decision_rng(self.engine.state.hand_id, seat,
                           int(self.engine.state.street), len(self.decisions))
        return local_decision(RAJ, self.engine.observation(seat), legal, rng)

    async def _hero_acts(self, legal):
        self.refresh_table(acting=self.cfg.hero_seat)
        result = await self.prompt.ask(
            legal, self.engine.state.pot, self.engine.state.current_bet
        )
        self.publish(action_bar=ActionBar(), input_line=InputLine())
        return result

    def _toggle_review(self) -> None:
        self.coach.enabled = not self.coach.enabled
        self.publish(review_on=self.coach.enabled)
        self._note(
            "Hand reviews ON -- they cost the most per hand."
            if self.coach.enabled else
            "Hand reviews OFF.  Press v to turn them back on.",
            "subtle",
        )

    async def _seat_acts(self, seat: int, legal) -> Decision:
        agent = self.agents[seat]
        obs = self.engine.observation(seat)

        forced = forced_action(legal)
        if forced is not None:
            return Decision(action=forced, source=Source.FORCED, model="none")

        rng = decision_rng(obs.hand_id, seat, int(obs.street), len(self.decisions))
        dwell = self.cfg.paced_think(agent.dwell(obs, rng))

        self.refresh_table(acting=seat, thinking=seat)
        started = time.monotonic()
        if self._drafting:
            decision = await self.prompt.draft_until(agent.act(obs, legal))
        else:
            decision = await agent.act(obs, legal)

        # Hold the seat on screen for at least its dwell time.  A table where
        # five opponents act instantly is unreadable, and the floor also keeps
        # response time from revealing which model is behind which seat.
        remaining = dwell - (time.monotonic() - started)
        if remaining > 0:
            # The think-time floor is the longest window in the hand and the
            # most natural moment to prepare a move, so it collects keystrokes
            # rather than sleeping through them.
            await self._pause_raw(remaining)

        if decision.talk:
            self.publish(talk=decision.talk, talk_speaker=agent.persona.name)
        return decision

    # ------------------------------------------------------------ animation

    async def _pause(self, seconds: float) -> None:
        await self._pause_raw(self.cfg.paced(seconds))

    async def _pause_think(self, seconds: float) -> None:
        await self._pause_raw(self.cfg.paced_think(seconds))

    async def _pause_raw(self, delay: float) -> None:
        """Wait an already-scaled number of seconds, drafting if anyone is there."""
        if delay <= 0:
            return
        if self._drafting:
            await self.prompt.draft_for(delay)
        else:
            await asyncio.sleep(delay)

    @property
    def _drafting(self) -> bool:
        """Whether keystrokes should be collected as a prepared move.

        Never during a demo run: nobody is at the keyboard, and the reader
        would poll instead of sleeping.
        """
        return not self.cfg.demo_hands and self.keys.enabled

    async def _animate_deal(self) -> None:
        await self._pause(self.cfg.deal_pause)

    async def _animate_street_change(self) -> None:
        self.refresh_table()
        await self._pause(self.cfg.street_pause)

    async def _animate_showdown(self) -> None:
        if not self.engine.is_complete():
            return
        result = self.engine.result()
        winners: dict[int, int] = {}
        for award in result.awards:
            winners[award.seat] = winners.get(award.seat, 0) + award.amount

        labels = win_labels_for(result)
        reveal = set(result.shown)
        self.refresh_table(winners=winners, win_labels=labels, reveal=reveal)

        if len(result.pots) > 1:
            self._note(
                "  ".join(
                    f"{'main' if i == 0 else 'side'} {money(pot.amount)}"
                    for i, pot in enumerate(result.pots)
                ),
                "subtle",
            )
        for seat, amount in winners.items():
            name = "You" if seat == self.cfg.hero_seat else self.table.seats[seat].name
            which = labels.get(seat, "")
            what = {
                "MAIN": " the main pot",
                "SIDE": " a side pot",
            }.get(which, "")
            self._note(f"{name} wins{what} {money(amount)}", "seat.winner")

        self.table.settle(result)
        self.record = self._build_record(result)
        self.coach.start(self.record)
        await self._pause(self.cfg.showdown_pause)

    # --------------------------------------------------------------- review

    async def show_review(self) -> None:
        """Publish the review, if there is one.  Does not wait.

        Waiting is :meth:`wait_for_next_hand`'s job, so the review panel and
        the continue prompt are one stop rather than two.
        """
        if not hasattr(self, "record"):
            return
        if self.coach.pending():
            # Two models run back to back here, so say so rather than freezing.
            self.publish(review=ReviewView(pending=True,
                                           hand_id=self.record.hand_id))
        review = await self.coach.result()
        if review is not None:
            self.publish(review=review)

    def _table_label(self) -> str:
        left = ""
        if self.cfg.hands_per_table:
            remaining = max(0, self.cfg.hands_per_table - self.hands_at_table)
            left = f" ({remaining} to go)"
        return (f"table {self.table_number} \u00b7 {self.seat_size}-handed "
                f"\u00b7 {self.hands_at_table} hands{left}")

    def _hand_summary(self) -> str:
        record = getattr(self, "record", None)
        if record is None or record.result is None:
            return "Hand complete."
        net = record.result.net.get(self.cfg.hero_seat, 0)
        if net > 0:
            return f"Hand #{record.hand_id}: you won {money(net)}."
        if net < 0:
            return f"Hand #{record.hand_id}: you lost {money(-net)}."
        return f"Hand #{record.hand_id}: you broke even."

    async def wait_for_next_hand(self) -> None:
        """Hold until the player asks for the next hand.

        Hands used to run straight into each other, so the result of one was
        easy to miss entirely.  Nothing is dealt until this returns.
        """
        if self.cfg.demo_hands or self.quit:
            if self.cfg.demo_hands:
                await self._pause(self.cfg.showdown_pause)
                self.publish(review=ReviewView(), action_bar=ActionBar())
            return

        summary = self._hand_summary()
        hint = ("enter: next   \u00b7   r: replay   \u00b7   t: new table   "
                "\u00b7   v: reviews   \u00b7   q: quit")
        hint_short = "enter \u00b7 r replay \u00b7 t table \u00b7 v reviews \u00b7 q quit"
        while True:
            self.publish(
                action_bar=ActionBar(
                    active=False,
                    message=f"{summary}   \u00b7   {self._table_label()}",
                ),
                input_line=InputLine(active=True, hint=hint,
                                     hint_short=hint_short),
            )
            key = await self.keys.key()
            if key is None:
                continue
            lowered = key.lower()
            if lowered == "q":
                self.quit = True
                return
            if lowered == "v":
                self._toggle_review()
                continue
            if lowered == "r":
                await self._replay_last_hand()
                continue
            if lowered == "t":
                await self.change_table("You asked for a new table.")
                self.publish(review=ReviewView(), action_bar=ActionBar(),
                             input_line=InputLine())
                return
            if key in (K.KEY_ENTER, " "):
                self.publish(review=ReviewView(), action_bar=ActionBar(),
                             input_line=InputLine())
                return

    async def between_hands(self) -> None:
        self.store.append_hand(self.record)
        self._remember_hero()
        self.hands_at_table += 1
        self.store.save(self.table, table_number=self.table_number)
        hero = self.table.human
        if hero.stack < self.cfg.big_blind * 2:
            self.table.rebuy(self.cfg.hero_seat)
            self._note("You reloaded to " + money(self.cfg.buy_in), "subtle")

    async def _replay_last_hand(self) -> None:
        """Step back through the hand just played, without leaving the game.

        It is read from disk like any other stored hand, so this is the same
        code path as ``poker hand N`` -- and it proves the hand was written.
        """
        from poker.app import replay
        from poker.app.replay_view import ReplayViewer

        record = getattr(self, "record", None)
        if record is None:
            return

        stored = replay.find(self.cfg, record.hand_id)
        if stored is None:
            self._note("That hand is not on disk yet.", "prompt.error")
            return

        viewer = ReplayViewer(stored, self.cfg, self.console)
        saved = self._view
        try:
            # Reuse the live session's reader and screen rather than opening a
            # second Live, which would fight the one already running.
            await viewer.run_within(self.keys, self.publish_raw)
        finally:
            self._view = saved

    def publish_raw(self, view: TableView) -> None:
        self._view = view

    # ----------------------------------------------------------- bookkeeping

    def _record(self, seat: int, action: Action, source: Source,
                decision: Decision | None = None) -> None:
        self.decisions.append({
            "seat": seat,
            "street": self.engine.state.street.name.lower(),
            "action": str(action),
            "source": source.value,
            "model": decision.model if decision else "",
            "latency_ms": decision.latency_ms if decision else 0,
            "note": decision.note if decision else "",
            "talk": decision.talk if decision else "",
        })

    def _build_record(self, result) -> HandRecord:
        return HandRecord(
            hand_id=result.hand_id,
            button=result.button,
            hero_seat=self.cfg.hero_seat,
            names={s.seat: s.name for s in self.table.seats},
            events=[e.to_dict(include_private=True) for e in self.engine.log.all()],
            public_events=[e.to_dict() for e in self.engine.log.public()],
            decisions=list(self.decisions),
            personas={s: p.key for s, p in self.personas.items()},
            result=result,
            stacks_before={
                s.seat: result.stacks_after[s.seat] - result.net[s.seat]
                for s in self.table.seats
            },
        )

    def _log_action(self, seat: int, action: Action, pot_before: int) -> None:
        state = self.engine.state
        if state.street is not self._street:
            self._flush_street()
            self._street = state.street
        name = "YOU" if seat == self.cfg.hero_seat else self.table.seats[seat].name
        self._street_parts.append(f"{name} {action}")
        self._render_log()

    def _flush_street(self) -> None:
        if self._street_parts and self._street is not None:
            self._log_lines.append((
                f"{street_tag(self._street)}  " + " · ".join(self._street_parts),
                "log",
            ))
        self._street_parts = []

    def _render_log(self) -> None:
        live = list(self._log_lines)
        if self._street_parts and self._street is not None:
            live.append((
                f"{street_tag(self._street)}  " + " · ".join(self._street_parts),
                "log",
            ))
        self.publish(log_lines=tuple(live[-4:]))

    def _note(self, text: str, style: str = "log") -> None:
        self._log_lines.append((text, style))
        self._render_log()

    def _print_farewell(self) -> None:
        hero = self.table.human
        self.console.print()
        if self.offline_reason:
            self.console.print(
                f"[prompt.error]Played offline:[/] {self.offline_reason}"
            )
            self.console.print(
                "[subtle]The opponents used their local policies, so nothing "
                "was billed.[/]"
            )
            self.console.print()
        self.console.print(
            f"[title]Session over.[/]  {hero.hands_played} hands, "
            f"net [{'seat.stack' if hero.profit >= 0 else 'prompt.error'}]"
            f"{'+' if hero.profit >= 0 else ''}{money(hero.profit)}[/]"
        )
        if self.client is not None and self.cfg.debug_hud:
            self.console.print(f"[debug]{self.client.usage.summary()}[/]")
        self.console.print(f"[subtle]Hand histories: {self.store.directory}[/]")


def offline_banner(error: str) -> str:
    """A short, specific reason for the top-right badge.

    A bare "OFFLINE" tells the player nothing about whether it is their key,
    their billing, their network, or a deliberate flag.
    """
    lowered = error.lower()
    if any(w in lowered for w in ("credit balance", "billing", "quota",
                                  "purchase", "insufficient funds")):
        return "OFFLINE \u00b7 no API credit"
    if any(w in lowered for w in ("api key", "authentication", "missing or invalid")):
        return "OFFLINE \u00b7 bad API key"
    if any(w in lowered for w in ("cannot reach", "connection")):
        return "OFFLINE \u00b7 no connection"
    if "may not use" in lowered or "permission" in lowered:
        return "OFFLINE \u00b7 key lacks access"
    if "not available" in lowered or "not found" in lowered:
        return "OFFLINE \u00b7 model unavailable"
    return "OFFLINE \u00b7 API error"


def _view_fields(view: TableView) -> dict:
    from dataclasses import fields

    return {f.name: getattr(view, f.name) for f in fields(view)}
