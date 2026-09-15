"""Card representation, deck, and rendering helpers.

A card is a plain ``int`` in ``0..51``:

    rank = card >> 2     # 0 = Deuce, 12 = Ace
    suit = card & 3      # 0 = spades, 1 = hearts, 2 = diamonds, 3 = clubs

An int (rather than a class or a tuple) keeps comparison and sorting free and
makes the rank histograms in :mod:`poker.engine.evaluator` trivial.  The
evaluator runs often enough that this matters.

Use :func:`card_str` (``"Ah"``) in logs and LLM prompts; :func:`card_glyph`
(``"A♥"``) is for the renderer only.
"""

from __future__ import annotations

import random
from typing import Iterable, Sequence

Card = int

RANKS = "23456789TJQKA"
SUITS = "shdc"
SUIT_GLYPHS = ("♠", "♥", "♦", "♣")  # spade heart diamond club
RED_SUITS = frozenset({1, 2})  # hearts, diamonds

DECK_SIZE = 52

RANK_NAMES = (
    "Deuce", "Three", "Four", "Five", "Six", "Seven", "Eight",
    "Nine", "Ten", "Jack", "Queen", "King", "Ace",
)
RANK_PLURALS = (
    "Deuces", "Threes", "Fours", "Fives", "Sixes", "Sevens", "Eights",
    "Nines", "Tens", "Jacks", "Queens", "Kings", "Aces",
)

_RANK_INDEX = {ch: i for i, ch in enumerate(RANKS)}
_SUIT_INDEX = {ch: i for i, ch in enumerate(SUITS)}


def make_card(rank: int, suit: int) -> Card:
    """Build a card from a rank index (0-12) and suit index (0-3)."""
    if not 0 <= rank < 13:
        raise ValueError(f"rank out of range: {rank}")
    if not 0 <= suit < 4:
        raise ValueError(f"suit out of range: {suit}")
    return (rank << 2) | suit


def card_rank(c: Card) -> int:
    return c >> 2


def card_suit(c: Card) -> int:
    return c & 3


def card_str(c: Card) -> str:
    """ASCII form, e.g. ``"Ah"``.  Used in logs and LLM prompts."""
    return RANKS[c >> 2] + SUITS[c & 3]


def card_glyph(c: Card) -> str:
    """Unicode form, e.g. ``"A♥"``.  Render-time only."""
    return RANKS[c >> 2] + SUIT_GLYPHS[c & 3]


def parse_card(s: str) -> Card:
    """Parse ``"Ah"`` / ``"ah"`` / ``"AH"`` into a card int."""
    s = s.strip()
    if len(s) != 2:
        raise ValueError(f"not a card: {s!r}")
    rank_ch, suit_ch = s[0].upper(), s[1].lower()
    if rank_ch not in _RANK_INDEX or suit_ch not in _SUIT_INDEX:
        raise ValueError(f"not a card: {s!r}")
    return make_card(_RANK_INDEX[rank_ch], _SUIT_INDEX[suit_ch])


def parse_cards(s: str) -> list[Card]:
    """Parse a whitespace-separated run of cards: ``"Ah Kd 7c"``."""
    return [parse_card(tok) for tok in s.split()]


def cards_str(cs: Iterable[Card]) -> str:
    return " ".join(card_str(c) for c in cs)


def cards_glyph(cs: Iterable[Card]) -> str:
    return " ".join(card_glyph(c) for c in cs)


def is_red(c: Card) -> bool:
    return (c & 3) in RED_SUITS


class Deck:
    """A shuffled 52-card deck.

    Deals from the end of the list so dealing is an O(1) pop.  The RNG is
    injected so a hand can be replayed exactly from its recorded seed.
    """

    __slots__ = ("_cards",)

    def __init__(self, rng: random.Random) -> None:
        self._cards: list[Card] = list(range(DECK_SIZE))
        rng.shuffle(self._cards)

    def deal(self, n: int = 1) -> list[Card]:
        if n > len(self._cards):
            raise ValueError(f"deck exhausted: wanted {n}, have {len(self._cards)}")
        out = self._cards[-n:]
        del self._cards[-n:]
        out.reverse()  # deal in pop order
        return out

    def deal_one(self) -> Card:
        if not self._cards:
            raise ValueError("deck exhausted")
        return self._cards.pop()

    def remaining(self) -> int:
        return len(self._cards)

    def remove(self, cards: Sequence[Card]) -> None:
        """Remove specific cards (used to stack a deck in tests)."""
        for c in cards:
            self._cards.remove(c)
