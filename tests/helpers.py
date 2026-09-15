"""Test helpers for constructing betting states without a full hand."""

from __future__ import annotations

from poker.engine.actions import Action
from poker.engine.betting import advance_to_act, apply_action, begin_street
from poker.engine.events import HandLog
from poker.engine.state import GameConfig, HandState, PlayerState, Street


def make_state(
    stacks: list[int] | None = None,
    *,
    button: int = 0,
    config: GameConfig | None = None,
) -> HandState:
    cfg = config or GameConfig()
    stacks = stacks if stacks is not None else [300] * cfg.num_seats
    players = [PlayerState(seat=i, stack=s) for i, s in enumerate(stacks)]
    return HandState(
        hand_id=1, config=cfg, button=button, players=players, log=HandLog()
    )


def post_blinds(state: HandState) -> None:
    """Post blinds the way the engine does: commit chips, leave acted False."""
    from poker.engine.betting import _commit
    from poker.engine.positions import blind_seats

    sb_seat, bb_seat = blind_seats(state.button, state.num_seats)
    _commit(state, state.players[sb_seat], min(state.config.small_blind,
                                               state.players[sb_seat].stack))
    _commit(state, state.players[bb_seat], min(state.config.big_blind,
                                               state.players[bb_seat].stack))


def start_preflop(state: HandState) -> HandState:
    post_blinds(state)
    begin_street(state, Street.PREFLOP)
    return state


def start_postflop(state: HandState, street: Street = Street.FLOP) -> HandState:
    begin_street(state, street)
    return state


def act(state: HandState, action: Action) -> int:
    """Apply an action for whoever is to act, then advance."""
    assert state.to_act is not None, "no one to act"
    added = apply_action(state, state.to_act, action)
    advance_to_act(state)
    return added
