"""Opponents get exactly what the human sees, and nothing more.

The rule: an agent's prompt may contain its own two hole cards and any card
that has been publicly revealed.  Any other seat's hole cards appearing
anywhere in the prompt is a leak.  Cards are unique within a deck, so a simple
substring scan is a sound test.
"""

from __future__ import annotations

import re


from poker.agents.personas import ALL_PERSONAS
from poker.agents.prompts import build_system_prompt, render_state
from poker.engine.actions import Action
from poker.engine.cards import card_str
from poker.engine.events import EventType
from poker.engine.hand import HandEngine
from poker.engine.state import GameConfig

NAMES = ["Hero", "Deb", "Walter", "Tank", "Raj", "Sofia"]


def engine(seed: int) -> HandEngine:
    return HandEngine(GameConfig(), NAMES, [300] * 6, button=seed % 6,
                      hand_id=seed, seed=seed)


def revealed_seats(e: HandEngine, seat: int) -> set[int]:
    return {
        ev.data["seat"]
        for ev in e.log.view_for(seat)
        if ev.type is EventType.CARDS_REVEALED
    }


# A card token is a rank followed by a suit letter, on word boundaries.  A bare
# substring scan gives false positives -- "Ac" hides inside "Action".
_CARD_TOKEN = re.compile(r"\b([2-9TJQKA][shdc])\b")


def cards_mentioned(text: str) -> set[str]:
    return set(_CARD_TOKEN.findall(text))


def assert_no_leak(e: HandEngine, seat: int) -> None:
    """Every card named in the prompt must be one this seat is entitled to see."""
    obs = e.observation(seat)
    text = render_state(obs)

    allowed_seats = revealed_seats(e, seat) | {seat}
    allowed = {card_str(c) for c in e.state.board}
    for s in allowed_seats:
        allowed |= {card_str(c) for c in e.state.players[s].hole}

    leaked = cards_mentioned(text) - allowed
    assert not leaked, (
        f"seat {seat}'s prompt names cards it may not see: {sorted(leaked)}"
    )


def test_prompts_never_leak_another_seats_hole_cards() -> None:
    for seed in range(120):
        e = engine(seed)
        e.start()
        while not e.is_complete():
            for seat in range(6):
                assert_no_leak(e, seat)
            legal = e.legal_actions()
            e.apply(Action.check() if legal.can_check else Action.call())
        for seat in range(6):
            assert_no_leak(e, seat)


def test_prompts_never_leak_mucked_cards() -> None:
    """A hand that mucks at showdown must stay hidden from every other seat."""
    found = False
    for seed in range(300):
        e = engine(seed)
        e.start()
        while not e.is_complete():
            legal = e.legal_actions()
            e.apply(Action.check() if legal.can_check else Action.call())

        mucked = {
            ev.data["seat"]
            for ev in e.log.all()
            if ev.type is EventType.CARDS_MUCKED
        }
        if not mucked:
            continue
        found = True
        for seat in range(6):
            named = cards_mentioned(render_state(e.observation(seat)))
            for other in mucked:
                if other == seat:
                    continue
                for card in e.state.players[other].hole:
                    assert card_str(card) not in named
        break
    assert found, "no muck occurred in 300 seeded hands"


def test_agent_sees_its_own_cards() -> None:
    e = engine(11)
    e.start()
    for seat in range(6):
        text = render_state(e.observation(seat))
        for card in e.state.players[seat].hole:
            assert card_str(card) in text


def test_revealed_cards_are_visible_to_everyone() -> None:
    """An all-in run-out reveals cards -- that is public, and must be shared."""
    e = HandEngine(GameConfig(), NAMES, [300, 300, 300, 100, 100, 300],
                   button=0, hand_id=1, seed=3)
    e.start()
    e.apply(Action.raise_to(100))
    e.apply(Action.call())
    for _ in range(4):
        e.apply(Action.fold())

    for seat in range(6):
        text = render_state(e.observation(seat))
        for revealed in (3, 4):
            for card in e.state.players[revealed].hole:
                assert card_str(card) in text


def test_system_prompt_is_byte_stable_and_carries_no_hand_state() -> None:
    """A volatile system prompt would invalidate the cached prefix every turn.

    It legitimately *describes* the LEGAL ACTIONS format; what it must not do
    is contain any value from an actual hand.
    """
    for persona in ALL_PERSONAS:
        text = build_system_prompt(persona)
        assert text == build_system_prompt(persona), "not byte-stable"
        assert "HAND #" not in text
        assert "Pot: $" not in text
        assert "Board: " not in text
        assert not cards_mentioned(text), "a concrete card is baked into the prompt"


def test_no_persona_leaks_into_another_personas_prompt() -> None:
    """Each agent knows only its own character."""
    for persona in ALL_PERSONAS:
        text = build_system_prompt(persona)
        for other in ALL_PERSONAS:
            if other.key == persona.key:
                continue
            assert other.profile not in text
            # Word boundaries: "nit" otherwise matches inside "community".
            pattern = re.compile(rf"\b{re.escape(other.archetype)}\b", re.I)
            assert not pattern.search(text), (
                f"{persona.key}'s prompt mentions {other.archetype!r}"
            )


def test_no_derived_statistics_are_handed_to_agents() -> None:
    """No stat HUD: the human was not given one either.

    Guards the information-symmetry rule against a future regression that
    reintroduces a computed tendency digest.
    """
    e = engine(5)
    e.start()
    text = render_state(e.observation(e.current_actor())).lower()
    for term in ("vpip", "pfr", "wtsd", "fold-to-cbet", "fold to cbet",
                 "aggression factor", "%"):
        assert term not in text, f"derived statistic {term!r} leaked into the prompt"


def test_decision_roll_is_present_and_carries_no_game_information() -> None:
    """The roll replaces sampling temperature, which the API no longer accepts.

    It must be a bare number: anything derived from the hand would hand the
    agent information the human does not have.
    """
    e = engine(23)
    e.start()
    seat = e.current_actor()
    obs = e.observation(seat)

    without = render_state(obs)
    assert "Decision roll" not in without

    with_roll = render_state(obs, roll=42)
    assert "Decision roll: 42 (0-99)" in with_roll
    # Adding the roll changes nothing else about the prompt.
    assert with_roll.replace("\n\nDecision roll: 42 (0-99)", "") == without
    # And it introduces no new card.
    assert cards_mentioned(with_roll) == cards_mentioned(without)


def test_every_roll_value_leaves_the_prompt_leak_free() -> None:
    e = engine(31)
    e.start()
    while not e.is_complete():
        for seat in range(6):
            obs = e.observation(seat)
            allowed = {card_str(c) for c in e.state.board}
            for s in revealed_seats(e, seat) | {seat}:
                allowed |= {card_str(c) for c in e.state.players[s].hole}
            for roll in (0, 50, 99):
                named = cards_mentioned(render_state(obs, roll=roll))
                assert not (named - allowed)
        legal = e.legal_actions()
        e.apply(Action.check() if legal.can_check else Action.call())
