"""The immutable render model.

``rich.Live`` refreshes from a background thread while the async app mutates
state, so every view object here is frozen.  The app publishes a new frame by
**replacing the whole object**; the renderable reads the reference exactly once
per frame.  CPython attribute rebinding is atomic, so no lock is needed and no
frame can ever show half of one state and half of another.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from poker.engine.cards import Card
from poker.engine.state import PlayerStatus, Street


@dataclass(frozen=True, slots=True)
class SeatView:
    seat: int
    name: str
    stack: int
    position: str
    status: PlayerStatus
    is_hero: bool = False
    hole: tuple[Card, ...] | None = None
    face_up: bool = False
    street_bet: int = 0
    is_acting: bool = False
    is_winner: bool = False
    won: int = 0
    win_label: str = "WINS"
    """MAIN or SIDE when a hand splits into several pots.

    Two bare "WINS" badges read as a chop, which is the opposite of what a
    side pot means -- the short stack could only win what it matched.
    """
    thinking: bool = False
    note: str = ""
    """Short status word shown in the seat: FOLDED, ALL-IN, WINS $174."""

    @property
    def folded(self) -> bool:
        return self.status is PlayerStatus.FOLDED

    @property
    def all_in(self) -> bool:
        return self.status is PlayerStatus.ALL_IN


@dataclass(frozen=True, slots=True)
class ActionBar:
    """What the human may do right now, straight from LegalActions."""

    active: bool = False
    to_call: int = 0
    pot: int = 0
    can_fold: bool = False
    can_check: bool = False
    can_call: bool = False
    can_bet: bool = False
    can_raise: bool = False
    min_to: int = 0
    max_to: int = 0
    stack: int = 0
    message: str = ""
    error_flash: bool = False


@dataclass(frozen=True, slots=True)
class InputLine:
    """The editable command line.

    Nothing is submitted until Enter, so the player always sees what they are
    about to do and can edit or clear it first.
    """

    active: bool = False
    text: str = ""
    cursor: int = 0
    preview: str = ""
    """What Enter will do right now."""
    error: str = ""
    hint: str = ""
    """The legal actions, built from the engine so it cannot go stale."""
    draft: bool = False
    """Typed out of turn: shown greyed out and cannot be submitted yet.

    It carries into the live prompt when the action reaches you, where it still
    has to be committed with Enter -- preparing a move is not making one.
    """
    message: str = ""
    show_help: bool = False

    @property
    def before(self) -> str:
        return self.text[: self.cursor]

    @property
    def at(self) -> str:
        return self.text[self.cursor: self.cursor + 1] or " "

    @property
    def after(self) -> str:
        return self.text[self.cursor + 1:]


@dataclass(frozen=True, slots=True)
class ReviewNote:
    street: str
    text: str


@dataclass(frozen=True, slots=True)
class ReviewView:
    active: bool = False
    pending: bool = False
    verdict: str = "ok"
    headline: str = ""
    notes: tuple[ReviewNote, ...] = ()
    better_line: str = ""
    hand_id: int = 0


@dataclass(frozen=True, slots=True)
class TableView:
    hand_id: int = 0
    street: Street = Street.PREFLOP
    board: tuple[Card, ...] = ()
    pot: int = 0
    seats: tuple[SeatView, ...] = ()
    hero_seat: int = 0
    button: int = 0
    small_blind: int = 1
    big_blind: int = 3
    session_profit: int = 0
    hands_played: int = 0
    session_label: str = ""
    """Replaces the session P/L in the header when set (used by replays)."""
    log_lines: tuple[tuple[str, str], str | tuple] = ()
    """Recent action lines as ``(text, style)`` pairs."""
    talk: str = ""
    talk_speaker: str = ""
    action_bar: ActionBar = field(default_factory=ActionBar)
    input_line: InputLine = field(default_factory=InputLine)
    review: ReviewView = field(default_factory=ReviewView)
    banner: str = ""
    review_on: bool = True
    debug: str = ""
    paused_for_size: bool = False

    def with_(self, **kw) -> "TableView":
        return replace(self, **kw)

    def seat_view(self, seat: int) -> SeatView | None:
        for s in self.seats:
            if s.seat == seat:
                return s
        return None
