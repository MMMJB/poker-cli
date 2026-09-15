"""Build the immutable render model from engine state.

The single adapter between the engine's vocabulary and the renderer's.  If the
engine ever renames something, this is the only file that needs to know.
"""

from __future__ import annotations

from poker.engine.hand import HandEngine
from poker.engine.positions import position_name
from poker.engine.state import Street
from poker.engine.table import Table
from poker.ui.model import ActionBar, SeatView, TableView


def build_seats(
    engine: HandEngine,
    table: Table,
    *,
    acting: int | None = None,
    thinking: int | None = None,
    winners: dict[int, int] | None = None,
    reveal: set[int] | None = None,
) -> tuple[SeatView, ...]:
    state = engine.state
    winners = winners or {}
    reveal = reveal or set()
    hero = table.human_seat

    out: list[SeatView] = []
    for p in state.players:
        show = p.seat == hero or p.seat in reveal or p.revealed
        out.append(SeatView(
            seat=p.seat,
            name=table.seats[p.seat].name,
            stack=p.stack,
            position=position_name(p.seat, state.button, state.num_seats),
            status=p.status,
            is_hero=p.seat == hero,
            hole=p.hole or None,
            face_up=show and bool(p.hole),
            street_bet=p.street_committed,
            is_acting=p.seat == acting,
            is_winner=p.seat in winners,
            won=winners.get(p.seat, 0),
            thinking=p.seat == thinking,
        ))
    return tuple(out)


def build_view(
    engine: HandEngine,
    table: Table,
    base: TableView,
    **kw,
) -> TableView:
    """A fresh frame from current engine state, preserving UI-only fields."""
    state = engine.state
    seats = build_seats(
        engine, table,
        acting=kw.pop("acting", None),
        thinking=kw.pop("thinking", None),
        winners=kw.pop("winners", None),
        reveal=kw.pop("reveal", None),
    )
    return base.with_(
        hand_id=state.hand_id,
        street=state.street,
        board=tuple(state.board),
        pot=state.pot,
        seats=seats,
        hero_seat=table.human_seat,
        button=state.button,
        small_blind=table.config.small_blind,
        big_blind=table.config.big_blind,
        session_profit=table.human.profit,
        hands_played=table.human.hands_played,
        **kw,
    )


def action_bar_from(legal, pot: int) -> ActionBar:
    """The prompt is built from LegalActions, so an illegal key is unrepresentable."""
    return ActionBar(
        active=True,
        to_call=legal.call_cost,
        pot=pot,
        can_fold=legal.can_fold,
        can_check=legal.can_check,
        can_call=legal.can_call,
        can_bet=legal.can_bet,
        can_raise=legal.can_raise,
        min_to=legal.min_to,
        max_to=legal.max_to,
        stack=legal.stack,
    )


def street_tag(street: Street) -> str:
    return {
        Street.PREFLOP: "PF",
        Street.FLOP: "F ",
        Street.TURN: "T ",
        Street.RIVER: "R ",
        Street.SHOWDOWN: "SD",
    }.get(street, "  ")
