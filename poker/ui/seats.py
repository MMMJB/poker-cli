"""Layout geometry: where the six seats, the felt, the board, and the bets go.

The hero is *always* bottom-centre and every other seat maps onto a fixed
anchor clockwise from there.  When the button rotates, the **badges** move --
the boxes never do.  That is what lets the player build muscle memory about
who sits where.
"""

from __future__ import annotations

from dataclasses import dataclass

# Clockwise from the hero.  In poker the next player to act is to your left,
# which on screen means up the left-hand side and back down the right.
#
# One ring per table size rather than points on an ellipse: seat boxes are
# twenty columns wide, so a computed ring overlaps at eight seats and the
# arrangement has to be chosen, not derived.
RINGS: dict[int, tuple[str, ...]] = {
    2: ("hero", "top"),
    3: ("hero", "ul", "ur"),
    4: ("hero", "ll", "top", "lr"),
    5: ("hero", "ll", "tl", "tr", "lr"),
    6: ("hero", "ll", "ul", "top", "ur", "lr"),
    7: ("hero", "ll", "ul", "tl", "tr", "ur", "lr"),
    8: ("hero", "ll", "ul", "t1", "t2", "t3", "ur", "lr"),
}
SEAT_RING = RINGS[6]

MAX_SEATS = max(RINGS)
MIN_SEATS = min(RINGS)


@dataclass(frozen=True, slots=True)
class SeatSlot:
    x: int
    y: int
    bet_x: int
    bet_y: int
    bet_align: str = "left"  # left | right | center


@dataclass(frozen=True, slots=True)
class Layout:
    name: str
    cols: int
    rows: int
    boxed: bool
    seat_w: int
    seat_h: int
    slots: dict[str, SeatSlot]
    felt: tuple[int, int, int, int]
    board_x: int
    board_y: int
    pot_y: int
    street_y: int
    header_y: int
    action_y: int
    prompt_y: int
    log_y: int
    log_lines: int
    sep_rows: tuple[int, ...]

    def ring(self, num_seats: int) -> tuple[str, ...]:
        try:
            return RINGS[num_seats]
        except KeyError:  # pragma: no cover - guarded at config time
            raise ValueError(f"unsupported table size: {num_seats}") from None

    def slot_for(self, seat: int, hero_seat: int, num_seats: int) -> SeatSlot:
        offset = (seat - hero_seat) % num_seats
        return self.slots[self.ring(num_seats)[offset]]


FULL = Layout(
    name="full",
    cols=100,
    rows=32,
    boxed=True,
    seat_w=20,
    seat_h=4,
    slots={
        "top": SeatSlot(40, 3, 50, 9, "center"),
        "tl": SeatSlot(26, 3, 36, 9, "center"),
        "tr": SeatSlot(54, 3, 64, 9, "center"),
        "t1": SeatSlot(16, 3, 34, 9, "center"),
        "t2": SeatSlot(40, 3, 50, 9, "center"),
        "t3": SeatSlot(64, 3, 66, 9, "center"),
        "ul": SeatSlot(2, 8, 29, 10, "left"),
        "ur": SeatSlot(78, 8, 70, 10, "right"),
        "ll": SeatSlot(2, 16, 29, 16, "left"),
        "lr": SeatSlot(78, 16, 70, 16, "right"),
        "hero": SeatSlot(40, 21, 50, 17, "center"),
    },
    felt=(25, 7, 50, 13),
    board_x=36,
    board_y=11,
    pot_y=15,
    street_y=16,
    header_y=1,
    action_y=26,
    prompt_y=27,
    log_y=29,
    log_lines=2,
    sep_rows=(2, 25, 28, 31),
)

COMPACT = Layout(
    name="compact",
    cols=80,
    rows=24,
    boxed=False,
    seat_w=18,
    seat_h=2,
    slots={
        "top": SeatSlot(31, 3, 40, 7, "center"),
        "tl": SeatSlot(21, 3, 33, 7, "center"),
        "tr": SeatSlot(41, 3, 47, 7, "center"),
        "t1": SeatSlot(2, 3, 30, 7, "center"),
        "t2": SeatSlot(31, 3, 40, 7, "center"),
        "t3": SeatSlot(60, 3, 50, 7, "center"),
        "ul": SeatSlot(2, 7, 24, 7, "left"),
        "ur": SeatSlot(59, 7, 55, 7, "right"),
        "ll": SeatSlot(2, 12, 24, 11, "left"),
        "lr": SeatSlot(59, 12, 55, 11, "right"),
        "hero": SeatSlot(31, 15, 40, 13, "center"),
    },
    felt=(22, 6, 36, 9),
    board_x=25,
    board_y=8,
    pot_y=12,
    street_y=13,
    header_y=1,
    action_y=19,
    prompt_y=20,
    log_y=22,
    log_lines=1,
    sep_rows=(2, 18, 21, 23),
)


def choose_layout(cols: int, rows: int) -> Layout | None:
    """Pick the richest layout that fits, or None if the terminal is too small."""
    if cols >= FULL.cols and rows >= FULL.rows:
        return FULL
    if cols >= COMPACT.cols and rows >= COMPACT.rows:
        return COMPACT
    return None
