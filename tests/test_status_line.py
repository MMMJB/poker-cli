"""The between-hands status line must not be eaten by the key hints.

They used to share a row, the hint was drawn first at full length, and the
message -- which says what just happened -- got cut off mid-word.
"""

from __future__ import annotations

import pytest

from poker.engine.state import PlayerStatus
from poker.ui.cards import probe_glyph_width
from poker.ui.model import ActionBar, InputLine, SeatView, TableView
from poker.ui.seats import COMPACT, FULL
from poker.ui.table import render_frame
from poker.ui.theme import THEME

probe_glyph_width()

LONG_HINT = ("enter: next   ·   r: replay   ·   t: new table   "
             "·   v: reviews   ·   q: quit")
SHORT_HINT = "enter · r replay · t table · v reviews · q quit"

SEATS = tuple(
    SeatView(i, name, 300, pos, PlayerStatus.ACTIVE, is_hero=(i == 0))
    for i, (name, pos) in enumerate([
        ("YOU", "BTN"), ("Deb", "SB"), ("Walt", "BB"),
        ("Tank", "UTG"), ("Raj", "HJ"), ("Sofia", "CO"),
    ])
)


def rows_for(message: str, layout, hint=LONG_HINT, short=SHORT_HINT):
    from rich.console import Console

    view = TableView(
        hand_id=142, seats=SEATS,
        action_bar=ActionBar(active=False, message=message),
        input_line=InputLine(active=True, hint=hint, hint_short=short),
    )
    console = Console(theme=THEME, width=layout.cols + 1, height=layout.rows + 2)
    rendered = list(render_frame(view, layout, 0.0).rows(console))
    line = lambda i: "".join(seg.text for seg in rendered[i])
    return line(layout.action_y), line(layout.prompt_y)


MESSAGES = [
    "Hand #69: you broke even.   ·   table 1 · 6-handed · 69 hands (31 to go)",
    "Hand #142: you lost $1,309.   ·   table 3 · 8-handed · 7 hands (93 to go)",
    "Hand #7: you won $2,016.   ·   table 12 · 4-handed · 100 hands (0 to go)",
]


@pytest.mark.parametrize("message", MESSAGES)
@pytest.mark.parametrize("layout", [FULL, COMPACT], ids=["full", "compact"])
def test_the_message_survives_intact(message: str, layout) -> None:
    action, _ = rows_for(message, layout)
    assert message in action, f"truncated to: {action.strip()!r}"


@pytest.mark.parametrize("layout", [FULL, COMPACT], ids=["full", "compact"])
def test_the_hint_sits_on_the_prompt_row(layout) -> None:
    action, prompt = rows_for(MESSAGES[0], layout)
    assert "enter" in prompt
    assert "enter" not in action, "the hint must not share the message's row"


@pytest.mark.parametrize("layout", [FULL, COMPACT], ids=["full", "compact"])
def test_every_option_is_still_offered(layout) -> None:
    """A narrow hint may be worded shorter, but must not lose a key."""
    _, prompt = rows_for(MESSAGES[0], layout)
    for key in ("enter", "r", "t", "v", "q"):
        assert key in prompt
    for word in ("replay", "table", "reviews", "quit"):
        assert word in prompt, f"{word} dropped at {layout.name}"


def test_the_full_hint_is_used_when_it_fits() -> None:
    _, prompt = rows_for(MESSAGES[0], FULL)
    assert "enter: next" in prompt


def test_the_short_hint_is_used_when_the_full_one_will_not_fit() -> None:
    _, prompt = rows_for(MESSAGES[0], COMPACT)
    assert "enter: next" not in prompt
    assert SHORT_HINT in prompt


def test_the_prompt_marker_is_never_overwritten() -> None:
    """The hint used to run all the way back over the input cursor."""
    for layout in (FULL, COMPACT):
        _, prompt = rows_for(MESSAGES[1], layout)
        assert "›" in prompt, f"{layout.name}: prompt marker lost"
        marker = prompt.index("›")
        hint_start = min(
            (prompt.index(w) for w in ("enter",) if w in prompt),
            default=len(prompt),
        )
        assert hint_start > marker + 1, "the hint runs over the cursor"


@pytest.mark.parametrize("layout", [FULL, COMPACT], ids=["full", "compact"])
def test_nothing_exceeds_the_width(layout) -> None:
    for line in rows_for(MESSAGES[1], layout):
        assert len(line.rstrip()) <= layout.cols


def test_an_absurdly_long_message_is_cut_at_a_separator() -> None:
    """Better to drop a whole clause than to end mid-word."""
    from poker.ui.table import _truncate_at_separator

    sep = "\u00b7"
    text = "a" * 30 + f" {sep} " + "b" * 30 + f" {sep} " + "c" * 30
    out = _truncate_at_separator(text, 70)

    assert len(out) <= 70
    assert text.startswith(out), "the result must be a prefix of the original"
    # It must stop on a boundary, never part-way through a run of characters.
    rest = text[len(out):]
    assert rest == "" or rest[0] in (" ", sep), f"cut mid-token: {out!r}"

    assert _truncate_at_separator("short", 70) == "short"
    assert _truncate_at_separator("wordy text here", 0) == ""

    # With no separator at all it falls back to a word boundary.
    words = _truncate_at_separator("one two three four five", 12)
    assert len(words) <= 12
    assert not words.endswith("thre"), "cut mid-word"


