"""Deterministic local play.

Two jobs:

1. The safety net whenever the API is unavailable, refuses, times out, or
   returns something unusable -- the game must never hang or crash.
2. The entire opponent implementation under ``POKER_OFFLINE=1``, which makes
   the whole app playable and demoable at zero cost.

Two invariants:

* It chooses from ``LegalActions`` directly, so it **cannot** produce an
  illegal action by construction.
* Its RNG is seeded from the hand position, so any fallback decision replays
  exactly from the hand log.

It judges hands with a cheap local strength estimate rather than pot odds
alone.  Without that the policies fold almost every hand preflop -- a call of
one big blind into a small pot always looks bad on price -- which made offline
mode useless as a development harness and unwatchable as a demo.  Nothing here
calls the network.
"""

from __future__ import annotations

import random

from poker.agents.personas import Persona
from poker.engine.actions import Action, LegalActions
from poker.engine.cards import Card, card_rank, card_suit
from poker.engine.evaluator import category_of, evaluate7
from poker.engine.hand import Observation


def decision_rng(hand_id: int, seat: int, street: int, index: int) -> random.Random:
    """Reproducible per-decision randomness."""
    return random.Random((hand_id * 7919 + seat) * 131 + street * 17 + index)


# --------------------------------------------------------------------------
# Local hand strength, 0.0 - 1.0
# --------------------------------------------------------------------------

_CATEGORY_STRENGTH = {
    0: 0.12,   # high card
    1: 0.40,   # one pair (refined below)
    2: 0.62,   # two pair
    3: 0.76,   # trips
    4: 0.83,   # straight
    5: 0.88,   # flush
    6: 0.94,   # full house
    7: 0.98,   # quads
    8: 1.00,   # straight flush
}


def preflop_strength(hole: tuple[Card, ...]) -> float:
    """A Chen-ish score for two cards, normalised to 0-1."""
    if len(hole) < 2:
        return 0.0
    a, b = sorted((card_rank(hole[0]), card_rank(hole[1])), reverse=True)
    suited = card_suit(hole[0]) == card_suit(hole[1])

    if a == b:  # pocket pair
        return _clamp01(0.52 + (a / 12) * 0.46)

    score = 0.14 + ((a * 0.65 + b * 0.35) / 12) * 0.50
    if suited:
        score += 0.08
    gap = a - b
    if gap == 1:
        score += 0.06
    elif gap == 2:
        score += 0.03
    elif gap >= 5:
        score -= 0.06
    if a >= 10 and b >= 10:  # two broadway cards
        score += 0.05
    return _clamp01(score)


def postflop_strength(hole: tuple[Card, ...], board: tuple[Card, ...]) -> float:
    """Made-hand strength, with top pair separated from a weak pair."""
    cards = list(hole) + list(board)
    if len(cards) < 5:
        return preflop_strength(hole)

    value = evaluate7(cards)
    category = category_of(value)
    score = _CATEGORY_STRENGTH.get(category, 0.3)

    if category == 1:  # one pair: where it sits matters enormously
        pair_rank = (value >> 16) & 0xF
        top_board = max(card_rank(c) for c in board)
        if pair_rank > top_board:
            score = 0.58          # overpair
        elif pair_rank == top_board:
            score = 0.50          # top pair
        else:
            score = 0.30          # middle or bottom pair

    if len(board) >= 5 and evaluate7(board) == value:
        score = min(score, 0.20)  # playing the board: everyone has it

    return _clamp01(score)


def hand_strength(obs: Observation) -> float:
    if not obs.board:
        return preflop_strength(obs.hole)
    return postflop_strength(obs.hole, obs.board)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# --------------------------------------------------------------------------
# The policy
# --------------------------------------------------------------------------

def local_decision(
    persona: Persona,
    obs: Observation,
    legal: LegalActions,
    rng: random.Random,
) -> Action:
    """A legal action in the spirit of the persona, with no network call."""
    pol = persona.fallback
    strength = hand_strength(obs)
    pot = max(1, obs.pot)
    to_call = legal.call_cost
    pot_odds = to_call / (pot + to_call) if to_call else 0.0

    # --- nothing to call ---------------------------------------------------
    if legal.can_check:
        wants_value = strength >= pol.bet_strength
        bluffing = rng.random() < pol.bluff_prob
        if legal.can_bet and (wants_value or bluffing):
            target = _clamp(round(pot * pol.bet_pot_frac), legal.min_to, legal.max_to)
            return Action.bet(target)
        return Action.check()

    if not legal.can_call:
        return Action.fold()

    # --- facing a bet ------------------------------------------------------
    if legal.can_raise and strength >= pol.raise_strength:
        if rng.random() < pol.raise_prob:
            target = _clamp(
                legal.min_to + round(pot * 0.35), legal.min_to, legal.max_to
            )
            return Action.raise_to(target)

    # Good prices lower the bar, bad prices raise it.
    needed = pol.call_strength + (pot_odds - 0.25) * pol.odds_sensitivity

    # Never stack off on a marginal hand just because the price looked fine.
    if to_call >= legal.stack * pol.commit_stack_frac and strength < pol.commit_strength:
        return Action.fold()

    if strength >= needed:
        return Action.call()
    return Action.fold()


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(int(value), high))


def local_talk(persona: Persona, rng: random.Random) -> str:
    """Occasional flavour so offline play is not silent.

    Deliberately generic: nothing here may hint at the persona's archetype.
    """
    if rng.random() >= persona.talk_rate:
        return ""
    return rng.choice(_TALK.get(persona.key, ("",)))


_TALK: dict[str, tuple[str, ...]] = {
    "deb": ("I had a feeling.", "You always have it.", "Oh, why not.",
            "I can never fold this."),
    "walter": ("That's how you go broke.", "Nice hand, I guess.",
               "Take it.", "Not today."),
    "tank": ("Ship it.", "You got it?", "I'm never folding tonight.",
             "Let's gamble.", "Here we go."),
    "raj": ("Nice hand.", "I had to look you up.", "Well played."),
    "sofia": ("You don't look happy about that.", "Interesting.", "Hmm."),
}
