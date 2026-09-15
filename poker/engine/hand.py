"""Hand lifecycle -- the engine's public entry point.

Driven by a synchronous pull loop::

    engine.start()
    while not engine.is_complete():
        seat = engine.current_actor()
        action = decide(engine.observation(seat), engine.legal_actions())
        engine.apply(action)

:meth:`HandEngine.apply` owns everything downstream of an action -- closing the
round, dealing the next street, running out the board, showdown, and awarding.
Callers never advance streets themselves.  No generators, no callbacks, no
async: the whole hand is replayable from its seed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Mapping, Sequence

from poker.engine.actions import Action, ActionType, LegalActions
from poker.engine.betting import (
    advance_to_act, apply_action, begin_street, is_round_complete,
    legal_actions, post_blind,
)
from poker.engine.cards import Card, Deck, cards_str
from poker.engine.errors import EngineInvariantError
from poker.engine.events import EventType, HandLog
from poker.engine.evaluator import best_five, describe, evaluate7
from poker.engine.positions import (
    blind_seats, order_from, order_from_button, position_name,
)
from poker.engine.pots import (
    Award, Pot, award_pots, award_uncontested, build_pots, return_uncalled_bet,
)
from poker.engine.state import (
    CARDS_PER_STREET, GameConfig, HandState, PlayerState, PlayerStatus, Street,
)

_NEXT_STREET = {
    Street.PREFLOP: Street.FLOP,
    Street.FLOP: Street.TURN,
    Street.TURN: Street.RIVER,
}


@dataclass(frozen=True, slots=True)
class Observation:
    """One seat's complete view of the situation.  Never leaks other holes."""

    hand_id: int
    seat: int
    position: str
    hole: tuple[Card, ...]
    board: tuple[Card, ...]
    street: Street
    pot: int
    to_call: int
    current_bet: int
    min_raise_to: int
    max_raise_to: int
    button: int
    big_blind: int
    small_blind: int
    names: Mapping[int, str]
    stacks: Mapping[int, int]
    street_committed: Mapping[int, int]
    total_committed: Mapping[int, int]
    statuses: Mapping[int, PlayerStatus]
    positions: Mapping[int, str]
    legal: LegalActions
    history: tuple = ()
    """``log.view_for(seat)`` -- public events plus this seat's own privates."""


@dataclass(frozen=True, slots=True)
class HandResult:
    hand_id: int
    button: int
    board: tuple[Card, ...]
    pots: tuple[Pot, ...]
    awards: tuple[Award, ...]
    net: Mapping[int, int]
    stacks_after: Mapping[int, int]
    hole_cards: Mapping[int, tuple[Card, ...]]
    """God view -- every seat's cards, mucks included.  For the log, not the UI."""
    shown: frozenset[int]
    went_to_showdown: bool
    hand_values: Mapping[int, int]


