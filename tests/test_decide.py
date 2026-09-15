"""Coercion: shape is guaranteed by the schema, legality is not."""

from __future__ import annotations

import random


from poker.agents.decide import Verdict, clean_talk, coerce, forced_action
from poker.agents.personas import BY_KEY, leak_denylist
from poker.agents.schema import RawDecision
from poker.engine.actions import ActionType, LegalActions


def raw(action: str, amount: int = 0, talk: str = "") -> RawDecision:
    return RawDecision(action=action, amount=amount, table_talk=talk)


def facing_bet(to_call: int = 20, min_to: int = 40, max_to: int = 300) -> LegalActions:
    return LegalActions(
        seat=3, can_fold=True, can_check=False, can_call=True, call_cost=to_call,
        call_is_all_in=False, can_bet=False, can_raise=True, min_to=min_to,
        max_to=max_to, stack=max_to,
    )


def checked_to(min_to: int = 3, max_to: int = 300) -> LegalActions:
    return LegalActions(
        seat=3, can_fold=True, can_check=True, can_call=False, call_cost=0,
        call_is_all_in=False, can_bet=True, can_raise=False, min_to=min_to,
        max_to=max_to, stack=max_to,
    )


def facing_all_in(to_call: int = 200) -> LegalActions:
    """Nothing to raise with -- calling puts you all-in."""
    return LegalActions(
        seat=3, can_fold=True, can_check=False, can_call=True, call_cost=to_call,
        call_is_all_in=True, can_bet=False, can_raise=False, min_to=0,
        max_to=to_call, stack=to_call,
    )


# --- clean passthroughs ---------------------------------------------------

def test_legal_actions_pass_through_untouched() -> None:
    action, verdict, _ = coerce(raw("fold"), facing_bet())
    assert action.type is ActionType.FOLD and verdict is Verdict.OK

    action, verdict, _ = coerce(raw("call"), facing_bet())
    assert action.type is ActionType.CALL and verdict is Verdict.OK

    action, verdict, _ = coerce(raw("check"), checked_to())
    assert action.type is ActionType.CHECK and verdict is Verdict.OK

    action, verdict, _ = coerce(raw("bet", 30), checked_to())
    assert action.type is ActionType.BET and action.to_amount == 30
    assert verdict is Verdict.OK


# --- monotone corrections -------------------------------------------------

def test_fold_with_a_free_check_becomes_a_check() -> None:
    action, verdict, note = coerce(raw("fold"), checked_to())
    assert action.type is ActionType.CHECK
    assert verdict is Verdict.COERCED
    assert "free check" in note


def test_call_with_nothing_to_call_becomes_a_check() -> None:
    action, verdict, _ = coerce(raw("call"), checked_to())
    assert action.type is ActionType.CHECK
    assert verdict is Verdict.COERCED


def test_bet_facing_a_bet_becomes_a_raise() -> None:
    action, verdict, _ = coerce(raw("bet", 60), facing_bet())
    assert action.type is ActionType.RAISE
    assert action.to_amount == 60
    assert verdict is Verdict.COERCED


def test_raise_below_the_minimum_is_clamped_up() -> None:
    action, verdict, note = coerce(raw("raise", 25), facing_bet(min_to=40))
    assert action.to_amount == 40
    assert verdict is Verdict.COERCED
    assert "below min" in note


def test_raise_above_the_maximum_is_clamped_to_all_in() -> None:
    action, verdict, note = coerce(raw("raise", 5000), facing_bet(max_to=300))
    assert action.to_amount == 300
    assert verdict is Verdict.COERCED
    assert "above max" in note


def test_all_in_ignores_a_wrong_amount() -> None:
    action, verdict, _ = coerce(raw("all_in", 77), facing_bet(max_to=300))
    assert action.to_amount == 300
    assert verdict is Verdict.COERCED


def test_all_in_with_the_right_amount_is_clean() -> None:
    action, verdict, _ = coerce(raw("all_in", 300), facing_bet(max_to=300))
    assert action.to_amount == 300
    assert verdict is Verdict.OK


def test_raise_when_only_calling_is_possible_becomes_a_call() -> None:
    action, verdict, _ = coerce(raw("raise", 400), facing_all_in())
    assert action.type is ActionType.CALL
    assert verdict is Verdict.COERCED


def test_all_in_facing_an_all_in_becomes_a_call() -> None:
    action, verdict, _ = coerce(raw("all_in", 200), facing_all_in())
    assert action.type is ActionType.CALL
    assert verdict is Verdict.COERCED


# --- contradictions must be re-asked, never guessed at --------------------

def test_check_facing_a_bet_is_re_asked() -> None:
    action, verdict, note = coerce(raw("check"), facing_bet())
    assert action is None
    assert verdict is Verdict.MUST_REASK
    assert "facing a bet" in note


def test_unknown_action_is_re_asked() -> None:
    action, verdict, _ = coerce(raw("shove_it"), facing_bet())
    assert action is None
    assert verdict is Verdict.MUST_REASK


def test_coercion_never_returns_an_illegal_amount() -> None:
    rng = random.Random(4)
    legal = facing_bet(to_call=20, min_to=40, max_to=300)
    for _ in range(2000):
        action_name = rng.choice(["fold", "check", "call", "bet", "raise", "all_in"])
        amount = rng.randint(-500, 5000)
        action, verdict, _ = coerce(raw(action_name, amount), legal)
        if action is None:
            continue
        if action.type in (ActionType.BET, ActionType.RAISE):
            assert legal.min_to <= action.to_amount <= legal.max_to


# --- table talk -----------------------------------------------------------

def test_talk_is_truncated_and_rate_limited() -> None:
    persona = BY_KEY["tank"]
    long = "x" * 200
    out = clean_talk(long, persona, _AlwaysTalk(), ())
    assert len(out) <= 71


def test_talk_matching_the_denylist_is_dropped() -> None:
    persona = BY_KEY["tank"]
    deny = tuple(w.lower() for w in leak_denylist())
    assert clean_talk("I'm just a language model", persona, _AlwaysTalk(), deny) == ""
    assert clean_talk("you are such a nit", persona, _AlwaysTalk(), deny) == ""


def test_talk_rate_silences_most_actions() -> None:
    persona = BY_KEY["sofia"]  # talk_rate 0.08
    spoken = sum(
        bool(clean_talk("Hmm.", persona, random.Random(i), ()))
        for i in range(500)
    )
    assert spoken < 100


class _AlwaysTalk(random.Random):
    def random(self) -> float:  # noqa: D102
        return 0.0


# --- forced actions -------------------------------------------------------

def test_forced_action_is_none_when_there_is_a_real_decision() -> None:
    assert forced_action(facing_bet()) is None
    assert forced_action(checked_to()) is None
    assert forced_action(facing_all_in()) is None
