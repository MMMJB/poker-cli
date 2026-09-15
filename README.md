# Poker Trainer

A terminal No-Limit Hold'em trainer. Fixed **$1/$3 cash, $300 (100BB) buy-in, 6-handed** — you
plus five opponents, each driven by a Claude agent following a realistic low-stakes persona.

The personas are never revealed to you. Reading them is the exercise.

```
╭──────────────────────────────────────────────────────────────────────────────────────────────────╮
│  $1/$3 NL Hold'em · 6-max                                       Hand #1    Session +$0 (0 hands) │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                       ╭──────────────────╮                                       │
│                                       │ Deb         $297 │                                       │
│                                       │ ▒▒ ▒▒        UTG │                                       │
│                                       ╰──────────────────╯                                       │
│                         ╭──────────────────────────────────────────────╮                         │
│ ╭──────────────────╮   ╭╯                                              ╰╮   ╭──────────────────╮ │
│ │ Sofia       $297 │   │                                                │   │ Tank        $286 │ │
│ │ ▒▒ ▒▒         BB │   │                                         $11    │   │ ▒▒ ▒▒         HJ │ │
│ ╰──────────────────╯   │                            ░░░░░ ░░░░░         │   ╰──────────────────╯ │
│                        │           2♠    J♥    7♥   ░░░░░ ░░░░░         │                        │
│                        │                            ░░░░░ ░░░░░         │                        │
│                        │                FLOP  ·  POT  $24               │                        │
│ ╭──────────────────╮   │                                                │   ╭──────────────────╮ │
│ │ Raj         $299 │   │                                                │   │ Walter      $300 │ │
│ │ --  --        SB │   ╰╮                                              ╭╯   │ --  --        CO │ │
│ ╰─┤ FOLDED ├───────╯    ╰──────────────────────────────────────────────╯    ╰─┤ FOLDED ├───────╯ │
│                                       ╔══════════════════╗                                       │
│                                       ║ YOU         $297 ║                                       │
│                                       ║ K♦  5♣       BTN ║                                       │
│                                       ╚══════════════════╝                                       │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ YOUR ACTION  ·  pot $24  ·  to call $11            [f]old   [c]all $11   [r]aise   [a]ll-in $297 │
│ raise to ▸█  min 22  max 297                 [h] ½pot 29   [t] ¾pot 37   [p] pot 46   [esc] back │
╰──────────────────────────────────────────────────────────────────────────────────────────────────╯
```

## Install

```bash
./install.sh
```

Creates the virtualenv, installs the dependencies, and links `poker` into `~/.local/bin` so
you can run it from anywhere. It adds that directory to your `PATH` only if it isn't there
already, and it's safe to re-run — it updates in place and never adds a duplicate `PATH` line.

