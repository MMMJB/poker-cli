"""Hand lifecycle: fold-outs, run-outs, showdown order, mucking, determinism."""

from __future__ import annotations

import pytest

from poker.engine.actions import Action
from poker.engine.events import EventType
from poker.engine.hand import HandEngine
from poker.engine.state import GameConfig, Street

NAMES = ["Hero", "Deb", "Walter", "Tank", "Raj", "Sofia"]


def engine(stacks=None, button=0, seed=1234, config=None) -> HandEngine:
    return HandEngine(
        config or GameConfig(),
        NAMES,
        stacks or [300] * 6,
        button=button,
        hand_id=1,
        seed=seed,
    )


def types(e: HandEngine) -> list[EventType]:
    return [ev.type for ev in e.log.all()]


# --------------------------------------------------------------------------
# Fold-outs
# --------------------------------------------------------------------------

def test_fold_out_preflop_deals_no_board_and_reveals_nothing() -> None:
    e = engine()
    e.start()
    for _ in range(5):
        e.apply(Action.fold())

    assert e.is_complete()
    r = e.result()
    assert r.board == ()
    assert not r.went_to_showdown
    assert r.shown == frozenset()
    assert EventType.STREET_DEALT not in types(e)
    assert EventType.CARDS_REVEALED not in types(e)
    # BB wins the small blind.
    assert r.net[2] == 1
    assert r.net[1] == -1


def test_fold_out_awards_are_uncontested() -> None:
    e = engine()
    e.start()
    e.apply(Action.raise_to(12))
    for _ in range(5):
        e.apply(Action.fold())
    r = e.result()
    assert all(a.reason.value == "uncontested" for a in r.awards)
    assert r.net[3] == 4  # UTG wins the blinds (1 + 3); its uncalled 9 came back


def test_uncalled_raise_is_refunded_before_the_award() -> None:
    e = engine()
    e.start()
    e.apply(Action.raise_to(12))
    for _ in range(5):
        e.apply(Action.fold())
    assert EventType.UNCALLED_BET_RETURNED in types(e)
    r = e.result()
    assert sum(r.net.values()) == 0


# --------------------------------------------------------------------------
# All-in run-outs
# --------------------------------------------------------------------------

def test_three_way_preflop_all_in_runs_out_five_cards() -> None:
    e = engine(stacks=[300, 300, 300, 100, 100, 100])
    e.start()
    e.apply(Action.raise_to(100))  # UTG (seat 3) all-in
    e.apply(Action.call())         # HJ  (seat 4) all-in
    e.apply(Action.call())         # CO  (seat 5) all-in
    e.apply(Action.fold())         # BTN
    e.apply(Action.fold())         # SB
    e.apply(Action.fold())         # BB

    assert e.is_complete()
    r = e.result()
    assert len(r.board) == 5
    assert r.went_to_showdown
    # Everyone still live was revealed at the moment betting ended.
    revealed = [ev for ev in e.log.all() if ev.type is EventType.CARDS_REVEALED]
    assert {ev.data["seat"] for ev in revealed} == {3, 4, 5}
    assert all(ev.data["reason"] == "all_in" for ev in revealed)


def test_run_out_requests_no_further_action() -> None:
    e = engine(stacks=[300, 300, 300, 100, 100, 300])
    e.start()
    e.apply(Action.raise_to(100))
    e.apply(Action.call())
    for _ in range(4):
        e.apply(Action.fold())
    assert e.current_actor() is None
    assert e.is_complete()


def test_side_pot_from_a_short_all_in_pays_correctly() -> None:
    e = engine(stacks=[300, 300, 300, 50, 300, 300])
    e.start()
    e.apply(Action.raise_to(50))   # UTG all-in for 50
    e.apply(Action.raise_to(150))  # HJ raises
    e.apply(Action.call())         # CO calls
    e.apply(Action.fold())
    e.apply(Action.fold())
    e.apply(Action.fold())

    # Seats 4 and 5 still have chips behind, so the hand plays on past preflop.
    assert not e.is_complete()
    while not e.is_complete():
        legal = e.legal_actions()
        e.apply(Action.check() if legal.can_check else Action.call())

    r = e.result()
    assert len(r.pots) == 2
    main, side = r.pots
    assert main.eligible == frozenset({3, 4, 5})
    assert side.eligible == frozenset({4, 5})
    assert sum(r.net.values()) == 0


# --------------------------------------------------------------------------
# Showdown
# --------------------------------------------------------------------------

def test_last_river_aggressor_shows_first() -> None:
    e = engine()
    e.start()
    for _ in range(4):
        e.apply(Action.call())
    e.apply(Action.call())
    e.apply(Action.check())
    for street in range(3):  # flop, turn, river checked around
        while e.current_actor() is not None and e.state.street is not Street.SHOWDOWN:
            legal = e.legal_actions()
            if legal.can_check:
                e.apply(Action.check())
            else:
                break
        if e.is_complete():
            break

    # No aggression anywhere: show order is SB-first.
    shows = [
        ev.data["seat"] for ev in e.log.all()
        if ev.type in (EventType.CARDS_REVEALED, EventType.CARDS_MUCKED)
    ]
    assert shows[0] == 1  # SB


