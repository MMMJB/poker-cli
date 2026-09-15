"""Pure poker logic: cards, betting rules, pots, and the hand lifecycle.

No I/O, no async, no LLM.  Everything is integer dollars.
"""

from poker.engine.actions import Action, ActionType, LegalActions
from poker.engine.cards import (
    Card, Deck, card_glyph, card_rank, card_str, card_suit, cards_glyph,
    cards_str, is_red, parse_card, parse_cards,
)
from poker.engine.errors import EngineInvariantError, IllegalAction
from poker.engine.evaluator import (
    best_five, category_of, describe, evaluate7, CATEGORY_NAMES,
)
from poker.engine.events import Event, EventType, HandLog
from poker.engine.hand import HandEngine, HandResult, Observation
from poker.engine.positions import position_name
from poker.engine.pots import Award, AwardReason, Pot
from poker.engine.state import (
    GameConfig, HandState, PlayerState, PlayerStatus, Street,
)
from poker.engine.table import Seat, Table, TopUp

__all__ = [
    "Action", "ActionType", "LegalActions",
    "Card", "Deck", "card_glyph", "card_rank", "card_str", "card_suit",
    "cards_glyph", "cards_str", "is_red", "parse_card", "parse_cards",
    "EngineInvariantError", "IllegalAction",
    "best_five", "category_of", "describe", "evaluate7", "CATEGORY_NAMES",
    "Event", "EventType", "HandLog",
    "HandEngine", "HandResult", "Observation",
    "position_name",
    "Award", "AwardReason", "Pot",
    "GameConfig", "HandState", "PlayerState", "PlayerStatus", "Street",
    "Seat", "Table", "TopUp",
]
