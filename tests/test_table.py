"""Session bookkeeping: stacks across hands, top-ups, and P/L accounting."""

from __future__ import annotations

from poker.engine.actions import Action
from poker.engine.state import GameConfig
from poker.engine.table import Table

NAMES = ["Hero", "Deb", "Walter", "Tank", "Raj", "Sofia"]


def table(**kw) -> Table:
    return Table(GameConfig(), NAMES, session_seed=99, **kw)


def test_button_advances_and_stacks_carry_over() -> None:
    t = table()
    for expected_button in range(6):
        e = t.start_hand()
        assert t.button == expected_button
        while not e.is_complete():
            legal = e.legal_actions()
            e.apply(Action.check() if legal.can_check else Action.fold())
        t.settle(e.result())
    assert sum(s.stack for s in t.seats) == 6 * 300
    assert all(s.hands_played == 6 for s in t.seats)


def test_topup_fires_only_below_the_threshold() -> None:
    t = table()
    t.seats[1].stack = 59   # below 20bb
    t.seats[2].stack = 61   # above it
    t.seats[3].stack = 0    # busted
    topups = t.apply_topups()
    by_seat = {x.seat: x.amount for x in topups}
    assert by_seat == {1: 241, 3: 300}
    assert t.seats[2].stack == 61


def test_topup_adds_to_the_cost_basis_so_profit_stays_honest() -> None:
    t = table()
    t.seats[3].stack = 0
    t.apply_topups()
    assert t.seats[3].stack == 300
    assert t.seats[3].total_bought_in == 600
    assert t.seats[3].profit == -300


def test_a_winning_seat_shows_positive_profit() -> None:
    t = table()
    t.seats[0].stack = 460
    assert t.seats[0].profit == 160


def test_bb_per_100() -> None:
    t = table()
    t.seats[0].stack = 300 + 90   # +30bb
    t.seats[0].hands_played = 100
    assert t.bb_per_100(0) == 30.0
    t.seats[1].hands_played = 0
    assert t.bb_per_100(1) == 0.0


def test_topups_are_applied_at_hand_start_and_logged() -> None:
    from poker.engine.events import EventType

    t = table()
    t.seats[4].stack = 10
    e = t.start_hand()
    topups = [ev for ev in e.log.all() if ev.type is EventType.TOPUP]
    assert len(topups) == 1
    assert topups[0].data["seat"] == 4
    assert e.state.players[4].stack >= 290


def test_chips_are_conserved_across_a_long_session() -> None:
    t = table()
    bought_in = 6 * 300
    for _ in range(60):
        e = t.start_hand()
        while not e.is_complete():
            legal = e.legal_actions()
            e.apply(Action.check() if legal.can_check else Action.call())
        t.settle(e.result())
    on_table = sum(s.stack for s in t.seats)
    total_bought = sum(s.total_bought_in for s in t.seats)
    assert on_table == total_bought
    assert sum(s.profit for s in t.seats) == 0
    assert total_bought >= bought_in
