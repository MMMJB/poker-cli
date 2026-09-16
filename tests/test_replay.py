"""Reading hands back and rebuilding them.

A stored hand carries its RNG seed, so replaying the recorded actions through a
fresh engine must reproduce the identical deal.  That is what lets the ordinary
renderer draw a replay -- and it makes every replay a determinism check.
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
from pathlib import Path

import pytest
from rich.console import Console

from poker.app import replay
from poker.app.loop import App
from poker.app.replay_view import ReplayViewer
from poker.config import CFG
from poker.ui.theme import THEME


def played_session(tmp_path: Path, hands: int = 6):
    """Play a real offline session so there are hands on disk."""
    config = dataclasses.replace(
        CFG, offline=True, demo_hands=hands, log_dir=tmp_path,
        pace=0.0, review_enabled=False, refresh_per_second=1,
    )
    console = Console(theme=THEME, file=io.StringIO(), width=100, height=32,
                      force_terminal=False)
    asyncio.run(App(config, console).run())
    return config


def test_hands_are_found_on_disk(tmp_path: Path) -> None:
    config = played_session(tmp_path, hands=5)
    hands = replay.all_hands(config)
    assert len(hands) == 5
    assert [h.hand_id for h in hands] == [1, 2, 3, 4, 5]


def test_find_locates_a_specific_hand(tmp_path: Path) -> None:
    config = played_session(tmp_path, hands=4)
    hand = replay.find(config, 3)
    assert hand is not None and hand.hand_id == 3
    assert replay.find(config, 999) is None


def test_every_stored_hand_rebuilds_identically(tmp_path: Path) -> None:
    """The load-bearing property: seed plus actions reproduces the deal."""
    config = played_session(tmp_path, hands=10)
    for hand in replay.all_hands(config):
        assert replay.verify(hand) is None, f"hand {hand.hand_id} drifted"


def test_a_rebuild_matches_the_logged_result(tmp_path: Path) -> None:
    config = played_session(tmp_path, hands=6)
    for hand in replay.all_hands(config):
        engine = replay.rebuild(hand)
        result = engine.result()
        from poker.engine.cards import cards_str

        assert cards_str(result.board) == hand.board
        for seat, stack in result.stacks_after.items():
            assert stack == hand.data["stacks_after"][str(seat)]
        # Hole cards must match too, which only the seed can guarantee.
        for seat, cards in result.hole_cards.items():
            assert cards_str(cards) == hand.data["hole_cards"][str(seat)]


def test_stepping_walks_from_the_deal_to_the_end(tmp_path: Path) -> None:
    config = played_session(tmp_path, hands=3)
    hand = replay.all_hands(config)[0]
    states = list(replay.steps(hand))
    assert len(states) == len(replay.recorded_actions(hand)) + 1
    assert not states[0].is_complete()
    assert states[-1].is_complete()
    # The pot only ever grows as the hand progresses.
    pots = [s.state.pot for s in states[:-1]]
    assert pots == sorted(pots)


def test_rebuilding_partway_stops_there(tmp_path: Path) -> None:
    config = played_session(tmp_path, hands=3)
    hand = replay.all_hands(config)[0]
    total = len(replay.recorded_actions(hand))
    half = replay.rebuild(hand, total // 2)
    assert not half.is_complete()
    assert replay.rebuild(hand, 0).state.pot > 0  # blinds are already posted


def test_summaries_are_one_line_and_informative(tmp_path: Path) -> None:
    config = played_session(tmp_path, hands=4)
    for hand in replay.all_hands(config):
        line = replay.summarize(hand)
        assert "\n" not in line
        assert f"#{hand.hand_id}" in line


def test_a_corrupt_line_does_not_break_the_rest(tmp_path: Path) -> None:
    config = played_session(tmp_path, hands=3)
    path = replay.sessions(config)[0] / "hands.jsonl"
    path.write_text(path.read_text() + '{"truncated": \n', encoding="utf-8")
    assert len(replay.all_hands(config)) == 3


def test_no_sessions_is_not_an_error(tmp_path: Path) -> None:
    config = dataclasses.replace(CFG, log_dir=tmp_path / "nothing-here")
    assert replay.sessions(config) == []
    assert replay.all_hands(config) == []
    assert replay.find(config, 1) is None


# --------------------------------------------------------------------------
# The viewer
# --------------------------------------------------------------------------

def test_the_viewer_renders_every_step(tmp_path: Path) -> None:
    from poker.ui.seats import COMPACT, FULL
    from poker.ui.table import render_frame

    config = played_session(tmp_path, hands=4)
    hand = replay.all_hands(config)[-1]
    viewer = ReplayViewer(hand, config,
                          Console(theme=THEME, file=io.StringIO()))

    for step in range(viewer.total + 1):
        viewer.index = step
        view = viewer.frame()
        for layout in (FULL, COMPACT):
            canvas = render_frame(view, layout, 0.0)
            out = Console(theme=THEME, file=io.StringIO(), width=layout.cols + 1)
            out.print(canvas)          # would raise on a bad style
            assert canvas.height == layout.rows


def test_the_viewer_reveals_every_hand(tmp_path: Path) -> None:
    """It is your own history -- there is nothing left to hide."""
    config = played_session(tmp_path, hands=4)
    hand = replay.all_hands(config)[-1]
    viewer = ReplayViewer(hand, config, Console(theme=THEME, file=io.StringIO()))
    viewer.index = viewer.total
    for seat in viewer.frame().seats:
        assert seat.face_up, f"seat {seat.seat} still hidden in a replay"


def test_the_viewer_marks_itself_as_a_replay(tmp_path: Path) -> None:
    """So it can never be mistaken for a live hand."""
    config = played_session(tmp_path, hands=2)
    hand = replay.all_hands(config)[0]
    view = ReplayViewer(hand, config, Console(theme=THEME, file=io.StringIO())).frame()
    assert view.banner.startswith("REPLAY")
    assert view.session_label == "replay"


def test_stepping_is_clamped_at_both_ends(tmp_path: Path) -> None:
    config = played_session(tmp_path, hands=2)
    hand = replay.all_hands(config)[0]
    viewer = ReplayViewer(hand, config, Console(theme=THEME, file=io.StringIO()))

    for _ in range(viewer.total + 5):
        viewer._apply("right")
    assert viewer.index == viewer.total
    for _ in range(viewer.total + 5):
        viewer._apply("left")
    assert viewer.index == 0


def test_q_leaves_the_replay(tmp_path: Path) -> None:
    config = played_session(tmp_path, hands=2)
    hand = replay.all_hands(config)[0]
    viewer = ReplayViewer(hand, config, Console(theme=THEME, file=io.StringIO()))
    assert viewer._apply("q") is False
    assert viewer._apply("right") is not False


# --------------------------------------------------------------------------
# The CLI
# --------------------------------------------------------------------------

def test_listing_hands_from_the_cli(tmp_path: Path, monkeypatch) -> None:
    from poker.__main__ import list_hands

    config = played_session(tmp_path, hands=5)
    console = Console(theme=THEME, file=io.StringIO(), width=120)
    assert list_hands(config, console, limit=3) == 0
    out = console.file.getvalue()
    assert "#5" in out and "#4" in out and "#3" in out
    assert "#1" not in out


def test_listing_with_nothing_recorded(tmp_path: Path) -> None:
    from poker.__main__ import list_hands

    config = dataclasses.replace(CFG, log_dir=tmp_path / "empty")
    console = Console(theme=THEME, file=io.StringIO(), width=120)
    assert list_hands(config, console) == 0
    assert "No hands recorded" in console.file.getvalue()


def test_replaying_a_missing_hand_explains_itself(tmp_path: Path) -> None:
    from poker.__main__ import replay_one

    config = played_session(tmp_path, hands=2)
    console = Console(theme=THEME, file=io.StringIO(), width=120)
    assert replay_one(config, console, "999") == 2
    assert "No hand #999" in console.file.getvalue()


def test_a_non_numeric_hand_id_is_rejected(tmp_path: Path) -> None:
    from poker.__main__ import replay_one

    config = played_session(tmp_path, hands=2)
    console = Console(theme=THEME, file=io.StringIO(), width=120)
    assert replay_one(config, console, "banana") == 2
    assert "not a hand number" in console.file.getvalue()


@pytest.mark.parametrize("argv,expected", [
    (["hands"], "hands"),
    (["hands", "5"], "hands"),
    (["hand", "3"], "hand"),
    (["replay", "3"], "hand"),
])
def test_cli_parses_the_commands(argv, expected) -> None:
    from poker.__main__ import parse_args

    args = parse_args(argv)
    assert args.command[0] == argv[0]


def test_playing_still_works_with_no_command() -> None:
    from poker.__main__ import parse_args

    assert parse_args([]).command == []
    assert parse_args(["--offline"]).command == []
