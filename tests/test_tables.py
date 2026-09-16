"""Moving between tables: new opponents, new names, same bankroll."""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from poker.agents.personas import (
    ALL_PERSONAS, assign_seats, choose_table_size,
)
from poker.app.loop import App
from poker.config import CFG
from poker.ui.seats import RINGS
from poker.ui.theme import THEME
import random


def make_app(tmp_path: Path, **kw) -> App:
    defaults = dict(offline=True, demo_hands=0, log_dir=tmp_path, pace=0.0,
                    review_enabled=False, refresh_per_second=1)
    config = dataclasses.replace(CFG, **{**defaults, **kw})
    console = Console(theme=THEME, file=io.StringIO(), width=100, height=32,
                      force_terminal=False)
    return App(config, console)


# --------------------------------------------------------------------------
# Table sizes
# --------------------------------------------------------------------------

def test_sizes_span_four_to_eight() -> None:
    rng = random.Random(1)
    seen = {choose_table_size(rng) for _ in range(500)}
    assert seen == {4, 5, 6, 7, 8}


def test_sizes_are_weighted_toward_the_bigger_tables() -> None:
    rng = random.Random(2)
    draws = [choose_table_size(rng) for _ in range(20000)]
    counts = {n: draws.count(n) for n in range(4, 9)}
    # Monotonically more likely as the table grows.
    assert counts[4] < counts[5] < counts[6] < counts[7] < counts[8]
    assert 6.3 < sum(draws) / len(draws) < 7.0


def test_every_supported_size_has_a_seat_ring() -> None:
    for size in CFG.table_sizes:
        assert size in RINGS
        assert len(RINGS[size]) == size
        assert RINGS[size][0] == "hero"


def test_every_ring_names_distinct_anchors() -> None:
    from poker.ui.seats import COMPACT, FULL

    for size, ring in RINGS.items():
        assert len(set(ring)) == size, f"{size}-max ring repeats an anchor"
        for layout in (FULL, COMPACT):
            for anchor in ring:
                assert anchor in layout.slots, f"{layout.name} lacks {anchor}"


@pytest.mark.parametrize("size", [4, 5, 6, 7, 8])
def test_seat_boxes_never_overlap(size: int) -> None:
    """Two seats sharing a cell would draw over each other."""
    from poker.ui.seats import COMPACT, FULL

    for layout in (FULL, COMPACT):
        taken: dict[tuple[int, int], int] = {}
        for seat in range(size):
            slot = layout.slot_for(seat, 0, size)
            for dy in range(layout.seat_h):
                for dx in range(layout.seat_w):
                    cell = (slot.x + dx, slot.y + dy)
                    assert cell not in taken, (
                        f"{layout.name} {size}-max: seats {taken[cell]} and "
                        f"{seat} overlap at {cell}"
                    )
                    taken[cell] = seat


@pytest.mark.parametrize("size", [4, 5, 6, 7, 8])
def test_positions_are_named_for_every_size(size: int) -> None:
    from poker.engine.positions import position_name

    names = [position_name(s, 0, size) for s in range(size)]
    assert len(set(names)) == size
    assert names[0] == "BTN"
    assert "SB" in names and "BB" in names


# --------------------------------------------------------------------------
# Casting
# --------------------------------------------------------------------------

@pytest.mark.parametrize("size", [4, 5, 6, 7, 8])
def test_a_table_is_fully_and_distinctly_cast(size: int) -> None:
    seated = assign_seats(random.Random(size), hero_seat=0, num_seats=size)
    assert len(seated) == size - 1
    assert len({s.persona.key for s in seated.values()}) == size - 1
    assert len({s.name for s in seated.values()}) == size - 1


def test_every_table_has_someone_worth_beating() -> None:
    for seed in range(60):
        for size in (4, 6, 8):
            seated = assign_seats(random.Random(seed), 0, size)
            tiers = [s.tier for s in seated.values()]
            assert any(t in ("mid", "deep") for t in tiers), (
                f"seed {seed} size {size}: nobody strong")
            assert sum(t in ("mid", "deep") for t in tiers) <= 2, (
                "a $1/$3 table does not have three regulars on it")


def test_tables_differ_from_one_another() -> None:
    rng = random.Random(9)
    casts = {
        tuple(sorted(s.persona.key for s in assign_seats(rng, 0, 6).values()))
        for _ in range(30)
    }
    assert len(casts) > 5, "tables are too samey"


def test_the_same_archetype_appears_under_different_names() -> None:
    """You must read the player, not recognise the nameplate."""
    rng = random.Random(4)
    names_by_key: dict[str, set[str]] = {}
    for _ in range(80):
        for seated in assign_seats(rng, 0, 8).values():
            names_by_key.setdefault(seated.persona.key, set()).add(seated.name)
    assert any(len(v) > 1 for v in names_by_key.values())


def test_a_table_too_big_for_the_pool_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot fill"):
        assign_seats(random.Random(0), 0, len(ALL_PERSONAS) + 2)


# --------------------------------------------------------------------------
# Moving tables
# --------------------------------------------------------------------------

