"""Session-level bookkeeping: seats, stacks across hands, and top-ups."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence

from poker.engine.events import EventType
from poker.engine.hand import HandEngine, HandResult
from poker.engine.state import GameConfig


@dataclass(slots=True)
class Seat:
    seat: int
    name: str
    is_human: bool
    stack: int
    total_bought_in: int
    hands_played: int = 0

    @property
    def profit(self) -> int:
        """Never flattered by auto-rebuys -- reloads add to the cost basis."""
        return self.stack - self.total_bought_in


@dataclass(slots=True)
class TopUp:
    seat: int
    amount: int
    stack_after: int


class Table:
    """Holds the seats and deals hands.

    Top-ups happen **only between hands** -- both a real rule and a
    bug-prevention measure.
    """

    def __init__(
        self,
        config: GameConfig,
        names: Sequence[str],
        human_seat: int = 0,
        session_seed: int | None = None,
        stacks: Sequence[int] | None = None,
        button: int | None = None,
        hand_number: int = 0,
    ) -> None:
        if len(names) != config.num_seats:
            raise ValueError("one name per seat")
        self.config = config
        self.human_seat = human_seat
        self.session_seed = (
            session_seed if session_seed is not None else random.getrandbits(64)
        )
        self._rng = random.Random(self.session_seed)
        start_stacks = list(stacks) if stacks else [config.buy_in] * config.num_seats
        self.seats = [
            Seat(
                seat=i,
                name=name,
                is_human=(i == human_seat),
                stack=start_stacks[i],
                total_bought_in=config.buy_in,
            )
            for i, name in enumerate(names)
        ]
        # Start one behind so the first start_hand() puts the button on seat 0.
        self.button = (
            button if button is not None else config.num_seats - 1
        )
        self.hand_number = hand_number

    # --------------------------------------------------------------- top-ups

    def apply_topups(self) -> list[TopUp]:
        """Reload short stacks.

        A threshold rather than always-top-up: pinning every stack at exactly
        100BB would erase the short- and deep-stack spots a trainer should be
        teaching.
        """
        out: list[TopUp] = []
        for s in self.seats:
            if s.is_human and not self.config.auto_topup_human:
                continue
            if s.stack >= self.config.rebuy_threshold:
                continue
            amount = self.config.topup_to - s.stack
            if amount <= 0:
                continue
            s.stack += amount
            s.total_bought_in += amount
            out.append(TopUp(s.seat, amount, s.stack))
        return out

    def rebuy(self, seat: int, to: int | None = None) -> TopUp | None:
        """Explicit reload, e.g. the human accepting a rebuy prompt."""
        s = self.seats[seat]
        target = to if to is not None else self.config.topup_to
        amount = target - s.stack
        if amount <= 0:
            return None
        s.stack += amount
        s.total_bought_in += amount
        return TopUp(seat, amount, s.stack)

    # ----------------------------------------------------------------- hands

    def start_hand(self) -> HandEngine:
        """Advance the button, top up, and deal."""
        self.button = (self.button + 1) % self.config.num_seats
        self.hand_number += 1
        topups = self.apply_topups()

        engine = HandEngine(
            config=self.config,
            names=[s.name for s in self.seats],
            stacks=[s.stack for s in self.seats],
            button=self.button,
            hand_id=self.hand_number,
            seed=self._rng.getrandbits(64),
        )
        for t in topups:
            engine.log.emit(EventType.TOPUP, {
                "seat": t.seat, "amount": t.amount, "stack_after": t.stack_after,
            })
        engine.start()
        return engine

    def settle(self, result: HandResult) -> None:
        for s in self.seats:
            s.stack = result.stacks_after[s.seat]
            s.hands_played += 1

    # ----------------------------------------------------------------- stats

    def standings(self) -> list[Seat]:
        return sorted(self.seats, key=lambda s: s.profit, reverse=True)

    def bb_per_100(self, seat: int) -> float:
        s = self.seats[seat]
        if not s.hands_played:
            return 0.0
        return s.profit / self.config.big_blind / s.hands_played * 100

    @property
    def human(self) -> Seat:
        return self.seats[self.human_seat]
