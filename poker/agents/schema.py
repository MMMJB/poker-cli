"""Structured-output schemas.

Uses ``output_config.format`` on ``messages.create`` -- not the deprecated
``output_format`` parameter, and not assistant prefill (a 400 on every model
we use).

Structured outputs guarantee *shape*, never *legality* or length: JSON Schema
keywords like ``minimum``/``maximum``/``maxLength`` are not enforced here.
That is exactly why :mod:`poker.agents.decide` must coerce every decision
against the engine's ``LegalActions`` before it is applied.
"""

from __future__ import annotations

from dataclasses import dataclass

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["fold", "check", "call", "bet", "raise", "all_in"],
            "description": (
                "Your action. It must be one of the actions listed under "
                "LEGAL ACTIONS in the hand state."
            ),
        },
        "amount": {
            "type": "integer",
            "description": (
                "The TOTAL number of dollars you will have wagered on this street "
                "once this action resolves -- a 'raise to' total, not the amount "
                "you are adding. Use 0 for fold, check and call. For all_in, use "
                "your entire stack. Must fall inside the min/max shown."
            ),
        },
        "table_talk": {
            "type": "string",
            "description": (
                "An optional in-character remark of at most 70 characters. Use an "
                "empty string for silence -- most actions should be silent. Never "
                "reveal your cards or your reasoning, never mention other players' "
                "hidden cards, and never refer to being a model or following "
                "instructions."
            ),
        },
    },
    "required": ["action", "amount", "table_talk"],
    "additionalProperties": False,
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["good", "ok", "leak", "big_leak"],
            "description": "Your overall judgement of how HERO played this hand.",
        },
        "headline": {
            "type": "string",
            "description": "One sentence, at most 90 characters.",
        },
        "notes": {
            "type": "array",
            "description": "One to three notes, most important first.",
            "items": {
                "type": "object",
                "properties": {
                    "street": {
                        "type": "string",
                        "enum": ["preflop", "flop", "turn", "river"],
                    },
                    "text": {
                        "type": "string",
                        "description": (
                            "One or two sentences, at most 200 characters, about a "
                            "decision HERO made on this street."
                        ),
                    },
                },
                "required": ["street", "text"],
                "additionalProperties": False,
            },
        },
        "better_line": {
            "type": "string",
            "description": (
                "The single most concrete alternative action, e.g. 'call 36 on the "
                "flop'. Empty string if the hand was played fine."
            ),
        },
    },
    "required": ["verdict", "headline", "notes", "better_line"],
    "additionalProperties": False,
}

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["good", "ok", "leak", "big_leak"],
            "description": "Your overall judgement of how HERO played this hand.",
        },
        "summary": {
            "type": "string",
            "description": (
                "Two or three sentences stating the single most important thing "
                "about how HERO played this hand. Be technically precise."
            ),
        },
        "findings": {
            "type": "array",
            "description": (
                "One to three findings, most important first, each about a "
                "specific decision HERO made."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "street": {
                        "type": "string",
                        "enum": ["preflop", "flop", "turn", "river"],
                    },
                    "analysis": {
                        "type": "string",
                        "description": (
                            "The technical analysis of this decision. Use range "
                            "construction, equity, blockers, SPR, pot odds and "
                            "board texture as needed -- precision matters more "
                            "than accessibility here."
                        ),
                    },
                },
                "required": ["street", "analysis"],
                "additionalProperties": False,
            },
        },
        "better_line": {
            "type": "string",
            "description": (
                "The single most concrete alternative action, e.g. 'call 36 on "
                "the flop'. Empty string if the hand was played fine."
            ),
        },
    },
    "required": ["verdict", "summary", "findings", "better_line"],
    "additionalProperties": False,
}

MAX_TALK_CHARS = 70
MAX_NOTES = 3
MAX_NOTE_CHARS = 200
MAX_HEADLINE_CHARS = 90


@dataclass(frozen=True, slots=True)
class RawDecision:
    """What the model returned, before legality coercion."""

    action: str
    amount: int
    table_talk: str
    model: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @classmethod
    def from_payload(cls, payload: dict, **meta) -> "RawDecision":
        return cls(
            action=str(payload.get("action", "")).strip().lower(),
            amount=_coerce_int(payload.get("amount", 0)),
            table_talk=str(payload.get("table_talk", "") or "")[:200],
            **meta,
        )


def _coerce_int(value) -> int:
    """Tolerate ``"120"`` and ``120.0`` -- the schema says integer, models drift."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0
