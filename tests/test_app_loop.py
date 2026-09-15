"""End-to-end: the whole app, offline, with pacing removed.

Exercises the real loop -- engine, agents, view building, rendering, review and
persistence -- without a terminal and without a single API call.
"""

from __future__ import annotations

import asyncio
import dataclasses
import io
import json
from pathlib import Path

from rich.console import Console

from poker.app.loop import App
from poker.config import CFG
from poker.ui.theme import THEME


def fast_config(tmp_path: Path, hands: int = 6, **kw):
    return dataclasses.replace(
        CFG,
        offline=True,
        demo_hands=hands,
        log_dir=tmp_path,
        deal_pause=0.0,
        street_pause=0.0,
        pot_sweep=0.0,
        showdown_pause=0.0,
        talk_hold=0.0,
        bet_flash=0.0,
        dwell_scale=0.0,
        refresh_per_second=1,
        **kw,
    )


def run_app(cfg) -> App:
    console = Console(theme=THEME, file=io.StringIO(), width=100, height=32,
                      force_terminal=False)
    app = App(cfg, console)
    asyncio.run(app.run())
    return app


def test_offline_session_plays_and_conserves_chips(tmp_path: Path) -> None:
    app = run_app(fast_config(tmp_path, hands=8))
    assert app.table.hand_number == 8
    on_table = sum(s.stack for s in app.table.seats)
    bought = sum(s.total_bought_in for s in app.table.seats)
    assert on_table == bought
    assert sum(s.profit for s in app.table.seats) == 0


def test_session_files_are_written(tmp_path: Path) -> None:
    run_app(fast_config(tmp_path, hands=5))
    sessions = list((tmp_path / "sessions").iterdir())
    assert len(sessions) == 1
    directory = sessions[0]

    state = json.loads((directory / "session.json").read_text())
    assert state["hand_number"] == 5
    assert len(state["stacks"]) == 6

    hands = [json.loads(line) for line in
             (directory / "hands.jsonl").read_text().splitlines()]
    assert len(hands) == 5
    for hand in hands:
        assert sum(hand["net"].values()) == 0
        assert sum(a["amount"] for a in hand["awards"]) == sum(
            p["amount"] for p in hand["pots"]
        )


def test_a_session_resumes_from_disk(tmp_path: Path) -> None:
    from poker.app.session import SessionStore

    first = run_app(fast_config(tmp_path, hands=4))
    ended_with = [s.stack for s in first.table.seats]

    # What a fresh process would read back.
    resumed = SessionStore(fast_config(tmp_path)).load()
    assert resumed is not None
    assert resumed.hand_number == 4
    assert resumed.stacks == ended_with
    assert resumed.session_seed == first.table.session_seed

    second = run_app(fast_config(tmp_path, hands=2))
    assert second.table.hand_number == 6, "should continue, not restart"
    # Only one session directory: the second run continued the first.
    assert len(list((tmp_path / "sessions").iterdir())) == 1
    # A rebuy can only ever add to the cost basis, never reduce it.
    for later, earlier in zip(second.table.seats, first.table.seats):
        assert later.total_bought_in >= earlier.total_bought_in


def test_no_hand_log_leaks_a_persona_into_public_events(tmp_path: Path) -> None:
    """The god-view log records personas; the public event stream must not."""
    run_app(fast_config(tmp_path, hands=6))
    directory = next((tmp_path / "sessions").iterdir())
    hands = [json.loads(line) for line in
             (directory / "hands.jsonl").read_text().splitlines()]

    from poker.agents.personas import ALL_PERSONAS

    for hand in hands:
        assert hand["personas"], "the developer log should record them"
        blob = json.dumps(hand["events"]).lower()
        for persona in ALL_PERSONAS:
            assert persona.archetype not in blob
            assert persona.profile.lower() not in blob


def test_every_decision_is_attributed(tmp_path: Path) -> None:
    run_app(fast_config(tmp_path, hands=6))
    directory = next((tmp_path / "sessions").iterdir())
    hands = [json.loads(line) for line in
             (directory / "hands.jsonl").read_text().splitlines()]

    sources = {d["source"] for h in hands for d in h["decisions"]}
    assert sources <= {"offline", "human", "forced", "fallback"}
    assert "offline" in sources, "opponents should be playing the local policy"
    # No network was touched.
    assert not any(d["model"] not in ("", "local", "none")
                   for h in hands for d in h["decisions"])


def test_offline_play_actually_reaches_flops(tmp_path: Path) -> None:
    """Guards the regression where every seat folded preflop every hand."""
    run_app(fast_config(tmp_path, hands=25))
    directory = next((tmp_path / "sessions").iterdir())
    hands = [json.loads(line) for line in
             (directory / "hands.jsonl").read_text().splitlines()]
    with_board = sum(1 for h in hands if h["board"])
    assert with_board >= 5, f"only {with_board}/25 hands saw a flop"


def test_rendering_runs_without_a_terminal(tmp_path: Path) -> None:
    """A render exception would be swallowed by Live; assert frames are produced."""
    from poker.ui.seats import COMPACT, FULL
    from poker.ui.table import render_frame

    app = run_app(fast_config(tmp_path, hands=3))
    for layout in (FULL, COMPACT):
        canvas = render_frame(app._view, layout, tick=0.5)
        assert canvas.height == layout.rows
        out = Console(theme=THEME, file=io.StringIO(), width=layout.cols + 1)
        out.print(canvas)  # would raise on a bad style name


def test_a_session_runs_with_reviews_disabled(tmp_path: Path) -> None:
    app = run_app(fast_config(tmp_path, hands=6, review_enabled=False))
    assert not app.coach.enabled
    assert app.table.hand_number == 6
    assert sum(s.profit for s in app.table.seats) == 0


def test_the_header_shows_when_reviews_are_off(tmp_path: Path) -> None:
    import io as _io

    from poker.ui.seats import COMPACT, FULL
    from poker.ui.table import render_frame

    app = run_app(fast_config(tmp_path, hands=2, review_enabled=False))
    for layout in (FULL, COMPACT):
        console = Console(theme=THEME, file=_io.StringIO(), width=layout.cols + 1)
        console.print(render_frame(app._view, layout, tick=0.0))
        assert "review off" in console.file.getvalue()


def test_the_indicator_is_absent_when_reviews_are_on(tmp_path: Path) -> None:
    import io as _io

    from poker.ui.seats import FULL
    from poker.ui.table import render_frame

    app = run_app(fast_config(tmp_path, hands=2))
    console = Console(theme=THEME, file=_io.StringIO(), width=FULL.cols + 1)
    console.print(render_frame(app._view, FULL, tick=0.0))
    assert "review off" not in console.file.getvalue()
