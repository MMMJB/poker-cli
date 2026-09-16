"""The human action prompt.

A single editable command line, rendered inside the live frame.  The player
types, sees exactly what they typed and what it will do, and presses Enter to
commit.  **Nothing auto-submits**, so no single keypress can fold a hand, and
any half-typed action can be edited or cleared before it takes effect.

``rich.prompt.Prompt`` is unusable here: it blocks the event loop, writes
outside the live region and fights the alternate screen, so the editing is done
by hand against :class:`~poker.ui.keys.KeyReader`.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Callable

from poker.app.commands import (
    QUIT, TOGGLE_REVIEW, Parsed, hint_for, parse,
)
from poker.app.view import action_bar_from
from poker.engine.actions import Action, LegalActions
from poker.ui import keys as K
from poker.ui.keys import KeyReader
from poker.ui.model import ActionBar, InputLine

DRAFT_HINT = "not your turn yet \u2014 preparing your move"
DRAFT_HINT_SHORT = "preparing \u2014 not your turn"

HELP_TEXT = (
    "fold / f    check / k    call / c    bet <amt>    raise <amt>    all-in / a"
    "    ·    amounts: a number, min, max, half, 3/4, pot"
    "    ·    v reviews    q quit"
)


class LineEditor:
    """A single-line text buffer with a cursor.

    Deliberately a plain object rather than a frozen one: it is mutated in
    place by the key loop and snapshotted into the immutable view each frame.
    """

    __slots__ = ("text", "cursor")

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.cursor = len(text)

    def clear(self) -> None:
        self.text = ""
        self.cursor = 0

    def insert(self, chars: str) -> None:
        self.text = self.text[: self.cursor] + chars + self.text[self.cursor:]
        self.cursor += len(chars)

    def backspace(self) -> None:
        if self.cursor:
            self.text = self.text[: self.cursor - 1] + self.text[self.cursor:]
            self.cursor -= 1

    def delete(self) -> None:
        if self.cursor < len(self.text):
            self.text = self.text[: self.cursor] + self.text[self.cursor + 1:]

    def delete_word(self) -> None:
        left = self.text[: self.cursor].rstrip()
        cut = left.rfind(" ") + 1
        self.text = self.text[:cut] + self.text[self.cursor:]
        self.cursor = cut

    def kill_to_end(self) -> None:
        self.text = self.text[: self.cursor]

    def left(self) -> None:
        self.cursor = max(0, self.cursor - 1)

    def right(self) -> None:
        self.cursor = min(len(self.text), self.cursor + 1)

    def home(self) -> None:
        self.cursor = 0

    def end(self) -> None:
        self.cursor = len(self.text)

    def apply(self, key: str) -> bool:
        """Handle an editing key.  Returns True if it was consumed."""
        if key == K.KEY_BACKSPACE:
            self.backspace()
        elif key == K.KEY_DELETE:
            self.delete()
        elif key == K.KEY_LEFT:
            self.left()
        elif key == K.KEY_RIGHT:
            self.right()
        elif key in (K.KEY_HOME, K.KEY_CTRL_A):
            self.home()
        elif key in (K.KEY_END, K.KEY_CTRL_E):
            self.end()
        elif key == K.KEY_CTRL_U:
            self.clear()
        elif key == K.KEY_CTRL_W:
            self.delete_word()
        elif key == K.KEY_CTRL_K:
            self.kill_to_end()
        elif len(key) == 1 and key.isprintable():
            self.insert(key)
        else:
            return False
        return True


class ActionPrompt:
    def __init__(
        self,
        keys: KeyReader,
        publish: Callable[..., None],
        flash_seconds: float = 0.12,
    ) -> None:
        self._keys = keys
        self._publish = publish
        self._flash = flash_seconds
        self._history: list[str] = []
        self._draft = LineEditor()
        """Typed while it is someone else's turn, carried into the next ask()."""

    # ------------------------------------------------------------- drafting

    @property
    def draft_text(self) -> str:
        return self._draft.text

    def clear_draft(self) -> None:
        self._draft.clear()

    def _publish_draft(self, hint: str) -> None:
        self._publish(input_line=InputLine(
            active=True,
            text=self._draft.text,
            cursor=self._draft.cursor,
            hint=hint,
            hint_short=DRAFT_HINT_SHORT,
            draft=True,
        ))

    async def draft_for(self, seconds: float, hint: str = DRAFT_HINT) -> None:
        """Accept keystrokes for a fixed wait, instead of sleeping through it.

        Deliberately *replaces* the sleep rather than running alongside it: two
        coroutines awaiting the key queue at once would split the stream
        between them, and a cancelled `queue.get()` can swallow a keypress.
        There is exactly one reader at any moment.
        """
        if seconds <= 0:
            return
        deadline = asyncio.get_running_loop().time() + seconds
        self._publish_draft(hint)
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            key = await self._keys.key(timeout=min(remaining, 0.05))
            if key is not None and self._draft_key(key):
                self._publish_draft(hint)

    async def draft_until(self, awaitable, hint: str = DRAFT_HINT):
        """Accept keystrokes until ``awaitable`` finishes, then return it."""
        task = asyncio.ensure_future(awaitable)
        self._publish_draft(hint)
        while not task.done():
            key = await self._keys.key(timeout=0.05)
            if key is not None and self._draft_key(key):
                self._publish_draft(hint)
        return await task

    def _draft_key(self, key: str) -> bool:
        """Apply one key to the draft.  Returns True if the line changed."""
        if key == K.KEY_ESC:
            if not self._draft.text:
                return False
            self._draft.clear()
            return True
        if key == K.KEY_ENTER:
            # Cannot be submitted out of turn; leave it staged instead.
            return False
        return self._draft.apply(key)

    async def ask(
        self, legal: LegalActions, pot: int, current_bet: int
    ) -> Action | str:
        """Block until the player commits a line.  Returns an Action or a command."""
        bar = action_bar_from(legal, pot)
        # Whatever was prepared out of turn becomes the live line -- it still
        # has to be committed with Enter. Preparing a move is not making one.
        editor = LineEditor(self._draft.text)
        carried = bool(self._draft.text.strip())
        self._draft.clear()
        hint = hint_for(legal)
        error = ""
        if carried:
            # A move prepared before the action arrived may no longer be legal
            # -- someone raised, or the price moved. Say so straight away
            # rather than waiting for Enter to reject it.
            error = parse(editor.text, legal, pot, current_bet).error
        show_help = False
        history_index = len(self._history)

        while True:
            parsed = parse(editor.text, legal, pot, current_bet)
            self._publish(
                action_bar=bar,
                input_line=InputLine(
                    active=True,
                    text=editor.text,
                    cursor=editor.cursor,
                    preview=parsed.preview,
                    # A live error while typing would flicker on every
                    # keystroke of a longer word, so only show one that was
                    # raised by actually pressing Enter.
                    error=error,
                    hint=HELP_TEXT if show_help else hint,
                    show_help=show_help,
                ),
            )

            key = await self._keys.key()
            if key is None:
                continue

            if key == K.KEY_CTRL_C:
                return QUIT

            if key == K.KEY_ESC:
                if editor.text:
                    editor.clear()       # cancel what was typed
                    error = ""
                else:
                    show_help = False
                continue

            if key == K.KEY_UP:
                if history_index > 0:
                    history_index -= 1
                    editor.text = self._history[history_index]
                    editor.end()
                    error = ""
                continue

            if key == K.KEY_DOWN:
                if history_index < len(self._history) - 1:
                    history_index += 1
                    editor.text = self._history[history_index]
                else:
                    history_index = len(self._history)
                    editor.clear()
                editor.end()
                error = ""
                continue

            if key == K.KEY_ENTER:
                result = await self._submit(editor, parsed, bar)
                if result is _KEEP_EDITING:
                    error = parsed.error
                    if not error and editor.text.strip():
                        error = "type an action, or ? for the list"
                    continue
                if result is _SHOW_HELP:
                    show_help = True
                    editor.clear()
                    error = ""
                    continue
                self._publish(action_bar=ActionBar(), input_line=InputLine())
                return result

            if editor.apply(key):
                error = ""
                history_index = len(self._history)
                continue

    async def _submit(self, editor: LineEditor, parsed: Parsed, bar: ActionBar):
        if not parsed.ok:
            await self._flash_error(bar)
            return _KEEP_EDITING

        if parsed.command == "help":
            return _SHOW_HELP

        if parsed.command == QUIT:
            if await self._confirm_quit(bar):
                self._history.append(editor.text.strip())
                return QUIT
            # Declined: reset the line rather than leaving "quit" sitting in it
            # for the next thing they type to land on the end of.
            editor.clear()
            return _KEEP_EDITING

        self._history.append(editor.text.strip())

        if parsed.command == TOGGLE_REVIEW:
            return TOGGLE_REVIEW

        assert parsed.action is not None
        return parsed.action

    async def _flash_error(self, bar: ActionBar) -> None:
        self._publish(action_bar=replace(bar, error_flash=True))
        await asyncio.sleep(self._flash)
        self._publish(action_bar=bar)

    async def _confirm_quit(self, bar: ActionBar) -> bool:
        self._publish(
            action_bar=replace(bar, message="Quit this session? [y/n]"),
            input_line=InputLine(active=True, text="", cursor=0,
                                 hint="y to quit, anything else to stay"),
        )
        key = await self._keys.key()
        return (key or "").lower() == "y"


class _Sentinel:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return self.name


_KEEP_EDITING = _Sentinel("KEEP_EDITING")
_SHOW_HELP = _Sentinel("SHOW_HELP")