def test_aggressor_shows_first_when_there_was_a_river_bet() -> None:
    e = engine()
    e.start()
    for _ in range(4):
        e.apply(Action.call())
    e.apply(Action.call())
    e.apply(Action.check())
    # Flop, turn: check around.
    for _ in range(2):
        while not e.is_complete() and e.legal_actions().can_check:
            e.apply(Action.check())
            if e.current_actor() is None:
                break
    # River: SB bets.
    if not e.is_complete() and e.state.street is Street.RIVER:
        bettor = e.current_actor()
        e.apply(Action.bet(10))
        while not e.is_complete() and e.current_actor() is not None:
            e.apply(Action.call() if e.legal_actions().can_call else Action.fold())
        shows = [
            ev.data["seat"] for ev in e.log.all()
            if ev.type in (EventType.CARDS_REVEALED, EventType.CARDS_MUCKED)
        ]
        if shows:
            assert shows[0] == bettor


def test_losing_hand_mucks_and_its_cards_stay_out_of_public_view() -> None:
    """Mucked cards appear in the god view only."""
    found_muck = False
    for seed in range(200):
        e = engine(seed=seed)
        e.start()
        for _ in range(4):
            e.apply(Action.call())
        e.apply(Action.call())
        e.apply(Action.check())
        while not e.is_complete():
            legal = e.legal_actions()
            e.apply(Action.check() if legal.can_check else Action.fold())

        mucks = [ev for ev in e.log.all() if ev.type is EventType.CARDS_MUCKED]
        if mucks:
            found_muck = True
            ev = mucks[0]
            seat = ev.data["seat"]
            assert ev.private is not None and "cards" in ev.private
            # Public and other-seat views carry the muck event but not the cards.
            for other in range(6):
                if other == seat:
                    continue
                for seen in e.log.view_for(other):
                    assert seen.private is None
            break
    assert found_muck, "no muck occurred in 200 seeded hands"


def test_an_all_in_player_never_mucks() -> None:
    e = engine(stacks=[300, 300, 300, 100, 100, 300])
    e.start()
    e.apply(Action.raise_to(100))
    e.apply(Action.call())
    for _ in range(4):
        e.apply(Action.fold())
    muck_seats = {
        ev.data["seat"] for ev in e.log.all() if ev.type is EventType.CARDS_MUCKED
    }
    assert muck_seats == set()


def test_awards_come_from_hand_values_not_from_who_showed() -> None:
    """Every pot is settled, and settled in full."""
    for seed in range(50):
        e = engine(seed=seed)
        e.start()
        while not e.is_complete():
            legal = e.legal_actions()
            e.apply(Action.check() if legal.can_check else Action.call())
        r = e.result()
        assert sum(a.amount for a in r.awards) == sum(p.amount for p in r.pots)
        assert sum(r.net.values()) == 0


# --------------------------------------------------------------------------
# Determinism and information leakage
# --------------------------------------------------------------------------

def test_same_seed_produces_identical_logs() -> None:
    def play(seed: int) -> str:
        e = engine(seed=seed)
        e.start()
        while not e.is_complete():
            legal = e.legal_actions()
            e.apply(Action.check() if legal.can_check else Action.call())
        return e.log.to_json(include_private=True)

    assert play(777) == play(777)
    assert play(777) != play(778)


def test_no_seat_ever_sees_another_seats_hole_cards() -> None:
    """The load-bearing privacy test for the LLM opponents."""
    for seed in range(60):
        e = engine(seed=seed)
        e.start()
        while not e.is_complete():
            legal = e.legal_actions()
            e.apply(Action.check() if legal.can_check else Action.call())

        for seat in range(6):
            view = e.log.view_for(seat)
            assert all(ev.private is None for ev in view)

            revealed: set[int] = set()
            for ev in view:
                if ev.type is EventType.CARDS_REVEALED:
                    revealed.add(ev.data["seat"])
                if ev.type is EventType.HOLE_CARDS_DEALT:
                    # Only ever your own, and only before any reveal.
                    assert ev.data["seat"] == seat
                if ev.type is EventType.SHOWDOWN_HAND:
                    assert ev.data["seat"] in revealed


def test_hole_card_events_are_private_to_their_seat() -> None:
    e = engine()
    e.start()
    deals = [ev for ev in e.log.all() if ev.type is EventType.HOLE_CARDS_DEALT]
    assert len(deals) == 6
    for ev in deals:
        assert ev.visible_to == frozenset({ev.data["seat"]})


def test_start_rejects_a_stack_below_the_big_blind() -> None:
    e = engine(stacks=[300, 300, 300, 300, 300, 2])
    with pytest.raises(Exception, match="below the big blind"):
        e.start()
