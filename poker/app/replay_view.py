"""Stepping through a stored hand on the normal table.

Because a replay is rebuilt through the real engine, every frame is real engine
state and the ordinary renderer draws it with no special cases.  The only
difference from live play is that all hole cards are face up: the hand is over
and this is your own history to study.
"""

from __future__ import annotations

import asyncio

from rich.console import Console
from rich.live import Live

from poker.app.replay import StoredHand, rebuild, recorded_actions, table_for
from poker.app.view import build_view, street_tag, win_labels_for
from poker.config import Config
from poker.engine.events import EventType
from poker.engine.state import Street
from poker.ui import keys as K
from poker.ui.cards import probe_glyph_width
from poker.ui.keys import KeyReader
from poker.ui.model import ActionBar, InputLine, TableView
from poker.ui.table import TableRenderable, money
from poker.ui.theme import THEME

HINT = "← → step   s: end   a: start   q: done"


class ReplayViewer:
    def __init__(self, hand: StoredHand, config: Config,
                 console: Console | None = None) -> None:
        self.hand = hand
        self.cfg = config
        self.console = console or Console(theme=THEME)
        self.table = table_for(hand)
        self.total = len(recorded_actions(hand))
        self.index = 0
        self._view = TableView()

    # ------------------------------------------------------------------ frame

    def frame(self) -> TableView:
        engine = rebuild(self.hand, self.index)
        seats = range(len(self.table.seats))

        winners: dict[int, int] = {}
        labels: dict[int, str] = {}
        if engine.is_complete():
            result = engine.result()
            for award in result.awards:
                winners[award.seat] = winners.get(award.seat, 0) + award.amount
            labels = win_labels_for(result)

        view = build_view(
            engine, self.table, self._view,
            acting=engine.current_actor(),
            winners=winners,
            win_labels=labels,
            reveal=set(seats),          # the hand is over: show everything
        )
        return view.with_(
            banner=f"REPLAY · hand #{self.hand.hand_id}",
            session_label="replay",
            review_on=True,
            action_bar=ActionBar(active=False, message=self._status(engine)),
            input_line=InputLine(active=True, hint=HINT),
            log_lines=self._log(engine),
            talk="",
            talk_speaker="",
        )

    def _status(self, engine) -> str:
        step = f"step {self.index}/{self.total}"
        if engine.is_complete():
            net = self.hand.net
            outcome = ("you won" if net > 0 else
                       "you lost" if net < 0 else "you broke even")
            amount = f" {money(abs(net))}" if net else ""
            return f"{step}  ·  hand over, {outcome}{amount}"
        seat = engine.current_actor()
        if seat is None:
            return f"{step}  ·  dealing"
        return f"{step}  ·  {self.table.seats[seat].name} to act"

    def _log(self, engine) -> tuple:
        """The action so far, street by street, from the rebuilt engine."""
        lines: list[tuple[str, str]] = []
        street = Street.PREFLOP
        parts: list[str] = []

        def flush() -> None:
            if parts:
                lines.append((f"{street_tag(street)}  " + " · ".join(parts),
                              "log"))

        for event in engine.log.all():
            if event.type is EventType.STREET_DEALT:
                flush()
                parts.clear()
                street = Street[event.data["street"].upper()]
            elif event.type is EventType.ACTION_TAKEN:
                name = self.table.seats[event.data["seat"]].name
                if event.data["seat"] == self.hand.hero_seat:
                    name = "YOU"
                action = event.data["action"]
                if action in ("bet", "raise"):
                    text = f"{name} {action} {event.data['to_amount']}"
                elif action == "call":
                    text = f"{name} call {event.data['amount_added']}"
                else:
                    text = f"{name} {action}"
                parts.append(text)
        flush()
        return tuple(lines[-4:])

    # ------------------------------------------------------------------- loop

    async def run_within(self, keys: KeyReader, publish) -> None:
        """Drive the replay inside a Live screen someone else already owns."""
        probe_glyph_width()
        self.index = 0
        publish(self.frame())
        keys.drain()
        while True:
            key = await keys.key()
            if key is None:
                continue
            if self._apply(key) is False:
                return
            publish(self.frame())

    def _apply(self, key: str) -> bool | None:
        """Handle a key.  Returns False to leave the replay."""
        if key in ("q", "Q", K.KEY_ESC, K.KEY_CTRL_C):
            return False
        if key in (K.KEY_RIGHT, K.KEY_ENTER, " ", "n"):
            self.index = min(self.total, self.index + 1)
        elif key in (K.KEY_LEFT, "p"):
            self.index = max(0, self.index - 1)
        elif key in ("s", K.KEY_END):
            self.index = self.total
        elif key in ("a", K.KEY_HOME):
            self.index = 0
        return None

    async def run(self) -> None:
        probe_glyph_width()
        self._view = self.frame()
        keys = KeyReader()
        renderable = TableRenderable(lambda: self._view, lambda: 0.0)

        async with keys:
            with Live(renderable, console=self.console, screen=True,
                      auto_refresh=True,
                      refresh_per_second=self.cfg.refresh_per_second):
                while True:
                    key = await keys.key()
                    if key is None:
                        continue
                    if self._apply(key) is False:
                        return
                    self._view = self.frame()


def replay_hand(hand: StoredHand, config: Config,
                console: Console | None = None) -> None:
    asyncio.run(ReplayViewer(hand, config, console).run())
