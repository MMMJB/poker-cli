"""The table renderable.

A single renderable, rebuilt from the view model every frame -- no incremental
patching.  Everything here is a pure function of a :class:`TableView`, so a
dropped frame simply skips ahead instead of desynchronizing.
"""

from __future__ import annotations

from typing import Callable

from rich.console import Console, ConsoleOptions, RenderResult
from rich.segment import Segment

from poker.engine.state import Street
from poker.ui import cards as uicards
from poker.ui.canvas import Canvas, DOUBLE, ROUNDED
from poker.ui.model import TableView
from poker.ui.seats import Layout, SeatSlot, choose_layout

FULL_WIDTH = 100
DOTS = ("·", "··", "···")


def money(n: int) -> str:
    return f"${n:,}"


def signed_money(n: int) -> str:
    return f"{'+' if n >= 0 else '-'}${abs(n):,}"


class TableRenderable:
    """Wraps a zero-argument callable returning the current view."""

    def __init__(self, get_view: Callable[[], TableView], get_tick: Callable[[], float]):
        self._get_view = get_view
        self._get_tick = get_tick

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        # Read the view reference exactly once: it is immutable, so this frame
        # is internally consistent even if the app publishes mid-render.
        view = self._get_view()
        tick = self._get_tick()

        cols, rows = console.size
        layout = choose_layout(cols, rows)
        if layout is None:
            yield from _centred(_too_small(cols, rows), console, cols, rows)
            return

        yield from _centred(render_frame(view, layout, tick), console, cols, rows)


_NEWLINE = Segment("\n")


