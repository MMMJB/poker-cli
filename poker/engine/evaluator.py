"""Seven-card hand evaluation.

:func:`evaluate7` ranks 5, 6, or 7 cards into a packed integer where **higher
is better and equality means an exact tie** (a chopped pot).  No other
comparison function is needed anywhere in the codebase.

    value = (category << 20) | r1<<16 | r2<<12 | r3<<8 | r4<<4 | r5

``r1..r5`` are rank indices, zero-padded for slots a category doesn't use.
The padding is what makes ``==`` a valid chop test: two hands in the same
category always fill the same slots, so an unused slot can never separate them.

The implementation evaluates 7 cards directly rather than scoring all 21
five-card subsets.  ``tests/reference_eval.py`` keeps the slow subset version
as the correctness oracle.
"""

from __future__ import annotations

from itertools import combinations
from typing import Sequence

from poker.engine.cards import Card, RANK_NAMES, RANK_PLURALS

HIGH_CARD = 0
PAIR = 1
TWO_PAIR = 2
TRIPS = 3
STRAIGHT = 4
FLUSH = 5
FULL_HOUSE = 6
QUADS = 7
STRAIGHT_FLUSH = 8

CATEGORY_NAMES = (
    "High card", "One pair", "Two pair", "Three of a kind", "Straight",
    "Flush", "Full house", "Four of a kind", "Straight flush",
)

_MASK_BITS = 1 << 13


def _build_straight_table() -> list[int]:
    """``table[rank_mask]`` -> high rank of the best straight, or -1.

    Built by brute force over the ten straight patterns, the wheel included, so
    there is no hand-rolled bit-shifting to get wrong.
    """
    patterns: list[tuple[int, int]] = []
    for high in range(4, 13):  # 6-high (index 4) through Ace-high (index 12)
        patterns.append((high, sum(1 << (high - i) for i in range(5))))
    # The wheel A-5-4-3-2 ranks as Five-high (index 3).
    patterns.append((3, (1 << 12) | (1 << 3) | (1 << 2) | (1 << 1) | 1))
    patterns.sort(key=lambda p: p[0], reverse=True)

    table = [-1] * _MASK_BITS
    for mask in range(_MASK_BITS):
        for high, pattern in patterns:
            if mask & pattern == pattern:
                table[mask] = high
                break
    return table


def _build_top5_table() -> list[tuple[int, int, int, int, int]]:
    """``table[rank_mask]`` -> the five highest set ranks, zero-padded."""
    table: list[tuple[int, int, int, int, int]] = []
    for mask in range(_MASK_BITS):
        out: list[int] = []
        for r in range(12, -1, -1):
            if mask >> r & 1:
                out.append(r)
                if len(out) == 5:
                    break
        while len(out) < 5:
            out.append(0)
        table.append((out[0], out[1], out[2], out[3], out[4]))
    return table


_STRAIGHT_TABLE = _build_straight_table()
_TOP5 = _build_top5_table()


def _pack(cat: int, r1: int = 0, r2: int = 0, r3: int = 0, r4: int = 0, r5: int = 0) -> int:
    return (cat << 20) | (r1 << 16) | (r2 << 12) | (r3 << 8) | (r4 << 4) | r5


