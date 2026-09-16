"""Turning a model response into a legal action.

Structured outputs guarantee *shape*, never *legality*, so every decision goes
through :func:`coerce` before it reaches the engine.

The rule is: **coerce when the intent is unambiguous and the correction is
monotone; re-ask when the intent is contradictory.**  Clamping a raise up to
the minimum preserves what the model meant.  Checking into a bet does not --
that is a misunderstanding, and silently turning it into a call or a fold would
invent an intent the model never had.
"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from enum import Enum

import anthropic

from poker.agents.client import (
    AgentClient, ModelRefusal, TruncatedOutput, _message_of, is_billing_error,
)
from poker.agents.fallback import decision_rng, local_decision, local_talk
from poker.agents.personas import Seated
from poker.agents.prompts import build_reask, build_system_prompt, render_state
from poker.agents.schema import MAX_TALK_CHARS, RawDecision
from poker.config import TIER_DOWNGRADE, TIERS, Config
from poker.engine.actions import Action, LegalActions
from poker.engine.hand import Observation
from poker.engine.state import Street


class Source(str, Enum):
    MODEL = "model"
    COERCED = "coerced"
    REASK = "reask"
    FALLBACK = "fallback"
    OFFLINE = "offline"
    FORCED = "forced"
    HUMAN = "human"


class Verdict(str, Enum):
    OK = "ok"
    COERCED = "coerced"
    MUST_REASK = "must_reask"


@dataclass(frozen=True, slots=True)
class Decision:
    action: Action
    talk: str = ""
    source: Source = Source.MODEL
    model: str = ""
    latency_ms: int = 0
    note: str = ""
    """Why it was coerced or fell back -- written to the hand log, not the UI."""


# --------------------------------------------------------------------------
# Legality coercion
# --------------------------------------------------------------------------

def coerce(raw: RawDecision, legal: LegalActions) -> tuple[Action | None, Verdict, str]:
    """Map a raw decision onto a legal action, or ask for another."""
    action = raw.action
    amount = raw.amount

    if action == "fold":
        if legal.can_check:
            # Nobody folds for free; treating it as a check keeps the table
            # realistic and costs the model nothing it intended.
            return Action.check(), Verdict.COERCED, "fold with a free check"
        return Action.fold(), Verdict.OK, ""

    if action == "check":
        if legal.can_check:
            return Action.check(), Verdict.OK, ""
        return None, Verdict.MUST_REASK, "checked while facing a bet"

    if action == "call":
        if legal.can_call:
            return Action.call(), Verdict.OK, ""
        if legal.can_check:
            return Action.check(), Verdict.COERCED, "called with nothing to call"
        return None, Verdict.MUST_REASK, "called with no call available"

    if action == "all_in":
        if legal.can_bet or legal.can_raise:
            target = legal.max_to
            verdict = Verdict.OK if amount == target else Verdict.COERCED
            note = "" if verdict is Verdict.OK else "all-in amount ignored"
            build = Action.bet if legal.can_bet else Action.raise_to
            return build(target), verdict, note
        if legal.can_call:
            return Action.call(), Verdict.COERCED, "all-in becomes a call"
        return None, Verdict.MUST_REASK, "all-in with nothing to put in"

    if action in ("bet", "raise"):
        if not (legal.can_bet or legal.can_raise):
            if legal.can_call:
                return Action.call(), Verdict.COERCED, "cannot raise; called instead"
            if legal.can_check:
                return Action.check(), Verdict.COERCED, "cannot bet; checked instead"
            return None, Verdict.MUST_REASK, "aggression with nothing behind"

        note = ""
        target = amount
        if target < legal.min_to:
            note = f"{target} below min {legal.min_to}"
            target = legal.min_to
        elif target > legal.max_to:
            note = f"{target} above max {legal.max_to}"
            target = legal.max_to

        build = Action.bet if legal.can_bet else Action.raise_to
        wanted_bet = action == "bet"
        if wanted_bet is not legal.can_bet and not note:
            note = "bet/raise swapped"
        verdict = Verdict.COERCED if note else Verdict.OK
        return build(target), verdict, note

    return None, Verdict.MUST_REASK, f"unknown action {action!r}"


def clean_talk(text: str, persona: Seated, rng: random.Random,
               denylist: tuple[str, ...]) -> str:
    """Trim, filter, and apply the persona's talk rate."""
    text = (text or "").strip().strip('"')
    if not text:
        return ""
    lowered = text.lower()
    if any(word in lowered for word in denylist):
        return ""
    if len(text) > MAX_TALK_CHARS:
        text = text[:MAX_TALK_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    # Rolled client-side: cheaper and more consistent than asking a model to
    # be quiet most of the time.
    if rng.random() >= persona.talk_rate:
        return ""
    return text


# --------------------------------------------------------------------------
# The agent
# --------------------------------------------------------------------------

class OpponentAgent:
    """One seat.  Always returns a legal action, whatever the API does."""

    def __init__(
        self,
        persona: Seated,
        seat: int,
        client: AgentClient | None,
        config: Config,
        denylist: tuple[str, ...] = (),
        on_event=None,
    ) -> None:
        self.persona = persona
        self.seat = seat
        self.client = client
        self.config = config
        self.denylist = denylist
        self.system_prompt = build_system_prompt(persona)
        self.degraded = False
        """Set after a 400: every later decision for this seat stays local."""
        self._decision_index = 0
        self._on_event = on_event

    # ------------------------------------------------------------------ main

    async def act(self, obs: Observation, legal: LegalActions) -> Decision:
        self._decision_index += 1
        rng = decision_rng(
            obs.hand_id, self.seat, int(obs.street), self._decision_index
        )

        if self.client is None or self.config.offline or self.degraded:
            return self._local(obs, legal, rng,
                               Source.OFFLINE if self.config.offline
                               else Source.FALLBACK)

        try:
            return await asyncio.wait_for(
                self._ask(obs, legal, rng),
                timeout=TIERS[self.persona.tier].timeout_s + 4,
            )
        except asyncio.TimeoutError:
            return self._local(obs, legal, rng, Source.FALLBACK,
                               "hard timeout")
        except Exception as exc:  # last resort: the game must not stop
            return self._local(obs, legal, rng, Source.FALLBACK,
                               f"unhandled {type(exc).__name__}")

    async def _ask(self, obs: Observation, legal: LegalActions,
                   rng: random.Random) -> Decision:
        # A private die, drawn from the same reproducible stream as everything
        # else in this hand, so a replay produces the same decision.
        state_text = render_state(obs, roll=rng.randrange(100))
        escalate = obs.street is Street.RIVER or obs.pot >= 60 * obs.big_blind
        tier = self.persona.tier

        raw, note = await self._request(state_text, tier, escalate)
        if raw is None:
            return self._local(obs, legal, rng, Source.FALLBACK, note)

        action, verdict, why = coerce(raw, legal)

        if verdict is Verdict.MUST_REASK:
            reask = build_reask(legal.describe(), legal.call_cost)
            raw2, note2 = await self._request(
                state_text, tier, escalate,
                extra_turns=[
                    {"role": "assistant", "content": json.dumps({
                        "action": raw.action, "amount": raw.amount,
                        "table_talk": raw.table_talk,
                    })},
                    {"role": "user", "content": reask},
                ],
            )
            if raw2 is None:
                return self._local(obs, legal, rng, Source.FALLBACK, note2)
            action, verdict, why2 = coerce(raw2, legal)
            if action is None:
                return self._local(obs, legal, rng, Source.FALLBACK,
                                   f"reask still illegal: {why2}")
            return Decision(
                action=action,
                talk=clean_talk(raw2.table_talk, self.persona, rng, self.denylist),
                source=Source.REASK,
                model=raw2.model,
                latency_ms=raw.latency_ms + raw2.latency_ms,
                note=f"{why} -> {why2}".strip(" ->"),
            )

        assert action is not None
        return Decision(
            action=action,
            talk=clean_talk(raw.table_talk, self.persona, rng, self.denylist),
            source=Source.COERCED if verdict is Verdict.COERCED else Source.MODEL,
            model=raw.model,
            latency_ms=raw.latency_ms,
            note=why,
        )

    # ------------------------------------------------------- the error matrix

    async def _request(
        self,
        state_text: str,
        tier: str,
        escalate: bool,
        extra_turns: list[dict] | None = None,
        _retried_tier: bool = False,
        _retried_tokens: bool = False,
    ) -> tuple[RawDecision | None, str]:
        assert self.client is not None
        try:
            raw = await self.client.decide(
                tier_name=tier,
                system_prompt=self.system_prompt,
                state_text=state_text,
                escalate=escalate,
                extra_turns=extra_turns,
            )
            return raw, ""

        except anthropic.NotFoundError:
            self.degraded = True
            return None, "model not found"

        except (anthropic.RateLimitError, anthropic.InternalServerError):
            # The SDK already retried with backoff.  Step one tier down once,
            # then give up and play locally -- silently, as far as the user sees.
            lower = TIER_DOWNGRADE.get(tier)
            if lower and not _retried_tier:
                return await self._request(state_text, lower, escalate,
                                           extra_turns, _retried_tier=True)
            return None, "rate limited"

        except anthropic.BadRequestError as exc:
            # Never retry either way, but say which it is: a malformed request
            # is our bug, an unfunded account is not and the whole session is
            # about to fall back to local play for a reason worth surfacing.
            self.degraded = True
            if is_billing_error(exc):
                self._emit("billing", _message_of(exc))
                return None, "account cannot spend (check billing)"
            self._emit("bad_request", str(exc))
            return None, f"bad request: {exc}"

        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            self.degraded = True
            return None, "auth failure"

        except anthropic.APIStatusError as exc:
            return None, f"api error {exc.status_code}"

        except (anthropic.APIConnectionError, anthropic.APITimeoutError):
            # The latency budget is already blown; a retry only makes it worse.
            return None, "connection/timeout"

        except ModelRefusal as exc:
            return None, str(exc)  # never retry: it will refuse again

        except TruncatedOutput:
            if not _retried_tokens:
                return await self._request(state_text, tier, escalate,
                                           extra_turns, _retried_tier,
                                           _retried_tokens=True)
            return None, "truncated output"

        except json.JSONDecodeError:
            if not _retried_tokens:
                return await self._request(state_text, tier, escalate,
                                           extra_turns, _retried_tier,
                                           _retried_tokens=True)
            return None, "unparseable json"

    # ----------------------------------------------------------------- local

    def _local(self, obs: Observation, legal: LegalActions, rng: random.Random,
               source: Source, note: str = "") -> Decision:
        return Decision(
            action=local_decision(self.persona, obs, legal, rng),
            talk=clean_talk(local_talk(self.persona, rng), self.persona,
                            rng, self.denylist),
            source=source,
            model="local",
            note=note,
        )

    def _emit(self, kind: str, detail: str) -> None:
        if self._on_event:
            self._on_event(self.seat, kind, detail)

    # ------------------------------------------------------------------ pace

    def dwell(self, obs: Observation, rng: random.Random) -> float:
        """Minimum on-screen think time for this seat.

        A hand where five opponents act instantly is unreadable, and a real
        table has people taking a second or two.  The per-seat ranges overlap
        across tiers deliberately: otherwise the timing itself would tell a
        sharp player which seats are running which model.
        """
        low, high = self.persona.dwell
        value = rng.uniform(low, high)
        if obs.street is Street.RIVER:
            value += self.persona.river_dwell_bonus
        return value


def forced_action(legal: LegalActions) -> Action | None:
    """The only real decision, if there is exactly one.

    Saves an API call in spots where the model has nothing to decide.  Folding
    is always technically legal, so a free check with no chips behind is the
    only genuinely forced case.
    """
    if legal.can_bet or legal.can_raise or legal.can_call:
        return None
    if legal.can_check:
        return Action.check()
    return None
