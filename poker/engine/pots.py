"""Pot construction and awarding.

Two separable jobs:

1. :func:`return_uncalled_bet` -- run at the close of *every* betting round, so
   an uncalled overbet goes back before it can distort the displayed pot.
2. :func:`build_pots` / :func:`award_pots` -- split total commitments into main
   and side pots and pay them out, odd chips included.

Both are pure functions over commitments, so they are unit-testable without
constructing a hand.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from poker.engine.positions import order_from_button
from poker.engine.state import HandState


@dataclass(frozen=True, slots=True)
class Pot:
    amount: int
    eligible: frozenset[int]
    """Live seats that may win this pot.  Folded contributors never appear."""


class AwardReason(str, Enum):
    SHOWDOWN = "showdown"
    UNCONTESTED = "uncontested"
    ODD_CHIP = "odd_chip"


@dataclass(frozen=True, slots=True)
class Award:
    pot_index: int
    seat: int
    amount: int
    reason: AwardReason


def return_uncalled_bet(state: HandState) -> tuple[int, int] | None:
    """Refund the portion of a bet nobody matched.  Returns ``(seat, amount)``.

    Folded players' commitments count here: someone who called $200 and then
    folded to a later raise still has that money in, and it caps the refund.
    """
    commits = [(p.street_committed, p.seat) for p in state.players]
    commits.sort(reverse=True)
    if len(commits) < 2:
        return None

    top, top_seat = commits[0]
    second = commits[1][0]
    if top <= second:
        return None
    # A tie at the top means the bet was matched -- nothing to return.
    if commits[1][0] == top:  # pragma: no cover - implied by top <= second
        return None

    refund = top - second
    p = state.players[top_seat]
    p.stack += refund
    p.street_committed -= refund
    p.total_committed -= refund
    return top_seat, refund


def build_pots(total_committed: Mapping[int, int], live: set[int]) -> list[Pot]:
    """Split commitments into main + side pots.

    Walks the distinct commitment levels bottom-up.  At each level every player
    contributes the slice of their stack that falls in that band, and only live
    players who reached the level are eligible to win it.
    """
    levels = sorted({c for c in total_committed.values() if c > 0})
    pots: list[Pot] = []
    prev = 0

    for lvl in levels:
        amount = sum(
            min(c, lvl) - min(c, prev) for c in total_committed.values()
        )
        eligible = frozenset(
            s for s in live if total_committed.get(s, 0) >= lvl
        )
        if amount > 0:
            if pots and pots[-1].eligible == eligible:
                # Adjacent layers with identical eligibility are one pot.
                pots[-1] = Pot(pots[-1].amount + amount, eligible)
            else:
                pots.append(Pot(amount, eligible))
        prev = lvl

    return _absorb_dead_layers(pots, total_committed)


def _absorb_dead_layers(
    pots: list[Pot], total_committed: Mapping[int, int]
) -> list[Pot]:
    """Fold any layer with no eligible live player into the highest live pot.

    Reachable when every player who could win a level later folded.  If no pot
    has eligible players at all, the money is refunded pro-rata by the caller.
    """
    if all(p.eligible for p in pots):
        return pots

    live_pots = [p for p in pots if p.eligible]
    dead = sum(p.amount for p in pots if not p.eligible)
    if not live_pots:
        return pots  # caller refunds; nothing can be awarded
    live_pots[-1] = Pot(live_pots[-1].amount + dead, live_pots[-1].eligible)
    return live_pots


def award_pots(
    pots: list[Pot],
    hand_values: Mapping[int, int],
    button: int,
    num_seats: int,
) -> list[Award]:
    """Decide who gets what, odd chips included.

    Winners are decided purely from ``hand_values`` -- never from who "showed".
    Mucking is a display concept; a player who is awarded a pot wins it whether
    or not their cards were turned face up.
    """
    awards: list[Award] = []
    # SB first, BTN last: the standard order for odd chips and show order.
    tiebreak_order = order_from_button(button, num_seats)

    for i, pot in enumerate(pots):
        contenders = [s for s in pot.eligible if s in hand_values]
        if not contenders:
            continue
        best = max(hand_values[s] for s in contenders)
        # Equality of the packed evaluator value IS the chop test.
        winners = [s for s in contenders if hand_values[s] == best]
        winners.sort(key=tiebreak_order.index)

        base, remainder = divmod(pot.amount, len(winners))
        for j, seat in enumerate(winners):
            amount = base + (1 if j < remainder else 0)
            if amount == 0:
                continue
            reason = (
                AwardReason.ODD_CHIP
                if base == 0 and remainder
                else AwardReason.SHOWDOWN
            )
            awards.append(Award(i, seat, amount, reason))

    return awards


def award_uncontested(pots: list[Pot], seat: int) -> list[Award]:
    """Everyone folded: one player takes every pot."""
    return [
        Award(i, seat, pot.amount, AwardReason.UNCONTESTED)
        for i, pot in enumerate(pots)
        if pot.amount
    ]
