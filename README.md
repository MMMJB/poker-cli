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

Put your key in a `.env` beside the project:

```bash
cp .env.example .env    # then edit it
poker
```

If the top-right badge says `OFFLINE`, it names the cause — `no API credit`, `bad API key`,
`no connection`. The full message is repeated when you quit. Live play is checked once at
startup, so after fixing the cause you need to restart.

`.env` is gitignored and never committed. Anything already exported in your shell wins over
it, so `ANTHROPIC_API_KEY=... poker` still overrides for a single run. A `.env` in
`~/.poker-trainer/` works too, which is handy when the checkout lives somewhere transient.

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
| `--seats N` | Seats at the opening table (4-8). |
| `--hands-per-table N` | Move tables after N hands; `0` stays put. |
| `--slow` / `--pace N` | Slow the action down (`--pace 2` = half speed, `0.5` = double). |
| `--fast` | Drop the pacing entirely. |
| `--debug` | Show latency, token, cache-hit and cost instrumentation. |
| `--review-all` | Review every hand, including preflop folds. |

**Controls.** You type your action on a command line and press `enter`. **Nothing
auto-submits** — a stray keypress can never fold your hand, and you can edit or cancel
anything before it takes effect.

```
› raise pot   ↵ raise to $46
```

| Type | Does |
|---|---|
| `f` / `fold`, `k` / `check`, `c` / `call` | the obvious |
| `raise 50`, `r 50`, `raise to 50`, or just `50` | raise to that total |
| `bet 20` when there's no bet outstanding | bet |
| `r half`, `r 3/4`, `r pot`, `r min`, `r max` | pot-relative sizing |
| `a` / `all-in` / `shove` | shove |
| `?` help · `v` toggle reviews · `q` quit |  |

**You can type out of turn.** While the table is still acting, whatever you type is captured as
a greyed-out draft — prepare your fold, or size a raise, before the action reaches you. It
carries into the live prompt when your turn comes and *still* has to be committed with Enter;
preparing a move is not making one. If the spot changed while you were typing, the line tells
you right away rather than waiting for you to press Enter.

Full line editing: arrow keys, `home`/`end`, `ctrl-u` clear, `ctrl-w` delete word,
`ctrl-k` kill to end, `↑`/`↓` for history. `esc` clears the line.

As you type, the right-hand side previews exactly what `enter` will do, or tells you why it
won't be accepted — the whole thing is built from the engine's legal actions, so it can never
submit something the rules would reject.

The board is centred in the terminal. Needs at least 80×24; it uses a roomier layout at
100×32 and above.

**At showdown**, when a short stack is all in the pot splits and there can be several winners
who are not splitting anything. Badges say `MAIN` and `SIDE` in that case, so it doesn't read
as a chop, and the log prints the pot breakdown.

**Between hands**, nothing is dealt until you ask for it — `enter` for the next hand, `r` to
replay it, `t` for a new table, `v` to toggle reviews, `q` to quit. The review, if there is one, stays on screen while you decide.

**Pacing.** Each opponent holds the screen for a moment before acting and again after, so a
hand reads as a sequence of decisions rather than a blur. Offline play stretches the
*think-time* further (`offline_pace`, 1.6×): what offline is missing is request latency, and
that only ever filled the think-time floor — the fixed beats like the showdown hold were
tuned for how long a human needs to read them and don't depend on where the decision came
from. Roughly 36s a hand offline at the default, 22s at `--pace 0.6`, 62s at `--slow`.

## Tables

Tables are **4 to 8 handed**, weighted toward the bigger ones — mean about 6.7, which is how a
real room looks: short games exist, full ones are the norm. You move to a new table every
**100 hands** by default, or whenever you press `t` between hands.

A move brings an entirely new cast: different archetypes, different names, fresh stacks. Your
own chips and P/L come with you, so the session reads as one continuous bankroll.

```bash
poker --seats 8              # open at an eight-handed table
poker --hands-per-table 25   # move more often
poker --hands-per-table 0    # stay put
```

Each archetype has a pool of nameplates and draws a fresh one per table, and names from the
table you just left are avoided. The same archetype under a new name is deliberate: the
exercise is reading how someone plays, not recognising who they were an hour ago.

## The opponents

Ten archetypes you actually meet in a $1/$3 game — a calling station, a nit, a maniac, an ABC
reg, a thinking TAG, a young online LAG, a recreational tourist, a short-stacker, an
old-school limper, and a tricky trapper. Each table draws from the pool and is **never
labelled in the UI**.

The mix is shaped rather than purely random: at least one opponent genuinely worth beating so
there is something to learn, and never more than two, because a $1/$3 table does not have five
regulars on it and the strong tiers are the expensive ones. The contrast is the point — against the same
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

## Revisiting hands

Every hand is replayable, because the log stores the RNG seed rather than just a description of
what happened. Replaying the recorded actions through a freshly seeded engine reproduces the
identical deal, so a replay is real engine state drawn by the normal renderer — and a
determinism check every time you open one.

```bash
poker hands        # list recent hands, newest last
poker hands 50     # …the last 50
poker hand 42      # step through hand 42
```

Pressing `r` between hands replays the one you just finished without leaving the game.

In a replay every hand is face up, folds included — it's your own history, there's nothing
left to hide. `←`/`→` step, `s` jumps to the end, `a` back to the deal, `q` leaves.

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