On another machine, once this is on GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/MMMJB/poker-cli/main/install.sh | bash
```

That clones to `~/.poker-trainer-app` and does the same thing. Re-running it pulls and
updates. Point it somewhere else with `POKER_REPO=...`, `POKER_HOME=...`, or `POKER_BIN=...`.

Undo everything with `./install.sh --uninstall` — it removes the launcher and the `PATH` line,
and leaves your checkout and hand histories alone.

## Running it

```bash
export ANTHROPIC_API_KEY=sk-ant-...
poker
```

No key, or just want to see it work? The opponents fall back to local policies and the whole
game runs at **zero cost**:

```bash
poker --offline
```

Without installing, `.venv/bin/python -m poker` does the same thing.

| Flag | Effect |
|---|---|
| `--offline` | No API calls. Deterministic local opponents. |
| `--no-review` | Turn off the post-hand review — by far the costliest call per hand. |
| `--demo N` | Autoplay N hands with no keyboard — how the app is smoke-tested. |
| `--fast` | Drop the think-time pacing between actions. |
| `--debug` | Show latency, token, cache-hit and cost instrumentation. |
| `--review-all` | Review every hand, including preflop folds. |

**Controls.** `f` fold · `c` call · `k` check · `r` raise · `a` all-in · `v` toggle reviews · `q` quit.
In the raise prompt: type an amount, or `h` ½-pot, `t` ¾-pot, `p` pot; `enter` commits,
`esc` goes back. The prompt is built from the engine's legal actions, so it cannot submit
something the rules would reject.

Needs at least an 80×24 terminal; it uses a roomier layout at 100×32 and above.

## The opponents

Five archetypes you actually meet in a $1/$3 game. They are assigned to seats randomly each
session and are **never labelled in the UI**. The contrast is the point — against the same
flop c-bet one calls, one folds, one raises, one folds unless it has it, and one thinks about
what your betting says.

Model tier is matched to what each persona's decisions actually require: three run on
`claude-haiku-4-5` (their strategy is a rule list — a stronger model just drifts toward playing
*well*, which defeats the exercise), one on `claude-sonnet-5`, and the one opponent that has to
genuinely hand-read runs on `claude-opus-5`.

Because a persona's tendencies are stated as frequencies, each decision carries a private
0–99 roll drawn from the hand seed. That makes "3-bets about 18% of the time" executable and
reproducible, and it replaces the sampling temperature the current API no longer accepts.

**Information symmetry is a hard rule.** An opponent sees exactly what you see — the public
event record — plus its own two hole cards. No hole cards you can't see, no mucked cards, no
computed statistics you weren't given either. This is enforced structurally: `HandLog.view_for(seat)`
is the only path by which an agent receives history, and it is pinned by tests
(`tests/test_information_symmetry.py`).

## The post-hand review

Optional, and on by default. Turn it off with `--no-review`, `POKER_NO_REVIEW=1`, or by
pressing `v` at any point during a hand — the header shows `review off` while it is disabled.
It is the most expensive thing the app does, so it is meant to be easy to switch off.

After any hand where you invested more than a blind, you get a verdict, a one-line headline,
up to three notes, and one concrete alternative. It is produced in **two stages**:

1. **Analysis** — `claude-fable-5-1` reads the hand and writes a technically precise
   assessment, using ranges, equity, blockers and SPR wherever they sharpen the point.
2. **Translation** — `claude-opus-4-8` rewrites that in plain language, keeping the numbers
   you can act on and dropping the jargon. It never re-analyses or changes the verdict.

The split exists so neither job compromises the other: the analysis is never simplified to
stay readable, and the prose is never technical just because the analysis was. If the
translator fails you get the analyst's own words — denser, but correct. If the analyst fails
you get a mechanical local summary. The panel always renders something.

Two API details this relies on: Fable always thinks (so the `thinking` parameter is omitted
entirely — sending it is a 400 — and thinking tokens count against `max_tokens`), and its
safety classifiers can decline a request, so **server-side fallbacks are enabled** and a
decline is rescued inside the same call rather than costing you the review.

It may tell you anything you could have observed at the table — positions, sizings, the board,
cards shown at showdown, what someone did *in this hand*. It may never reveal a persona, any
claim about a player's general style, or a card that was not shown. That is enforced by data
flow first: the review's context is built only from public events, the field holding the
personas is never read, and the translator is handed nothing but the analyst's JSON — so it
cannot reintroduce anything. A prompt rule, a scrubber applied after *both* stages, and a
deterministic offline summary sit behind that.

## Layout

```
poker/
├── engine/   pure poker logic — no I/O, no async, no LLM, integer dollars only
├── agents/   personas, prompts, the API client, coercion and local fallbacks
├── ui/       character canvas, card and seat drawing, the table renderable
└── app/      the async loop, view building, input, review, persistence
```

The engine is the foundation and is tested hardest: the evaluator is validated against a
brute-force oracle and the full 2,598,960-hand category census, and the betting state machine
is driven by a fuzz harness that checks chip conservation and every other invariant after
*each individual action* across 40,000 randomized hands.

## Sessions

Stacks carry over, and a session under 12 hours old resumes automatically. Everything lands in
`~/.poker-trainer/sessions/<timestamp>/`:

- `session.json` — stacks, button, seed, P/L. Written atomically after every hand.
- `hands.jsonl` — one complete hand per line: the full event log, all hole cards including
  mucks, every action tagged with its source (`model`, `coerced`, `reask`, `fallback`,
  `forced`, `human`), per-decision latency and cost. For study and debugging — not read by
  the UI or the coach.

Opponents reload below 20BB rather than always topping up to exactly 100BB, which keeps the
short- and deep-stack spots a trainer should be teaching.

## Tests

```bash
.venv/bin/python -m pytest          # fast suite
.venv/bin/python -m pytest -m slow  # exhaustive evaluator census + 40k-hand fuzz
```

`tools/preview_table.py` and `tools/preview_live_frame.py` render frames without running the
app, for working on the layout.

`bin/poker` is the launcher. It resolves itself through symlinks and sets `PYTHONPATH`, so a
plain `git pull` picks up changes with no reinstall.

## Cost

Two separate bills.

**The table** — roughly **$3 per 100 hands** with the default tier mix. Dropping the strongest
opponent to the mid tier roughly halves that, at the cost of the one player worth beating.

**The review** — the expensive half, because Fable is an order of magnitude pricier per token
than the seats and it always thinks. Expect roughly **$5–8 per 100 hands** on top, given it
fires on a bit under half of them. `--no-review` removes it entirely, `v` toggles it
mid-session, and `--debug` shows the running total either way.

So: about **$8–11 per 100 hands** with everything on, or **$3** with reviews off. Playing
`--offline` costs nothing at all.
