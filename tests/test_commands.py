"""Parsing typed input into actions, always against the engine's rules."""

from __future__ import annotations

import pytest

from poker.app.commands import (
    QUIT, TOGGLE_REVIEW, parse, raise_to_for_fraction,
)
from poker.engine.actions import ActionType, LegalActions

FACING = LegalActions(
    seat=0, can_fold=True, can_check=False, can_call=True, call_cost=11,
    call_is_all_in=False, can_bet=False, can_raise=True, min_to=22,
    max_to=297, stack=297,
)
OPEN = LegalActions(
    seat=0, can_fold=True, can_check=True, can_call=False, call_cost=0,
    call_is_all_in=False, can_bet=True, can_raise=False, min_to=3,
    max_to=300, stack=300,
)
CAPPED = LegalActions(  # facing an all-in: only fold or call
    seat=0, can_fold=True, can_check=False, can_call=True, call_cost=200,
    call_is_all_in=True, can_bet=False, can_raise=False, min_to=0,
    max_to=200, stack=200,
)


def p(text, legal=FACING, pot=24, current_bet=11):
    return parse(text, legal, pot, current_bet)


@pytest.mark.parametrize("text", ["f", "F", "fold", "  fold  ", "FOLD"])
def test_fold_spellings(text: str) -> None:
    assert p(text).action.type is ActionType.FOLD


@pytest.mark.parametrize("text", ["c", "call"])
def test_call_spellings(text: str) -> None:
    assert p(text).action.type is ActionType.CALL


@pytest.mark.parametrize("text", ["k", "x", "check"])
def test_check_spellings(text: str) -> None:
    assert p(text, legal=OPEN, pot=10, current_bet=0).action.type is ActionType.CHECK


@pytest.mark.parametrize("text", ["a", "allin", "all-in", "shove", "jam", "ship"])
def test_all_in_spellings(text: str) -> None:
    parsed = p(text)
    assert parsed.action.to_amount == FACING.max_to
    assert "all-in" in parsed.preview


@pytest.mark.parametrize("text,amount", [
    ("raise 50", 50), ("r 50", 50), ("raise to 50", 50),
    ("R 50", 50), ("raise $50", 50), ("raise 1,00", 100),
])
def test_raise_amounts(text: str, amount: int) -> None:
    action = p(text).action
    assert action.type is ActionType.RAISE
    assert action.to_amount == amount


def test_a_bare_number_is_a_raise() -> None:
    assert p("60").action.to_amount == 60


def test_pot_relative_sizes() -> None:
    assert p("r half").action.to_amount == raise_to_for_fraction(FACING, 24, 11, 0.5)
    assert p("r 3/4").action.to_amount == raise_to_for_fraction(FACING, 24, 11, 0.75)
    assert p("r pot").action.to_amount == raise_to_for_fraction(FACING, 24, 11, 1.0)
    assert p("pot").action.to_amount == p("r pot").action.to_amount


def test_min_and_max() -> None:
    assert p("r min").action.to_amount == FACING.min_to
    assert p("r max").action.to_amount == FACING.max_to


def test_an_overshoot_becomes_all_in() -> None:
    parsed = p("raise 99999")
    assert parsed.action.to_amount == FACING.max_to
    assert "all-in" in parsed.preview


def test_bet_is_used_when_no_bet_is_outstanding() -> None:
    action = p("bet 20", legal=OPEN, pot=10, current_bet=0).action
    assert action.type is ActionType.BET


def test_raise_word_still_works_when_betting() -> None:
    """The engine is strict about BET vs RAISE; the player should not have to be."""
    action = p("raise 20", legal=OPEN, pot=10, current_bet=0).action
    assert action.type is ActionType.BET


def test_bet_word_still_works_when_raising() -> None:
    action = p("bet 50").action
    assert action.type is ActionType.RAISE


# --- refusals -------------------------------------------------------------

def test_check_facing_a_bet_is_refused() -> None:
    parsed = p("check")
    assert parsed.action is None
    assert "cannot check" in parsed.error


def test_call_with_nothing_to_call_is_refused() -> None:
    parsed = p("call", legal=OPEN, pot=10, current_bet=0)
    assert parsed.action is None
    assert "check" in parsed.error


def test_below_minimum_is_refused() -> None:
    parsed = p("raise 5")
    assert parsed.action is None
    assert "$22" in parsed.error


def test_raise_without_an_amount_is_refused_with_an_example() -> None:
    parsed = p("raise")
    assert parsed.action is None
    assert "22" in parsed.error


def test_raising_when_only_fold_or_call_is_possible() -> None:
    parsed = p("raise 400", legal=CAPPED, pot=300, current_bet=200)
    assert parsed.action is None
    assert "only fold or call" in parsed.error


def test_all_in_facing_an_all_in_becomes_a_call() -> None:
    parsed = p("all-in", legal=CAPPED, pot=300, current_bet=200)
    assert parsed.action.type is ActionType.CALL


def test_unknown_words_are_refused() -> None:
    parsed = p("wibble")
    assert parsed.action is None
    assert "not a command" in parsed.error


def test_a_non_numeric_amount_is_refused() -> None:
    parsed = p("raise banana")
    assert parsed.action is None
    assert "not an amount" in parsed.error


def test_empty_input_is_neither_ok_nor_an_error() -> None:
    parsed = p("")
    assert not parsed.ok
    assert not parsed.error


# --- commands -------------------------------------------------------------

def test_commands() -> None:
    assert p("q").command == QUIT
    assert p("quit").command == QUIT
    assert p("v").command == TOGGLE_REVIEW
    assert p("?").command == "help"


def test_folding_for_free_warns_in_the_preview() -> None:
    parsed = p("fold", legal=OPEN, pot=10, current_bet=0)
    assert parsed.action.type is ActionType.FOLD
    assert "free" in parsed.preview


# --- sizing maths ---------------------------------------------------------

def test_fraction_sizing_is_clamped_into_the_legal_band() -> None:
    tiny = LegalActions(
        seat=0, can_fold=True, can_check=False, can_call=True, call_cost=5,
        call_is_all_in=False, can_bet=False, can_raise=True, min_to=10,
        max_to=12, stack=12,
    )
    for frac in (0.5, 0.75, 1.0, 1.25):
        value = raise_to_for_fraction(tiny, 1000, 5, frac)
        assert tiny.min_to <= value <= tiny.max_to


def test_fraction_sizing_matches_the_stated_formula() -> None:
    # current_bet + fraction x (pot + call)
    assert raise_to_for_fraction(FACING, 24, 11, 1.0) == 11 + (24 + 11)
    assert raise_to_for_fraction(FACING, 24, 11, 0.5) == 11 + round((24 + 11) * 0.5)
