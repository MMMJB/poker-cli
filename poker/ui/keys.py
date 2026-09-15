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

KEY_ESC = "\x1b"
KEY_ENTER = "\r"
KEY_BACKSPACE = "\x7f"
KEY_CTRL_C = "\x03"


class KeyReader:
    """Async iterator over single keypresses."""

    def __init__(self, stream=None) -> None:
        self._stream = stream or sys.stdin
        self._fd: int | None = None
        self._saved = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._attached = False
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
            data = os.read(self._fd, 64)  # type: ignore[arg-type]
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            self.restore()
            return
        for ch in data.decode("utf-8", errors="ignore"):
            self._queue.put_nowait(ch)

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

    def feed(self, text: str) -> None:
        """Inject keys -- used by tests and the scripted demo."""
        for ch in text:
            self._queue.put_nowait(ch)
        self.enabled = True
