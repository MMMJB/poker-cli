"""Evaluator correctness.

The two heavyweight tests here -- the full five-card category census and the
cross-validation against the brute-force oracle -- are what actually buy
confidence in a hand-rolled evaluator.  The named cases below them pin the
specific traps.
"""

from __future__ import annotations

import random
from itertools import combinations

import pytest

from poker.engine.cards import DECK_SIZE, parse_cards
from poker.engine.evaluator import (
    FLUSH, FULL_HOUSE, PAIR, QUADS, STRAIGHT, STRAIGHT_FLUSH, TRIPS, TWO_PAIR,
    best_five, category_of, describe, evaluate7, ranks_of,
)
from tests.reference_eval import evaluate_ref, score_best

# Known five-card hand frequencies, indexed by our category numbering.
EXPECTED_FREQUENCIES = {
    0: 1302540,   # high card
    1: 1098240,   # one pair
    2: 123552,    # two pair
    3: 54912,     # trips
    4: 10200,     # straight
    5: 5108,      # flush
    6: 3744,      # full house
    7: 624,       # quads
    8: 40,        # straight flush
}


def ev(s: str) -> int:
    return evaluate7(parse_cards(s))


@pytest.mark.slow
def test_five_card_category_census() -> None:
    """Every one of the 2,598,960 five-card hands, bucketed by category.

    A single test that catches essentially every categorization bug.
    """
    counts = dict.fromkeys(EXPECTED_FREQUENCIES, 0)
    for combo in combinations(range(DECK_SIZE), 5):
        counts[category_of(evaluate7(combo))] += 1
    assert counts == EXPECTED_FREQUENCIES


@pytest.mark.slow
def test_cross_validation_against_oracle() -> None:
    """Agree with the brute-force oracle on value AND on ordering."""
    rng = random.Random(0xC0FFEE)
    deck = list(range(DECK_SIZE))
    previous: tuple[list[int], int] | None = None
    for _ in range(200_000):
        hand = rng.sample(deck, 7)
        mine = evaluate7(hand)
        theirs = evaluate_ref(hand)
        assert mine == theirs, (
            f"{[c for c in hand]}: got {mine} ({describe(mine)}), "
            f"oracle {theirs} (score {score_best(hand)})"
        )
        if previous is not None:
            prev_hand, prev_val = previous
            # Ordering must agree too, not just the packed values.
            assert (mine > prev_val) == (theirs > evaluate_ref(prev_hand))
        previous = (hand, mine)


def test_cross_validation_small_sample() -> None:
    """A fast subset of the above, so the default test run still covers it."""
    rng = random.Random(7)
    deck = list(range(DECK_SIZE))
    for _ in range(5_000):
        hand = rng.sample(deck, 7)
        assert evaluate7(hand) == evaluate_ref(hand)


def test_category_ordering() -> None:
    """Full house beats flush beats straight -- the check-order regression."""
    assert ev("Ah Ac Ad Kh Kc 2s 3d") > ev("Ah Kh Qh 9h 2h 3s 4d")   # boat > flush
    assert ev("Ah Kh Qh 9h 2h 3s 4d") > ev("9c 8d 7h 6s 5c 2d 3h")   # flush > straight
    assert ev("As Ac Ad Kh Qc 2s 3d") > ev("Ah Ac Kh Kc Qd 2s 3h")   # trips > two pair
    assert ev("2c 2d 2h 2s 3c 4d 5h") > ev("Ah Ac Ad Kh Kc 2s 3d")   # quads > boat


def test_wheel_is_the_worst_straight() -> None:
    wheel = ev("Ah 2c 3d 4s 5h 9c Kd")
    six_high = ev("2h 3c 4d 5s 6h 9c Kd")
    assert category_of(wheel) == STRAIGHT
    assert category_of(six_high) == STRAIGHT
    assert six_high > wheel
    assert ranks_of(wheel)[0] == 3  # ranked as Five-high


def test_wheel_straight_flush() -> None:
    v = ev("Ah 2h 3h 4h 5h 9c Kd")
    assert category_of(v) == STRAIGHT_FLUSH
    assert ranks_of(v)[0] == 3
    assert v < ev("2h 3h 4h 5h 6h 9c Kd")


def test_royal_flush_is_the_maximum() -> None:
    v = ev("Ah Kh Qh Jh Th 2c 3d")
    assert category_of(v) == STRAIGHT_FLUSH
    assert describe(v) == "Royal flush"


def test_six_and_seven_card_flushes_use_top_five() -> None:
    six = ev("Ah Kh 9h 7h 4h 2h 3c")
    assert category_of(six) == FLUSH
    assert ranks_of(six) == (12, 11, 7, 5, 2)  # A K 9 7 4
    seven = ev("Ah Kh 9h 7h 4h 2h 3h")
    assert ranks_of(seven) == (12, 11, 7, 5, 2)


