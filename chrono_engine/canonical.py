"""Canonical data types for the deterministic temporal parser.

No Telegram, no Android, no LLM code belongs in this module or anywhere in
chrono_engine -- it must stay a self-contained, offline-testable module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple


class Status(Enum):
    """Outcome of parsing one time expression.

    NORMALIZED -- unambiguous, safe to use directly.
    WARN       -- usable, but something was defaulted/assumed and should be
                  surfaced to the user (e.g. no timezone marker given).
    CLARIFY    -- cannot safely produce one canonical time; the caller must
                  ask the user, ideally using `candidates` to make the
                  follow-up question concrete and answerable.
    REJECT     -- invalid or unparseable; `message` explains why.
    """

    NORMALIZED = "NORMALIZED"
    WARN = "WARN"
    CLARIFY = "CLARIFY"
    REJECT = "REJECT"


class TzPolicy(Enum):
    """How the timezone/offset for a parsed time was determined."""

    EXPLICIT_LOCAL = "EXPLICIT_LOCAL"  # "LT" marker: explicit local/shipboard time
    EXPLICIT_UTC = "EXPLICIT_UTC"      # "Z" / "UTC" marker
    UNSPECIFIED = "UNSPECIFIED"        # no marker; caller's default_tz used


class Candidate(NamedTuple):
    """One concrete reading offered back to the user during CLARIFY.

    A NamedTuple so callers can use either `candidate.hour` or unpack it as
    `(label, hour, minute)`.
    """

    label: str   # e.g. "AM", "PM"
    hour: int
    minute: int


@dataclass(frozen=True)
class ParsedTime:
    """Result of parse_time(). Always check `status` before trusting hour/minute."""

    status: Status
    hour: int | None = None
    minute: int | None = None
    tz_policy: TzPolicy | None = None
    tz_name: str | None = None
    message: str = ""
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)
    raw_input: str = ""
