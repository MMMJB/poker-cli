"""Showdown badges must not make a side pot look like a chop.

Two bare "WINS" badges read as a split, which is the opposite of what happened:
a short all-in wins only up to what it matched, and the rest goes to the best
remaining hand.
"""

from __future__ import annotations

from poker.app.view import build_seats, win_labels_for
from poker.engine.actions import Action
from poker.engine.hand import HandEngine
from poker.engine.pots import Award, AwardReason, Pot
from poker.engine.state import GameConfig
from poker.engine.table import Table

NAMES = ["You", "Sofia", "Walter", "Raj", "Tank", "Deb"]


class FakeResult:
    def __init__(self, pots, awards):
        self.pots = tuple(pots)
        self.awards = tuple(awards)


def award(pot: int, seat: int, amount: int) -> Award:
    return Award(pot, seat, amount, AwardReason.SHOWDOWN)


def test_a_single_pot_needs_no_qualifier() -> None:
    result = FakeResult([Pot(200, frozenset({0, 1}))], [award(0, 0, 200)])
    assert win_labels_for(result) == {}


def test_a_genuine_chop_is_not_labelled_main_or_side() -> None:
    """One pot, two winners really is a split -- both should just say WINS."""
    result = FakeResult(
        [Pot(100, frozenset({0, 1}))],
        [award(0, 0, 50), award(0, 1, 50)],
    )
    assert win_labels_for(result) == {}


def test_main_and_side_are_named() -> None:
    """The real hand 50: Raj all-in short takes the main, Deb takes the rest."""
    result = FakeResult(
        [Pot(1100, frozenset({1, 3, 4, 5})),
         Pot(87, frozenset({1, 4, 5})),
         Pot(122, frozenset({1, 5}))],
        [award(0, 3, 1100), award(1, 5, 87), award(2, 5, 122)],
    )
    assert win_labels_for(result) == {3: "MAIN", 5: "SIDE"}


def test_a_seat_that_takes_every_pot_is_not_called_main() -> None:
    result = FakeResult(
        [Pot(100, frozenset({0, 1})), Pot(50, frozenset({0}))],
        [award(0, 0, 100), award(1, 0, 50)],
    )
    assert win_labels_for(result) == {0: "WINS"}


def test_a_side_pot_winner_who_missed_the_main_says_side() -> None:
    result = FakeResult(
        [Pot(300, frozenset({0, 1, 2})), Pot(80, frozenset({1, 2}))],
        [award(0, 0, 300), award(1, 2, 80)],
    )
    labels = win_labels_for(result)
    assert labels[0] == "MAIN"
    assert labels[2] == "SIDE"


def test_labels_reach_the_rendered_seat() -> None:
    table = Table(GameConfig(), NAMES, human_seat=0, session_seed=5)
    engine = table.start_hand()
    seats = build_seats(engine, table, winners={3: 1100, 5: 209},
                        win_labels={3: "MAIN", 5: "SIDE"})
    by_seat = {s.seat: s for s in seats}
    assert by_seat[3].win_label == "MAIN"
    assert by_seat[5].win_label == "SIDE"
    assert by_seat[1].win_label == "WINS"  # default for everyone else


def test_badges_read_correctly_for_each_case() -> None:
    from poker.ui.table import _seat_note

    seats = build_seats(
        Table(GameConfig(), NAMES, human_seat=0, session_seed=5).start_hand(),
        Table(GameConfig(), NAMES, human_seat=0, session_seed=5),
        winners={3: 1100, 5: 209},
        win_labels={3: "MAIN", 5: "SIDE"},
    )
    notes = {s.seat: _seat_note(s) for s in seats}
    assert notes[3] == "MAIN $1,100"
    assert notes[5] == "SIDE $209"


def test_a_real_side_pot_hand_labels_main_and_side() -> None:
    """Drive the engine into a genuine multi-pot showdown."""
    engine = HandEngine(GameConfig(), NAMES, [300, 60, 300, 300, 300, 300],
                        button=0, hand_id=1, seed=17)
    engine.start()
    engine.apply(Action.raise_to(200))   # UTG
    engine.apply(Action.call())          # HJ
    engine.apply(Action.fold())          # CO
    engine.apply(Action.fold())          # BTN
    engine.apply(Action.call())          # SB, all-in for 60
    engine.apply(Action.fold())          # BB
    while not engine.is_complete():
        legal = engine.legal_actions()
        engine.apply(Action.check() if legal.can_check else Action.call())

    result = engine.result()
    assert len(result.pots) >= 2, "expected a side pot"
    labels = win_labels_for(result)
    # Whatever the cards did, a multi-pot result must be labelled, and never
    # leave two unqualified badges that read as a chop.
    assert labels
    assert set(labels.values()) <= {"MAIN", "SIDE", "WINS"}
    if len(labels) > 1:
        assert "WINS" not in labels.values()
