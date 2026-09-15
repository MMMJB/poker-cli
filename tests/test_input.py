"""The command line: visible input, full editing, and nothing auto-submitted."""

from __future__ import annotations

import asyncio

from poker.app.commands import QUIT, TOGGLE_REVIEW, hint_for
from poker.app.input import ActionPrompt, LineEditor
from poker.engine.actions import ActionType, LegalActions
from poker.ui import keys as K
from poker.ui.keys import KeyReader

FACING_BET = LegalActions(
    seat=0, can_fold=True, can_check=False, can_call=True, call_cost=11,
    call_is_all_in=False, can_bet=False, can_raise=True, min_to=22,
    max_to=297, stack=297,
)
CHECKED_TO = LegalActions(
    seat=0, can_fold=True, can_check=True, can_call=False, call_cost=0,
    call_is_all_in=False, can_bet=True, can_raise=False, min_to=3,
    max_to=300, stack=300,
)


# --------------------------------------------------------------------------
# The line editor
# --------------------------------------------------------------------------

def test_typing_and_backspace() -> None:
    e = LineEditor()
    for ch in "raise":
        e.apply(ch)
    assert (e.text, e.cursor) == ("raise", 5)
    e.apply(K.KEY_BACKSPACE)
    assert (e.text, e.cursor) == ("rais", 4)


def test_cursor_movement_and_mid_line_insert() -> None:
    e = LineEditor("raise 50")
    e.apply(K.KEY_HOME)
    assert e.cursor == 0
    e.apply(K.KEY_RIGHT)
    e.apply(K.KEY_RIGHT)
    e.apply("X")
    assert e.text == "raXise 50"
    e.apply(K.KEY_END)
    assert e.cursor == len(e.text)


def test_delete_removes_forward() -> None:
    e = LineEditor("fold")
    e.apply(K.KEY_HOME)
    e.apply(K.KEY_DELETE)
    assert e.text == "old"


def test_cursor_cannot_leave_the_line() -> None:
    e = LineEditor("ab")
    e.apply(K.KEY_HOME)
    e.apply(K.KEY_LEFT)
    assert e.cursor == 0
    e.apply(K.KEY_END)
    e.apply(K.KEY_RIGHT)
    assert e.cursor == 2
    e.apply(K.KEY_BACKSPACE)
    e.apply(K.KEY_BACKSPACE)
    e.apply(K.KEY_BACKSPACE)
    assert e.text == ""


def test_kill_word_and_line() -> None:
    e = LineEditor("raise to 50")
    e.apply(K.KEY_CTRL_W)
    assert e.text == "raise to "
    e.apply(K.KEY_CTRL_U)
    assert e.text == ""

    e = LineEditor("raise 50")
    e.apply(K.KEY_HOME)
    for _ in range(5):
        e.apply(K.KEY_RIGHT)
    e.apply(K.KEY_CTRL_K)
    assert e.text == "raise"


def test_unknown_keys_are_ignored() -> None:
    e = LineEditor("f")
    assert not e.apply(K.KEY_UP)
    assert not e.apply("\x00")
    assert e.text == "f"


def test_view_splits_around_the_cursor() -> None:
    from poker.ui.model import InputLine

    line = InputLine(text="raise", cursor=2)
    assert (line.before, line.at, line.after) == ("ra", "i", "se")
    end = InputLine(text="raise", cursor=5)
    assert (end.before, end.at, end.after) == ("raise", " ", "")


# --------------------------------------------------------------------------
# The prompt -- the part that must never auto-submit
# --------------------------------------------------------------------------

TIMED_OUT = object()


def drive(keys_in, legal=FACING_BET, pot=24, current_bet=11, timeout=0.4):
    """Feed keypresses to the prompt.

    Keys are fed *after* ``ask`` has started, because it drains the queue on
    entry -- that discard is deliberate (it throws away keys mashed while an
    opponent was acting, so they cannot act for you), and feeding earlier would
    simply be swallowed by it.

    Returns ``(result, frames)``.  ``result`` is ``TIMED_OUT`` when the prompt
    is still waiting for input -- the expected outcome whenever Enter was never
    pressed, which is what most of these tests assert.
    """
    frames: list[dict] = []
    outcome: list = [TIMED_OUT]

    async def go():
        reader = KeyReader()
        reader.enabled = True
        prompt = ActionPrompt(reader, lambda **kw: frames.append(kw),
                              flash_seconds=0.0)
        task = asyncio.ensure_future(prompt.ask(legal, pot, current_bet))
        await asyncio.sleep(0.01)   # let ask() drain and reach its first await
        reader.feed(*keys_in)
        try:
            outcome[0] = await asyncio.wait_for(task, timeout)
        except asyncio.TimeoutError:
            task.cancel()

    asyncio.run(go())
    return outcome[0], frames


def last_line(frames: list[dict]):
    for frame in reversed(frames):
        if "input_line" in frame:
            return frame["input_line"]
    raise AssertionError("no input line was published")


# --- nothing auto-submits -------------------------------------------------

def test_typing_an_action_does_not_submit_it() -> None:
    """The point of the redesign: 'f' alone must not fold."""
    result, frames = drive(["f"])
    assert result is TIMED_OUT
    assert last_line(frames).text == "f"


def test_a_whole_word_still_does_not_submit() -> None:
    result, frames = drive(list("fold"))
    assert result is TIMED_OUT
    assert last_line(frames).text == "fold"


def test_typed_text_is_echoed_as_it_is_entered() -> None:
    _, frames = drive(list("raise 5"))
    texts = [f["input_line"].text for f in frames if "input_line" in f]
    assert "r" in texts and "ra" in texts and "raise 5" in texts


