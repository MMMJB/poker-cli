"""Player actions.

**``to_amount`` is a "raise to" total, never an increment.**  It is the total
this player will have committed *on the current street* after the action
resolves.  Mixing "to" and "by" semantics is the single most common source of
betting bugs, and LLM opponents happily produce both -- the agent adapter
normalizes to "to", and the engine rejects anything ambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class ActionType(IntEnum):
    FOLD = 0
    CHECK = 1
    CALL = 2
    BET = 3
    RAISE = 4


@dataclass(frozen=True, slots=True)
class Action:
    type: ActionType
    to_amount: int = 0
    """For BET/RAISE only: the TOTAL street commitment after acting."""

    def __str__(self) -> str:
        if self.type in (ActionType.BET, ActionType.RAISE):
            return f"{self.type.name.lower()} to {self.to_amount}"
        return self.type.name.lower()

    @staticmethod
    def fold() -> "Action":
        return Action(ActionType.FOLD)

    @staticmethod
    def check() -> "Action":
        return Action(ActionType.CHECK)

    @staticmethod
    def call() -> "Action":
        return Action(ActionType.CALL)

    @staticmethod
    def bet(to_amount: int) -> "Action":
        return Action(ActionType.BET, to_amount)

    @staticmethod
    def raise_to(to_amount: int) -> "Action":
        return Action(ActionType.RAISE, to_amount)


@dataclass(frozen=True, slots=True)
class LegalActions:
    """Exactly what the seat to act may legally do."""

    seat: int
    can_fold: bool
    can_check: bool
    can_call: bool
    call_cost: int
    """Chips to add to call, already capped at the player's stack."""
    call_is_all_in: bool
    can_bet: bool
    """True when there is no outstanding bet this street."""
    can_raise: bool
    min_to: int
    """Minimum legal BET/RAISE total, already clamped down to an all-in."""
    max_to: int
    """street_committed + stack -- i.e. shoving."""
    stack: int

    @property
    def to_call(self) -> int:
        return self.call_cost

    @property
    def can_aggress(self) -> bool:
        return self.can_bet or self.can_raise

    def describe(self) -> str:
        parts = []
        if self.can_fold:
            parts.append("fold")
        if self.can_check:
            parts.append("check")
        if self.can_call:
            parts.append(f"call {self.call_cost}")
        if self.can_bet:
            parts.append(f"bet {self.min_to}..{self.max_to}")
        if self.can_raise:
            parts.append(f"raise to {self.min_to}..{self.max_to}")
        return ", ".join(parts)
