"""Parsing what the player types into an action.

Nothing here auto-submits.  The player types, sees exactly what they typed, and
presses Enter -- so a stray keypress can never fold a hand.  Everything is
parsed against the engine's ``LegalActions``, so a command the rules forbid is
rejected with a reason instead of being sent.
"""

from __future__ import annotations

from dataclasses import dataclass

from poker.engine.actions import Action, LegalActions

QUIT = "__quit__"
TOGGLE_REVIEW = "__toggle_review__"

FOLD_WORDS = {"f", "fold"}
CHECK_WORDS = {"k", "x", "check"}
CALL_WORDS = {"c", "call"}
BET_WORDS = {"b", "bet"}
RAISE_WORDS = {"r", "raise"}
ALLIN_WORDS = {"a", "allin", "all-in", "shove", "jam", "ship"}
QUIT_WORDS = {"q", "quit", "exit"}
REVIEW_WORDS = {"v", "review", "reviews"}
HELP_WORDS = {"?", "h", "help"}

# Pot-relative sizes, usable wherever an amount is.
FRACTIONS = {
    "half": 0.5, "h": 0.5, "1/2": 0.5, "½": 0.5,
    "3/4": 0.75, "tq": 0.75, "¾": 0.75, "three-quarter": 0.75,
    "pot": 1.0, "p": 1.0,
    "over": 1.25, "overbet": 1.25,
}


@dataclass(frozen=True, slots=True)
class Parsed:
    """The outcome of reading one line of input."""

    action: Action | None = None
    command: str | None = None
    """QUIT, TOGGLE_REVIEW, or "help"."""
    preview: str = ""
    """What pressing Enter will do, shown live as the player types."""
    error: str = ""
    """Why it will not be accepted."""

    @property
    def ok(self) -> bool:
        return (self.action is not None or self.command is not None) and not self.error


def raise_to_for_fraction(
    legal: LegalActions, pot: int, current_bet: int, fraction: float
) -> int:
    """A pot-relative raise total, clamped into the legal band.

    Sized the way players size: match the bet first, then bet a fraction of the
    resulting pot.
    """
    after_call = pot + legal.call_cost
    target = current_bet + round(after_call * fraction)
    return max(legal.min_to, min(int(target), legal.max_to))


def _money(n: int) -> str:
    return f"${n:,}"


def parse(
    text: str, legal: LegalActions, pot: int, current_bet: int
) -> Parsed:
    """Read one typed line.  Never raises; always explains itself."""
    raw = text.strip()
    if not raw:
        return Parsed(preview="")

    parts = raw.lower().split()
    word = parts[0]
    rest = parts[1:]

    # "raise to 50" reads naturally; drop the filler word.
    if rest and rest[0] == "to":
        rest = rest[1:]

    if word in HELP_WORDS:
        return Parsed(command="help", preview="show the command list")

    if word in QUIT_WORDS:
        return Parsed(command=QUIT, preview="quit the session")

    if word in REVIEW_WORDS:
        return Parsed(command=TOGGLE_REVIEW, preview="toggle hand reviews")

    if word in FOLD_WORDS:
        if not legal.can_fold:
            return Parsed(error="you cannot fold here")
        if legal.can_check:
            return Parsed(action=Action.fold(),
                          preview="fold -- checking is free, are you sure?")
        return Parsed(action=Action.fold(), preview="fold")

    if word in CHECK_WORDS:
        if not legal.can_check:
            return Parsed(error=f"you cannot check, it is {_money(legal.call_cost)} to call")
        return Parsed(action=Action.check(), preview="check")

    if word in CALL_WORDS:
        if not legal.can_call:
            if legal.can_check:
                return Parsed(error="nothing to call -- type check")
            return Parsed(error="there is nothing to call")
        suffix = " (all-in)" if legal.call_is_all_in else ""
        return Parsed(action=Action.call(),
                      preview=f"call {_money(legal.call_cost)}{suffix}")

    if word in ALLIN_WORDS:
        if legal.can_bet or legal.can_raise:
            return Parsed(action=_aggress(legal, legal.max_to),
                          preview=f"all-in for {_money(legal.max_to)}")
        if legal.can_call:
            return Parsed(action=Action.call(),
                          preview=f"call {_money(legal.call_cost)} (all-in)")
        return Parsed(error="you have nothing to put in")

    if word in BET_WORDS or word in RAISE_WORDS:
        return _parse_aggression(word, rest, legal, pot, current_bet)

    # A bare size, e.g. "pot" or "1/2" or just "60".
    amount = _amount(word, legal, pot, current_bet)
    if amount is not None:
        if not (legal.can_bet or legal.can_raise):
            return Parsed(error="you cannot bet or raise here")
        return _aggression_result(legal, amount)

    return Parsed(error=f"{word!r} is not a command -- type ? for the list")


def _parse_aggression(
    word: str, rest: list[str], legal: LegalActions, pot: int, current_bet: int
) -> Parsed:
    verb = "bet" if word in BET_WORDS else "raise"
    if not (legal.can_bet or legal.can_raise):
        if legal.can_call:
            return Parsed(error="you cannot raise -- only fold or call")
        return Parsed(error=f"you cannot {verb} here")

    if not rest:
        return Parsed(error=f"{verb} needs an amount, e.g. "
                            f"'{verb} {legal.min_to}' or '{verb} pot'")

    amount = _amount(rest[0], legal, pot, current_bet)
    if amount is None:
        return Parsed(error=f"{rest[0]!r} is not an amount")
    return _aggression_result(legal, amount)


def _aggression_result(legal: LegalActions, amount: int) -> Parsed:
    verb = "bet" if legal.can_bet else "raise to"
    if amount < legal.min_to:
        return Parsed(error=f"minimum is {_money(legal.min_to)}")
    if amount > legal.max_to:
        # Asking for more than the stack plainly means "all-in".
        return Parsed(action=_aggress(legal, legal.max_to),
                      preview=f"all-in for {_money(legal.max_to)}")
    if amount == legal.max_to:
        return Parsed(action=_aggress(legal, amount),
                      preview=f"all-in for {_money(amount)}")
    return Parsed(action=_aggress(legal, amount),
                  preview=f"{verb} {_money(amount)}")


def _amount(
    token: str, legal: LegalActions, pot: int, current_bet: int
) -> int | None:
    if token in FRACTIONS:
        return raise_to_for_fraction(legal, pot, current_bet, FRACTIONS[token])
    if token in ("min", "minimum"):
        return legal.min_to
    if token in ("max", "maximum"):
        return legal.max_to
    cleaned = token.lstrip("$").replace(",", "")
    if cleaned.isdigit():
        return int(cleaned)
    return None


def _aggress(legal: LegalActions, to_amount: int) -> Action:
    """BET when no bet is outstanding, RAISE otherwise -- the engine is strict."""
    return Action.bet(to_amount) if legal.can_bet else Action.raise_to(to_amount)


def hint_for(legal: LegalActions) -> str:
    """The one-line reminder of what is available, built from the rules."""
    bits: list[str] = []
    if legal.can_fold:
        bits.append("fold")
    if legal.can_check:
        bits.append("check")
    if legal.can_call:
        bits.append(f"call {legal.call_cost}")
    if legal.can_bet:
        bits.append("bet <amt|half|pot>")
    elif legal.can_raise:
        bits.append("raise <amt|half|pot>")
    if legal.can_bet or legal.can_raise:
        bits.append(f"all-in {legal.max_to}")
    return "  ·  ".join(bits)