def test_the_preview_shows_what_enter_will_do() -> None:
    _, frames = drive(list("call"))
    assert "call $11" in last_line(frames).preview


# --- committing -----------------------------------------------------------

def test_enter_submits_the_typed_action() -> None:
    result, _ = drive(list("fold") + [K.KEY_ENTER])
    assert result.type is ActionType.FOLD


def test_abbreviations_work() -> None:
    assert drive(["c", K.KEY_ENTER])[0].type is ActionType.CALL
    assert drive(list("call") + [K.KEY_ENTER])[0].type is ActionType.CALL


def test_a_raise_carries_its_amount() -> None:
    action, _ = drive(list("raise 50") + [K.KEY_ENTER])
    assert action.type is ActionType.RAISE
    assert action.to_amount == 50


def test_pot_relative_sizing() -> None:
    action, _ = drive(list("r pot") + [K.KEY_ENTER])
    assert action.to_amount == 46  # 11 + (24 + 11)


def test_check_is_accepted_when_it_is_legal() -> None:
    action, _ = drive(list("check") + [K.KEY_ENTER], legal=CHECKED_TO, pot=10,
                      current_bet=0)
    assert action.type is ActionType.CHECK


# --- editing and cancelling ----------------------------------------------

def test_backspace_edits_before_committing() -> None:
    action, _ = drive(list("foldx") + [K.KEY_BACKSPACE, K.KEY_ENTER])
    assert action.type is ActionType.FOLD


def test_escape_cancels_what_was_typed() -> None:
    result, frames = drive(list("raise 200") + [K.KEY_ESC])
    assert result is TIMED_OUT
    assert last_line(frames).text == ""


def test_a_cancelled_line_can_be_retyped() -> None:
    action, _ = drive(
        list("raise 200") + [K.KEY_ESC] + list("fold") + [K.KEY_ENTER]
    )
    assert action.type is ActionType.FOLD


def test_cursor_editing_mid_line() -> None:
    action, _ = drive(
        list("rise 50") + [K.KEY_HOME, K.KEY_RIGHT, "a", K.KEY_ENTER]
    )
    assert action.type is ActionType.RAISE
    assert action.to_amount == 50


# --- rejection keeps you editing -----------------------------------------

def test_an_illegal_action_is_refused_and_the_line_survives() -> None:
    result, frames = drive(list("check") + [K.KEY_ENTER])
    assert result is TIMED_OUT, "checking into a bet must not be submitted"
    line = last_line(frames)
    assert line.text == "check"
    assert "cannot check" in line.error


def test_a_below_minimum_raise_is_refused() -> None:
    result, frames = drive(list("raise 5") + [K.KEY_ENTER])
    assert result is TIMED_OUT
    assert "minimum" in last_line(frames).error


def test_gibberish_is_refused_with_a_reason() -> None:
    result, frames = drive(list("xyzzy") + [K.KEY_ENTER])
    assert result is TIMED_OUT
    assert last_line(frames).error


def test_an_empty_line_does_nothing() -> None:
    result, _ = drive([K.KEY_ENTER])
    assert result is TIMED_OUT


def test_a_refused_line_can_be_corrected_and_submitted() -> None:
    # "raise 5" is refused for being under the minimum; typing another digit
    # onto the surviving line makes it "raise 50", which is fine.
    action, _ = drive(
        list("raise 5") + [K.KEY_ENTER] + list("0") + [K.KEY_ENTER]
    )
    assert action.type is ActionType.RAISE
    assert action.to_amount == 50


# --- commands -------------------------------------------------------------

def test_quit_asks_for_confirmation() -> None:
    result, _ = drive(list("quit") + [K.KEY_ENTER, "y"])
    assert result == QUIT


def test_declining_the_quit_confirmation_returns_to_the_prompt() -> None:
    action, _ = drive(list("quit") + [K.KEY_ENTER, "n"] + list("fold")
                      + [K.KEY_ENTER])
    assert action.type is ActionType.FOLD


def test_review_toggle_is_a_command() -> None:
    result, _ = drive(["v", K.KEY_ENTER])
    assert result == TOGGLE_REVIEW


def test_help_shows_the_command_list_without_acting() -> None:
    result, frames = drive(["?", K.KEY_ENTER])
    assert result is TIMED_OUT
    line = last_line(frames)
    assert line.show_help
    assert "fold" in line.hint and "raise" in line.hint


def test_ctrl_c_quits() -> None:
    result, _ = drive([K.KEY_CTRL_C])
    assert result == QUIT


# --- history --------------------------------------------------------------

def test_up_recalls_the_previous_command() -> None:
    async def go():
        reader = KeyReader()
        reader.enabled = True
        prompt = ActionPrompt(reader, lambda **kw: None, flash_seconds=0.0)

        task = asyncio.ensure_future(prompt.ask(FACING_BET, 24, 11))
        await asyncio.sleep(0.01)
        reader.feed(*(list("raise 50") + [K.KEY_ENTER]))
        first = await task

        task = asyncio.ensure_future(prompt.ask(FACING_BET, 24, 11))
        await asyncio.sleep(0.01)
        reader.feed(K.KEY_UP, K.KEY_ENTER)
        second = await task
        return first, second

    first, second = asyncio.run(asyncio.wait_for(go(), 3.0))
    assert first.to_amount == second.to_amount == 50


# --- the hint comes from the rules ---------------------------------------

def test_the_hint_only_offers_legal_actions() -> None:
    hint = hint_for(FACING_BET)
    assert "fold" in hint and "call 11" in hint and "raise" in hint
    assert "check" not in hint

    hint = hint_for(CHECKED_TO)
    assert "check" in hint and "bet" in hint
    assert "call" not in hint