def test_flush_beats_a_mixed_suit_straight_on_the_same_board() -> None:
    """Board makes a straight in mixed suits; the player holds a flush."""
    v = ev("Ah Kh 9h 5h 2h 6c 7d")
    assert category_of(v) == FLUSH


def test_straight_flush_when_both_live_in_the_flush_suit() -> None:
    v = ev("9h 8h 7h 6h 5h Ac Kd")
    assert category_of(v) == STRAIGHT_FLUSH
    assert ranks_of(v)[0] == 7  # Nine-high


def test_straight_present_only_in_mixed_suits_is_not_a_straight_flush() -> None:
    """Flush suit has no straight; the mixed-suit straight must not upgrade."""
    v = ev("9h 8c 7h 6h 5h 2h 3d")  # hearts: 9 7 6 5 2 -> flush, not straight
    assert category_of(v) == FLUSH


def test_three_pairs_kicker_is_the_third_pairs_rank() -> None:
    v = ev("Ah Ac Kh Kc Qh Qc 2d")
    assert category_of(v) == TWO_PAIR
    assert ranks_of(v)[:3] == (12, 11, 10)  # aces & kings, QUEEN kicker
    assert describe(v) == "Two pair, Aces and Kings, Queen kicker"


def test_three_pairs_low_third_pair_still_beats_a_lone_kicker() -> None:
    """The third pair competes with the loose cards for the kicker slot."""
    v = ev("Ah Ac Kh Kc 5h 5c 4d")
    assert ranks_of(v)[:3] == (12, 11, 3)  # five kicker beats the four


def test_two_sets_of_trips_makes_a_boat() -> None:
    v = ev("Ah Ac Ad Kh Kc Kd 2s")
    assert category_of(v) == FULL_HOUSE
    assert ranks_of(v)[:2] == (12, 11)  # aces full of kings


def test_trips_plus_lower_pair_prefers_the_higher_pair() -> None:
    v = ev("9h 9c 9d Kh Kc 2s 2d")
    assert category_of(v) == FULL_HOUSE
    assert ranks_of(v)[:2] == (7, 11)  # nines full of KINGS, not deuces


def test_two_trips_prefers_the_higher_pair_between_trips_and_pair() -> None:
    v = ev("5h 5c 5d 3h 3c 3d Kh")
    assert category_of(v) == FULL_HOUSE
    assert ranks_of(v)[:2] == (3, 1)  # fives full of threes


def test_quads_on_board_with_a_pocket_pair() -> None:
    """Board is quads; the best kicker is the highest remaining card."""
    v = ev("7h 7c 7d 7s Ah Kc Kd")
    assert category_of(v) == QUADS
    assert ranks_of(v)[:2] == (5, 12)  # quad sevens, ACE kicker


def test_board_plays_for_both_players_is_an_exact_chop() -> None:
    board = "Ah Kh Qh Jh Th"
    a = ev(f"{board} 2c 3d")
    b = ev(f"{board} 4s 5c")
    assert a == b


def test_counterfeited_two_pair_on_the_river() -> None:
    """Holding 65 on A-A-K-K-Q: the board's two pair plus an ace kicker plays."""
    v = ev("Ah Ac Kh Kc Qd 6s 5s")
    assert category_of(v) == TWO_PAIR
    assert ranks_of(v)[:3] == (12, 11, 10)


def test_pair_uses_three_kickers() -> None:
    v = ev("Ah Ac Kh Qc Jd 3s 2h")
    assert category_of(v) == PAIR
    assert ranks_of(v)[:4] == (12, 11, 10, 9)


def test_trips_uses_two_kickers() -> None:
    v = ev("Ah Ac Ad Kh Qc 3s 2h")
    assert category_of(v) == TRIPS
    assert ranks_of(v)[:3] == (12, 11, 10)


def test_best_five_matches_the_evaluated_value() -> None:
    rng = random.Random(99)
    deck = list(range(DECK_SIZE))
    for _ in range(500):
        hand = rng.sample(deck, 7)
        five = best_five(hand)
        assert len(five) == 5
        assert set(five) <= set(hand)
        assert evaluate7(five) == evaluate7(hand)


def test_evaluate_handles_five_and_six_cards() -> None:
    assert evaluate7(parse_cards("Ah Kh Qh Jh Th")) == evaluate7(
        parse_cards("Ah Kh Qh Jh Th")
    )
    six = evaluate7(parse_cards("Ah Kh Qh Jh Th 2c"))
    assert category_of(six) == STRAIGHT_FLUSH
