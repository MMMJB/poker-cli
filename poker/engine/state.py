"""Hand state.

All money is integer dollars.  A float anywhere in a pot calculation is a bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

from poker.engine.cards import Card

if TYPE_CHECKING:  # pragma: no cover
    from poker.engine.events import HandLog


class Street(IntEnum):
    PREFLOP = 0
    FLOP = 1
    TURN = 2
    RIVER = 3
    SHOWDOWN = 4
    COMPLETE = 5

    @property
    def label(self) -> str:
        return self.name.capitalize() if self != Street.PREFLOP else "Preflop"


BETTING_STREETS = (Street.PREFLOP, Street.FLOP, Street.TURN, Street.RIVER)
CARDS_PER_STREET = {Street.FLOP: 3, Street.TURN: 1, Street.RIVER: 1}


class PlayerStatus(IntEnum):
    ACTIVE = 0
    """In the hand and able to act."""
    ALL_IN = 1
    """In the hand, contesting pots, but cannot act again."""
    FOLDED = 2


@dataclass(slots=True)
class PlayerState:
    seat: int
    stack: int
    hole: tuple[Card, ...] = ()
    status: PlayerStatus = PlayerStatus.ACTIVE
    street_committed: int = 0
    total_committed: int = 0
    acted_this_round: bool = False
    """Has acted since the last full bet or raise.

    Posting a blind does NOT set this -- that single fact is the entire
    big-blind-option mechanism.
    """
    revealed: bool = False

    @property
    def is_live(self) -> bool:
        return self.status is not PlayerStatus.FOLDED

    @property
    def can_act(self) -> bool:
        return self.status is PlayerStatus.ACTIVE


@dataclass(slots=True)
class GameConfig:
    small_blind: int = 1
    big_blind: int = 3
    num_seats: int = 6
    buy_in: int = 300
    rebuy_threshold: int = 60
    """Below this at the start of a hand, a seat tops back up."""
    topup_to: int = 300
    auto_topup_human: bool = True
    reveal_all_at_showdown: bool = False


@dataclass(slots=True)
class HandState:
    hand_id: int
    config: GameConfig
    button: int
    players: list[PlayerState]
    log: "HandLog"
    seed: int = 0
    board: list[Card] = field(default_factory=list)
    street: Street = Street.PREFLOP
    current_bet: int = 0
    """Highest street_committed this street."""
    last_full_raise_size: int = 0
    """Size of the last FULL raise increment -- the minimum raise step.

    A short all-in never updates this: after bet 10, call, all-in 15, the next
    legal raise is to 25, not 20.
    """
    to_act: int | None = None
    last_aggressor: int | None = None

    @property
    def num_seats(self) -> int:
        return len(self.players)

    @property
    def pot(self) -> int:
        """Derived, never stored -- a separate counter would drift."""
        return sum(p.total_committed for p in self.players)

    def player(self, seat: int) -> PlayerState:
        return self.players[seat]

    def live_players(self) -> list[PlayerState]:
        return [p for p in self.players if p.is_live]

    def active_players(self) -> list[PlayerState]:
        return [p for p in self.players if p.can_act]
