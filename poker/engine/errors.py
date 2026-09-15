"""Engine exceptions."""

from __future__ import annotations


class EngineError(Exception):
    """Base class for engine errors."""


class IllegalAction(EngineError):
    """An action was rejected by the betting rules.

    The engine never silently normalizes a bad action -- coercing LLM output is
    the agent adapter's job, where it can be logged.  Silent coercion down here
    would hide prompt bugs.
    """


class EngineInvariantError(EngineError):
    """An internal invariant was violated -- always an engine bug."""
