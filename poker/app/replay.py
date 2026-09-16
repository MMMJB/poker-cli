"""Reading hands back off disk and reconstructing them.

Every hand is logged with its RNG seed, so a hand is not merely *described* by
the log -- it can be rebuilt.  Replaying the recorded actions through a fresh
engine seeded the same way reproduces the identical deal, which means the
normal renderer can draw a replay with no special cases.

It also means a replay is a live determinism check: if a rebuilt board differs
from the logged one, the engine has drifted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from poker.config import Config
from poker.engine.actions import Action
from poker.engine.cards import cards_str
from poker.engine.hand import HandEngine
from poker.engine.state import GameConfig
from poker.engine.table import Table


class ReplayError(Exception):
    """The hand on disk could not be rebuilt."""


@dataclass(frozen=True, slots=True)
class StoredHand:
    hand_id: int
    session: Path
    data: dict

    @property
    def names(self) -> dict[int, str]:
        return {int(k): v for k, v in self.data["names"].items()}

    @property
    def hero_seat(self) -> int:
        return int(self.data["hero_seat"])

    @property
    def net(self) -> int:
        return int(self.data["net"].get(str(self.hero_seat), 0))

    @property
    def board(self) -> str:
        return self.data.get("board", "")

    @property
    def hole(self) -> str:
        return self.data["hole_cards"].get(str(self.hero_seat), "")

    @property
    def went_to_showdown(self) -> bool:
        return bool(self.data.get("went_to_showdown"))

    @property
    def pot(self) -> int:
        return sum(p["amount"] for p in self.data.get("pots", []))


# --------------------------------------------------------------------------
# Finding hands on disk
# --------------------------------------------------------------------------

def sessions(config: Config) -> list[Path]:
    """Session directories, newest first."""
    root = Path(config.log_dir) / "sessions"
    if not root.is_dir():
        return []
    found = [p for p in root.iterdir() if (p / "hands.jsonl").is_file()]
    return sorted(found, key=lambda p: (p / "hands.jsonl").stat().st_mtime,
                  reverse=True)


def read_session(path: Path) -> list[StoredHand]:
    out: list[StoredHand] = []
    try:
        text = (path / "hands.jsonl").read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn final line from a crash should not stop a replay
        out.append(StoredHand(int(data["hand_id"]), path, data))
    return out


def all_hands(config: Config, session: Path | None = None) -> list[StoredHand]:
    """Every stored hand, oldest first, across sessions unless one is named."""
    paths = [session] if session is not None else sessions(config)
    hands: list[StoredHand] = []
    for path in reversed(paths):       # oldest session first
        hands.extend(read_session(path))
    return hands


def find(config: Config, hand_id: int,
         session: Path | None = None) -> StoredHand | None:
    """The most recent hand with this id."""
    matches = [h for h in all_hands(config, session) if h.hand_id == hand_id]
    return matches[-1] if matches else None


# --------------------------------------------------------------------------
# Summaries
# --------------------------------------------------------------------------

def summarize(hand: StoredHand) -> str:
    """One line, for picking a hand out of a list."""
    net = hand.net
    money = f"{'+' if net >= 0 else '-'}${abs(net):,}"
    hole = hand.hole or "--"
    board = hand.board or "(no flop)"
    ending = "showdown" if hand.went_to_showdown else "no showdown"
    return (f"#{hand.hand_id:<5} {money:>9}   {hole:<7} on {board:<16} "
            f"pot ${hand.pot:<7,} {ending}")


# --------------------------------------------------------------------------
# Rebuilding
# --------------------------------------------------------------------------

def recorded_actions(hand: StoredHand) -> list[Action]:
    """The actions taken, in order, as engine actions."""
    out: list[Action] = []
    for event in hand.data.get("events", []):
        if event.get("type") != "action_taken":
            continue
        kind = event.get("action")
        if kind == "fold":
            out.append(Action.fold())
        elif kind == "check":
            out.append(Action.check())
        elif kind == "call":
            out.append(Action.call())
        elif kind == "bet":
            out.append(Action.bet(int(event["to_amount"])))
        elif kind == "raise":
            out.append(Action.raise_to(int(event["to_amount"])))
        else:  # pragma: no cover - unknown action type in an old log
            raise ReplayError(f"unrecognised action {kind!r} in hand "
                              f"{hand.hand_id}")
    return out


def _start_event(hand: StoredHand) -> dict:
    for event in hand.data.get("events", []):
        if event.get("type") == "hand_started":
            return event
    raise ReplayError(f"hand {hand.hand_id} has no start event")


def table_for(hand: StoredHand) -> Table:
    """A Table matching the hand, so the normal view builder can be reused."""
    start = _start_event(hand)
    seats = sorted(start["seats"], key=lambda s: s["seat"])
    game = GameConfig(
        small_blind=int(start.get("small_blind", 1)),
        big_blind=int(start.get("big_blind", 3)),
        num_seats=len(seats),
    )
    return Table(
        game,
        [s["name"] for s in seats],
        human_seat=hand.hero_seat,
        session_seed=int(start.get("seed", 0)),
        stacks=[int(s["stack"]) for s in seats],
        button=int(start["button"]),
    )


def rebuild(hand: StoredHand, upto: int | None = None) -> HandEngine:
    """Replay the hand to ``upto`` actions (all of them when None).

    Re-runs from the start each time rather than snapshotting state.  A hand is
    at most a few dozen actions and the engine is fast, so this is far simpler
    than deep-copying mutable state and cannot drift from it.
    """
    start = _start_event(hand)
    seats = sorted(start["seats"], key=lambda s: s["seat"])
    game = GameConfig(
        small_blind=int(start.get("small_blind", 1)),
        big_blind=int(start.get("big_blind", 3)),
        num_seats=len(seats),
    )
    engine = HandEngine(
        config=game,
        names=[s["name"] for s in seats],
        stacks=[int(s["stack"]) for s in seats],
        button=int(start["button"]),
        hand_id=hand.hand_id,
        seed=int(start["seed"]),
    )
    engine.start()

    actions = recorded_actions(hand)
    limit = len(actions) if upto is None else max(0, min(upto, len(actions)))
    for action in actions[:limit]:
        if engine.is_complete():
            break
        engine.apply(action)
    return engine


def verify(hand: StoredHand) -> str | None:
    """Rebuild fully and compare against the log.  Returns a mismatch, or None.

    The seed makes the deal reproducible, so a difference here means the engine
    no longer plays the hand the way it did when it was recorded.
    """
    try:
        engine = rebuild(hand)
    except Exception as exc:  # pragma: no cover - corrupt log
        return f"could not rebuild: {exc}"

    if not engine.is_complete():
        return "replay did not reach the end of the hand"

    board = cards_str(engine.state.board)
    if board != hand.board:
        return f"board differs: rebuilt {board!r}, logged {hand.board!r}"

    result = engine.result()
    for seat, stack in result.stacks_after.items():
        logged = int(hand.data["stacks_after"].get(str(seat), stack))
        if stack != logged:
            return (f"seat {seat} ends with {stack}, log says {logged}")
    return None


def steps(hand: StoredHand) -> Iterator[HandEngine]:
    """Every state of the hand, from the deal to the end."""
    for index in range(len(recorded_actions(hand)) + 1):
        yield rebuild(hand, index)
