"""Deterministic temporal parser: text -> ParsedTime, never a guess.

Pipeline (see canonical.py for the types):

    raw text
      -> normalize (Unicode digit folding, preposition stripping)
      -> try ONE pattern family, in priority order, against the whole string
         (lexicon words -> colon/dot separator -> 'h' separator -> compact digits)
      -> if no single pattern matches the whole string, try splitting on
         conjunctions ("and", "/", ",", "&") and parsing each part, checking
         all parts agree (this is the ONLY multi-expression handling; it
         exists to catch "13:00 and 1400" as a conflict, not to parse dates)
      -> validate (range checks)
      -> resolve ambiguity (am/pm unknown? -> CLARIFY, never guess)
      -> resolve timezone marker (LT / Z / UTC / unspecified)
      -> ParsedTime

No Telegram, Android, or LLM code belongs here. This module is pure,
deterministic, and fully unit-testable offline.
"""
from __future__ import annotations

import re

from .canonical import Candidate, ParsedTime, Status, TzPolicy
from .normalize import preprocess

_MERIDIEM_TOKEN = re.compile(r"^(a\.?m\.?|p\.?m\.?)$", re.IGNORECASE)
_TZ_TOKEN = re.compile(r"^(lt|utc|z)$", re.IGNORECASE)

_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_WORD_NUM_ALT = "|".join(_WORD_NUM.keys())

_SPLIT_RE = re.compile(r"\s+and\s+|\s*[/,&]\s*", re.IGNORECASE)

_SEP_COLON_DOT_RE = re.compile(r"^(?P<hour>\d{1,2})[:.](?P<minute>\d{2})(?P<suffix>.*)$")
_SEP_H_RE = re.compile(r"^(?P<hour>\d{1,2})h(?P<minute>\d{2})?(?P<suffix>.*)$", re.IGNORECASE)
_COMPACT_RE = re.compile(r"^(?P<digits>\d{1,4})(?P<suffix>.*)$")

_LEXICON = [
    (re.compile(r"^noon(?P<suffix>.*)$", re.IGNORECASE),
     lambda m: (12, 0, "pm")),
    (re.compile(r"^midnight(?P<suffix>.*)$", re.IGNORECASE),
     lambda m: (0, 0, "am")),
    (re.compile(rf"^half past (?P<w>{_WORD_NUM_ALT})(?P<suffix>.*)$", re.IGNORECASE),
     lambda m: (_WORD_NUM[m.group("w").lower()], 30, None)),
    (re.compile(rf"^quarter past (?P<w>{_WORD_NUM_ALT})(?P<suffix>.*)$", re.IGNORECASE),
     lambda m: (_WORD_NUM[m.group("w").lower()], 15, None)),
    (re.compile(rf"^quarter to (?P<w>{_WORD_NUM_ALT})(?P<suffix>.*)$", re.IGNORECASE),
     lambda m: ((_WORD_NUM[m.group("w").lower()] - 1) or 12, 45, None)),
    (re.compile(rf"^(?P<w>{_WORD_NUM_ALT}) o'?clock(?P<suffix>.*)$", re.IGNORECASE),
     lambda m: (_WORD_NUM[m.group("w").lower()], 0, None)),
]


def parse_time(raw: str, *, default_tz: str = "UTC") -> ParsedTime:
    text = preprocess(raw)
    if not text:
        return ParsedTime(Status.REJECT, message="empty input", raw_input=raw)

    single = _try_single(text, raw, default_tz)
    if single is not None:
        return single

    parts = [p.strip() for p in _SPLIT_RE.split(text) if p.strip()]
    if len(parts) <= 1:
        return ParsedTime(Status.REJECT, message=f"could not parse '{raw}' as a time", raw_input=raw)

    results = []
    for part in parts:
        result = _try_single(part, raw, default_tz)
        if result is None:
            return ParsedTime(
                Status.REJECT,
                message=f"could not parse '{raw}' as a time (unrecognized part: '{part}')",
                raw_input=raw,
            )
        results.append(result)

    if any(r.status in (Status.REJECT, Status.CLARIFY) for r in results):
        return ParsedTime(
            Status.REJECT,
            message=f"'{raw}' contains conflicting or ambiguous time expressions",
            raw_input=raw,
        )

    distinct = {(r.hour, r.minute) for r in results}
    if len(distinct) > 1:
        return ParsedTime(
            Status.REJECT,
            message=f"'{raw}' contains multiple different times -- not scheduling a guess",
            raw_input=raw,
        )

    return results[0]


