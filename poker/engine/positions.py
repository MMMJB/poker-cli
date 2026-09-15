"""Seat geometry: position names, action order, and blind seats.

Seats are fixed indices ``0..n-1`` clockwise.  The button advances one seat per
hand.  Positions are named by offset from the button.
"""

from __future__ import annotations

# 6-max position names by offset from the button.
_POSITION_NAMES = {
    6: ("BTN", "SB", "BB", "UTG", "HJ", "CO"),
    5: ("BTN", "SB", "BB", "UTG", "CO"),
    4: ("BTN", "SB", "BB", "CO"),
    3: ("BTN", "SB", "BB"),
    2: ("BTN/SB", "BB"),
}


def position_name(seat: int, button: int, n: int) -> str:
    """Position label for ``seat`` given the button seat."""
    names = _POSITION_NAMES.get(n)
    if names is None:
        raise ValueError(f"unsupported table size: {n}")
    return names[(seat - button) % n]


def order_from(start: int, n: int) -> list[int]:
    """All seats clockwise starting at ``start``."""
    return [(start + i) % n for i in range(n)]


def order_from_button(button: int, n: int) -> list[int]:
    """Seats in SB-first ... BTN-last order.

    This is the canonical tie-break order: it decides who receives odd chips
    from a split pot and who shows first at an unraised showdown.
    """
    return order_from((button + 1) % n, n)


def blind_seats(button: int, n: int) -> tuple[int, int]:
    """``(small_blind_seat, big_blind_seat)``.

    Heads-up is the exception everywhere in poker: the button posts the small
    blind.  The trainer never reaches heads-up because opponents auto-reload,
    but getting it right here costs ten lines and removes a landmine.
    """
    if n < 2:
        raise ValueError("need at least two seats")
    if n == 2:
        return button, (button + 1) % n
    return (button + 1) % n, (button + 2) % n


def first_to_act_preflop(button: int, n: int) -> int:
    """Seat that acts first preflop -- left of the big blind.

    Heads-up: the button/SB acts first preflop.
    """
    if n == 2:
        return button
    return (button + 3) % n


def first_to_act_postflop(button: int, n: int) -> int:
    """Seat that acts first postflop -- left of the button.

    Heads-up: the big blind acts first postflop.  Callers must still advance
    clockwise from here to the first seat that can actually act.
    """
    return (button + 1) % n
