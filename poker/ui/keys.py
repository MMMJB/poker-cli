"""Non-blocking keyboard input inside a ``rich.Live`` region.

``rich.prompt.Prompt`` blocks the event loop, writes outside the live region,
and fights the alternate screen, so input is read directly from the tty in
cbreak mode and fed to the loop as an async stream.

Terminal state is restored in ``__aexit__`` -- including on an exception, which
is the case that otherwise leaves a user's shell unusable.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import termios
import tty

KEY_ESC = "escape"
KEY_ENTER = "enter"
KEY_BACKSPACE = "backspace"
KEY_DELETE = "delete"
KEY_LEFT = "left"
KEY_RIGHT = "right"
KEY_UP = "up"
KEY_DOWN = "down"
KEY_HOME = "home"
KEY_END = "end"
KEY_CTRL_C = "ctrl-c"
KEY_CTRL_U = "ctrl-u"
KEY_CTRL_W = "ctrl-w"
KEY_CTRL_A = "ctrl-a"
KEY_CTRL_E = "ctrl-e"
KEY_CTRL_K = "ctrl-k"

# Single control bytes that carry a name rather than inserting a character.
_CONTROL = {
    "\r": KEY_ENTER,
    "\n": KEY_ENTER,
    "\x7f": KEY_BACKSPACE,
    "\x08": KEY_BACKSPACE,
    "\x03": KEY_CTRL_C,
    "\x01": KEY_CTRL_A,
    "\x05": KEY_CTRL_E,
    "\x0b": KEY_CTRL_K,
    "\x15": KEY_CTRL_U,
    "\x17": KEY_CTRL_W,
}

# Final byte of a CSI sequence (ESC [ ...) -> key name.
_CSI_FINAL = {
    "A": KEY_UP, "B": KEY_DOWN, "C": KEY_RIGHT, "D": KEY_LEFT,
    "H": KEY_HOME, "F": KEY_END,
}

# CSI sequences of the form ESC [ <number> ~
_CSI_TILDE = {
    "1": KEY_HOME, "3": KEY_DELETE, "4": KEY_END,
    "7": KEY_HOME, "8": KEY_END,
}


class KeyReader:
    """Async iterator over single keypresses."""

    def __init__(self, stream=None) -> None:
        self._stream = stream or sys.stdin
        self._fd: int | None = None
        self._saved = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._attached = False
        self._pending = ""
        self.enabled = False

    async def __aenter__(self) -> "KeyReader":
        self._loop = asyncio.get_running_loop()
        try:
            self._fd = self._stream.fileno()
            if not os.isatty(self._fd):
                return self  # piped input, e.g. tests: stay disabled
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self._loop.add_reader(self._fd, self._on_readable)
            self._attached = True
            self.enabled = True
        except (OSError, termios.error, ValueError):
            self.enabled = False
        return self

    async def __aexit__(self, *exc) -> None:
        self.restore()

    def restore(self) -> None:
        """Idempotent: safe to call from a signal handler or a finally block."""
        if self._attached and self._loop is not None and self._fd is not None:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(self._fd)
            self._attached = False
        if self._saved is not None and self._fd is not None:
            with contextlib.suppress(Exception):
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            self._saved = None
        self.enabled = False

    def _on_readable(self) -> None:
        try:
            data = os.read(self._fd, 1024)  # type: ignore[arg-type]
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            self.restore()
            return
        for key in self._decode(data.decode("utf-8", errors="ignore")):
            self._queue.put_nowait(key)

    def _decode(self, text: str) -> list[str]:
        """Turn raw bytes into key names, one per keypress.

        Arrow and navigation keys arrive as multi-byte escape sequences, so
        they have to be reassembled here rather than surfaced as a stray ESC
        followed by letters -- otherwise pressing Left would type a "D".
        """
        buf = self._pending + text
        self._pending = ""
        out: list[str] = []
        i = 0

        while i < len(buf):
            ch = buf[i]

            if ch != "\x1b":
                out.append(_CONTROL.get(ch, ch))
                i += 1
                continue

            # An escape sequence, or a bare Escape keypress.
            rest = buf[i + 1:]
            if not rest:
                # Nothing follows in this chunk: treat it as the Escape key.
                # A real sequence arrives in a single read, so this is safe.
                out.append(KEY_ESC)
                i += 1
                continue

            if rest[0] not in ("[", "O"):
                out.append(KEY_ESC)
                i += 1
                continue

            j = i + 2
            params = ""
            while j < len(buf) and (buf[j].isdigit() or buf[j] == ";"):
                params += buf[j]
                j += 1
            if j >= len(buf):
                self._pending = buf[i:]  # incomplete; wait for the rest
                return out

            final = buf[j]
            if final == "~":
                out.append(_CSI_TILDE.get(params.split(";")[0], ""))
            else:
                out.append(_CSI_FINAL.get(final, ""))
            i = j + 1

        return [k for k in out if k]

    async def key(self, timeout: float | None = None) -> str | None:
        """Next keypress, or None if ``timeout`` elapses first."""
        if not self.enabled:
            if timeout is None:
                await asyncio.sleep(3600)
                return None
            await asyncio.sleep(timeout)
            return None
        if timeout is None:
            return await self._queue.get()
        try:
            return await asyncio.wait_for(self._queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    def drain(self) -> None:
        while not self._queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()

    def feed(self, *keys: str) -> None:
        """Inject key names -- used by tests and the scripted demo.

        Each argument is one keypress, so multi-character names like "enter"
        stay a single key.
        """
        for key in keys:
            self._queue.put_nowait(key)
        self.enabled = True

    def feed_text(self, text: str) -> None:
        """Inject each character of ``text`` as its own keypress."""
        self.feed(*text)