def test_a_hint_with_no_short_form_still_fits() -> None:
    _, prompt = rows_for(MESSAGES[0], COMPACT, hint=LONG_HINT, short="")
    assert len(prompt.rstrip()) <= COMPACT.cols


# --------------------------------------------------------------------------
# The input block collapses to one row when the status row has nothing to say
# --------------------------------------------------------------------------

def view_for(**kw) -> TableView:
    base = dict(hand_id=61, seats=SEATS, hero_seat=0, button=0)
    return TableView(**{**base, **kw})


# The real draft hints, so the rendering assertions below test what ships.
from poker.app.input import DRAFT_HINT, DRAFT_HINT_SHORT  # noqa: E402

DRAFT = InputLine(active=True, text="raise 40", cursor=8, draft=True,
                  hint=DRAFT_HINT, hint_short=DRAFT_HINT_SHORT)
LIVE = InputLine(active=True, text="raise 60", cursor=8, preview="raise to $60",
                 hint="fold  ·  call 24  ·  raise")


def test_one_row_while_waiting_on_an_opponent() -> None:
    from poker.ui.table import input_rows_needed

    assert input_rows_needed(view_for(input_line=DRAFT)) == 1


def test_one_row_when_nothing_is_happening() -> None:
    from poker.ui.table import input_rows_needed

    assert input_rows_needed(view_for()) == 1


def test_two_rows_when_it_is_your_turn() -> None:
    from poker.ui.table import input_rows_needed

    view = view_for(action_bar=ActionBar(active=True, pot=48), input_line=LIVE)
    assert input_rows_needed(view) == 2


def test_two_rows_between_hands() -> None:
    from poker.ui.table import input_rows_needed

    view = view_for(action_bar=ActionBar(active=False, message="Hand #61: you won $92."),
                    input_line=InputLine(active=True, hint=LONG_HINT))
    assert input_rows_needed(view) == 2


def test_two_rows_when_someone_is_talking() -> None:
    from poker.ui.table import input_rows_needed

    view = view_for(talk="I'm never folding tonight.", talk_speaker="Tank",
                    input_line=DRAFT)
    assert input_rows_needed(view) == 2


def render(view: TableView, layout) -> list[str]:
    from rich.console import Console
    from poker.ui.table import render_frame

    console = Console(theme=THEME, width=layout.cols + 1, height=layout.rows + 2)
    return ["".join(seg.text for seg in row)
            for row in render_frame(view, layout, 0.0).rows(console)]


@pytest.mark.parametrize("layout", [FULL, COMPACT], ids=["full", "compact"])
def test_the_frame_keeps_its_height_either_way(layout) -> None:
    """The freed row is given back to the table, not removed from the frame."""
    one = render(view_for(input_line=DRAFT), layout)
    two = render(view_for(action_bar=ActionBar(active=True, pot=48),
                          input_line=LIVE), layout)
    assert len(one) == len(two) == layout.rows


@pytest.mark.parametrize("layout", [FULL, COMPACT], ids=["full", "compact"])
def test_collapsing_leaves_a_blank_row_under_the_hero(layout) -> None:
    lines = render(view_for(input_line=DRAFT), layout)
    hero = layout.slot_for(0, 0, 6)
    below = hero.y + layout.seat_h        # first row under the hero's box

    # That row is blank, and the separator has moved down one to make it so.
    assert lines[below].strip("│ ") == "", f"row {below} is not blank"
    assert lines[layout.input_sep].strip("│ ") == ""
    assert "├" in lines[layout.input_sep + 1], "separator did not move down"


@pytest.mark.parametrize("layout", [FULL, COMPACT], ids=["full", "compact"])
def test_expanded_keeps_the_separator_where_it_was(layout) -> None:
    lines = render(view_for(action_bar=ActionBar(active=True, pot=48),
                            input_line=LIVE), layout)
    assert "├" in lines[layout.input_sep]


@pytest.mark.parametrize("layout", [FULL, COMPACT], ids=["full", "compact"])
def test_a_collapsed_row_still_shows_the_input_and_its_hint(layout) -> None:
    """With only one row, the hint has nowhere else to go, so it shares it."""
    lines = render(view_for(input_line=DRAFT), layout)
    row = lines[layout.prompt_y]
    assert "raise 40" in row
    assert "turn" in row, "the hint vanished when the row collapsed"
    assert len(row.rstrip()) <= layout.cols


@pytest.mark.parametrize("layout", [FULL, COMPACT], ids=["full", "compact"])
def test_the_collapsed_row_does_not_double_up_on_notes(layout) -> None:
    """The draft note and the hint say the same thing; only one should show."""
    row = render(view_for(input_line=DRAFT), layout)[layout.prompt_y]
    assert "ready when it's your turn" not in row
