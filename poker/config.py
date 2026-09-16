"""Every tunable constant in the application lives here.

No constant is defined anywhere else.  Environment overrides let the game be
run offline (no API spend) or with debug instrumentation without code edits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class TierSpec:
    """Per-tier model settings.

    The ``None`` defaults are load-bearing -- several of these parameters are a
    400 on the wrong model, so a tier that must not send one leaves it None and
    the client omits the key entirely:

    * ``effort`` errors on ``claude-haiku-4-5``.
    * ``budget_tokens`` is rejected on the mid and deep tiers, so thinking on
      the fast tier is simply omitted rather than configured.

    There is deliberately no temperature setting: sampling parameters are gone
    from ``messages.create`` in the current SDK.  Per-persona variance comes
    from the private decision roll in :mod:`poker.agents.prompts` instead,
    which is both model-independent and reproducible from the hand seed.
    """

    model: str
    max_tokens: int
    timeout_s: float
    thinking: dict | None = None
    effort: str | None = None
    escalate_effort: str | None = None
    """Effort used on the river or in a big pot, when the decision is worth it."""


TIERS: dict[str, TierSpec] = {
    "fast": TierSpec(
        model="claude-haiku-4-5",
        max_tokens=300,
        timeout_s=6.0,
        thinking=None,   # omitted entirely: latency is the whole point
        effort=None,     # MUST NOT be sent to haiku-4-5
    ),
    "mid": TierSpec(
        model="claude-sonnet-5",
        max_tokens=1500,
        timeout_s=9.0,
        thinking={"type": "adaptive"},
        effort="low",
    ),
    "deep": TierSpec(
        model="claude-opus-5",
        max_tokens=2000,
        timeout_s=12.0,
        thinking={"type": "adaptive"},
        effort="low",
        escalate_effort="medium",
    ),
}

TIER_DOWNGRADE = {"deep": "mid", "mid": "fast", "fast": None}
"""One step down on a rate limit or server error, then the local policy."""


@dataclass(frozen=True, slots=True)
class Config:
    # --- game -------------------------------------------------------------
    small_blind: int = 1
    big_blind: int = 3
    buy_in: int = 300
    num_seats: int = 6
    """Seats at the opening table.  Later tables are drawn from the range below."""

    table_sizes: tuple[int, ...] = (4, 5, 6, 7, 8)
    table_size_weights: tuple[int, ...] = (1, 2, 3, 4, 5)
    """Weighted toward the bigger tables, which is how a real room looks:
    short-handed games exist but full ones are the norm.  Mean is about 6.7."""

    hands_per_table: int = 100
    """Move to a new table after this many hands.  0 disables the automatic move."""
    hero_seat: int = 0
    rebuy_threshold: int = 60
    auto_rebuy_opponents: bool = True

    # --- rendering --------------------------------------------------------
    full_cols: int = 100
    full_rows: int = 32
    compact_cols: int = 80
    compact_rows: int = 24
    min_cols: int = 80
    min_rows: int = 24
    refresh_per_second: int = 20

    # --- pacing (seconds, before the multipliers below) --------------------
    deal_pause: float = 0.40
    card_stagger: float = 0.10
    street_pause: float = 0.80
    pot_sweep: float = 0.50
    showdown_pause: float = 2.40
    talk_hold: float = 1.60
    bet_flash: float = 0.15
    action_hold: float = 0.45
    """How long an action stays on screen after it resolves.

    The think-time dwell happens *before* a seat acts, so without this the
    result of the action is replaced almost immediately by the next seat
    starting to think, and the hand is hard to follow.
    """

    pace: float = 1.0
    """Global multiplier on every delay.  0.0 removes pacing entirely."""
    offline_pace: float = 1.6
    """Extra multiplier on *think time only*, when offline.

    What offline play is missing is request latency, and that only ever filled
    the think-time floor.  The fixed beats -- dealing, the street change, the
    showdown hold -- were tuned for how long a human needs to read them and do
    not depend on where the decision came from, so they are left alone.
    """

    @property
    def effective_pace(self) -> float:
        """The multiplier on think time, which is what offline stretches."""
        if self.pace <= 0:
            return 0.0
        return self.pace * (self.offline_pace if self.offline else 1.0)

    def paced(self, seconds: float) -> float:
        """Scale a fixed animation beat."""
        return 0.0 if self.pace <= 0 else seconds * self.pace

    def paced_think(self, seconds: float) -> float:
        """Scale a think-time delay, including the offline padding."""
        return seconds * self.effective_pace

    # --- agents -----------------------------------------------------------
    review_enabled: bool = True
    """Master switch.  The review is the trainer's teaching layer, but it is
    also by far the most expensive call per hand, so it must be easy to turn
    off -- from the CLI, from the environment, or with a keystroke mid-session."""

    # The review runs in two stages: a strong analyst reads the hand, then a
    # second model rewrites its findings in plain language.  Splitting them
    # means the analysis is never dumbed down to stay readable, and the prose
    # is never technical just because the analysis was.
    analyst_model: str = "claude-fable-5-1"
    analyst_effort: str = "medium"
    analyst_max_tokens: int = 16000
    """Thinking tokens count against this, and Fable always thinks."""
    analyst_timeout_s: float = 180.0

    translator_model: str = "claude-opus-4-8"
    translator_effort: str = "low"
    translator_max_tokens: int = 2000
    translator_timeout_s: float = 45.0

    review_min_investment_bb: int = 1
    """Skip the review when the hero put in no more than this (a preflop fold)."""
    review_all: bool = False

    # --- modes ------------------------------------------------------------
    offline: bool = False
    """No API calls at all -- opponents use their deterministic local policies."""
    demo_hands: int = 0
    """Autoplay N hands with no keyboard: the hero is played by a local policy.

    Makes the whole loop verifiable without a tty, which is how the app is
    smoke-tested in CI and how the rendering is checked end to end.
    """
    spoil_personas: bool = False
    """Dev only.  Reveals persona and model tier in the UI."""
    debug_hud: bool = False

    # --- persistence ------------------------------------------------------
    log_dir: Path = field(default=Path.home() / ".poker-trainer")
    resume_window_hours: int = 12

    @property
    def starting_stack(self) -> int:
        return self.buy_in

    @property
    def stack_bb(self) -> int:
        return self.buy_in // self.big_blind

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            offline=_env_flag("POKER_OFFLINE"),
            spoil_personas=_env_flag("POKER_SPOIL"),
            debug_hud=_env_flag("POKER_DEBUG"),
            review_all=_env_flag("POKER_REVIEW_ALL"),
            review_enabled=not _env_flag("POKER_NO_REVIEW"),
            log_dir=Path(os.environ.get("POKER_LOG_DIR",
                                        str(Path.home() / ".poker-trainer"))),
            refresh_per_second=_env_int("POKER_FPS", 20),
            pace=_env_float("POKER_PACE", 1.0),
        )


CFG = Config.from_env()
