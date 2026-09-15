"""Betting round rules.

These cases are the traps: the big-blind option, short all-ins not reopening
the action, min-raise escalation, and the guard clauses in
``is_round_complete``.
"""

from __future__ import annotations

import pytest

from poker.engine.actions import Action
from poker.engine.betting import is_round_complete, legal_actions
from poker.engine.errors import IllegalAction
from poker.engine.state import PlayerStatus, Street
from tests.helpers import act, make_state, start_postflop, start_preflop


# --------------------------------------------------------------------------
# The big-blind option
# --------------------------------------------------------------------------

def test_bb_option_after_everyone_limps() -> None:
    """Four limps and the SB completes: the BB still has the option."""
    s = start_preflop(make_state())
    assert s.to_act == 3  # UTG
    for _ in range(4):    # UTG, HJ, CO, BTN limp
        act(s, Action.call())
    act(s, Action.call())  # SB completes

    assert not is_round_complete(s), "BB must still get the option"
    assert s.to_act == 2  # BB

    legal = legal_actions(s, 2)
    assert legal.can_check
    assert legal.can_raise
    assert legal.min_to == 6  # raise to 2bb


def test_bb_checks_to_close_preflop() -> None:
    s = start_preflop(make_state())
    for _ in range(5):
        act(s, Action.call())
    act(s, Action.check())
    assert is_round_complete(s)
    assert s.to_act is None


def test_bb_raising_its_option_reopens_the_action() -> None:
    s = start_preflop(make_state())
    for _ in range(5):
        act(s, Action.call())
    act(s, Action.raise_to(12))

    assert not is_round_complete(s)
    assert s.to_act == 3  # back around to UTG
    legal = legal_actions(s, 3)
    assert legal.can_raise
    assert legal.call_cost == 9
    assert legal.min_to == 21  # 12 + a 9 increment


def test_sb_completing_is_not_a_raise() -> None:
    """The SB completing to the BB does not reopen anything."""
    s = start_preflop(make_state())
    for _ in range(4):
        act(s, Action.call())
    act(s, Action.call())   # SB completes to 3
    assert s.current_bet == 3
    assert s.last_full_raise_size == 3


def test_everyone_folds_to_the_big_blind() -> None:
    s = start_preflop(make_state())
    for _ in range(5):
        act(s, Action.fold())
    assert is_round_complete(s)
    assert len(s.live_players()) == 1
    assert s.live_players()[0].seat == 2  # BB


# --------------------------------------------------------------------------
# Short all-ins do not reopen the action
# --------------------------------------------------------------------------

def test_short_all_in_does_not_reopen_for_players_who_acted() -> None:
    """Bet 10, call, then an all-in to 15 -- a raise of only 5."""
    s = make_state([300, 300, 300, 300, 300, 15], button=0)
    start_postflop(s)
    assert s.to_act == 1  # SB first postflop

    act(s, Action.bet(10))       # seat 1 bets 10
    act(s, Action.call())        # seat 2 calls
    act(s, Action.fold())        # seat 3
    act(s, Action.fold())        # seat 4
    act(s, Action.raise_to(15))  # seat 5 all-in for 15 (a short raise)

    assert s.current_bet == 15
    assert s.last_full_raise_size == 10, "a short all-in must not change the step"

    # Seat 1 already acted: fold or call only.
    l1 = legal_actions(s, 1)
    assert l1.can_call and l1.call_cost == 5
    assert not l1.can_raise, "short all-in must not reopen the action"


def test_short_all_in_leaves_min_raise_at_the_last_full_increment() -> None:
    """A player yet to act may still raise -- to 25, not 20."""
    s = make_state([300, 300, 300, 300, 300, 15], button=0)
    start_postflop(s)
    act(s, Action.bet(10))   # seat 1
    act(s, Action.call())    # seat 2
    act(s, Action.fold())    # seat 3
    act(s, Action.fold())    # seat 4
    act(s, Action.raise_to(15))  # seat 5, all-in short

    assert s.to_act == 0, "seat 0 has not acted yet this street"
    l0 = legal_actions(s, 0)
    assert l0.can_raise
    assert l0.min_to == 25, "min raise is current_bet + last FULL increment"


def test_a_full_all_in_raise_does_reopen() -> None:
    s = make_state([300, 300, 300, 300, 300, 30], button=0)
    start_postflop(s)
    act(s, Action.bet(10))       # seat 1
    act(s, Action.call())        # seat 2
    act(s, Action.fold())        # seat 3
    act(s, Action.fold())        # seat 4
    act(s, Action.raise_to(30))  # seat 5 all-in: a full 20 raise

    assert s.last_full_raise_size == 20
    act(s, Action.fold())        # seat 0
    l1 = legal_actions(s, 1)
    assert l1.can_raise, "a full raise reopens the action"
    assert l1.min_to == 50


def test_all_in_call_for_less_changes_nothing() -> None:
    s = make_state([300, 300, 300, 300, 300, 4], button=0)
    start_postflop(s)
    act(s, Action.bet(10))   # seat 1
    act(s, Action.fold())    # seat 2
    act(s, Action.fold())    # seat 3
    act(s, Action.fold())    # seat 4
    act(s, Action.call())    # seat 5 all-in for 4 < 10

    assert s.players[5].status is PlayerStatus.ALL_IN
    assert s.players[5].street_committed == 4
    assert s.current_bet == 10, "a short all-in call must not move current_bet"
    assert s.last_full_raise_size == 10


