"""The Anthropic client wrapper.

One long-lived :class:`AsyncAnthropic` for the whole process: rebuilding it per
decision would add a TLS handshake to every one of the ~7 opponent decisions in
a hand, which is most of the latency budget spent on nothing.

Credentials resolve the standard way (``ANTHROPIC_API_KEY`` and friends) -- no
key is ever read or stored by this module.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import anthropic
from anthropic import AsyncAnthropic

from poker.agents.schema import DECISION_SCHEMA, RawDecision
from poker.config import TIERS

# Per-million-token rates, for the running cost estimate in the debug HUD.
PRICES = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-fable-5-1": (10.00, 50.00),
}
CACHE_READ_DISCOUNT = 0.10
CACHE_WRITE_MULTIPLIER = 1.25


class ModelRefusal(Exception):
    """``stop_reason == "refusal"``.  Never retry -- it will refuse again."""


class TruncatedOutput(Exception):
    """``stop_reason == "max_tokens"``: the JSON is cut off."""


@dataclass
class Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float = 0.0
    by_model: dict[str, int] = field(default_factory=dict)

    def add(self, model: str, usage) -> tuple[int, int, int, int]:
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cread = getattr(usage, "cache_read_input_tokens", 0) or 0
        cwrite = getattr(usage, "cache_creation_input_tokens", 0) or 0

        self.requests += 1
        self.input_tokens += inp
        self.output_tokens += out
        self.cache_read_tokens += cread
        self.cache_write_tokens += cwrite
        self.by_model[model] = self.by_model.get(model, 0) + 1

        in_rate, out_rate = PRICES.get(model, (0.0, 0.0))
        self.cost += (
            inp * in_rate
            + cread * in_rate * CACHE_READ_DISCOUNT
            + cwrite * in_rate * CACHE_WRITE_MULTIPLIER
            + out * out_rate
        ) / 1_000_000
        return inp, out, cread, cwrite

    def summary(self) -> str:
        return (
            f"{self.requests} req  "
            f"in {self.input_tokens} (cache r{self.cache_read_tokens}/"
            f"w{self.cache_write_tokens})  out {self.output_tokens}  "
            f"${self.cost:.3f}"
        )


class AgentClient:
    """Issues decision and review requests, and tracks spend."""

    def __init__(self, client: AsyncAnthropic | None = None) -> None:
        self._client = client or AsyncAnthropic(max_retries=2)
        self.usage = Usage()

    async def aclose(self) -> None:
        try:
            await self._client.close()
        except Exception:
            pass

    # ------------------------------------------------------------- preflight

    async def preflight(self, models: list[str]) -> str | None:
        """Validate credentials and each model before the first hand.

        Catches auth and model-name errors up front, so they cannot surface
        mid-hand as a silent degrade.  Returns an error string, or None.
        """
        for model in sorted(set(models)):
            try:
                await self._client.with_options(timeout=20.0).messages.create(
                    model=model,
                    max_tokens=1,
                    messages=[{"role": "user", "content": "hi"}],
                )
            except anthropic.NotFoundError:
                return f"model not available: {model}"
            except anthropic.AuthenticationError:
                return "ANTHROPIC_API_KEY is missing or invalid"
            except anthropic.PermissionDeniedError:
                return f"this key may not use {model}"
            except anthropic.RateLimitError:
                continue  # transient, not a configuration problem
            except anthropic.BadRequestError as exc:
                # A 400 here is usually an account state rather than a bad
                # request -- an unfunded balance is the common one -- so pass
                # the server's own wording through instead of "API error 400".
                return f"{model}: {_message_of(exc)}"
            except anthropic.APIStatusError as exc:
                return f"{model}: API error {exc.status_code}"
            except anthropic.APIConnectionError:
                return "cannot reach the Anthropic API -- check your connection"
        return None

    # -------------------------------------------------------------- decisions

    async def decide(
        self,
        *,
        tier_name: str,
        system_prompt: str,
        state_text: str,
        escalate: bool = False,
        extra_turns: list[dict] | None = None,
    ) -> RawDecision:
        tier = TIERS[tier_name]

        output_config: dict = {
            "format": {"type": "json_schema", "schema": DECISION_SCHEMA}
        }
        effort = tier.escalate_effort if (escalate and tier.escalate_effort) else tier.effort
        if effort is not None:
            # `effort` is rejected outright on the fast tier's model.
            output_config["effort"] = effort

        messages: list[dict] = [{"role": "user", "content": state_text}]
        if extra_turns:
            messages.extend(extra_turns)

        kwargs: dict = {
            "model": tier.model,
            "max_tokens": tier.max_tokens,
            "system": [{
                "type": "text",
                "text": system_prompt,
                # Real on sonnet-5 (1024 min) and opus-5 (512 min); a silent
                # no-op on haiku-4-5, whose minimum cacheable prefix is 4096
                # tokens.  Left in place deliberately: it costs nothing and is
                # correct the day that minimum drops.  Padding the prompt to
                # force a hit would be caching theatre.
                "cache_control": {"type": "ephemeral"},
            }],
            "messages": messages,
            "output_config": output_config,
        }
        if tier.thinking is not None:
            kwargs["thinking"] = tier.thinking
        # No temperature: the parameter no longer exists on messages.create.

        started = time.monotonic()
        msg = await self._client.with_options(
            timeout=tier.timeout_s
        ).messages.create(**kwargs)
        latency_ms = int((time.monotonic() - started) * 1000)

        # Always check stop_reason before touching content.
        if msg.stop_reason == "refusal":
            raise ModelRefusal(_refusal_detail(msg))
        if msg.stop_reason == "max_tokens":
            raise TruncatedOutput(f"{tier.model} hit max_tokens")

        inp, out, cread, cwrite = self.usage.add(tier.model, msg.usage)
        payload = json.loads(_first_text(msg))
        return RawDecision.from_payload(
            payload,
            model=tier.model,
            latency_ms=latency_ms,
            input_tokens=inp,
            output_tokens=out,
            cache_read_tokens=cread,
            cache_write_tokens=cwrite,
        )

    # ----------------------------------------------------------------- review

    async def analyse(
        self,
        *,
        model: str,
        effort: str,
        max_tokens: int,
        timeout_s: float,
        system_prompt: str,
        context: str,
        schema: dict,
    ) -> dict:
        """Stage one of the review, on the analyst model.

        Written for Claude Fable 5.1, whose rules differ from the Opus family:

        * Thinking is always on, so the ``thinking`` parameter is omitted
          entirely -- sending ``{"type": "disabled"}`` or a ``budget_tokens``
          form is a 400.  Depth is controlled with ``output_config.effort``.
        * Thinking tokens count against ``max_tokens``, hence the large ceiling.
        * Turns can run long, so this streams: it avoids an HTTP timeout on a
          request that may think for a while.
        * Safety classifiers can decline a request outright.  Server-side
          fallbacks are enabled so a decline is rescued inside the same call
          rather than costing the user their review.
        """
        async with self._client.with_options(timeout=timeout_s).beta.messages.stream(
            model=model,
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=[{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": context}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        ) as stream:
            msg = await stream.get_final_message()

        if msg.stop_reason == "refusal":
            raise ModelRefusal(_refusal_detail(msg))
        if msg.stop_reason == "max_tokens":
            raise TruncatedOutput("analysis hit max_tokens")
        self.usage.add(_served_by(msg, model), msg.usage)
        return json.loads(_first_text(msg))

    async def translate(
        self,
        *,
        model: str,
        effort: str,
        max_tokens: int,
        timeout_s: float,
        system_prompt: str,
        content: str,
        schema: dict,
    ) -> dict:
        """Stage two: rewrite the analysis in plain language.

        On Opus 4.8 ``{"type": "adaptive"}`` is the only on-mode and omitting
        the parameter means *no* thinking -- which also makes the model prone to
        writing its reasoning into the visible answer.  So it is set explicitly,
        at low effort: this is a rewriting task, not a reasoning one.
        """
        msg = await self._client.with_options(timeout=timeout_s).messages.create(
            model=model,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            system=[{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": content}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        )
        if msg.stop_reason == "refusal":
            raise ModelRefusal(_refusal_detail(msg))
        if msg.stop_reason == "max_tokens":
            raise TruncatedOutput("translation hit max_tokens")
        self.usage.add(model, msg.usage)
        return json.loads(_first_text(msg))

    async def count_tokens(self, model: str, system: str, text: str) -> int:
        result = await self._client.messages.count_tokens(
            model=model,
            system=system,
            messages=[{"role": "user", "content": text}],
        )
        return result.input_tokens


def _first_text(msg) -> str:
    """The first text block.

    With thinking enabled ``content[0]`` is a ThinkingBlock, so indexing
    position zero would blow up on the mid and deep tiers.
    """
    for block in msg.content:
        if block.type == "text":
            return block.text
    raise ValueError("response contained no text block")


def is_billing_error(exc: Exception) -> bool:
    """A 400 that means "this account cannot spend", not "this request is wrong".

    It needs distinguishing because the two demand opposite responses: a
    malformed request should degrade that seat permanently, while an unfunded
    account should stop the whole session with an explanation.
    """
    text = _message_of(exc).lower()
    return any(s in text for s in ("credit balance", "billing", "quota",
                                   "insufficient funds", "purchase"))


def _message_of(exc: Exception) -> str:
    """The server's own sentence, without the JSON envelope around it.

    ``exc.message`` is the whole ``Error code: 400 - {...}`` dump, which is
    unreadable in a one-line banner.
    """
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        message = body.get("message")
        if isinstance(message, str) and message:
            return message

    message = getattr(exc, "message", None)
    if isinstance(message, str) and message:
        # Fall back to digging the message out of the dumped envelope.
        match = re.search(r"'message':\s*'([^']+)'", message)
        if match:
            return match.group(1)
        return message
    return str(exc)


def _served_by(msg, requested: str) -> str:
    """Which model actually answered.

    With server-side fallbacks a declined turn is re-run on another model and
    billed at that model's rates, so the cost tracker must follow it.
    """
    return getattr(msg, "model", None) or requested


def _refusal_detail(msg) -> str:
    details = getattr(msg, "stop_details", None)
    category = getattr(details, "category", None) if details else None
    return f"model declined (category={category})"
