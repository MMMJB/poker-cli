"""A fixed-coordinate character canvas.

Six seats around an oval is a *positional* problem, not a nesting one: getting
a felt outline, seat boxes at diagonal anchors, and chips travelling from a
seat to the pot out of a box model means fighting the box model for every cell.
A character grid makes all of it a one-liner.

Rendering merges runs of adjacent same-style cells, which keeps a full 100x32
frame down to a few hundred Segments.
"""

from __future__ import annotations

from typing import Callable, Iterator

from rich.console import Console, ConsoleOptions, RenderResult
from rich.segment import Segment
from rich.style import Style

# Box-drawing sets: (tl, tr, bl, br, horizontal, vertical)
ROUNDED = ("╭", "╮", "╰", "╯", "─", "│")
SQUARE = ("┌", "┐", "└", "┘", "─", "│")
DOUBLE = ("╔", "╗", "╚", "╝", "═", "║")


class Canvas:
    """A ``width`` x ``height`` grid of characters, each with an optional style."""

    __slots__ = ("width", "height", "_ch", "_st")

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._ch: list[list[str]] = [[" "] * width for _ in range(height)]
        self._st: list[list[str | None]] = [[None] * width for _ in range(height)]

    # ------------------------------------------------------------- painting

    def put(self, x: int, y: int, text: str, style: str | None = None) -> None:
        """Draw ``text`` with its first character at ``(x, y)``, clipped."""
        if not (0 <= y < self.height) or not text:
            return
        row_ch = self._ch[y]
        row_st = self._st[y]
        for i, ch in enumerate(text):
            col = x + i
            if col < 0:
                continue
            if col >= self.width:
                break
            row_ch[col] = ch
            row_st[col] = style

    def put_center(self, cx: int, y: int, text: str, style: str | None = None) -> None:
        self.put(cx - len(text) // 2, y, text, style)

    def fill(self, x: int, y: int, w: int, h: int, ch: str = " ",
             style: str | None = None) -> None:
        for row in range(y, y + h):
            self.put(x, row, ch * w, style)

    def hline(self, x: int, y: int, w: int, ch: str = "─",
              style: str | None = None) -> None:
        self.put(x, y, ch * w, style)

    def vline(self, x: int, y: int, h: int, ch: str = "│",
              style: str | None = None) -> None:
        for row in range(y, y + h):
            self.put(x, row, ch, style)

    def box(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        style: str | None = None,
        chars: tuple[str, ...] = ROUNDED,
        title: str = "",
        title_style: str | None = None,
        footer: str = "",
        footer_style: str | None = None,
        fill: bool = False,
        fill_style: str | None = None,
    ) -> None:
        """Draw a box outline, optionally with a title/footer set into a border."""
        if w < 2 or h < 2:
            return
        tl, tr, bl, br, hz, vt = chars

        self.put(x, y, tl + hz * (w - 2) + tr, style)
        self.put(x, y + h - 1, bl + hz * (w - 2) + br, style)
        for row in range(y + 1, y + h - 1):
            self.put(x, row, vt, style)
            if fill:
                self.put(x + 1, row, " " * (w - 2), fill_style)
            self.put(x + w - 1, row, vt, style)

        if title:
            label = f"┤ {title} ├"
            self.put(x + 2, y, label[: max(0, w - 4)], title_style or style)
        if footer:
            label = f"┤ {footer} ├"
            self.put(x + 2, y + h - 1, label[: max(0, w - 4)], footer_style or style)

    def oval(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        style: str | None = None,
        fill_style: str | None = None,
    ) -> None:
        """A rounded 'felt' outline: straight edges with a one-cell taper.

            .. code-block:: text

                 \u256d\u2500\u2500\u2500\u2500\u256e
                \u256d\u256f    \u2570\u256e
                \u2502      \u2502
                \u2570\u256e    \u256d\u256f
                 \u2570\u2500\u2500\u2500\u2500\u256f
        """
        if w < 8 or h < 5:
            self.box(x, y, w, h, style)
            return

        right = x + w - 1

        # Top and bottom edges, inset by one cell at each end.
        self.put(x + 1, y, "\u256d" + "\u2500" * (w - 4) + "\u256e", style)
        self.put(x + 1, y + h - 1, "\u2570" + "\u2500" * (w - 4) + "\u256f", style)

        # The taper rows just inside them.
        self.put(x, y + 1, "\u256d\u256f", style)
        self.put(right - 1, y + 1, "\u2570\u256e", style)
        self.put(x, y + h - 2, "\u2570\u256e", style)
        self.put(right - 1, y + h - 2, "\u256d\u256f", style)

        # Straight sides.
        for row in range(y + 2, y + h - 2):
            self.put(x, row, "\u2502", style)
            self.put(right, row, "\u2502", style)

        if fill_style is not None:
            for row in range(y + 1, y + h - 1):
                pad = 2 if row in (y + 1, y + h - 2) else 1
                self.put(x + pad, row, " " * (w - 2 * pad), fill_style)

    def sub(self, x: int, y: int, w: int, h: int) -> "SubCanvas":
        return SubCanvas(self, x, y, w, h)

    # ------------------------------------------------------------ rendering

    def rows(self, console: Console) -> Iterator[list[Segment]]:
        resolve = _style_resolver(console)
        for row_ch, row_st in zip(self._ch, self._st):
            yield _run_length_segments(row_ch, row_st, resolve)

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        newline = Segment("\n")
        for segments in self.rows(console):
            yield from segments
            yield newline


class SubCanvas:
    """A translated view onto a parent canvas -- for panels and clipping."""

    __slots__ = ("_parent", "_x", "_y", "width", "height")

    def __init__(self, parent: Canvas, x: int, y: int, w: int, h: int) -> None:
        self._parent = parent
        self._x = x
        self._y = y
        self.width = w
        self.height = h

    def put(self, x: int, y: int, text: str, style: str | None = None) -> None:
        if not (0 <= y < self.height):
            return
        if x >= self.width:
            return
        if x < 0:
            text = text[-x:]
            x = 0
        self._parent.put(self._x + x, self._y + y, text[: self.width - x], style)

    def put_center(self, cx: int, y: int, text: str, style: str | None = None) -> None:
        self.put(cx - len(text) // 2, y, text, style)


def _run_length_segments(
    row_ch: list[str],
    row_st: list[str | None],
    resolve: "Callable[[str | None], Style | None]",
) -> list[Segment]:
    """Merge adjacent cells that share a style into one Segment."""
    out: list[Segment] = []
    if not row_ch:
        return out

    start = 0
    current = row_st[0]
    for i in range(1, len(row_ch)):
        if row_st[i] != current:
            out.append(Segment("".join(row_ch[start:i]), resolve(current)))
            start = i
            current = row_st[i]
    out.append(Segment("".join(row_ch[start:]), resolve(current)))
    return out


def _style_resolver(console: Console) -> "Callable[[str | None], Style | None]":
    """Theme-name -> Style, memoized per console.

    Names must go through ``console.get_style`` so the theme resolves them;
    ``Style.parse`` only understands literal colours.
    """
    cache: dict[str | None, Style | None] = getattr(console, "_canvas_styles", None)
    if cache is None:
        cache = {None: None}
        console._canvas_styles = cache  # type: ignore[attr-defined]

    def resolve(name: str | None) -> Style | None:
        if name in cache:
            return cache[name]
        try:
            style = console.get_style(name)
        except Exception:
            style = None
        cache[name] = style
        return style

    return resolve
