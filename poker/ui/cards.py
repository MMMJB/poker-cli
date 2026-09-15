"""Drawing cards onto a :class:`~poker.ui.canvas.Canvas`.

Cards render as white faces with red/black pips, which reads far better on a
green felt than coloured text on the terminal background.

**Unicode width.**  The suit glyphs are East-Asian *Ambiguous* width and occupy
two cells in some terminal/font combinations, which would shear the entire
fixed grid.  :func:`probe_glyph_width` checks at startup and falls back to
ASCII suit letters globally if they are not exactly one cell wide.
"""

from __future__ import annotations

from rich.cells import cell_len

from poker.engine.cards import Card, RANKS, SUIT_GLYPHS, SUITS, is_red
from poker.ui.canvas import Canvas

CARD_W = 5
CARD_H = 3
COMPACT_W = 3

_ASCII_ONLY = False
BACK_FILL = "▒"   # medium shade
SLOT_FILL = "░"   # light shade


def probe_glyph_width() -> bool:
    """Decide once whether the fancy glyphs are safe.  Returns True if ASCII."""
    global _ASCII_ONLY, BACK_FILL, SLOT_FILL
    probes = list(SUIT_GLYPHS) + [BACK_FILL, SLOT_FILL]
    _ASCII_ONLY = any(cell_len(ch) != 1 for ch in probes)
    if _ASCII_ONLY:
        BACK_FILL = "#"
        SLOT_FILL = "."
    return _ASCII_ONLY


def ascii_only() -> bool:
    return _ASCII_ONLY


def card_label(card: Card) -> str:
    """Two characters: rank plus suit (glyph, or letter in ASCII mode)."""
    rank = RANKS[card >> 2]
    suit = SUITS[card & 3] if _ASCII_ONLY else SUIT_GLYPHS[card & 3]
    return rank + suit


def card_style(card: Card) -> str:
    return "card.red" if is_red(card) else "card.black"


def draw_card(canvas: Canvas, x: int, y: int, card: Card, dim: bool = False) -> None:
    """A face-up card: 5 wide, 3 tall, white face."""
    face = "card.blank"
    label_style = card_style(card)
    if dim:
        face, label_style = "card.slot", "card.slot"
    for row in range(CARD_H):
        canvas.put(x, y + row, " " * CARD_W, face)
    canvas.put(x + 1, y + 1, card_label(card), label_style)


def draw_card_back(canvas: Canvas, x: int, y: int) -> None:
    for row in range(CARD_H):
        canvas.put(x, y + row, BACK_FILL * CARD_W, "card.back")


def draw_card_slot(canvas: Canvas, x: int, y: int) -> None:
    """An undealt board position, so the player can see how many streets remain."""
    for row in range(CARD_H):
        canvas.put(x, y + row, SLOT_FILL * CARD_W, "card.slot")


def draw_compact_card(canvas: Canvas, x: int, y: int, card: Card) -> None:
    canvas.put(x, y, " " * COMPACT_W, "card.blank")
    canvas.put(x, y, card_label(card), card_style(card))


def draw_compact_back(canvas: Canvas, x: int, y: int) -> None:
    canvas.put(x, y, BACK_FILL * 2, "card.back")


def draw_hand_compact(
    canvas: Canvas,
    x: int,
    y: int,
    cards: tuple[Card, ...] | None,
    *,
    face_up: bool,
    folded: bool = False,
) -> int:
    """Two cards on a single line, as they appear inside a seat box.

    Returns the width drawn.
    """
    if folded:
        canvas.put(x, y, "--  --", "seat.folded")
        return 6
    if not face_up or cards is None:
        draw_compact_back(canvas, x, y)
        draw_compact_back(canvas, x + 3, y)
        return 5
    draw_compact_card(canvas, x, y, cards[0])
    draw_compact_card(canvas, x + 4, y, cards[1])
    return 7


def draw_board(
    canvas: Canvas, x: int, y: int, board: tuple[Card, ...], slots: int = 5,
    gap: int = 1,
) -> int:
    """Up to five board cards left to right, with slots for the rest."""
    step = CARD_W + gap
    for i in range(slots):
        cx = x + i * step
        if i < len(board):
            draw_card(canvas, cx, y, board[i])
        else:
            draw_card_slot(canvas, cx, y)
    return slots * step - gap


def board_width(slots: int = 5, gap: int = 1) -> int:
    return slots * (CARD_W + gap) - gap
