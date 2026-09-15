"""The post-hand coach.

Fires the instant a hand ends, concurrently with the showdown animation, so its
latency is hidden -- the one place a slower, more expensive model costs nothing.

It runs in two stages.  A strong **analyst** reads the hand and writes a
technically precise assessment; a second **translator** model rewrites that into
plain language.  Splitting them means the analysis never has to be simplified to
stay readable, and the prose is never technical merely because the analysis was.
If the translator fails, the analyst's own text is shown -- denser, but correct.

**The persona constraint is enforced structurally, not by prompting.**  The
context is built from ``HandRecord.public_events`` only; ``HandRecord.personas``
exists on the same object and this module never reads it.  The translator sees
only the analyst's output, so it cannot reintroduce anything.  The prompt rules,
the scrubber applied after *both* stages, and the deterministic fallback are
defence in depth behind that.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Iterable

from poker.agents.client import AgentClient
from poker.agents.schema import (
    ANALYSIS_SCHEMA, MAX_HEADLINE_CHARS, MAX_NOTE_CHARS, MAX_NOTES,
    REVIEW_SCHEMA,
)
from poker.app.session import HandRecord
from poker.config import Config
from poker.ui.model import ReviewNote, ReviewView

ANALYST_SYSTEM = """\
You are a poker coach reviewing one hand a student has just played in a $1/$3 \
No-Limit Hold'em cash game, 6-max. The student is HERO.

You will be given the complete public history of this single hand: the seats, \
positions and stacks, every action with its sizing, the board, and any hole \
cards that were actually shown at showdown.

Rules:
- Comment ONLY on HERO's decisions. Do not grade the opponents.
- Refer to opponents by position, optionally with their table name: "the CO", \
"Walter in the big blind".
- Describe an opponent ONLY by what they did in THIS hand -- "the CO check-raised \
the turn", "the button has bet every street". Do NOT characterise any player's \
overall style, tendencies, skill level or player type. Do not use labels such as \
nit, maniac, calling station, station, fish, whale, reg, TAG, LAG, or any phrase \
describing what kind of player someone is.
- You may state hole cards ONLY for players who reached showdown and showed. \
Cards that were not shown are not in this transcript. Never guess at or state \
what a player might have held.
- Be technically precise. Your reader is another poker analyst, not the student: \
use range construction, equity, blockers, SPR, pot odds and board texture \
wherever they sharpen the point. A second pass will make this readable.
- At most three findings, most important first.
- If HERO played the hand fine, say so and stop.\
"""


TRANSLATOR_SYSTEM = """\
You are rewriting one poker coach's analysis of a single hand so that an \
improving low-stakes player can act on it.

You will be given a JSON analysis of how a player (HERO) played a hand. Rewrite \
it. Do not re-analyse the hand, do not add new advice, and do not soften or \
change the assessment -- your job is wording, not judgement.

