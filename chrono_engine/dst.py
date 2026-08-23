"""Resolves a floating local wall-clock time (one occurrence of a recurring
event) against a real IANA timezone and date -- the one place recurring
series and DST actually interact.

Design decision (stated here, not just in prose): a recurring series is
stored as a FLOATING LOCAL TIME (hour, minute + an IANA tz name), never a
fixed UTC instant. A weekly "9am standup" must stay 9am local on every
occurrence, including across a DST transition -- storing a fixed UTC instant
would silently shift the local wall-clock time by an hour the moment the
transition is crossed, which is exactly the bug real calendar apps hit.
Resolving one occurrence to a concrete UTC instant happens here, lazily,
per-occurrence -- and it is exactly this per-occurrence resolution step
where a DST transition can make the requested local time NONEXISTENT
(spring-forward gap) or AMBIGUOUS (fall-back overlap). Neither case is
silently guessed; both are surfaced so the caller can CLARIFY.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from zoneinfo import ZoneInfo

_UTC = ZoneInfo("UTC")


class OccurrenceStatus(Enum):
    OK = "OK"
    NONEXISTENT = "NONEXISTENT"  # spring-forward gap: this wall-clock time never happened
    AMBIGUOUS = "AMBIGUOUS"      # fall-back overlap: this wall-clock time happened twice


@dataclass(frozen=True)
class ResolvedOccurrence:
    status: OccurrenceStatus
    instant_utc: datetime | None = None
    message: str = ""


def resolve_weekly_occurrence(local_date: date, hour: int, minute: int, tz_name: str) -> ResolvedOccurrence:
    tz = ZoneInfo(tz_name)
    naive = datetime(local_date.year, local_date.month, local_date.day, hour, minute)

    # fold=0: PEP 495's "earlier" interpretation of this wall-clock time.
    aware_fold0 = naive.replace(tzinfo=tz, fold=0)
    utc_fold0 = aware_fold0.astimezone(_UTC)

    # NONEXISTENT check first: convert to UTC and back. If the wall-clock
    # time doesn't round-trip, it was skipped by a spring-forward gap --
    # this must be checked before the ambiguity check below, because a gap
    # ALSO produces different fold=0/fold=1 offsets (PEP 495 extrapolates an
    # offset on both sides of a gap too), so offset-difference alone can't
    # tell a gap and an overlap apart.
    roundtrip = utc_fold0.astimezone(tz)
    if (roundtrip.year, roundtrip.month, roundtrip.day, roundtrip.hour, roundtrip.minute) != (
        local_date.year,
        local_date.month,
        local_date.day,
        hour,
        minute,
    ):
        return ResolvedOccurrence(
            OccurrenceStatus.NONEXISTENT,
            message=(
                f"{local_date} {hour:02d}:{minute:02d} in {tz_name} does not exist "
                "-- a spring-forward DST transition skips this wall-clock time that day."
            ),
        )

    # AMBIGUOUS check: if fold=0 and fold=1 disagree on UTC offset, this
    # wall-clock time occurred twice (fall-back overlap) and picking either
    # one silently would be a guess.
    aware_fold1 = naive.replace(tzinfo=tz, fold=1)
    if aware_fold0.utcoffset() != aware_fold1.utcoffset():
        return ResolvedOccurrence(
            OccurrenceStatus.AMBIGUOUS,
            message=(
                f"{local_date} {hour:02d}:{minute:02d} in {tz_name} occurred twice "
                "-- a fall-back DST transition repeats this wall-clock time that day."
            ),
        )

    return ResolvedOccurrence(OccurrenceStatus.OK, instant_utc=utc_fold0)
