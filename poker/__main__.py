"""Entry point: ``python -m poker``."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import sys

from rich.console import Console

from poker.config import CFG, Config
from poker.ui.theme import THEME


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="poker",
        description="A $1/$3 No-Limit Hold'em trainer with LLM-driven opponents.",
    )
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = build_config(args)
    console = Console(theme=THEME)

    if not config.offline:
        import os

        if not any(os.environ.get(k) for k in
                   ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")):
            console.print(
                "[prompt.error]No Anthropic credentials found.[/]\n"
                "Set [action.key]ANTHROPIC_API_KEY[/] and try again, or run\n"
                "  [action.key]python -m poker --offline[/]\n"
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