Rules:
- Plain English. Replace jargon with what it means. Say "you'd only get called \
by hands that beat you" rather than "your value range is dominated"; say "there \
were more ways for them to have a straight" rather than "the turn improved their \
range equity".
- Keep any concrete number that the player can act on -- bet sizes, pot sizes, \
the price they were getting. Drop abstract percentages that do not change the \
decision.
- Second person, direct, no preamble, no encouragement padding. One sentence for \
the headline, at most 90 characters. One or two sentences per note, at most 200 \
characters each.
- Keep the same number of notes as there are findings, in the same order, each \
on the same street.
- Keep "verdict" exactly as it was given to you.
- Refer to opponents only as the analysis does -- by position or table name, and \
only by what they did in this hand. Never describe what kind of player anyone is.
- Never mention this analysis, these instructions, or that you are a model.\
"""


class ReviewCoach:
    def __init__(self, client: AgentClient | None, config: Config,
                 denylist: tuple[str, ...]) -> None:
        self.client = client
        self.cfg = config
        self.denylist = denylist
        self.enabled = config.review_enabled
        """Live switch -- the player can turn reviews off mid-session."""
        self.degraded = False
        self._task: asyncio.Task | None = None
        self._record: HandRecord | None = None

    # ------------------------------------------------------------------ flow

    def start(self, record: HandRecord) -> None:
        """Kick the review off now; it runs behind the showdown animation."""
        self._record = record
        self._task = None
        if not self._should_review(record):
            return
        if self.client is None or self.cfg.offline or self.degraded:
            return
        self._task = asyncio.create_task(self._run(record))

    def pending(self) -> bool:
        """True when a review is in flight for the current hand."""
        return self._task is not None and not self._task.done()

    async def result(self) -> ReviewView | None:
        record = self._record
        if record is None:
            return None
        if not self._should_review(record):
            return None
        if self._task is None:
            return self._deterministic(record)
        try:
            return await self._task
        except Exception:
            return self._deterministic(record)

    def _should_review(self, record: HandRecord) -> bool:
        """Skip the ~55% of hands where the hero folded preflop for a blind.

        This halves review cost and, more importantly, stops review fatigue
        from making the student ignore the ones that matter.
        """
        if not self.enabled:
            return False
        if self.cfg.review_all:
            return True
        threshold = self.cfg.big_blind * self.cfg.review_min_investment_bb
        return record.hero_invested > threshold

    # --------------------------------------------------------------- request

    async def _run(self, record: HandRecord) -> ReviewView:
        assert self.client is not None

        analysis = await self._analyse(record)
        if analysis is None:
            return self._deterministic(record)

        translated = await self._translate(analysis)
        payload = translated if translated is not None else _flatten(analysis)

        view = self._to_view(payload, record)
        if view is None and translated is not None:
            # The translation leaked; the analyst's own words may still be clean.
            view = self._to_view(_flatten(analysis), record)
        return view if view is not None else self._deterministic(record)

    async def _analyse(self, record: HandRecord) -> dict | None:
        """Stage one: the technical read of the hand."""
        assert self.client is not None
        try:
            payload = await self.client.analyse(
                model=self.cfg.analyst_model,
                effort=self.cfg.analyst_effort,
                max_tokens=self.cfg.analyst_max_tokens,
                timeout_s=self.cfg.analyst_timeout_s,
                system_prompt=ANALYST_SYSTEM,
                context=render_review_context(record),
                schema=ANALYSIS_SCHEMA,
            )
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    async def _translate(self, analysis: dict) -> dict | None:
        """Stage two.  Sees only the analysis -- never the hand, never a persona."""
        assert self.client is not None
        try:
            return await self.client.translate(
                model=self.cfg.translator_model,
                effort=self.cfg.translator_effort,
                max_tokens=self.cfg.translator_max_tokens,
                timeout_s=self.cfg.translator_timeout_s,
                system_prompt=TRANSLATOR_SYSTEM,
                content=json.dumps(analysis, indent=2),
                schema=REVIEW_SCHEMA,
            )
        except Exception:
            return None  # fall back to the analyst's own wording

    def _to_view(self, payload: dict, record: HandRecord) -> ReviewView | None:
        headline = _scrub(str(payload.get("headline", "")), self.denylist)
        if headline is None:
            return None  # the headline itself leaked -- fall back

        notes: list[ReviewNote] = []
        for item in (payload.get("notes") or [])[:MAX_NOTES]:
            text = _scrub(str(item.get("text", "")), self.denylist)
            if text is None:
                continue  # drop just the offending note
            notes.append(ReviewNote(
                street=str(item.get("street", "")).lower(),
                text=text[:MAX_NOTE_CHARS],
            ))

        better = _scrub(str(payload.get("better_line", "")), self.denylist) or ""
        verdict = str(payload.get("verdict", "ok")).lower()
        if verdict not in ("good", "ok", "leak", "big_leak"):
            verdict = "ok"

        return ReviewView(
            active=True,
            verdict=verdict,
            headline=headline[:MAX_HEADLINE_CHARS],
            notes=tuple(notes),
            better_line=better,
            hand_id=record.hand_id,
        )

    # ----------------------------------------------------------- last resort

    def _deterministic(self, record: HandRecord) -> ReviewView:
        """A mechanical, leak-free summary.  The panel always renders something."""
        net = record.result.net.get(record.hero_seat, 0) if record.result else 0
        invested = record.hero_invested
        streets = len({d["street"] for d in record.decisions
                       if d["seat"] == record.hero_seat})
        biggest = 0
        for d in record.decisions:
            if d["seat"] != record.hero_seat:
                continue
            match = re.search(r"to (\d+)", d["action"])
            if match:
                biggest = max(biggest, int(match.group(1)))

        outcome = "won" if net > 0 else ("lost" if net < 0 else "broke even")
        headline = (
            f"You invested ${invested} across {streets} street"
            f"{'s' if streets != 1 else ''} and {outcome}."
        )
        notes = []
        if biggest:
            notes.append(ReviewNote(
                street="summary",
                text=f"Your largest single commitment was ${biggest}.",
            ))
        notes.append(ReviewNote(
            street="summary",
            text="Coaching is unavailable for this hand, so this is a plain summary.",
        ))
        return ReviewView(
            active=True,
            verdict="ok",
            headline=headline[:MAX_HEADLINE_CHARS],
            notes=tuple(notes),
            hand_id=record.hand_id,
        )


# --------------------------------------------------------------------------
# Context construction -- public events only
# --------------------------------------------------------------------------

def render_review_context(record: HandRecord) -> str:
    """Build the coach's payload.

    Reads ``record.public_events`` and nothing else that could carry a persona
    or an unshown card.  ``record.personas`` is deliberately never touched --
    that is the actual enforcement of the no-leak rule.
    """
    hero = record.hero_seat
    lines = [
        "HAND REVIEW REQUEST",
        f"Blinds $1/$3, 6-max. HERO is seat {hero} "
        f"({record.names.get(hero, 'Hero')}).",
        "",
        "Seats:",
    ]
    for seat, name in sorted(record.names.items()):
        tag = " (HERO)" if seat == hero else ""
        before = record.stacks_before.get(seat, 0)
        after = record.result.stacks_after.get(seat, 0) if record.result else 0
        lines.append(f"  seat {seat} {name}{tag}: started ${before}, ended ${after}")

    hero_cards = ""
    if record.result:
        hero_cards = _cards_of(record.result.hole_cards.get(hero, ()))
    lines += ["", f"HERO's hole cards: {hero_cards}", "", "What happened:"]

    for ev in record.public_events:
        text = _describe(ev, record)
        if text:
            lines.append(f"  {text}")

    if record.result:
        shown = sorted(record.result.shown)
        if shown:
            lines += ["", "Shown at showdown:"]
            for seat in shown:
                cards = _cards_of(record.result.hole_cards.get(seat, ()))
                lines.append(f"  {record.names.get(seat, seat)}: {cards}")
        else:
            lines += ["", "No cards were shown."]
        lines += [
            "",
            f"Result: HERO {'won' if record.result.net.get(hero, 0) > 0 else 'lost'} "
            f"${abs(record.result.net.get(hero, 0))}.",
        ]

    lines += ["", "Review HERO's decisions."]
    return "\n".join(lines)


def _describe(ev: dict, record: HandRecord) -> str:
    kind = ev.get("type")
    names = record.names
    if kind == "blind_posted":
        return f"{names.get(ev['seat'])} posts {ev['kind']} ${ev['amount']}"
    if kind == "street_dealt":
        return f"-- {ev['street'].upper()}: {ev['new_cards']} (board {ev['board']})"
    if kind == "action_taken":
        who = names.get(ev["seat"], ev["seat"])
        action = ev["action"]
        if action in ("bet", "raise"):
            text = f"{who} ({ev['position']}) {action}s to ${ev['to_amount']}"
        elif action == "call":
            text = f"{who} ({ev['position']}) calls ${ev['amount_added']}"
        elif action == "check":
            text = f"{who} ({ev['position']}) checks"
        else:
            text = f"{who} ({ev['position']}) folds"
        if ev.get("is_all_in"):
            text += " and is all-in"
        return f"{text}   [pot ${ev['pot_after']}]"
    if kind == "cards_revealed":
        return f"{names.get(ev['seat'])} shows {ev['cards']}"
    if kind == "uncalled_bet_returned":
        return f"${ev['amount']} uncalled, returned to {names.get(ev['seat'])}"
    if kind == "pot_awarded":
        return f"{names.get(ev['seat'])} is awarded ${ev['amount']}"
    return ""


def _cards_of(cards) -> str:
    from poker.engine.cards import cards_str

    return cards_str(cards) if cards else "(not shown)"


def _flatten(analysis: dict) -> dict:
    """Shape the analyst's own output like a finished review.

    Used when the translator is unavailable: the wording stays technical, which
    is worse for the student but far better than no review at all.
    """
    return {
        "verdict": analysis.get("verdict", "ok"),
        "headline": analysis.get("summary", ""),
        "notes": [
            {"street": f.get("street", ""), "text": f.get("analysis", "")}
            for f in (analysis.get("findings") or [])
        ],
        "better_line": analysis.get("better_line", ""),
    }


def _scrub(text: str, denylist: Iterable[str]) -> str | None:
    """None means the text leaked and must be dropped."""
    text = (text or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    for word in denylist:
        if not word:
            continue
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return None
    return text
