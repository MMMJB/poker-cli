"""Session persistence.

Hand histories are the point of a trainer: a replayer, "show me every hand
where I 3-bet from the CO", a session leak report -- none of those can be
retrofitted if the data was not captured from hand one.  A few hundred KB per
session is a cheap option to hold.

``session.json`` is written atomically (temp file + ``os.replace``) after every
hand, so a crash loses at most the hand in progress.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from poker.config import Config
from poker.engine.cards import cards_str
from poker.engine.table import Table


@dataclass
class HandRecord:
    hand_id: int
    button: int
    hero_seat: int
    names: dict[int, str]
    events: list[dict]
    """God view, mucked cards included -- for study and debugging only."""
    public_events: list[dict]
    """Exactly what the human saw.  This is what the coach is built from."""
    decisions: list[dict]
    personas: dict[int, str]
    """Logged here for debugging.  Never read by the UI or the coach."""
    result: Any = None
    stacks_before: dict[int, int] = field(default_factory=dict)

    def to_json(self) -> dict:
        r = self.result
        return {
            "hand_id": self.hand_id,
            "button": self.button,
            "hero_seat": self.hero_seat,
            "names": {str(k): v for k, v in self.names.items()},
            "board": cards_str(r.board) if r else "",
            "stacks_before": {str(k): v for k, v in self.stacks_before.items()},
            "stacks_after": {str(k): v for k, v in (r.stacks_after.items() if r else [])},
            "net": {str(k): v for k, v in (r.net.items() if r else [])},
            "hole_cards": {
                str(k): cards_str(v) for k, v in (r.hole_cards.items() if r else [])
            },
            "shown": sorted(r.shown) if r else [],
            "went_to_showdown": bool(r.went_to_showdown) if r else False,
            "pots": [
                {"amount": p.amount, "eligible": sorted(p.eligible)}
                for p in (r.pots if r else [])
            ],
            "awards": [
                {"pot": a.pot_index, "seat": a.seat, "amount": a.amount,
                 "reason": a.reason.value}
                for a in (r.awards if r else [])
            ],
            "personas": {str(k): v for k, v in self.personas.items()},
            "decisions": self.decisions,
            "events": self.events,
        }

    @property
    def hero_won(self) -> int:
        if not self.result:
            return 0
        return sum(a.amount for a in self.result.awards if a.seat == self.hero_seat)

    @property
    def hero_invested(self) -> int:
        """Chips the hero put in this hand.

        ``net = won - invested``, so ``invested = won - net``.
        """
        if not self.result:
            return 0
        return self.hero_won - self.result.net.get(self.hero_seat, 0)


@dataclass
class ResumedSession:
    session_seed: int
    stacks: list[int]
    button: int
    hand_number: int
    bought_in: dict[int, int]
    hands_played: int
    table_number: int = 0


class SessionStore:
    def __init__(self, config: Config) -> None:
        self.cfg = config
        self.root = Path(config.log_dir) / "sessions"
        stamp = datetime.now().strftime("%Y-%m-%dT%H%M")
        self.directory = self.root / f"{stamp}-{os.getpid():05d}"
        self._hands_path = self.directory / "hands.jsonl"
        self._state_path = self.directory / "session.json"
        self._ready = False

    # ------------------------------------------------------------------ load

    def load(self) -> ResumedSession | None:
        """Resume the most recent session, if it is recent enough to matter."""
        if not self.root.exists():
            return None
        candidates = sorted(
            (p for p in self.root.iterdir() if (p / "session.json").exists()),
            key=lambda p: (p / "session.json").stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return None
        newest = candidates[0]
        age = time.time() - (newest / "session.json").stat().st_mtime
        if age > self.cfg.resume_window_hours * 3600:
            return None
        try:
            data = json.loads((newest / "session.json").read_text())
        except (OSError, json.JSONDecodeError):
            return None

        self.directory = newest
        self._hands_path = newest / "hands.jsonl"
        self._state_path = newest / "session.json"
        self._ready = True
        return ResumedSession(
            session_seed=int(data["session_seed"]),
            stacks=[int(x) for x in data["stacks"]],
            button=int(data["button"]),
            hand_number=int(data["hand_number"]),
            bought_in={int(k): int(v) for k, v in data.get("bought_in", {}).items()},
            hands_played=int(data.get("hands_played", 0)),
            table_number=int(data.get("table_number", 0)),
        )

    # ----------------------------------------------------------------- write

    def _ensure(self) -> None:
        if self._ready:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / "errors").mkdir(exist_ok=True)
        self._ready = True

    def append_hand(self, record: HandRecord) -> None:
        self._ensure()
        try:
            with self._hands_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record.to_json(), sort_keys=True) + "\n")
        except OSError:
            pass  # a logging failure must never interrupt play

    def save(self, table: Table, table_number: int = 0) -> None:
        self._ensure()
        payload = {
            "session_seed": table.session_seed,
            "stacks": [s.stack for s in table.seats],
            "button": table.button,
            "hand_number": table.hand_number,
            "bought_in": {str(s.seat): s.total_bought_in for s in table.seats},
            "hands_played": table.human.hands_played,
            "table_number": table_number,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        tmp = self._state_path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(tmp, self._state_path)  # atomic
        except OSError:
            pass

    def dump_error(self, name: str, detail: str) -> None:
        self._ensure()
        path = self.directory / "errors" / f"{name}-{int(time.time())}.txt"
        try:
            path.write_text(detail, encoding="utf-8")
        except OSError:
            pass
