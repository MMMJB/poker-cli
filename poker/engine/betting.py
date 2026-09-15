"""The betting round state machine.

This is the highest-risk module in the engine, so the model is deliberately
minimal: **three pieces of state generate every rule** --

* ``state.current_bet`` -- highest street commitment so far,
* ``state.last_full_raise_size`` -- the minimum raise increment,
* ``player.acted_this_round`` -- has acted since the last full bet/raise.

Between them they produce the big-blind option, the "a short all-in does not
reopen the action" rule, and min-raise legality, with no extra flags.
"""

from __future__ import annotations

from poker.engine.actions import Action, ActionType, LegalActions
from poker.engine.errors import EngineInvariantError, IllegalAction
from poker.engine.positions import (
    first_to_act_postflop, first_to_act_preflop, order_from,
)
from poker.engine.state import HandState, PlayerStatus, Street


def next_actor_from(state: HandState, start: int) -> int | None:
    """First seat at or clockwise from ``start`` that can act."""
    n = state.num_seats
    for seat in order_from(start % n, n):
        if state.players[seat].can_act:
            return seat
    return None


def begin_street(state: HandState, street: Street) -> None:
    """Reset per-street bookkeeping and pick the first actor.

    Blinds are posted by the caller (:mod:`poker.engine.hand`) before this runs
    for preflop, so that a blind post leaves ``acted_this_round`` False.
    """
    state.street = street
    state.last_aggressor = None
    for p in state.players:
        p.street_committed = 0 if street is not Street.PREFLOP else p.street_committed
        p.acted_this_round = False

    if street is Street.PREFLOP:
        state.current_bet = max(
            (p.street_committed for p in state.players), default=0
        )
        state.last_full_raise_size = state.config.big_blind
        start = first_to_act_preflop(state.button, state.num_seats)
    else:
        state.current_bet = 0
        # Minimum opening bet postflop is one big blind.
        state.last_full_raise_size = state.config.big_blind
        start = first_to_act_postflop(state.button, state.num_seats)

    state.to_act = next_actor_from(state, start)


def is_round_complete(state: HandState) -> bool:
    """Has the action closed for this street?

    The textbook rule is: every player who can still act has acted since the
    last aggressive action *and* has matched the current bet.  Two guard
    clauses cover the degenerate cases.
    """
    live = [p for p in state.players if p.is_live]
    if len(live) <= 1:
        return True  # everyone folded out

    actors = [p for p in live if p.can_act]
    if not actors:
        return True  # all-in run-out: nobody left to act

    if len(actors) == 1 and actors[0].street_committed >= state.current_bet:
        # A lone remaining actor who owes nothing should not be asked to bet
        # into a field of all-ins.  The `>=` is load-bearing: if they still owe
        # chips (a short all-in raised over them) they must get a fold/call
        # decision, which the general rule below correctly provides.
        return True

    return all(
        p.acted_this_round and p.street_committed == state.current_bet
        for p in actors
    )


def legal_actions(state: HandState, seat: int) -> LegalActions:
    """Exactly what ``seat`` may do right now."""
    p = state.players[seat]
    to_call = max(0, state.current_bet - p.street_committed)
    call_cost = min(to_call, p.stack)
    max_to = p.street_committed + p.stack

    can_check = to_call == 0
    can_call = to_call > 0 and p.stack > 0
    no_bet_yet = state.current_bet == 0

    can_bet = no_bet_yet and p.stack > 0
    # THE MAY-RAISE RULE.  `not acted_this_round` is the whole of it:
    #  - a full raise clears the flag for every other ACTIVE player, so they
    #    may raise again;
    #  - a short all-in clears nothing, so players who already acted get only
    #    fold/call -- that is the "does not reopen the action" rule;
    #  - a player yet to act (including the BB behind a blind post) still has
    #    the flag clear, so they keep the right to raise over a short all-in.
    can_raise = (not no_bet_yet) and (not p.acted_this_round) and (max_to > state.current_bet)

    # Clamping down to max_to is what makes "all-in for less than a full raise"
    # a legal action.
    min_to = min(state.current_bet + state.last_full_raise_size, max_to)
    if no_bet_yet:
        min_to = min(state.last_full_raise_size, max_to)

    return LegalActions(
        seat=seat,
        can_fold=True,
        can_check=can_check,
        can_call=can_call,
        call_cost=call_cost,
        call_is_all_in=can_call and call_cost == p.stack,
        can_bet=can_bet,
        can_raise=can_raise,
        min_to=min_to,
        max_to=max_to,
        stack=p.stack,
    )