def evaluate7(cards: Sequence[Card]) -> int:
    """Rank 5-7 cards.  Higher is better; equality is an exact tie."""
    rank_count = [0] * 13
    suit_masks = [0, 0, 0, 0]
    suit_counts = [0, 0, 0, 0]
    rank_mask = 0

    for c in cards:
        r = c >> 2
        s = c & 3
        rank_count[r] += 1
        suit_masks[s] |= 1 << r
        suit_counts[s] += 1
        rank_mask |= 1 << r

    # Flush facts are cheap, so compute them up front -- but do NOT return a
    # flush before checking quads and a full house, which both outrank it.
    flush_suit = -1
    for s in range(4):
        if suit_counts[s] >= 5:
            flush_suit = s  # with 7 cards at most one suit can qualify
            break

    if flush_suit >= 0:
        sf_high = _STRAIGHT_TABLE[suit_masks[flush_suit]]
        if sf_high >= 0:
            return _pack(STRAIGHT_FLUSH, sf_high)

    quads: list[int] = []
    trips: list[int] = []
    pairs: list[int] = []
    for r in range(12, -1, -1):
        n = rank_count[r]
        if n == 4:
            quads.append(r)
        elif n == 3:
            trips.append(r)
        elif n == 2:
            pairs.append(r)

    if quads:
        q = quads[0]
        kicker = _TOP5[rank_mask & ~(1 << q)][0]
        return _pack(QUADS, q, kicker)

    if trips:
        # Two sets of trips is the classic bug: the lower trips plays as a pair.
        if len(trips) >= 2:
            return _pack(FULL_HOUSE, trips[0], max(trips[1], pairs[0] if pairs else 0))
        if pairs:
            return _pack(FULL_HOUSE, trips[0], pairs[0])

    if flush_suit >= 0:
        return _pack(FLUSH, *_TOP5[suit_masks[flush_suit]])

    straight_high = _STRAIGHT_TABLE[rank_mask]
    if straight_high >= 0:
        return _pack(STRAIGHT, straight_high)

    if trips:
        t = trips[0]
        k1, k2 = _TOP5[rank_mask & ~(1 << t)][:2]
        return _pack(TRIPS, t, k1, k2)

    if len(pairs) >= 2:
        hi, lo = pairs[0], pairs[1]
        # The kicker is the highest remaining rank -- which may be a THIRD pair.
        kicker = _TOP5[rank_mask & ~(1 << hi) & ~(1 << lo)][0]
        return _pack(TWO_PAIR, hi, lo, kicker)

    if pairs:
        p = pairs[0]
        k1, k2, k3 = _TOP5[rank_mask & ~(1 << p)][:3]
        return _pack(PAIR, p, k1, k2, k3)

    return _pack(HIGH_CARD, *_TOP5[rank_mask])


def category_of(value: int) -> int:
    return value >> 20


def ranks_of(value: int) -> tuple[int, int, int, int, int]:
    return (
        (value >> 16) & 0xF, (value >> 12) & 0xF, (value >> 8) & 0xF,
        (value >> 4) & 0xF, value & 0xF,
    )


def best_five(cards: Sequence[Card]) -> tuple[Card, ...]:
    """The five cards forming the best hand.

    Only called at showdown (for the renderer and the coach), so the simple
    max-over-subsets implementation is the right one here.
    """
    if len(cards) == 5:
        return tuple(cards)
    return max(combinations(cards, 5), key=evaluate7)


def describe(value: int) -> str:
    """A human sentence, e.g. ``"Two pair, Aces and Kings, Queen kicker"``."""
    cat = category_of(value)
    r1, r2, r3, r4, r5 = ranks_of(value)
    if cat == STRAIGHT_FLUSH:
        if r1 == 12:
            return "Royal flush"
        return f"Straight flush, {RANK_NAMES[r1]}-high"
    if cat == QUADS:
        return f"Four of a kind, {RANK_PLURALS[r1]}, {RANK_NAMES[r2]} kicker"
    if cat == FULL_HOUSE:
        return f"Full house, {RANK_PLURALS[r1]} full of {RANK_PLURALS[r2]}"
    if cat == FLUSH:
        return f"Flush, {RANK_NAMES[r1]}-high"
    if cat == STRAIGHT:
        return f"Straight, {RANK_NAMES[r1]}-high"
    if cat == TRIPS:
        return f"Three of a kind, {RANK_PLURALS[r1]}, {RANK_NAMES[r2]} kicker"
    if cat == TWO_PAIR:
        return f"Two pair, {RANK_PLURALS[r1]} and {RANK_PLURALS[r2]}, {RANK_NAMES[r3]} kicker"
    if cat == PAIR:
        return f"Pair of {RANK_PLURALS[r1]}, {RANK_NAMES[r2]} kicker"
    return f"{RANK_NAMES[r1]}-high"
