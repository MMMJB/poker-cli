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
from poker.agents.personas import assign_seats, leak_denylist
from poker.app.commands import QUIT, TOGGLE_REVIEW
from poker.app.input import ActionPrompt
from poker.app.review import ReviewCoach
from poker.app.session import HandRecord, SessionStore
from poker.app.view import build_view, street_tag
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

        game = GameConfig(
            small_blind=config.small_blind,
            big_blind=config.big_blind,
            num_seats=config.num_seats,
            buy_in=config.buy_in,
            rebuy_threshold=config.rebuy_threshold,
            topup_to=config.buy_in,
        )
        self.store = SessionStore(config)
        resumed = self.store.load()

        self.rng = random.Random(resumed.session_seed if resumed else None)
        self.personas = assign_seats(self.rng, config.hero_seat, config.num_seats)
        names = [HERO_NAME] * config.num_seats
        for seat, persona in self.personas.items():
            names[seat] = persona.name

        self.table = Table(
            game, names,
            human_seat=config.hero_seat,
            session_seed=resumed.session_seed if resumed else None,
            stacks=resumed.stacks if resumed else None,
            button=resumed.button if resumed else None,
            hand_number=resumed.hand_number if resumed else 0,
        )
        if resumed:
            for seat, bought in resumed.bought_in.items():
                self.table.seats[seat].total_bought_in = bought
                self.table.seats[seat].hands_played = resumed.hands_played

        self.client: AgentClient | None = None if config.offline else AgentClient()
        self.denylist = tuple(w.lower() for w in leak_denylist())
        self.agents = {
            seat: OpponentAgent(persona, seat, self.client, config, self.denylist)
            for seat, persona in self.personas.items()
        }
        self.coach = ReviewCoach(self.client, config, self.denylist)
        self.keys = KeyReader()
        self.prompt = ActionPrompt(self.keys, self.publish)
        self._log_lines: list[tuple[str, str]] = []
        self._street_parts: list[str] = []
        self._street: Street | None = None

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
                banner = "OFFLINE"
                self._note(f"API unavailable -- {error}", "prompt.error")
                self._note("Playing against the local opponents instead.", "subtle")
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
        decision = await agent.act(obs, legal)

        # Hold the seat on screen for at least its dwell time.  A table where
        # five opponents act instantly is unreadable, and the floor also keeps
        # response time from revealing which model is behind which seat.
        remaining = dwell - (time.monotonic() - started)
        if remaining > 0:
            await asyncio.sleep(remaining)

        if decision.talk:
            self.publish(talk=decision.talk, talk_speaker=agent.persona.name)
        return decision

    # ------------------------------------------------------------ animation

    async def _pause(self, seconds: float) -> None:
        delay = self.cfg.paced(seconds)
        if delay > 0:
            await asyncio.sleep(delay)

    async def _pause_think(self, seconds: float) -> None:
        delay = self.cfg.paced_think(seconds)
        if delay > 0:
            await asyncio.sleep(delay)

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

        reveal = set(result.shown)
        self.refresh_table(winners=winners, reveal=reveal)

        for seat, amount in winners.items():
            name = "You" if seat == self.cfg.hero_seat else self.table.seats[seat].name
            self._note(f"{name} wins {money(amount)}", "seat.winner")

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
        hint = "enter: next hand   \u00b7   v: reviews on/off   \u00b7   q: quit"
        while True:
            self.publish(
                action_bar=ActionBar(active=False, message=summary),
                input_line=InputLine(active=True, hint=hint),
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
            if key in (K.KEY_ENTER, " "):
                self.publish(review=ReviewView(), action_bar=ActionBar(),
                             input_line=InputLine())
                return

    async def between_hands(self) -> None:
        self.store.append_hand(self.record)
        self.store.save(self.table)
        hero = self.table.human
        if hero.stack < self.cfg.big_blind * 2:
            self.table.rebuy(self.cfg.hero_seat)
            self._note("You reloaded to " + money(self.cfg.buy_in), "subtle")

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
        self.console.print(
            f"[title]Session over.[/]  {hero.hands_played} hands, "
            f"net [{'seat.stack' if hero.profit >= 0 else 'prompt.error'}]"
            f"{'+' if hero.profit >= 0 else ''}{money(hero.profit)}[/]"
        )
        if self.client is not None and self.cfg.debug_hud:
            self.console.print(f"[debug]{self.client.usage.summary()}[/]")
        self.console.print(f"[subtle]Hand histories: {self.store.directory}[/]")


def _view_fields(view: TableView) -> dict:
    from dataclasses import fields

    return {f.name: getattr(view, f.name) for f in fields(view)}
