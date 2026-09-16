"""Entry point: ``python -m poker``."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys

from rich.console import Console

from poker.config import CFG, Config
from poker.env import has_credentials, load_env
from poker.ui.theme import THEME


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="poker",
        description="A $1/$3 No-Limit Hold'em trainer with LLM-driven opponents.",
    )
    p.add_argument("command", nargs="*", metavar="COMMAND",
                   help="'hands [N]' to list past hands, "
                        "'hand N' to replay one; omit to play")
    p.add_argument("--offline", action="store_true",
                   help="play against deterministic local opponents (no API calls, $0)")
    p.add_argument("--debug", action="store_true",
                   help="show latency, token and cost instrumentation")
    p.add_argument("--no-review", action="store_true",
                   help="turn off the post-hand review (it is the costliest call)")
    p.add_argument("--review-all", action="store_true",
                   help="review every hand, including preflop folds")
    p.add_argument("--demo", type=int, default=0, metavar="N",
                   help="autoplay N hands with no keyboard (for smoke-testing)")
    p.add_argument("--pace", type=float, default=None, metavar="N",
                   help="scale every delay (2 = half speed, 0.5 = double speed)")
    p.add_argument("--slow", action="store_true",
                   help="slow the action down further (same as --pace 1.6)")
    p.add_argument("--fast", action="store_true",
                   help="remove the think-time pacing between actions")
    p.add_argument("--fps", type=int, default=None, help="render refresh rate")
    return p.parse_args(argv)


def build_config(args: argparse.Namespace) -> Config:
    overrides: dict = {}
    if args.offline:
        overrides["offline"] = True
    if args.debug:
        overrides["debug_hud"] = True
    if args.no_review:
        overrides["review_enabled"] = False
    if args.review_all:
        overrides["review_all"] = True
    if args.demo:
        overrides["demo_hands"] = args.demo
    # Later flags win, so --pace always overrides the presets.
    if args.fast:
        overrides["pace"] = 0.0
    if args.slow:
        overrides["pace"] = 1.6
    if args.pace is not None:
        overrides["pace"] = max(0.0, args.pace)
    if args.fps:
        overrides["refresh_per_second"] = args.fps
    return dataclasses.replace(CFG, **overrides) if overrides else CFG


def list_hands(config: Config, console: Console, limit: int = 20) -> int:
    from poker.app import replay

    hands = replay.all_hands(config)
    if not hands:
        console.print(f"[subtle]No hands recorded yet in {config.log_dir}.[/]")
        return 0

    console.print(f"[title]Last {min(limit, len(hands))} of "
                  f"{len(hands)} hands[/]  [subtle]({config.log_dir})[/]\n")
    for hand in hands[-limit:]:
        net = hand.net
        style = "seat.stack" if net > 0 else ("prompt.error" if net < 0 else "subtle")
        console.print(f"  [{style}]{replay.summarize(hand)}[/]")
    console.print("\n[subtle]Replay one with:[/] [action.key]poker hand <number>[/]")
    return 0


def replay_one(config: Config, console: Console, raw_id: str) -> int:
    from poker.app import replay
    from poker.app.replay_view import replay_hand

    try:
        hand_id = int(raw_id)
    except ValueError:
        console.print(f"[prompt.error]{raw_id!r} is not a hand number.[/]")
        return 2

    stored = replay.find(config, hand_id)
    if stored is None:
        console.print(f"[prompt.error]No hand #{hand_id} on record.[/]\n"
                      "[subtle]Run[/] [action.key]poker hands[/] "
                      "[subtle]to see what is there.[/]")
        return 2

    problem = replay.verify(stored)
    if problem:
        # The seed makes the deal reproducible, so this means the engine no
        # longer plays the hand the way it did when it was recorded.
        console.print(f"[prompt.error]Hand #{hand_id} does not rebuild "
                      f"cleanly:[/] {problem}")
        return 1

    replay_hand(stored, config, console)
    return 0


def main(argv: list[str] | None = None) -> int:
    # Before anything reads the environment: a local .env is how the key is
    # kept off the command line and out of the repo.
    load_env()

    args = parse_args(argv)
    config = build_config(args)
    console = Console(theme=THEME)

    command = list(args.command)
    if command:
        verb = command[0].lower()
        if verb in ("hands", "list", "history"):
            limit = 20
            if len(command) > 1 and command[1].isdigit():
                limit = int(command[1])
            return list_hands(config, console, limit)
        if verb in ("hand", "replay", "review"):
            if len(command) < 2:
                console.print("[prompt.error]Which hand?[/]  "
                              "[action.key]poker hand 42[/]")
                return 2
            return replay_one(config, console, command[1])
        console.print(f"[prompt.error]Unknown command {verb!r}.[/]  "
                      "[subtle]Try[/] [action.key]poker hands[/].")
        return 2

    if not config.offline and not has_credentials():
        console.print(
            "[prompt.error]No Anthropic credentials found.[/]\n"
            "Put [action.key]ANTHROPIC_API_KEY=sk-ant-...[/] in a "
            "[action.key].env[/] file next to the project, export it in your "
            "shell, or run\n"
            "  [action.key]poker --offline[/]\n"
            "to play against the local opponents at no cost."
        )
        return 2

    from poker.app.loop import App

    app = App(config, console)
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        # KeyReader restores termios in its __aexit__, including on exception.
        console.print("\n[subtle]Interrupted.[/]")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