def test_lone_actor_owing_chips_after_an_all_in() -> None:
    s = make_state([300, 300, 60, 300, 300, 300], button=0)
    start_postflop(s)
    act(s, Action.check())      # seat 1
    act(s, Action.bet(40))      # seat 2
    act(s, Action.fold())       # seat 3
    act(s, Action.fold())       # seat 4
    act(s, Action.fold())       # seat 5
    act(s, Action.fold())       # seat 0
    # Only seat 1 remains and owes 40.
    assert not is_round_complete(s)
    assert s.to_act == 1
    l1 = legal_actions(s, 1)
    assert l1.can_call and l1.call_cost == 40


def test_lone_actor_owing_nothing_closes_the_round() -> None:
    """Nobody should be asked to bet into a field of all-ins."""
    s = make_state([300, 300, 20, 20, 300, 300], button=0)
    start_postflop(s)
    act(s, Action.check())   # seat 1
    act(s, Action.bet(20))   # seat 2 all-in
    act(s, Action.call())    # seat 3 all-in
    act(s, Action.fold())    # seat 4
    act(s, Action.fold())    # seat 5
    act(s, Action.fold())    # seat 0
    act(s, Action.call())    # seat 1 calls 20
    assert is_round_complete(s)
    assert s.to_act is None


# --------------------------------------------------------------------------
# Min-raise escalation
# --------------------------------------------------------------------------

def test_escalating_min_raises() -> None:
    s = start_preflop(make_state())
    act(s, Action.raise_to(9))    # UTG opens to 9 (increment 6)
    assert s.last_full_raise_size == 6
    assert legal_actions(s, 4).min_to == 15

    act(s, Action.raise_to(27))   # HJ 3-bets to 27 (increment 18)
    assert s.last_full_raise_size == 18
    assert legal_actions(s, 5).min_to == 45


def test_min_raise_boundary_is_accepted_and_below_is_rejected() -> None:
    s = start_preflop(make_state())
    with pytest.raises(IllegalAction, match="below the minimum"):
        act(s, Action.raise_to(5))
    act(s, Action.raise_to(6))
    assert s.current_bet == 6


def test_postflop_minimum_bet_is_one_big_blind() -> None:
    s = make_state()
    start_postflop(s)
    assert legal_actions(s, 1).min_to == 3
    with pytest.raises(IllegalAction, match="below the minimum"):
        act(s, Action.bet(2))
    act(s, Action.bet(3))


def test_all_in_below_the_minimum_bet_is_legal() -> None:
    s = make_state([300, 2, 300, 300, 300, 300], button=0)
    start_postflop(s)
    legal = legal_actions(s, 1)
    assert legal.min_to == 2, "min bet clamps down to an all-in"
    act(s, Action.bet(2))
    assert s.players[1].status is PlayerStatus.ALL_IN


def test_raise_above_max_is_rejected() -> None:
    s = start_preflop(make_state())
    with pytest.raises(IllegalAction, match="exceeds the maximum"):
        act(s, Action.raise_to(1000))


# --------------------------------------------------------------------------
# Illegal action rejection
# --------------------------------------------------------------------------

def test_cannot_check_facing_a_bet() -> None:
    s = start_preflop(make_state())
    with pytest.raises(IllegalAction, match="cannot check"):
        act(s, Action.check())


def test_cannot_call_with_nothing_to_call() -> None:
    s = make_state()
    start_postflop(s)
    with pytest.raises(IllegalAction, match="nothing to call"):
        act(s, Action.call())


def test_cannot_bet_when_a_bet_exists() -> None:
    s = start_preflop(make_state())
    with pytest.raises(IllegalAction, match="already exists"):
        act(s, Action.bet(9))


def test_cannot_raise_when_no_bet_outstanding() -> None:
    s = make_state()
    start_postflop(s)
    with pytest.raises(IllegalAction, match="no bet outstanding"):
        act(s, Action.raise_to(10))


def test_cannot_act_out_of_turn() -> None:
    from poker.engine.betting import apply_action
    s = start_preflop(make_state())
    with pytest.raises(IllegalAction, match="out of turn"):
        apply_action(s, 5, Action.fold())


def test_fold_is_legal_when_checking_is_free() -> None:
    s = make_state()
    start_postflop(s)
    assert legal_actions(s, 1).can_fold
    act(s, Action.fold())
    assert s.players[1].status is PlayerStatus.FOLDED


# --------------------------------------------------------------------------
# Street reset
# --------------------------------------------------------------------------

def test_check_through_closes_the_round_and_resets_state() -> None:
    s = make_state()
    start_postflop(s)
    for _ in range(6):
        if is_round_complete(s):
            break
        act(s, Action.check())
    assert is_round_complete(s)

    start_postflop(s, Street.TURN)
    assert s.current_bet == 0
    assert s.last_full_raise_size == 3
    assert all(p.street_committed == 0 for p in s.players)
    assert all(not p.acted_this_round for p in s.players)
    assert s.to_act == 1


def test_preflop_action_starts_at_utg_postflop_at_the_sb() -> None:
    s = start_preflop(make_state(button=2))
    assert s.to_act == 5  # button 2 -> UTG is seat 5
    s2 = make_state(button=2)
    start_postflop(s2)
    assert s2.to_act == 3  # left of the button


def test_postflop_first_actor_skips_folded_seats() -> None:
    s = make_state()
    start_postflop(s)
    act(s, Action.fold())  # seat 1 folds
    start_postflop(s, Street.TURN)
    assert s.to_act == 2
