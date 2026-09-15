"""The structured hand-history log.

Every visible thing that happens in a hand becomes an event.  This log is the
spine of the whole application: the renderer, each opponent's view of the
action, the post-hand coach, and the on-disk history are all derived from it,
so the engine never needs to know any of them exist.

Visibility is carried on the event itself:

* ``visible_to is None``  -- public,
* ``visible_to == {seat}`` -- private to that seat (hole cards),
* ``private``             -- a god-view-only payload (mucked cards).

:meth:`HandLog.view_for` is the *only* sanctioned path by which an opponent
agent receives history.  Never hand an agent a ``HandState`` or ``HandLog.all``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Sequence

from poker.engine.cards import Card, cards_str


class EventType(str, Enum):
    HAND_STARTED = "hand_started"
    BLIND_POSTED = "blind_posted"
    HOLE_CARDS_DEALT = "hole_cards_dealt"
    ACTION_TAKEN = "action_taken"
    STREET_DEALT = "street_dealt"
    STREET_COMPLETE = "street_complete"
    UNCALLED_BET_RETURNED = "uncalled_bet_returned"
    CARDS_REVEALED = "cards_revealed"
    CARDS_MUCKED = "cards_mucked"
    SHOWDOWN_HAND = "showdown_hand"
    POTS_BUILT = "pots_built"
    POT_AWARDED = "pot_awarded"
    HAND_ENDED = "hand_ended"
    TOPUP = "topup"


@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    visible_to: frozenset[int] | None = None
    """None means public."""
    private: dict[str, Any] | None = None
    """God-view-only payload, stripped from every filtered view."""

    def is_visible_to(self, seat: int) -> bool:
        return self.visible_to is None or seat in self.visible_to

    def public_copy(self) -> "Event":
        """This event with the god-view payload removed."""
        if self.private is None:
            return self
        return Event(self.seq, self.type, self.data, self.visible_to, None)

    def to_dict(self, *, include_private: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {"seq": self.seq, "type": self.type.value, **self.data}
        if include_private and self.private:
            out["_private"] = self.private
        return out


class HandLog:
    """Append-only event log for a single hand."""

    __slots__ = ("_events",)

    def __init__(self) -> None:
        self._events: list[Event] = []

    def emit(
        self,
        type: EventType,
        data: dict[str, Any] | None = None,
        *,
        visible_to: Iterable[int] | None = None,
        private: dict[str, Any] | None = None,
    ) -> Event:
        ev = Event(
            seq=len(self._events),
            type=type,
            data=data or {},
            visible_to=None if visible_to is None else frozenset(visible_to),
            private=private,
        )
        self._events.append(ev)
        return ev

    def append(self, event: Event) -> None:
        self._events.append(event)

    def all(self) -> tuple[Event, ...]:
        """God view -- everything, including private payloads.

        For the coach and the on-disk log only.  Never for an opponent agent.
        """
        return tuple(self._events)

    def public(self) -> tuple[Event, ...]:
        return tuple(e.public_copy() for e in self._events if e.visible_to is None)

    def view_for(self, seat: int) -> tuple[Event, ...]:
        """One seat's information set: public events plus its own privates.

        This is what an opponent agent is allowed to see.
        """
        return tuple(
            e.public_copy() for e in self._events if e.is_visible_to(seat)
        )

    def of_type(self, *types: EventType) -> tuple[Event, ...]:
        wanted = set(types)
        return tuple(e for e in self._events if e.type in wanted)

    def to_json(self, *, include_private: bool = False) -> str:
        return json.dumps(
            [e.to_dict(include_private=include_private) for e in self._events],
            sort_keys=True,
        )

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self):
        return iter(self._events)


def fmt_cards(cards: Sequence[Card]) -> str:
    """ASCII card text for event payloads -- LLMs read ``Ah`` more reliably."""
    return cards_str(cards)