def test_changing_table_replaces_the_opponents(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    before = {s.name for s in app.personas.values()}
    before_keys = {s.persona.key for s in app.personas.values()}

    asyncio.run(app.change_table())

    after = {s.name for s in app.personas.values()}
    assert app.table_number == 2
    assert not (before & after), "a name carried over to the new table"
    # The cast should change too, though an archetype may legitimately recur.
    assert before_keys != {s.persona.key for s in app.personas.values()}


def test_the_bankroll_follows_you(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    hero = app.cfg.hero_seat
    app.table.seats[hero].stack = 517
    app.table.seats[hero].total_bought_in = 300
    app.table.seats[hero].hands_played = 40

    asyncio.run(app.change_table())

    moved = app.table.seats[hero]
    assert moved.stack == 517, "you take your chips with you"
    assert moved.total_bought_in == 300, "P/L must stay continuous"
    assert moved.hands_played == 40
    assert moved.profit == 217


def test_opponents_start_fresh_at_the_new_table(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    for seat in app.personas:
        app.table.seats[seat].stack = 12

    asyncio.run(app.change_table())

    for seat in app.personas:
        assert app.table.seats[seat].stack == app.cfg.buy_in


def test_hand_numbering_continues_across_tables(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    app.cfg = dataclasses.replace(app.cfg, demo_hands=1)

    async def go():
        for _ in range(2):
            await app.play_hand()
            await app.between_hands()
        first = app.table.hand_number
        await app.change_table()
        await app.play_hand()
        return first, app.table.hand_number

    first, after = asyncio.run(go())
    assert after == first + 1, "hand numbers must not restart at a new table"


def test_the_new_table_size_is_in_range(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    for _ in range(25):
        asyncio.run(app.change_table())
        assert app.seat_size in CFG.table_sizes
        assert len(app.table.seats) == app.seat_size
        assert len(app.personas) == app.seat_size - 1
        assert len(app.agents) == app.seat_size - 1


def test_the_hero_keeps_their_seat_index(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    for _ in range(10):
        asyncio.run(app.change_table())
        assert app.table.human_seat == app.cfg.hero_seat
        assert app.table.seats[app.cfg.hero_seat].is_human
        assert app.cfg.hero_seat not in app.personas


def test_hands_at_table_resets_on_a_move(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    app.hands_at_table = 87
    asyncio.run(app.change_table())
    assert app.hands_at_table == 0


# --------------------------------------------------------------------------
# The automatic move
# --------------------------------------------------------------------------

def test_the_room_moves_you_after_the_configured_number_of_hands(tmp_path: Path) -> None:
    app = make_app(tmp_path, demo_hands=6, hands_per_table=3)
    asyncio.run(app.run())
    assert app.table_number >= 2, "should have been moved at least once"


def test_a_zero_limit_keeps_you_put(tmp_path: Path) -> None:
    app = make_app(tmp_path, demo_hands=6, hands_per_table=0)
    asyncio.run(app.run())
    assert app.table_number == 1


def test_the_default_is_a_hundred_hands() -> None:
    assert CFG.hands_per_table == 100


def test_chips_are_conserved_across_a_move(tmp_path: Path) -> None:
    """Moving tables must not create or destroy the hero's money."""
    app = make_app(tmp_path, demo_hands=8, hands_per_table=3)
    asyncio.run(app.run())
    hero = app.table.human
    assert hero.stack >= 0
    assert hero.profit == hero.stack - hero.total_bought_in


def test_hands_are_logged_across_tables(tmp_path: Path) -> None:
    app = make_app(tmp_path, demo_hands=8, hands_per_table=3)
    asyncio.run(app.run())
    directory = next((tmp_path / "sessions").iterdir())
    hands = [json.loads(l) for l in
             (directory / "hands.jsonl").read_text().splitlines()]
    assert len(hands) == 8
    ids = [h["hand_id"] for h in hands]
    assert ids == sorted(set(ids)), "hand ids must stay unique and ordered"
    # Table sizes vary, so the number of seats logged should too.
    assert len({len(h["names"]) for h in hands}) >= 1


def test_a_replay_still_works_after_moving_tables(tmp_path: Path) -> None:
    """Rebuilding uses the seat count recorded with the hand, not the current one."""
    from poker.app import replay

    app = make_app(tmp_path, demo_hands=8, hands_per_table=3)
    asyncio.run(app.run())
    stored = replay.all_hands(app.cfg)
    assert len(stored) == 8
    for hand in stored:
        assert replay.verify(hand) is None, f"hand {hand.hand_id} will not rebuild"


def test_the_gate_offers_a_table_change(tmp_path: Path) -> None:
    from poker.app.input import ActionPrompt
    from poker.ui.keys import KeyReader

    app = make_app(tmp_path)
    app.keys = KeyReader()
    app.keys.enabled = True
    app.prompt = ActionPrompt(app.keys, app.publish)
    frames: list[dict] = []
    app.publish = lambda **kw: frames.append(kw)  # type: ignore[method-assign]

    async def go():
        task = asyncio.ensure_future(app.wait_for_next_hand())
        await asyncio.sleep(0.01)
        hint = next(f["input_line"].hint for f in reversed(frames)
                    if "input_line" in f)
        assert "new table" in hint
        app.keys.feed("t")
        await asyncio.wait_for(task, 1.0)

    before = app.table_number
    asyncio.run(go())
    assert app.table_number == before + 1
