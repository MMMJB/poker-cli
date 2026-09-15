"""Side pots, odd chips, and uncalled bets."""

from __future__ import annotations

import random

from poker.engine.actions import Action
from poker.engine.pots import (
    AwardReason, Pot, award_pots, award_uncontested, build_pots,
    return_uncalled_bet,
)
from tests.helpers import act, make_state, start_postflop, start_preflop


def test_simple_two_way_pot_has_no_side_pot() -> None:
    pots = build_pots({0: 50, 1: 50}, {0, 1})
    assert pots == [Pot(100, frozenset({0, 1}))]


def test_three_way_with_two_all_ins_at_different_depths() -> None:
    pots = build_pots({0: 100, 1: 50, 2: 200, 3: 200}, {0, 1, 2, 3})
    assert pots == [
        Pot(200, frozenset({0, 1, 2, 3})),  # 4 x 50
        Pot(150, frozenset({0, 2, 3})),     # 3 x 50
        Pot(200, frozenset({2, 3})),        # 2 x 100
    ]


def test_folded_players_money_lands_in_the_pot_but_never_in_eligible() -> None:
    """Seat 1 called 50 then folded: the chips stay, the eligibility does not."""
    pots = build_pots({0: 100, 1: 50, 2: 100}, {0, 2})
    # Both layers have the same eligibility, so they are a single pot -- and
    # seat 1's dead 50 is in it.
    assert pots == [Pot(250, frozenset({0, 2}))]
    assert 1 not in pots[0].eligible


def test_adjacent_layers_with_equal_eligibility_merge() -> None:
    pots = build_pots({0: 100, 1: 50, 2: 100}, {0, 2})
    assert all(p.eligible == frozenset({0, 2}) for p in pots)
    assert sum(p.amount for p in pots) == 250


def test_pot_with_a_single_eligible_player() -> None:
    """Seat 0's bet was matched only by someone who then folded."""
    pots = build_pots({0: 200, 1: 200}, {0})
    assert pots == [Pot(400, frozenset({0}))]
    awards = award_pots(pots, {0: 999}, button=0, num_seats=6)
    assert sum(a.amount for a in awards) == 400
    assert awards[0].seat == 0


def test_layer_with_no_eligible_players_merges_down() -> None:
    """Deepest contributors all folded; their money joins the live pot."""
    pots = build_pots({0: 50, 1: 300, 2: 300}, {0})
    assert len(pots) == 1
    assert pots[0].eligible == frozenset({0})
    assert pots[0].amount == 650


def test_chopped_side_pot_with_an_odd_chip() -> None:
    # Main pot 3 x 25 = 75 (seat 2 short), side pot 2 x 50 = 100.
    pots = build_pots({0: 75, 1: 75, 2: 25}, {0, 1, 2})
    assert pots == [
        Pot(75, frozenset({0, 1, 2})),
        Pot(100, frozenset({0, 1})),
    ]
    # Seat 2 wins the main outright; seats 0 and 1 chop the side pot.
    awards = award_pots(pots, {0: 500, 1: 500, 2: 900}, button=5, num_seats=6)
    main = [a for a in awards if a.pot_index == 0]
    side = [a for a in awards if a.pot_index == 1]
    assert main == [type(main[0])(0, 2, 75, AwardReason.SHOWDOWN)]
    assert sorted(a.amount for a in side) == [50, 50]


def test_odd_chip_goes_to_the_first_winner_left_of_the_button() -> None:
    pots = [Pot(101, frozenset({0, 1}))]
    # Button 5 -> SB-first order is 0, 1, 2, ... so seat 0 gets the odd chip.
    awards = award_pots(pots, {0: 500, 1: 500}, button=5, num_seats=6)
    by_seat = {a.seat: a.amount for a in awards}
    assert by_seat == {0: 51, 1: 50}

    # Button 0 -> order is 1, 2, ... so seat 1 gets it instead.
    awards = award_pots(pots, {0: 500, 1: 500}, button=0, num_seats=6)
    by_seat = {a.seat: a.amount for a in awards}
    assert by_seat == {1: 51, 0: 50}


def test_three_way_chop_distributes_two_odd_chips() -> None:
    pots = [Pot(101, frozenset({0, 2, 4}))]
    awards = award_pots(pots, {0: 7, 2: 7, 4: 7}, button=5, num_seats=6)
    by_seat = {a.seat: a.amount for a in awards}
    assert sum(by_seat.values()) == 101
    assert by_seat == {0: 34, 2: 34, 4: 33}


def test_awards_always_conserve_the_pot() -> None:
    rng = random.Random(1234)
    for _ in range(2000):
        n = rng.randint(2, 6)
        committed = {s: rng.randint(0, 300) for s in range(n)}
        live = {s for s in range(n) if rng.random() < 0.7} or {0}
        if not any(committed[s] > 0 for s in live):
            continue  # cannot happen in a real hand: a live player always has chips in
        pots = build_pots(committed, live)
        values = {s: rng.randint(0, 20) for s in live}
        awards = award_pots(pots, values, button=rng.randrange(n), num_seats=n)
        awarded = sum(a.amount for a in awards)
        pot_total = sum(p.amount for p in pots)
        assert awarded == pot_total, (committed, live)
        assert pot_total == sum(committed.values())


def test_uncalled_bet_returned_on_an_overbet_shove() -> None:
    s = make_state([300, 300, 60, 300, 300, 300], button=0)
    start_postflop(s)
    act(s, Action.bet(200))  # seat 1 shoves over a short stack
    act(s, Action.call())    # seat 2 all-in for 60
    for _ in range(4):
        act(s, Action.fold())

    assert s.pot == 260
    result = return_uncalled_bet(s)
    assert result == (1, 140)
    assert s.players[1].stack == 300 - 60
    assert s.pot == 120


def test_uncalled_bet_returned_when_everyone_folds_to_a_raise() -> None:
    s = start_preflop(make_state())
    act(s, Action.raise_to(12))  # UTG
    for _ in range(5):
        act(s, Action.fold())

    assert s.pot == 16  # 12 + 3 + 1
    seat, amount = return_uncalled_bet(s)
    assert (seat, amount) == (3, 9)  # matched only by the BB's 3
    assert s.pot == 7


def test_folded_players_commitment_caps_the_refund() -> None:
    """Seat 2 called 200 then folded; only the excess over 200 comes back."""
    s = make_state([300, 300, 280, 300, 300, 300], button=0)
    start_postflop(s)
    act(s, Action.bet(200))      # seat 1
    act(s, Action.raise_to(280)) # seat 2, all-in (a legal short raise)
    act(s, Action.fold())        # seat 3
    act(s, Action.fold())        # seat 4
    act(s, Action.fold())        # seat 5
    act(s, Action.fold())        # seat 0
    act(s, Action.fold())        # seat 1 folds to the raise

    seat, amount = return_uncalled_bet(s)
    assert seat == 2
    assert amount == 80  # 280 - seat 1's 200


def test_no_refund_when_the_top_bet_was_matched() -> None:
    s = make_state()
    start_postflop(s)
    act(s, Action.bet(50))
    act(s, Action.call())
    for _ in range(4):
        act(s, Action.fold())
    assert return_uncalled_bet(s) is None


def test_award_uncontested_gives_every_pot_to_one_seat() -> None:
    pots = build_pots({0: 100, 1: 40}, {0})
    awards = award_uncontested(pots, 0)
    assert sum(a.amount for a in awards) == 140
    assert all(a.reason is AwardReason.UNCONTESTED for a in awards)