def _commit(state: HandState, p, amount: int) -> None:
    if amount < 0:
        raise EngineInvariantError(f"negative commit {amount}")
    if amount > p.stack:
        raise EngineInvariantError(f"seat {p.seat} commit {amount} > stack {p.stack}")
    p.stack -= amount
    p.street_committed += amount
    p.total_committed += amount
    if p.stack == 0:
        p.status = PlayerStatus.ALL_IN


def apply_action(state: HandState, seat: int, action: Action) -> int:
    """Validate and apply an action.  Returns the chips added to the pot.

    Raises :class:`IllegalAction` on anything the rules forbid -- no silent
    normalization.
    """
    if state.to_act != seat:
        raise IllegalAction(f"seat {seat} acted out of turn (to_act={state.to_act})")

    p = state.players[seat]
    if not p.can_act:
        raise IllegalAction(f"seat {seat} cannot act (status={p.status.name})")

    legal = legal_actions(state, seat)
    added = 0

    if action.type is ActionType.FOLD:
        p.status = PlayerStatus.FOLDED
        p.acted_this_round = True

    elif action.type is ActionType.CHECK:
        if not legal.can_check:
            raise IllegalAction(
                f"seat {seat} cannot check facing a bet of {legal.call_cost}"
            )
        p.acted_this_round = True

    elif action.type is ActionType.CALL:
        if not legal.can_call:
            raise IllegalAction(f"seat {seat} has nothing to call -- use CHECK")
        added = legal.call_cost
        _commit(state, p, added)
        p.acted_this_round = True
        # An all-in call for LESS than the full call amount moves neither
        # current_bet nor last_full_raise_size, and reopens nothing.

    elif action.type in (ActionType.BET, ActionType.RAISE):
        want_bet = action.type is ActionType.BET
        if want_bet and not legal.can_bet:
            raise IllegalAction(f"seat {seat} cannot BET -- a bet already exists, use RAISE")
        if not want_bet and not legal.can_raise:
            if legal.can_bet:
                raise IllegalAction(f"seat {seat} cannot RAISE -- no bet outstanding, use BET")
            raise IllegalAction(f"seat {seat} may not raise (action is not reopened)")

        target = action.to_amount
        if target < legal.min_to:
            raise IllegalAction(
                f"seat {seat} {action.type.name.lower()} to {target} is below the "
                f"minimum of {legal.min_to}"
            )
        if target > legal.max_to:
            raise IllegalAction(
                f"seat {seat} {action.type.name.lower()} to {target} exceeds the "
                f"maximum of {legal.max_to}"
            )

        increment = target - state.current_bet
        added = target - p.street_committed
        _commit(state, p, added)

        if increment >= state.last_full_raise_size:
            state.last_full_raise_size = increment
            # A full raise reopens the action -- for ACTIVE players only.
            for q in state.players:
                if q.seat != seat and q.can_act:
                    q.acted_this_round = False
        else:
            # A short raise is legal only as an all-in, and it leaves
            # last_full_raise_size alone.
            if target != legal.max_to:
                raise EngineInvariantError(
                    f"short raise to {target} that is not all-in ({legal.max_to})"
                )

        state.current_bet = target
        state.last_aggressor = seat
        p.acted_this_round = True

    else:  # pragma: no cover - exhaustive
        raise IllegalAction(f"unknown action type {action.type}")

    return added


def advance_to_act(state: HandState) -> None:
    """Set ``state.to_act`` for the next player, or None if the round closed."""
    if is_round_complete(state):
        state.to_act = None
        return
    assert state.to_act is not None
    nxt = next_actor_from(state, state.to_act + 1)
    if nxt is None:  # pragma: no cover - is_round_complete should have caught it
        state.to_act = None
        return
    p = state.players[nxt]
    # The acted set is always a contiguous clockwise arc ending at the last
    # actor, so this holds by construction.  If it ever fails, the raise/reset
    # bookkeeping is broken.
    if p.acted_this_round and p.street_committed >= state.current_bet:
        raise EngineInvariantError(
            f"seat {nxt} selected to act but has already acted and owes nothing"
        )
    state.to_act = nxt


def post_blind(state: HandState, seat: int, amount: int) -> int:
    """Post a blind.  Returns the amount actually posted (capped at the stack).

    Deliberately leaves ``acted_this_round`` False: posting a blind is not an
    action, which is precisely why the big blind still gets its option.
    """
    p = state.players[seat]
    posted = min(amount, p.stack)
    _commit(state, p, posted)
    return posted