class HandEngine:
    """Plays exactly one hand."""

    def __init__(
        self,
        config: GameConfig,
        names: Sequence[str],
        stacks: Sequence[int],
        button: int,
        hand_id: int,
        seed: int,
    ) -> None:
        if len(names) != len(stacks):
            raise ValueError("names and stacks must be the same length")
        self.config = config
        self.names = list(names)
        self._rng = random.Random(seed)
        self._deck = Deck(self._rng)
        self._seed = seed
        self._stacks_before = list(stacks)
        self._final_aggressor: int | None = None
        self._pots: list[Pot] = []
        self._awards: list[Award] = []
        self._shown: set[int] = set()
        self._hand_values: dict[int, int] = {}
        self._went_to_showdown = False
        self._started = False

        players = [PlayerState(seat=i, stack=s) for i, s in enumerate(stacks)]
        self.state = HandState(
            hand_id=hand_id,
            config=config,
            button=button,
            players=players,
            log=HandLog(),
            seed=seed,
        )

    # ------------------------------------------------------------------ setup

    def start(self) -> None:
        s = self.state
        if self._started:
            raise EngineInvariantError("hand already started")
        self._started = True

        for p in s.players:
            if p.stack < self.config.big_blind:
                raise EngineInvariantError(
                    f"seat {p.seat} has {p.stack}, below the big blind -- "
                    "top up between hands"
                )

        s.log.emit(EventType.HAND_STARTED, {
            "hand_id": s.hand_id,
            "button": s.button,
            "small_blind": self.config.small_blind,
            "big_blind": self.config.big_blind,
            "seed": self._seed,
            "seats": [
                {
                    "seat": p.seat,
                    "name": self.names[p.seat],
                    "stack": p.stack,
                    "position": position_name(p.seat, s.button, s.num_seats),
                }
                for p in s.players
            ],
        })

        sb_seat, bb_seat = blind_seats(s.button, s.num_seats)
        for seat, kind, amount in (
            (sb_seat, "SB", self.config.small_blind),
            (bb_seat, "BB", self.config.big_blind),
        ):
            posted = post_blind(s, seat, amount)
            s.log.emit(EventType.BLIND_POSTED, {
                "seat": seat,
                "kind": kind,
                "amount": posted,
                "stack_after": s.players[seat].stack,
                "all_in": s.players[seat].status is PlayerStatus.ALL_IN,
            })

        self._deal_hole_cards()
        begin_street(s, Street.PREFLOP)
        if is_round_complete(s):  # pragma: no cover - needs degenerate stacks
            self._close_round()

    def _deal_hole_cards(self) -> None:
        s = self.state
        sb_seat, _ = blind_seats(s.button, s.num_seats)
        order = order_from(sb_seat, s.num_seats)
        dealt: dict[int, list[Card]] = {seat: [] for seat in order}
        for _ in range(2):  # two passes, one card at a time
            for seat in order:
                dealt[seat].append(self._deck.deal_one())
        for seat in order:
            cards = tuple(dealt[seat])
            s.players[seat].hole = cards
            s.log.emit(
                EventType.HOLE_CARDS_DEALT,
                {"seat": seat, "cards": cards_str(cards)},
                visible_to=[seat],
            )

    # ------------------------------------------------------------- driving it

    def is_complete(self) -> bool:
        return self.state.street is Street.COMPLETE

    def current_actor(self) -> int | None:
        return self.state.to_act

    def legal_actions(self) -> LegalActions:
        seat = self.state.to_act
        if seat is None:
            raise EngineInvariantError("no one is to act")
        return legal_actions(self.state, seat)

    def observation(self, seat: int) -> Observation:
        s = self.state
        p = s.players[seat]
        return Observation(
            hand_id=s.hand_id,
            seat=seat,
            position=position_name(seat, s.button, s.num_seats),
            hole=p.hole,
            board=tuple(s.board),
            street=s.street,
            pot=s.pot,
            to_call=max(0, s.current_bet - p.street_committed),
            current_bet=s.current_bet,
            min_raise_to=min(s.current_bet + s.last_full_raise_size,
                             p.street_committed + p.stack),
            max_raise_to=p.street_committed + p.stack,
            button=s.button,
            big_blind=self.config.big_blind,
            small_blind=self.config.small_blind,
            names={q.seat: self.names[q.seat] for q in s.players},
            stacks={q.seat: q.stack for q in s.players},
            street_committed={q.seat: q.street_committed for q in s.players},
            total_committed={q.seat: q.total_committed for q in s.players},
            statuses={q.seat: q.status for q in s.players},
            positions={
                q.seat: position_name(q.seat, s.button, s.num_seats)
                for q in s.players
            },
            legal=legal_actions(s, seat) if s.to_act == seat else _no_actions(seat),
            history=s.log.view_for(seat),
        )

    def apply(self, action: Action) -> None:
        s = self.state
        seat = s.to_act
        if seat is None:
            raise EngineInvariantError("no one is to act")

        added = apply_action(s, seat, action)
        p = s.players[seat]
        s.log.emit(EventType.ACTION_TAKEN, {
            "seat": seat,
            "position": position_name(seat, s.button, s.num_seats),
            "action": action.type.name.lower(),
            "amount_added": added,
            "to_amount": (
                action.to_amount
                if action.type in (ActionType.BET, ActionType.RAISE)
                else p.street_committed
            ),
            "street": s.street.name.lower(),
            "street_committed_after": p.street_committed,
            "stack_after": p.stack,
            "pot_after": s.pot,
            "is_all_in": p.status is PlayerStatus.ALL_IN,
        })

        advance_to_act(s)
        if s.to_act is None:
            self._close_round()

    # ------------------------------------------------------------ round close

    def _close_round(self) -> None:
        s = self.state

        refund = return_uncalled_bet(s)
        if refund is not None:
            seat, amount = refund
            s.log.emit(EventType.UNCALLED_BET_RETURNED, {
                "seat": seat,
                "amount": amount,
                "stack_after": s.players[seat].stack,
            })

        s.log.emit(EventType.STREET_COMPLETE, {
            "street": s.street.name.lower(),
            "pot_after": s.pot,
        })

        live = s.live_players()
        if len(live) <= 1:
            self._finish_uncontested(live[0])
            return

        # Capture before the next begin_street clears it -- showdown order needs
        # the aggressor of the FINAL street.
        if s.last_aggressor is not None:
            self._final_aggressor = s.last_aggressor

        if len(s.active_players()) < 2:
            self._run_out_and_showdown()
            return

        if s.street is Street.RIVER:
            self._showdown()
            return

        next_street = _NEXT_STREET[s.street]
        self._deal_street(next_street)
        begin_street(s, next_street)
        if is_round_complete(s):  # pragma: no cover - defensive
            self._close_round()

    def _deal_street(self, street: Street) -> None:
        s = self.state
        cards = self._deck.deal(CARDS_PER_STREET[street])
        s.board.extend(cards)
        s.street = street
        s.log.emit(EventType.STREET_DEALT, {
            "street": street.name.lower(),
            "new_cards": cards_str(cards),
            "board": cards_str(s.board),
        })

    def _run_out_and_showdown(self) -> None:
        """Fewer than two players can act: reveal, deal the rest, then show."""
        s = self.state
        for p in s.live_players():
            if not p.revealed:
                p.revealed = True
                self._shown.add(p.seat)
                s.log.emit(EventType.CARDS_REVEALED, {
                    "seat": p.seat,
                    "cards": cards_str(p.hole),
                    "reason": "all_in",
                })
        while s.street in (Street.PREFLOP, Street.FLOP, Street.TURN):
            self._deal_street(_NEXT_STREET[s.street])
        self._showdown()

    # ---------------------------------------------------------------- payouts

    def _finish_uncontested(self, winner: PlayerState) -> None:
        """Everyone folded.  No board cards, no reveal, no showdown."""
        s = self.state
        self._pots = build_pots(
            {p.seat: p.total_committed for p in s.players}, {winner.seat}
        )
        self._awards = award_uncontested(self._pots, winner.seat)
        self._pay_out()
        self._finish()

    def _showdown(self) -> None:
        s = self.state
        self._went_to_showdown = True
        s.street = Street.SHOWDOWN

        live = s.live_players()
        self._hand_values = {
            p.seat: evaluate7(list(p.hole) + s.board) for p in live
        }

        for seat in self._show_order():
            p = s.players[seat]
            value = self._hand_values[seat]
            best_so_far = max(
                (self._hand_values[q] for q in self._shown if q in self._hand_values),
                default=-1,
            )
            must_show = (
                p.revealed
                or self.config.reveal_all_at_showdown
                or not self._shown
                or value >= best_so_far
            )
            if must_show:
                if not p.revealed:
                    p.revealed = True
                    s.log.emit(EventType.CARDS_REVEALED, {
                        "seat": seat,
                        "cards": cards_str(p.hole),
                        "reason": "showdown",
                    })
                self._shown.add(seat)
                s.log.emit(EventType.SHOWDOWN_HAND, {
                    "seat": seat,
                    "best_five": cards_str(best_five(list(p.hole) + s.board)),
                    "category": describe(value),
                })
            else:
                s.log.emit(
                    EventType.CARDS_MUCKED,
                    {"seat": seat},
                    private={"cards": cards_str(p.hole)},
                )

        self._pots = build_pots(
            {p.seat: p.total_committed for p in s.players},
            {p.seat for p in live},
        )
        s.log.emit(EventType.POTS_BUILT, {
            "pots": [
                {"index": i, "amount": pot.amount, "eligible": sorted(pot.eligible)}
                for i, pot in enumerate(self._pots)
            ],
        })
        # Winners come from hand values, never from who showed.
        self._awards = award_pots(
            self._pots, self._hand_values, s.button, s.num_seats
        )
        self._pay_out()
        self._finish()

    def _show_order(self) -> list[int]:
        """Last aggressor of the final street shows first, else SB-first."""
        s = self.state
        live = {p.seat for p in s.live_players()}
        if self._final_aggressor is not None and self._final_aggressor in live:
            start = self._final_aggressor
            order = order_from(start, s.num_seats)
        else:
            order = order_from_button(s.button, s.num_seats)
        return [seat for seat in order if seat in live]

    def _pay_out(self) -> None:
        s = self.state
        for award in self._awards:
            p = s.players[award.seat]
            p.stack += award.amount
            s.log.emit(EventType.POT_AWARDED, {
                "pot_index": award.pot_index,
                "seat": award.seat,
                "amount": award.amount,
                "reason": award.reason.value,
                "stack_after": p.stack,
            })

    def _finish(self) -> None:
        s = self.state
        awarded = sum(a.amount for a in self._awards)
        pot_total = sum(p.amount for p in self._pots)
        if awarded != pot_total:
            raise EngineInvariantError(
                f"awarded {awarded} but pots hold {pot_total}"
            )

        net = {
            p.seat: p.stack - self._stacks_before[p.seat] for p in s.players
        }
        if sum(net.values()) != 0:
            raise EngineInvariantError(f"chips not conserved: net={net}")

        s.log.emit(EventType.HAND_ENDED, {
            "net": net,
            "stacks_after": {p.seat: p.stack for p in s.players},
            "board": cards_str(s.board),
        })
        s.street = Street.COMPLETE
        s.to_act = None

    # ----------------------------------------------------------------- output

    def result(self) -> HandResult:
        if not self.is_complete():
            raise EngineInvariantError("hand is not complete")
        s = self.state
        return HandResult(
            hand_id=s.hand_id,
            button=s.button,
            board=tuple(s.board),
            pots=tuple(self._pots),
            awards=tuple(self._awards),
            net={p.seat: p.stack - self._stacks_before[p.seat] for p in s.players},
            stacks_after={p.seat: p.stack for p in s.players},
            hole_cards={p.seat: p.hole for p in s.players},
            shown=frozenset(self._shown),
            went_to_showdown=self._went_to_showdown,
            hand_values=dict(self._hand_values),
        )

    @property
    def log(self) -> HandLog:
        return self.state.log


def _no_actions(seat: int) -> LegalActions:
    return LegalActions(
        seat=seat, can_fold=False, can_check=False, can_call=False,
        call_cost=0, call_is_all_in=False, can_bet=False, can_raise=False,
        min_to=0, max_to=0, stack=0,
    )
