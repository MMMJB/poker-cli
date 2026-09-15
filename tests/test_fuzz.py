"""Randomized play with per-action invariant checking.

A random-but-legal bot drives thousands of seeded hands, asserting the engine's
global invariants after *every single action*.  This is the test that catches
state-machine bugs the hand-written cases miss.
"""

from __future__ import annotations

import random

import pytest

from poker.engine.actions import Action
from poker.engine.hand import HandEngine
from poker.engine.state import GameConfig, PlayerStatus

NAMES = [f"P{i}" for i in range(6)]
MAX_ACTIONS_PER_HAND = 400


def random_legal_action(legal, rng: random.Random) -> Action:
    """Pick uniformly among the legal action *kinds*, then a legal amount."""
    choices = []
    if legal.can_fold:
        choices.append("fold")
    if legal.can_check:
        choices.append("check")
    if legal.can_call:
        choices.append("call")
    if legal.can_bet:
        choices.append("bet")
    if legal.can_raise:
        choices.append("raise")
    assert choices, "a player to act always has at least one legal action"

    kind = rng.choice(choices)
    if kind == "fold":
        return Action.fold()
    if kind == "check":
        return Action.check()
    if kind == "call":
        return Action.call()
    # Bias toward the boundaries, where the bugs live.
    amount = rng.choice([
        legal.min_to,
        legal.max_to,
        rng.randint(legal.min_to, legal.max_to),
    ])
    return Action.bet(amount) if kind == "bet" else Action.raise_to(amount)


def check_invariants(e: HandEngine, chips_at_start: int) -> None:
    s = e.state
    mid_hand = not e.is_complete()

    if mid_hand:
        # Chips live in exactly two places while the hand runs: behind a player
        # or out in front of them.  Once pots are awarded they are back in
        # stacks while total_committed still records the contribution, so this
        # form of the check only applies mid-hand.
        total = sum(p.stack + p.total_committed for p in s.players)
    else:
        total = sum(p.stack for p in s.players)
    assert total == chips_at_start, f"chips not conserved: {total} != {chips_at_start}"

    for p in s.players:
        assert p.stack >= 0, f"seat {p.seat} has a negative stack"
        assert p.street_committed <= p.total_committed
        assert p.street_committed >= 0 and p.total_committed >= 0
        if mid_hand and p.stack == 0 and p.status is PlayerStatus.ACTIVE:
            raise AssertionError(f"seat {p.seat} has no chips but is still ACTIVE")

    if s.to_act is not None:
        assert s.players[s.to_act].can_act, "the seat to act must be able to act"

    assert s.current_bet >= 0
    assert len(s.board) <= 5


def play_one_hand(seed: int, stacks: list[int], button: int) -> tuple[list[int], int]:
    rng = random.Random(seed)
    chips_at_start = sum(stacks)
    e = HandEngine(GameConfig(), NAMES, stacks, button, hand_id=seed, seed=seed)
    e.start()
    check_invariants(e, chips_at_start)

    street = e.state.street
    actions = 0
    while not e.is_complete():
        actions += 1
        assert actions < MAX_ACTIONS_PER_HAND, "hand failed to terminate"

        if e.state.street is not street:
            street = e.state.street

        before = e.state.current_bet
        e.apply(random_legal_action(e.legal_actions(), rng))
        check_invariants(e, chips_at_start)

        # current_bet is non-decreasing within a street.
        if e.state.street is street:
            assert e.state.current_bet >= before, "current_bet went backwards"

    r = e.result()
    assert sum(r.net.values()) == 0, f"net is not zero-sum: {r.net}"
    assert sum(r.stacks_after.values()) == chips_at_start
    assert sum(a.amount for a in r.awards) == sum(p.amount for p in r.pots)
    return [r.stacks_after[i] for i in range(6)], actions


def run_fuzz(n_hands: int, seed0: int = 0) -> None:
    stacks = [300] * 6
    button = 0
    for i in range(n_hands):
        # Top up between hands so blinds are always postable.
        stacks = [s if s >= 60 else 300 for s in stacks]
        stacks, _ = play_one_hand(seed0 * 1_000_003 + i, stacks, button)
        button = (button + 1) % 6


def test_fuzz_quick() -> None:
    run_fuzz(400, seed0=1)


def test_fuzz_short_stacks() -> None:
    """Shallow stacks make all-ins and side pots the common case."""
    rng = random.Random(42)
    for i in range(400):
        stacks = [rng.choice([6, 9, 15, 30, 60, 300]) for _ in range(6)]
        play_one_hand(900_000 + i, stacks, button=i % 6)


def test_fuzz_identical_stacks_force_chops() -> None:
    for i in range(200):
        play_one_hand(500_000 + i, [100] * 6, button=i % 6)


@pytest.mark.slow
def test_fuzz_exhaustive() -> None:
    run_fuzz(20_000, seed0=7)


@pytest.mark.slow
def test_fuzz_short_stacks_exhaustive() -> None:
    rng = random.Random(1_234_567)
    for i in range(20_000):
        stacks = [rng.choice([6, 7, 9, 12, 15, 30, 45, 60, 120, 300]) for _ in range(6)]
        play_one_hand(2_000_000 + i, stacks, button=i % 6)