def _try_single(text: str, raw: str, default_tz: str) -> ParsedTime | None:
    for pattern, extractor in _LEXICON:
        m = pattern.match(text)
        if m:
            hour, minute, implied_meridiem = extractor(m)
            parsed = _parse_suffix(m.group("suffix"))
            if parsed == "CONFLICT":
                return ParsedTime(Status.REJECT, message=f"'{raw}': conflicting am/pm or timezone markers", raw_input=raw)
            if parsed == "INVALID":
                return ParsedTime(Status.REJECT, message=f"'{raw}': unrecognized trailing text after time", raw_input=raw)
            meridiem, tz_marker = parsed
            # is_fixed_width=False: partial lexicon phrases ("half past X",
            # "X o'clock"...) still need the am/pm ambiguity check. Fully
            # resolved words (noon/midnight) ignore this flag entirely via
            # implied_meridiem below.
            return _finalize(hour, minute, meridiem, tz_marker, False, raw, default_tz, implied_meridiem)

    m = _SEP_COLON_DOT_RE.match(text)
    if m:
        hour, minute = int(m.group("hour")), int(m.group("minute"))
        parsed = _parse_suffix(m.group("suffix"))
        if parsed == "CONFLICT":
            return ParsedTime(Status.REJECT, message=f"'{raw}': conflicting am/pm or timezone markers", raw_input=raw)
        if parsed == "INVALID":
            return None
        meridiem, tz_marker = parsed
        return _finalize(hour, minute, meridiem, tz_marker, False, raw, default_tz)

    m = _SEP_H_RE.match(text)
    if m:
        hour = int(m.group("hour"))
        minute = int(m.group("minute")) if m.group("minute") else 0
        parsed = _parse_suffix(m.group("suffix"))
        if parsed == "CONFLICT":
            return ParsedTime(Status.REJECT, message=f"'{raw}': conflicting am/pm or timezone markers", raw_input=raw)
        if parsed == "INVALID":
            return None
        meridiem, tz_marker = parsed
        return _finalize(hour, minute, meridiem, tz_marker, True, raw, default_tz)

    m = _COMPACT_RE.match(text)
    if m:
        digits = m.group("digits")
        hour, minute, is_fixed_width = _split_compact(digits)
        parsed = _parse_suffix(m.group("suffix"))
        if parsed == "CONFLICT":
            return ParsedTime(Status.REJECT, message=f"'{raw}': conflicting am/pm or timezone markers", raw_input=raw)
        if parsed == "INVALID":
            return None
        meridiem, tz_marker = parsed
        return _finalize(hour, minute, meridiem, tz_marker, is_fixed_width, raw, default_tz)

    return None


def _split_compact(digits: str) -> tuple[int, int, bool]:
    """Fixed, deterministic split by digit count -- never multiple candidate
    splits for the same string, so there is nothing to guess between here."""
    if len(digits) <= 2:
        return int(digits), 0, False
    if len(digits) == 3:
        return int(digits[0]), int(digits[1:]), False
    return int(digits[:2]), int(digits[2:]), True  # len == 4: fixed HHMM convention


def _parse_suffix(suffix: str):
    """Returns (meridiem_or_None, tz_marker_or_None), or the string
    'CONFLICT' / 'INVALID'."""
    suffix = suffix.strip()
    if not suffix:
        return (None, None)
    tokens = suffix.split()
    meridiem_found = None
    tz_found = None
    meridiem_count = 0
    tz_count = 0
    for tok in tokens:
        if _MERIDIEM_TOKEN.match(tok):
            meridiem_count += 1
            meridiem_found = tok
        elif _TZ_TOKEN.match(tok):
            tz_count += 1
            tz_found = tok
        else:
            return "INVALID"
    if meridiem_count > 1 or tz_count > 1:
        return "CONFLICT"
    return (meridiem_found, tz_found)