def _centred(canvas: Canvas, console: Console, cols: int, rows: int):
    """Place a fixed-size canvas in the middle of the terminal.

    The board is drawn at a fixed size so the layout stays stable at any window
    size; this centres that block in whatever space it lands in.

    Newlines go *between* rows and never after the last one -- a trailing
    newline on the final row pushes the alternate screen down by one and the
    top of the board scrolls out of view.
    """
    lines = list(canvas.rows(console))
    left = max(0, (cols - canvas.width) // 2)
    top = max(0, (rows - len(lines)) // 2)
    pad = Segment(" " * left) if left else None

    for _ in range(top):
        yield _NEWLINE
    for i, segments in enumerate(lines):
        if i:
            yield _NEWLINE
        if pad is not None:
            yield pad
        yield from segments


def _too_small(cols: int, rows: int) -> Canvas:
    from poker.ui.seats import COMPACT

    c = Canvas(max(cols, 24), max(rows, 6))
    msg = [
        "Terminal too small.",
        f"Need at least {COMPACT.cols} x {COMPACT.rows}. Currently {cols} x {rows}.",
        "Resize the window -- the game is paused and nothing was lost.",
    ]
    top = max(0, (c.height - len(msg)) // 2)
    for i, line in enumerate(msg):
        c.put_center(c.width // 2, top + i, line[: c.width],
                     "title" if i == 0 else "subtle")
    return c


# --------------------------------------------------------------------------
# Frame assembly
# --------------------------------------------------------------------------

def render_frame(view: TableView, layout: Layout, tick: float) -> Canvas:
    canvas = Canvas(layout.cols, layout.rows)

    canvas.box(0, 0, layout.cols, layout.rows, "chrome", ROUNDED)
    for row in layout.sep_rows:
        if 0 < row < layout.rows - 1:
            canvas.put(0, row, "├" + "─" * (layout.cols - 2) + "┤",
                       "chrome")

    _draw_header(canvas, view, layout)

    if view.review.active or view.review.pending:
        _draw_review(canvas, view, layout)
    else:
        _draw_felt(canvas, view, layout)
        _draw_board(canvas, view, layout)
        _draw_seats(canvas, view, layout, tick)
        _draw_bets(canvas, view, layout)

    _draw_action_bar(canvas, view, layout)
    _draw_log(canvas, view, layout)

    if view.banner:
        canvas.put(layout.cols - len(view.banner) - 3, 0,
                   f" {view.banner} ", "banner.offline")
    if view.debug:
        canvas.put(2, layout.rows - 1, f" {view.debug} "[: layout.cols - 4], "debug")
    return canvas


def _draw_header(canvas: Canvas, view: TableView, layout: Layout) -> None:
    y = layout.header_y
    seats = len(view.seats) or 6
    left = (f" {money(view.small_blind)}/{money(view.big_blind)} NL Hold'em "
            f"· {seats}-max")
    canvas.put(2, y, left, "title")

    if view.session_label:
        right = f"Hand #{view.hand_id}    {view.session_label}"
        style = "subtle"
    else:
        profit = signed_money(view.session_profit)
        right = (f"Hand #{view.hand_id}    Session {profit} "
                 f"({view.hands_played} hands)")
        style = "seat.stack" if view.session_profit >= 0 else "prompt.error"
    canvas.put(layout.cols - len(right) - 2, y, right, style)

    if not view.review_on:
        tag = "review off"
        x = layout.cols - len(right) - len(tag) - 5
        if x > len(left) + 3:
            canvas.put(x, y, tag, "muted")


def _draw_felt(canvas: Canvas, view: TableView, layout: Layout) -> None:
    x, y, w, h = layout.felt
    canvas.oval(x, y, w, h, "felt")


def _draw_board(canvas: Canvas, view: TableView, layout: Layout) -> None:
    uicards.draw_board(canvas, layout.board_x, layout.board_y, view.board)

    fx, _, fw, _ = layout.felt
    cx = fx + fw // 2

    # The street label shares the pot line: as its own row it collided with the
    # lower bet anchors in the compact layout.
    label = {
        Street.PREFLOP: "PREFLOP",
        Street.FLOP: "FLOP",
        Street.TURN: "TURN",
        Street.RIVER: "RIVER",
        Street.SHOWDOWN: "SHOWDOWN",
    }.get(view.street, "")
    pot_text = f"POT  {money(view.pot)}"
    if label:
        pot_text = f"{label}  \u00b7  {pot_text}"
    canvas.put_center(cx, layout.pot_y, pot_text, "pot")


def _draw_seats(canvas: Canvas, view: TableView, layout: Layout, tick: float) -> None:
    for seat in view.seats:
        slot = layout.slot_for(seat.seat, view.hero_seat, len(view.seats))
        if layout.boxed:
            _draw_seat_box(canvas, seat, slot, layout, tick)
        else:
            _draw_seat_plain(canvas, seat, slot, layout, tick)


def _seat_styles(seat) -> tuple[str, str, str]:
    """(border, name, stack) styles for this seat's state."""
    if seat.is_winner:
        return "seat.winner", "seat.winner", "seat.winner"
    if seat.folded:
        return "seat.folded", "seat.folded", "seat.folded"
    if seat.is_acting:
        return "seat.acting", "seat.acting", "seat.stack"
    if seat.all_in:
        return "seat.allin", "seat.name", "seat.allin"
    if seat.is_hero:
        return "seat.hero", "seat.name", "seat.stack"
    return "seat", "seat.name", "seat.stack"


def _seat_note(seat) -> str:
    if seat.note:
        return seat.note
    if seat.is_winner and seat.won:
        return f"{seat.win_label} {money(seat.won)}"
    if seat.folded:
        return "FOLDED"
    if seat.all_in:
        return "ALL-IN"
    return ""


def _draw_seat_box(canvas, seat, slot: SeatSlot, layout: Layout, tick: float) -> None:
    w, h = layout.seat_w, layout.seat_h
    border, name_style, stack_style = _seat_styles(seat)
    chars = DOUBLE if seat.is_acting or seat.is_hero else ROUNDED

    note = _seat_note(seat)
    canvas.box(slot.x, slot.y, w, h, border, chars,
               footer=note, footer_style=border)

    inner_x = slot.x + 2
    inner_w = w - 4

    name = ("YOU" if seat.is_hero else seat.name)[: inner_w - 7]
    stack = money(seat.stack)
    canvas.put(inner_x, slot.y + 1, name, name_style)
    canvas.put(slot.x + w - 2 - len(stack), slot.y + 1, stack, stack_style)

    row = slot.y + 2
    if seat.thinking:
        dots = DOTS[int(tick * 3) % len(DOTS)]
        canvas.put(inner_x, row, f"{dots:<6}", "seat.acting")
    else:
        uicards.draw_hand_compact(
            canvas, inner_x, row, seat.hole,
            face_up=seat.face_up, folded=seat.folded,
        )

    badge = seat.position
    badge_style = "badge.btn" if badge == "BTN" else "badge.pos"
    canvas.put(slot.x + w - 2 - len(badge), row, badge, badge_style)


def _draw_seat_plain(canvas, seat, slot: SeatSlot, layout: Layout, tick: float) -> None:
    """Borderless seat for the compact layout -- two lines, no box."""
    w = layout.seat_w
    _, name_style, stack_style = _seat_styles(seat)

    marker = "▸" if seat.is_acting else " "
    name = ("YOU" if seat.is_hero else seat.name)[: w - 9]
    stack = money(seat.stack)
    canvas.put(slot.x, slot.y, f"{marker}{name}", name_style)
    canvas.put(slot.x + w - len(stack), slot.y, stack, stack_style)

    row = slot.y + 1
    if seat.thinking:
        dots = DOTS[int(tick * 3) % len(DOTS)]
        canvas.put(slot.x + 1, row, f"{dots:<6}", "seat.acting")
    else:
        uicards.draw_hand_compact(
            canvas, slot.x + 1, row, seat.hole,
            face_up=seat.face_up, folded=seat.folded,
        )

    note = _seat_note(seat)
    if note:
        canvas.put(slot.x + w - len(note), row, note,
                   "seat.winner" if seat.is_winner else "muted")
    else:
        badge = seat.position
        canvas.put(slot.x + w - len(badge), row, badge,
                   "badge.btn" if badge == "BTN" else "badge.pos")


def _draw_bets(canvas: Canvas, view: TableView, layout: Layout) -> None:
    for seat in view.seats:
        if seat.street_bet <= 0:
            continue
        slot = layout.slot_for(seat.seat, view.hero_seat, len(view.seats))
        text = money(seat.street_bet)
        if slot.bet_align == "center":
            canvas.put_center(slot.bet_x, slot.bet_y, text, "bet")
        elif slot.bet_align == "right":
            canvas.put(slot.bet_x - len(text), slot.bet_y, text, "bet")
        else:
            canvas.put(slot.bet_x, slot.bet_y, text, "bet")


def _draw_action_bar(canvas: Canvas, view: TableView, layout: Layout) -> None:
    """The status line above the input: pot, price, and what is legal."""
    bar = view.action_bar
    y = layout.action_y
    style = "action.bad" if bar.error_flash else "action"

    if bar.message:
        hint = view.input_line.hint
        room = layout.cols - 4
        if hint:
            hint_x = max(2, layout.cols - len(hint) - 2)
            canvas.put(hint_x, y, hint, "prompt.hint")
            room = hint_x - 4
        canvas.put(2, y, bar.message[: max(0, room)], "prompt")
        _draw_input_line(canvas, view, layout)
        return

    if not bar.active:
        if view.talk:
            canvas.put(2, y, f'"{view.talk}"'[: layout.cols - 20], "talk")
            speaker = f"\u2014 {view.talk_speaker}"
            canvas.put(layout.cols - len(speaker) - 2, y, speaker, "muted")
        _draw_input_line(canvas, view, layout)
        return

    hint = view.input_line.hint

    full = f"YOUR ACTION  \u00b7  pot {money(bar.pot)}"
    if bar.to_call:
        full += f"  \u00b7  to call {money(bar.to_call)}"
    short = f"pot {money(bar.pot)}"
    if bar.to_call:
        short += f" \u00b7 call {money(bar.to_call)}"

    # Keep a real gap between the status and the hint: at one space they read
    # as a single run-on line.
    gap = 4
    left = full
    for candidate in (full, short):
        if 2 + len(candidate) + gap + len(hint) + 2 <= layout.cols:
            left = candidate
            break
    else:
        left = short
        room = max(0, layout.cols - 2 - len(left) - gap - 2)
        hint = _truncate_at_separator(hint, room)

    canvas.put(2, y, left, style)
    if hint:
        canvas.put(layout.cols - len(hint) - 2, y, hint, "prompt.hint")

    _draw_input_line(canvas, view, layout)


def _truncate_at_separator(text: str, room: int) -> str:
    """Trim to the last whole item rather than cutting mid-number."""
    if len(text) <= room:
        return text
    cut = text.rfind("\u00b7", 0, room)
    return text[:cut].rstrip() if cut > 0 else ""


def _draw_input_line(canvas: Canvas, view: TableView, layout: Layout) -> None:
    """The editable command line, with a visible block cursor.

    Drawn in three pieces so the cell under the cursor can be inverted: that is
    what makes it obvious the line is being edited rather than submitted.

    A draft -- typed while someone else is still to act -- is drawn in grey, so
    it reads as prepared rather than pending submission.
    """
    line = view.input_line
    y = layout.prompt_y
    if not line.active:
        return

    text_style = "prompt.draft" if line.draft else "prompt"
    cursor_style = "prompt.draft.cursor" if line.draft else "prompt.cursor"

    prompt = "› "
    canvas.put(2, y, prompt, "prompt.draft" if line.draft else "prompt.hint")
    x = 2 + len(prompt)

    before, at, after = line.before, line.at, line.after
    canvas.put(x, y, before, text_style)
    canvas.put(x + len(before), y, at, cursor_style)
    canvas.put(x + len(before) + 1, y, after, text_style)

    tail_x = x + len(line.text) + 3
    room = max(0, layout.cols - tail_x - 2)
    if line.draft:
        # No preview while drafting: the legal actions are not known until the
        # action reaches you, and a raise that is legal now may not be by then.
        note = "ready when it's your turn" if line.text.strip() else ""
        canvas.put(tail_x, y, note[:room], "prompt.draft")
    elif line.error:
        canvas.put(tail_x, y, line.error[:room], "prompt.error")
    elif line.preview:
        canvas.put(tail_x, y, f"↵ {line.preview}"[:room], "prompt.preview")


def _draw_log(canvas: Canvas, view: TableView, layout: Layout) -> None:
    lines = list(view.log_lines)[-layout.log_lines:]
    for i, entry in enumerate(lines):
        text, style = entry if isinstance(entry, tuple) else (entry, "log")
        canvas.put(2, layout.log_y + i, text[: layout.cols - 4], style)


def _draw_review(canvas: Canvas, view: TableView, layout: Layout) -> None:
    from poker.ui.theme import VERDICT_LABEL, VERDICT_STYLE

    r = view.review
    x, y, w, h = layout.felt
    top = 4
    style = VERDICT_STYLE.get(r.verdict, "review.ok")

    canvas.box(3, top, layout.cols - 6, layout.action_y - top - 1, style, ROUNDED,
               title=f"HAND REVIEW · #{r.hand_id}", title_style="review.head")

    if r.pending:
        canvas.put(6, top + 2, "Reviewing the hand…", "subtle")
        return

    label = VERDICT_LABEL.get(r.verdict, r.verdict.upper())
    canvas.put(layout.cols - len(label) - 8, top, f"┤ {label} ├", style)

    row = top + 2
    canvas.put(6, row, r.headline[: layout.cols - 12], "review.head")
    row += 2

    for note in r.notes:
        if row >= layout.action_y - 3:
            break
        canvas.put(6, row, f"{note.street.upper():<7}", "review.street")
        for line in _wrap(note.text, layout.cols - 22):
            canvas.put(14, row, line, "log")
            row += 1
        row += 1

    if r.better_line and row < layout.action_y - 2:
        canvas.put(6, row, f"Better: {r.better_line}"[: layout.cols - 12], style)


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines or [""]
