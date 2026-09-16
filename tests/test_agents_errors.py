"""Telling account problems apart from code problems.

A 400 can mean two opposite things: the request was malformed (our bug, degrade
that seat) or the account cannot spend (not our bug, and the whole session is
about to go local for a reason the player needs to see).
"""

from __future__ import annotations

import pytest

from poker.agents.client import _message_of, is_billing_error


class FakeError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@pytest.mark.parametrize("message", [
    "Your credit balance is too low to access the Anthropic API.",
    "Please go to Plans & Billing to upgrade or purchase credits.",
    "insufficient funds for this request",
    "monthly quota exceeded",
])
def test_billing_failures_are_recognised(message: str) -> None:
    assert is_billing_error(FakeError(message))


@pytest.mark.parametrize("message", [
    "max_tokens: must be greater than 0",
    "thinking.budget_tokens is not supported for this model",
    "tool_choice: type 'any' is not supported for this model",
    "messages: at least one message is required",
])
def test_malformed_requests_are_not_billing_failures(message: str) -> None:
    assert not is_billing_error(FakeError(message))


def test_message_extraction_falls_back_to_str() -> None:
    assert _message_of(FakeError("boom")) == "boom"
    assert "plain" in _message_of(Exception("plain problem"))


def test_a_billing_failure_degrades_the_seat_with_its_own_reason() -> None:
    """The seat still stops calling, but the note explains why."""
    import asyncio

    import anthropic

    from poker.agents.decide import OpponentAgent
    from poker.agents.personas import BY_KEY
    from poker.config import CFG

    import httpx2

    def billing_error() -> anthropic.BadRequestError:
        # The SDK's exception needs a real response to build from.
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        return anthropic.BadRequestError(
            "Your credit balance is too low to access the Anthropic API.",
            response=httpx2.Response(400, request=request),
            body=None,
        )

    class Boom:
        async def decide(self, **kw):
            raise billing_error()

    events: list[tuple] = []
    agent = OpponentAgent(BY_KEY["walter"], 1, Boom(), CFG, (),
                          on_event=lambda *a: events.append(a))
    result, note = asyncio.run(agent._request("state", "fast", False))
    assert result is None
    assert "billing" in note or "cannot spend" in note
    assert agent.degraded
    assert events and events[0][1] == "billing"