def _resolve_tz(tz_marker: str | None, default_tz: str) -> tuple[TzPolicy, str]:
    if tz_marker is None:
        return TzPolicy.UNSPECIFIED, default_tz
    normalized = tz_marker.lower().replace(".", "")
    if normalized == "lt":
        return TzPolicy.EXPLICIT_LOCAL, default_tz
    return TzPolicy.EXPLICIT_UTC, "UTC"  # "z" or "utc"


def _finalize(
    hour: int,
    minute: int,
    meridiem: str | None,
    tz_marker: str | None,
    is_fixed_width: bool,
    raw: str,
    default_tz: str,
    implied_meridiem: str | None = None,
) -> ParsedTime:
    if not (0 <= minute <= 59):
        return ParsedTime(Status.REJECT, message=f"'{raw}': minute {minute} is out of range (0-59)", raw_input=raw)

    tz_policy, tz_name = _resolve_tz(tz_marker, default_tz)

    if implied_meridiem is not None:
        # Fully-resolved lexicon word (noon/midnight): already a complete,
        # deliberate specification -- NORMALIZED regardless of timezone
        # marker -- unless an explicit meridiem is given that contradicts it.
        if meridiem is not None:
            is_pm = meridiem.lower().startswith("p")
            implied_is_pm = implied_meridiem == "pm"
            if is_pm != implied_is_pm:
                return ParsedTime(
                    Status.REJECT,
                    message=f"'{raw}': already means {implied_meridiem.upper()}, conflicting meridiem given",
                    raw_input=raw,
                )
        return ParsedTime(Status.NORMALIZED, hour, minute, tz_policy, tz_name, raw_input=raw)

    if meridiem is not None:
        if not (1 <= hour <= 12):
            return ParsedTime(
                Status.REJECT,
                message=f"'{raw}': hour {hour} is not valid with am/pm (must be 1-12)",
                raw_input=raw,
            )
        is_pm = meridiem.lower().startswith("p")
        real_hour = (hour % 12) + (12 if is_pm else 0)
        # Explicit am/pm is a complete, deliberate specification --
        # NORMALIZED regardless of timezone marker. WARN is reserved for
        # the bare-numeric, no-meridiem case below, where the user may not
        # have thought about am/pm or timezone at all.
        return ParsedTime(Status.NORMALIZED, real_hour, minute, tz_policy, tz_name, raw_input=raw)

    # No meridiem given.
    if hour == 24 and minute == 0:
        return ParsedTime(
            Status.REJECT,
            message=f"'{raw}': '24:00' is not accepted -- use '00:00' for midnight (and say which day)",
            raw_input=raw,
        )
    if hour > 23:
        return ParsedTime(Status.REJECT, message=f"'{raw}': hour {hour} is impossible (must be 0-23)", raw_input=raw)

    if is_fixed_width or hour == 0 or hour > 12:
        status = Status.WARN if tz_policy == TzPolicy.UNSPECIFIED else Status.NORMALIZED
        return ParsedTime(status, hour, minute, tz_policy, tz_name, raw_input=raw)

    # 1..12, no meridiem, not a fixed-width/24h-only convention: genuinely
    # ambiguous. Do not guess -- offer both concrete readings.
    am_hour = hour % 12
    pm_hour = (hour % 12) + 12
    return ParsedTime(
        Status.CLARIFY,
        tz_policy=tz_policy,
        tz_name=tz_name,
        message=(
            f"'{raw}' could mean {am_hour:02d}:{minute:02d} (AM) or "
            f"{pm_hour:02d}:{minute:02d} (PM) -- please specify am/pm or use 24-hour format."
        ),
        candidates=(Candidate("AM", am_hour, minute), Candidate("PM", pm_hour, minute)),
        raw_input=raw,
    )
