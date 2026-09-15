"""The human action prompt.

A small state machine rendered *inside* the live frame.  Built entirely from
the engine's ``LegalActions``, which makes an illegal human action
unrepresentable: a key that is not offered simply does nothing.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Callable

from poker.app.view import action_bar_from, pot_fractions
from poker.engine.actions import Action, LegalActions
from poker.ui.keys import KEY_BACKSPACE, KEY_ENTER, KEY_ESC, KeyReader
from poker.ui.model import ActionBar, RaisePrompt

QUIT = "__quit__"
TOGGLE_REVIEW = "__toggle_review__"


class ActionPrompt:
    """Drives the two-level prompt: top level, then raise sizing."""

    def __init__(
        self,
        keys: KeyReader,
        publish: Callable[..., None],
        flash_seconds: float = 0.12,
    ) -> None:
        self._keys = keys
        self._publish = publish
        self._flash = flash_seconds

    async def ask(
        self, legal: LegalActions, pot: int, current_bet: int
    ) -> Action | str:
        """Block until the human commits.  Returns an Action, or ``QUIT``."""
        bar = action_bar_from(legal, pot)
        half, three_q, full = pot_fractions(legal, pot, current_bet)
        self._keys.drain()

        while True:
            self._publish(action_bar=bar, raise_prompt=RaisePrompt())
            key = (await self._keys.key()) or ""
            key = key.lower()

            if key == "q":
                if await self._confirm_quit(bar):
                    return QUIT
                continue

            if key == "v":
                return TOGGLE_REVIEW

            if key == "f" and legal.can_fold:
                return Action.fold()
            if key in ("k", " ") and legal.can_check:
                return Action.check()
            if key == "c" and legal.can_call:
                return Action.call()
            if key == "a" and (legal.can_bet or legal.can_raise):
                return _aggress(legal, legal.max_to)
            if key == "r" and (legal.can_bet or legal.can_raise):
                chosen = await self._ask_amount(bar, legal, pot, half, three_q, full)
                if chosen is None:
                    continue
                return _aggress(legal, chosen)

            await self._flash_error(bar)

    # ------------------------------------------------------------ sub-prompt

    async def _ask_amount(
        self,
        bar: ActionBar,
        legal: LegalActions,
        pot: int,
        half: int,
        three_q: int,
        full: int,
    ) -> int | None:
        typed = ""
        error = ""

        while True:
            prompt = RaisePrompt(
                active=True,
                typed=typed,
                min_to=legal.min_to,
                max_to=legal.max_to,
                half_pot=half,
                three_quarter_pot=three_q,
                pot_size=full,
                error=error,
            )
            self._publish(action_bar=bar, raise_prompt=prompt)
            key = (await self._keys.key()) or ""

            if key == KEY_ESC:
                self._publish(action_bar=bar, raise_prompt=RaisePrompt())
                return None
            if key == KEY_ENTER:
                value = prompt.value
                if value is None:
                    error = "enter an amount"
                    continue
                if value < legal.min_to:
                    error = f"below the minimum raise of {legal.min_to}"
                    continue
                if value > legal.max_to:
                    # Treat an overshoot as the shove the player clearly meant.
                    return legal.max_to
                self._publish(action_bar=bar, raise_prompt=RaisePrompt())
                return value
            if key == KEY_BACKSPACE:
                typed = typed[:-1]
                error = ""
                continue
            if key.isdigit():
                if len(typed) < 7:
                    typed += key
                error = ""
                continue

            # Letter shortcuts, not digits: 1/2/3 would be swallowed as typed
            # amounts and the shortcut would never fire.
            lowered = key.lower()
            if lowered == "h":
                typed = str(half)
                error = ""
            elif lowered == "t":
                typed = str(three_q)
                error = ""
            elif lowered == "p":
                typed = str(full)
                error = ""
            elif lowered == "a":
                self._publish(action_bar=bar, raise_prompt=RaisePrompt())
                return legal.max_to
            else:
                error = ""

    # ---------------------------------------------------------------- extras

    async def _flash_error(self, bar: ActionBar) -> None:
        self._publish(action_bar=replace(bar, error_flash=True))
        await asyncio.sleep(self._flash)
        self._publish(action_bar=bar)

    async def _confirm_quit(self, bar: ActionBar) -> bool:
        self._publish(
            action_bar=replace(bar, message="Quit this session? [y/n]"),
            raise_prompt=RaisePrompt(),
        )
        key = (await self._keys.key()) or ""
        return key.lower() == "y"


def _aggress(legal: LegalActions, to_amount: int) -> Action:
    """BET when no bet is outstanding, RAISE otherwise -- the engine is strict."""
    return Action.bet(to_amount) if legal.can_bet else Action.raise_to(to_amount)
