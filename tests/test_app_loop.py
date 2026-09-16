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

import pytest
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
        pace=0.0,
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


# --------------------------------------------------------------------------
# The between-hands gate
# --------------------------------------------------------------------------

def make_app(tmp_path: Path, **kw):
    """An app wired to a fed KeyReader, without running the full loop."""
    import io as _io

    from poker.ui.keys import KeyReader

    console = Console(theme=THEME, file=_io.StringIO(), width=100, height=32,
                      force_terminal=False)
    app = App(fast_config(tmp_path, hands=0, **kw), console)
    app.keys = KeyReader()
    app.keys.enabled = True
    return app


def run_gate(app, keys_in, timeout=0.5):
    """Drive wait_for_next_hand, returning True if it returned (i.e. advanced)."""
    async def go():
        task = asyncio.ensure_future(app.wait_for_next_hand())
        await asyncio.sleep(0.01)
        app.keys.feed(*keys_in)
        try:
            await asyncio.wait_for(task, timeout)
            return True
        except asyncio.TimeoutError:
            task.cancel()
            return False

    return asyncio.run(go())


def test_the_gate_waits_rather_than_dealing_the_next_hand(tmp_path: Path) -> None:
    """The whole point: no hand starts until the player asks for one."""
    app = make_app(tmp_path)
    assert run_gate(app, []) is False, "it advanced without any input"


def test_enter_advances_to_the_next_hand(tmp_path: Path) -> None:
    from poker.ui import keys as K

    app = make_app(tmp_path)
    assert run_gate(app, [K.KEY_ENTER]) is True
    assert not app.quit


def test_space_also_advances(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    assert run_gate(app, [" "]) is True


def test_q_quits_from_the_gate(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    assert run_gate(app, ["q"]) is True
    assert app.quit


def test_v_toggles_reviews_without_advancing(tmp_path: Path) -> None:
    """Both steps share one event loop: the key queue is loop-bound."""
    from poker.ui import keys as K

    app = make_app(tmp_path)
    before = app.coach.enabled

    async def go():
        task = asyncio.ensure_future(app.wait_for_next_hand())
        await asyncio.sleep(0.01)
        app.keys.feed("v")
        await asyncio.sleep(0.05)
        assert not task.done(), "'v' must not advance the hand"
        assert app.coach.enabled is not before
        app.keys.feed(K.KEY_ENTER)
        await asyncio.wait_for(task, 0.5)

    asyncio.run(go())


def test_unrelated_keys_do_not_advance(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    assert run_gate(app, ["x", "z", "1"]) is False


def test_the_gate_shows_the_hand_result_and_the_options(tmp_path: Path) -> None:
    frames: list[dict] = []
    app = make_app(tmp_path)
    app.publish = lambda **kw: frames.append(kw)  # type: ignore[method-assign]
    run_gate(app, [])
    line = next(f["input_line"] for f in reversed(frames) if "input_line" in f)
    bar = next(f["action_bar"] for f in reversed(frames) if "action_bar" in f)
    assert "next hand" in line.hint and "quit" in line.hint
    assert bar.message


def test_demo_mode_does_not_gate(tmp_path: Path) -> None:
    """Automated runs must not stall waiting for a keypress."""
    app = make_app(tmp_path)
    app.cfg = dataclasses.replace(app.cfg, demo_hands=3)
    assert run_gate(app, []) is True


# --------------------------------------------------------------------------
# Saying *why* live play is unavailable
# --------------------------------------------------------------------------

@pytest.mark.parametrize("error,expected", [
    ("Your credit balance is too low to access the Anthropic API.",
     "no API credit"),
    ("ANTHROPIC_API_KEY is missing or invalid", "bad API key"),
    ("cannot reach the Anthropic API -- check your connection", "no connection"),
    ("this key may not use claude-opus-5", "key lacks access"),
    ("model not available: claude-fable-5-1", "model unavailable"),
    ("claude-sonnet-5: API error 503", "API error"),
])
def test_the_banner_names_the_cause(error: str, expected: str) -> None:
    """A bare 'OFFLINE' does not tell you whether it is your key or your bill."""
    from poker.app.loop import offline_banner

    banner = offline_banner(error)
    assert banner.startswith("OFFLINE")
    assert expected in banner


def test_the_reason_survives_the_start_of_a_hand(tmp_path: Path) -> None:
    """Regression: it used to be written to the log, which play_hand clears.

    The explanation was gone before the first frame the player ever saw, so all
    that was left was an unexplained OFFLINE badge.
    """
    app = make_app(tmp_path)
    app.offline_reason = "Your credit balance is too low."
    app.publish(banner="OFFLINE · no API credit")

    app.engine = app.table.start_hand()
    app._log_lines = []          # what play_hand does
    app._render_log()

    assert app._view.banner == "OFFLINE · no API credit"
    assert app.offline_reason


def test_the_full_reason_is_repeated_on_exit(tmp_path: Path) -> None:
    import io as _io

    app = make_app(tmp_path)
    app.offline_reason = "Your credit balance is too low to access the API."
    app.console = Console(theme=THEME, file=_io.StringIO(), width=100)
    app._print_farewell()
    out = app.console.file.getvalue()
    assert "credit balance" in out
    assert "nothing was billed" in out


def test_no_offline_notice_when_everything_is_fine(tmp_path: Path) -> None:
    import io as _io

    app = make_app(tmp_path)
    app.console = Console(theme=THEME, file=_io.StringIO(), width=100)
    app._print_farewell()
    assert "Played offline" not in app.console.file.getvalue()
