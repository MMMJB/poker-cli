"""A deliberately slow, obviously-correct hand evaluator.

TEST ONLY.  This is the oracle that ``poker.engine.evaluator`` is validated
against.  It ranks 7 cards by brute force over all 21 five-card subsets, each
scored into a plain ``(category, r1, r2, r3, r4, r5)`` tuple.  It is written
for readability, not speed -- if it ever disagrees with the production
evaluator, this one is presumed right.
"""

from __future__ import annotations

from itertools import combinations
from typing import Sequence

from poker.engine.cards import Card, card_rank, card_suit

# Straight patterns as sorted descending rank lists, best first.
_STRAIGHTS: list[tuple[int, list[int]]] = [
    (high, [high - i for i in range(5)]) for high in range(12, 3, -1)
]
_STRAIGHTS.append((3, [12, 3, 2, 1, 0]))  # the wheel: A-5-4-3-2, ranked as Five-high


def score5(cards: Sequence[Card]) -> tuple[int, int, int, int, int, int]:
    """Score exactly five cards into a comparable tuple."""
    assert len(cards) == 5
    ranks = sorted((card_rank(c) for c in cards), reverse=True)
    suits = [card_suit(c) for c in cards]
    rank_set = set(ranks)

    is_flush = len(set(suits)) == 1

    straight_high = -1
    for high, pattern in _STRAIGHTS:
        if rank_set == set(pattern):
            straight_high = high
            break

    # Group ranks by how many times they appear, then order by (count, rank).
    counts: dict[int, int] = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    grouped = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    shape = [n for _, n in grouped]
    ordered = [r for r, _ in grouped]

    def pad(vals: list[int]) -> tuple[int, int, int, int, int]:
        v = (vals + [0, 0, 0, 0, 0])[:5]
        return (v[0], v[1], v[2], v[3], v[4])

    if is_flush and straight_high >= 0:
        return (8,) + pad([straight_high])
    if shape == [4, 1]:
        return (7,) + pad(ordered)
    if shape == [3, 2]:
        return (6,) + pad(ordered)
    if is_flush:
        return (5,) + pad(ranks)
    if straight_high >= 0:
        return (4,) + pad([straight_high])
    if shape == [3, 1, 1]:
        return (3,) + pad(ordered)
    if shape == [2, 2, 1]:
        return (2,) + pad(ordered)
    if shape == [2, 1, 1, 1]:
        return (1,) + pad(ordered)
    return (0,) + pad(ranks)


def score_best(cards: Sequence[Card]) -> tuple[int, int, int, int, int, int]:
    """Best five-card score out of 5, 6, or 7 cards."""
    assert 5 <= len(cards) <= 7
    return max(score5(combo) for combo in combinations(cards, 5))


def pack(score: tuple[int, int, int, int, int, int]) -> int:
    """Pack a score tuple the same way the production evaluator does."""
    cat, r1, r2, r3, r4, r5 = score
    return (cat << 20) | (r1 << 16) | (r2 << 12) | (r3 << 8) | (r4 << 4) | r5


def evaluate_ref(cards: Sequence[Card]) -> int:
    return pack(score_best(cards))
