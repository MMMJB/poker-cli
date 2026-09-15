"""The board sits in the middle of the terminal, at any window size.

The board itself is drawn at a fixed size so its layout stays stable; only its
placement changes.  These tests pin the placement.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from poker.engine.cards import parse_cards
from poker.engine.state import PlayerStatus, Street
from poker.ui.cards import probe_glyph_width
from poker.ui.model import SeatView, TableView
from poker.ui.seats import COMPACT, FULL
from poker.ui.table import TableRenderable
from poker.ui.theme import THEME

probe_glyph_width()

SEATS = tuple(
    SeatView(i, name, 300, pos, PlayerStatus.ACTIVE, is_hero=(i == 0))
    for i, (name, pos) in enumerate([
        ("YOU", "BTN"), ("Deb", "SB"), ("Walter", "BB"),
        ("Tank", "UTG"), ("Raj", "HJ"), ("Sofia", "CO"),
    ])
)
VIEW = TableView(hand_id=7, street=Street.FLOP,
                 board=tuple(parse_cards("As Kd 7c")), pot=24, seats=SEATS)


def render(cols: int, rows: int) -> list[str]:
    buf = io.StringIO()
    console = Console(theme=THEME, file=buf, width=cols, height=rows,
                      force_terminal=False, legacy_windows=False)
    console.print(TableRenderable(lambda: VIEW, lambda: 0.0))
    text = buf.getvalue()
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n")


def margins(lines: list[str], cols: int, rows: int) -> tuple[int, int, int, int]:
    """(top, bottom, left, right) blank space around the drawn block.

    Measured against the terminal, not against the captured text.  The board's
    last column is a border character and rich does not pad the bottom of a
    frame, so neither edge leaves trailing whitespace to count.
    """
    filled = [i for i, line in enumerate(lines) if line.strip()]
    top = filled[0]
    bottom = rows - filled[-1] - 1
    first = lines[filled[0]]
    left = len(first) - len(first.lstrip())
    right = cols - len(first.rstrip())
    return top, bottom, left, right


@pytest.mark.parametrize("cols,rows", [
    (140, 44), (120, 40), (118, 40), (110, 36), (101, 33),
])
def test_full_layout_is_centred(cols: int, rows: int) -> None:
    lines = render(cols, rows)
    top, bottom, left, right = margins(lines, cols, rows)
    # Odd leftovers go to the bottom/right, so allow a one-cell difference.
    assert abs(left - right) <= 1, f"{cols}x{rows}: left={left} right={right}"
    assert abs(top - bottom) <= 1, f"{cols}x{rows}: top={top} bottom={bottom}"
    assert left == (cols - FULL.cols) // 2
    assert top == (rows - FULL.rows) // 2


@pytest.mark.parametrize("cols,rows", [(96, 30), (90, 28), (84, 26)])
def test_compact_layout_is_centred(cols: int, rows: int) -> None:
    lines = render(cols, rows)
    top, _, left, right = margins(lines, cols, rows)
    assert abs(left - right) <= 1
    assert left == (cols - COMPACT.cols) // 2
    assert top == (rows - COMPACT.rows) // 2


@pytest.mark.parametrize("cols,rows", [(100, 32), (80, 24)])
def test_an_exact_fit_has_no_margin(cols: int, rows: int) -> None:
    top, bottom, left, right = margins(render(cols, rows), cols, rows)
    assert (top, left) == (0, 0)


def test_the_board_keeps_its_fixed_width_when_centred() -> None:
    """Centring must place the board, never stretch it."""
    for cols in (100, 110, 140, 200):
        lines = [l for l in render(cols, 40) if l.strip()]
        widths = {len(l.rstrip()) - (len(l) - len(l.lstrip())) for l in lines}
        assert widths == {FULL.cols}, f"{cols}: board width drifted to {widths}"


def test_no_line_exceeds_the_terminal_width() -> None:
    for cols, rows in [(100, 32), (118, 40), (140, 44), (96, 30), (70, 20)]:
        for line in render(cols, rows):
            assert len(line.rstrip()) <= cols


def test_content_never_overflows_the_terminal_height() -> None:
    """One line too many scrolls the alternate screen and clips the top."""
    for cols, rows in [(100, 32), (118, 40), (140, 44), (96, 30), (80, 24), (70, 20)]:
        assert len(render(cols, rows)) <= rows, f"{cols}x{rows} overflowed"


def test_the_too_small_notice_renders_as_separate_lines() -> None:
    """It used to yield row lists with no newlines and collapse into one line."""
    lines = [l for l in render(70, 20) if l.strip()]
    assert len(lines) >= 3
    joined = " ".join(lines)
    assert "Terminal too small." in joined
    assert "80" in joined and "24" in joined


def test_the_too_small_notice_is_also_centred() -> None:
    lines = render(70, 20)
    top, bottom, left, right = margins(lines, 70, 20)
    assert abs(left - right) <= 1
    assert top > 0
