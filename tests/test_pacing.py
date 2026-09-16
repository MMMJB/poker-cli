"""Pacing: how long the table takes to play, and how that is tuned."""

from __future__ import annotations

import dataclasses

from poker.config import CFG


def cfg(**kw):
    return dataclasses.replace(CFG, **kw)


def test_offline_stretches_think_time_but_not_the_fixed_beats() -> None:
    """Offline is missing request latency, which only filled the think floor.

    The showdown hold was tuned for how long a human needs to read it, and that
    does not change with where the decision came from.
    """
    live = cfg(offline=False)
    offline = cfg(offline=True)
    assert offline.effective_pace > live.effective_pace
    assert offline.paced_think(1.0) > live.paced_think(1.0)
    assert offline.paced(offline.showdown_pause) == live.paced(live.showdown_pause)


def test_pace_zero_removes_every_delay() -> None:
    c = cfg(pace=0.0, offline=True)
    assert c.effective_pace == 0.0
    for delay in (c.deal_pause, c.street_pause, c.showdown_pause, c.action_hold):
        assert c.paced(delay) == 0.0
        assert c.paced_think(delay) == 0.0


def test_pace_scales_linearly() -> None:
    base = cfg(offline=False)
    double = cfg(offline=False, pace=2.0)
    assert double.paced(base.street_pause) == 2 * base.paced(base.street_pause)


def test_a_negative_pace_is_treated_as_zero() -> None:
    assert cfg(pace=-1.0).effective_pace == 0.0


def test_an_action_hold_exists_so_results_can_be_read() -> None:
    """The dwell runs before a seat acts, so a beat afterwards is needed too."""
    assert CFG.action_hold > 0
    assert cfg(offline=True).paced_think(CFG.action_hold) >= 0.5


def test_cli_presets() -> None:
    from poker.__main__ import build_config, parse_args

    fast = build_config(parse_args(["--offline", "--fast"]))
    normal = build_config(parse_args(["--offline"]))
    slow = build_config(parse_args(["--offline", "--slow"]))
    assert fast.effective_pace == 0.0
    assert slow.effective_pace > normal.effective_pace > 0


def test_explicit_pace_overrides_the_presets() -> None:
    from poker.__main__ import build_config, parse_args

    assert build_config(parse_args(["--fast", "--pace", "2"])).pace == 2.0
    assert build_config(parse_args(["--slow", "--pace", "0.5"])).pace == 0.5


def test_pace_can_be_set_from_the_environment(monkeypatch) -> None:
    from poker.config import Config

    monkeypatch.setenv("POKER_PACE", "2.5")
    assert Config.from_env().pace == 2.5
    monkeypatch.setenv("POKER_PACE", "nonsense")
    assert Config.from_env().pace == 1.0
