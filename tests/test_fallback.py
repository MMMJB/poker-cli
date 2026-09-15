"""The local policy must never hang, crash, or produce an illegal action."""

from __future__ import annotations

import random

from poker.agents.fallback import decision_rng, local_decision
from poker.agents.personas import ALL_PERSONAS, BY_KEY, assign_seats
from poker.engine.actions import ActionType
from poker.engine.hand import HandEngine
from poker.engine.state import GameConfig

NAMES = ["Hero", "Deb", "Walter", "Tank", "Raj", "Sofia"]


def play(seed: int, stacks: list[int] | None = None) -> HandEngine:
    personas = assign_seats(random.Random(seed), hero_seat=0)
    e = HandEngine(GameConfig(), NAMES, stacks or [300] * 6,
                   button=seed % 6, hand_id=seed, seed=seed)
    e.start()
    index = 0
    while not e.is_complete():
        seat = e.current_actor()
        legal = e.legal_actions()
        persona = personas.get(seat) or ALL_PERSONAS[0]
        rng = decision_rng(seed, seat, int(e.state.street), index)
        e.apply(local_decision(persona, e.observation(seat), legal, rng))
        index += 1
    return e


def test_local_play_completes_every_hand() -> None:
    for seed in range(400):
        e = play(seed)
        assert e.is_complete()
        assert sum(e.result().net.values()) == 0


def test_local_play_survives_short_stacks() -> None:
    rng = random.Random(3)
    for seed in range(200):
        stacks = [rng.choice([6, 12, 30, 90, 300]) for _ in range(6)]
        e = play(1000 + seed, stacks)
        assert e.is_complete()


def test_local_decisions_are_reproducible() -> None:
    a = play(77).log.to_json(include_private=True)
    b = play(77).log.to_json(include_private=True)
    assert a == b


def test_station_calls_more_than_the_nit_folds() -> None:
    """The personas must actually differ, even in fallback mode."""
    import dataclasses

    from poker.engine.actions import LegalActions

    e = HandEngine(GameConfig(), NAMES, [300] * 6, button=0, hand_id=1, seed=1)
    e.start()
    # $20 to call into a $60 pot -- a real decision, not a 5x-pot shove that
    # every persona should fold to.
    obs = dataclasses.replace(e.observation(3), pot=60)
    legal = LegalActions(
        seat=3, can_fold=True, can_check=False, can_call=True, call_cost=20,
        call_is_all_in=False, can_bet=False, can_raise=True, min_to=40,
        max_to=300, stack=288,
    )

    def count(key: str, kind) -> int:
        # Vary the hole cards, not just the RNG: the policy is strength-driven.
        total = 0
        for i in range(120):
            seeded = dataclasses.replace(obs, hole=_hole(i))
            action = local_decision(BY_KEY[key], seeded, legal, random.Random(i))
            total += action.type is kind
        return total

    deb_calls = count("deb", ActionType.CALL)
    walter_calls = count("walter", ActionType.CALL)
    tank_raises = count("tank", ActionType.RAISE)
    walter_folds = count("walter", ActionType.FOLD)

    assert deb_calls > walter_calls, "the station must call more than the nit"
    assert walter_folds > deb_calls // 2, "the nit must fold a lot"
    assert tank_raises > 0, "the aggressive persona must sometimes raise back"


def _hole(i: int):
    """Deterministic distinct two-card holdings."""
    a = (i * 7) % 52
    b = (a + 1 + (i % 17)) % 52
    if b == a:
        b = (b + 1) % 52
    return (a, b)


def test_strength_ordering_is_sane() -> None:
    from poker.agents.fallback import postflop_strength, preflop_strength
    from poker.engine.cards import parse_cards

    aces = preflop_strength(tuple(parse_cards("Ah As")))
    ak_s = preflop_strength(tuple(parse_cards("Ah Kh")))
    trash = preflop_strength(tuple(parse_cards("7h 2c")))
    assert aces > ak_s > trash

    board = tuple(parse_cards("Ah Kd 7c 3s 2h"))
    top_pair = postflop_strength(tuple(parse_cards("Ac Qd")), board)
    low_pair = postflop_strength(tuple(parse_cards("7d 5c")), board)
    two_pair = postflop_strength(tuple(parse_cards("Ac Kc")), board)
    playing_board = postflop_strength(tuple(parse_cards("8d 9c")), board)
    assert two_pair > top_pair > low_pair > playing_board


def test_talk_never_mentions_an_archetype() -> None:
    from poker.agents.personas import leak_denylist

    deny = {w.lower() for w in leak_denylist()}
    for persona in ALL_PERSONAS:
        for line in _all_talk(persona):
            lowered = line.lower()
            for word in deny:
                assert word not in lowered.split(), (
                    f"{persona.key} talk leaks {word!r}: {line!r}"
                )


def _all_talk(persona):
    from poker.agents.fallback import _TALK

    return _TALK.get(persona.key, ())
