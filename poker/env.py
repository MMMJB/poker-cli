"""Loading local secrets from a ``.env`` file.

The Anthropic SDK resolves credentials from the environment, so this only has
to get the values *into* the environment before a client is built.  A hand-
rolled reader rather than ``python-dotenv``: the format we need is a few
``KEY=value`` lines, and it is not worth a dependency.

**Anything already exported in the shell wins.**  A file on disk must never
silently override a key the user set deliberately for one run.
"""

from __future__ import annotations

import os
from pathlib import Path

SECRET_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def find_env_file(start: Path | None = None) -> Path | None:
    """The nearest ``.env``, searched from the project root then the home dir.

    The launcher runs from an arbitrary working directory, so looking only at
    the cwd would miss it.
    """
    candidates = []
    if start is not None:
        candidates.append(Path(start))
    candidates.append(Path(__file__).resolve().parent.parent)  # project root
    candidates.append(Path.cwd())
    candidates.append(Path.home() / ".poker-trainer")

    for directory in candidates:
        path = directory / ".env"
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def parse_env(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines, ignoring comments and blanks."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def load_env(start: Path | None = None) -> list[str]:
    """Load the nearest ``.env`` into ``os.environ``.

    Returns the names of the variables it set.  Never overwrites a variable
    that is already present.
    """
    path = find_env_file(start)
    if path is None:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    applied: list[str] = []
    for key, value in parse_env(text).items():
        if os.environ.get(key):
            continue  # an explicit export always wins
        os.environ[key] = value
        applied.append(key)
    return applied


def has_credentials() -> bool:
    return any(os.environ.get(name) for name in SECRET_KEYS)


def redact(value: str) -> str:
    """A safe-to-display form of a secret, for diagnostics only."""
    if not value:
        return "(unset)"
    if len(value) <= 12:
        return "*" * len(value)
    return f"{value[:10]}...{value[-4:]} ({len(value)} chars)"
