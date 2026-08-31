"""Adversarial test suite for chrono_engine, written BEFORE the engine exists.

Kill-test bar (stated up front, per the design brief): a test case fails the
parser if it silently produces a WRONG canonical time. It is always
acceptable for the parser to REJECT or CLARIFY instead of guessing. So:

- For inputs with exactly one correct reading: assert the exact
  (hour, minute) and status NORMALIZED (or WARN, if timezone was defaulted).
- For genuinely ambiguous inputs: assert status == CLARIFY and that no
  canonical (hour, minute) is asserted as ground truth -- the whole point is
  that there ISN'T one.
- For invalid/impossible inputs: assert status == REJECT.

Run before the engine exists: every test here fails on import. That failure
is the point -- these tests define the spec, not describe the code.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from chrono_engine.canonical import Status, TzPolicy
from chrono_engine.parser import parse_time
from chrono_engine.dst import OccurrenceStatus, next_occurrence_utc, resolve_weekly_occurrence


TZ = "Asia/Manila"  # arbitrary fixed default_tz for tests that don't care


def parse(raw: str, **kw):
    kw.setdefault("default_tz", TZ)
    return parse_time(raw, **kw)


# ---------------------------------------------------------------------------
# The exact 14 inputs named in the brief, one by one, on purpose -- these are
# the ones explicitly called out as "do not assume visually similar formats
# have the same semantics."
# ---------------------------------------------------------------------------

class TestNamedStressInputs:
    def test_1354LT_is_24h_explicit_local(self):
        r = parse("1354LT")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (13, 54)
        assert r.tz_policy == TzPolicy.EXPLICIT_LOCAL

    def test_1354_bare_is_unambiguous_24h(self):
        # 4-digit bare, no meridiem: fixed-width HHMM convention is always
        # unambiguous regardless of hour value.
        r = parse("1354")
        assert r.status in (Status.NORMALIZED, Status.WARN)
        assert (r.hour, r.minute) == (13, 54)
        assert r.tz_policy == TzPolicy.UNSPECIFIED

    def test_1354pm_conflicts_and_is_rejected(self):
        # hour=13 from a 4-digit split is outside the 1-12 range a pm
        # suffix requires -- genuine conflict, not a guessable case.
        r = parse("1354pm")
        assert r.status == Status.REJECT

    def test_1_54pm_colon_unambiguous(self):
        r = parse("1:54pm")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (13, 54)

    def test_1_54_space_pm(self):
        r = parse("1:54 pm")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (13, 54)

    def test_1_54_space_PM_uppercase(self):
        r = parse("1:54 PM")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (13, 54)

    def test_154pm_compact_unambiguous(self):
        # 3-digit + explicit pm: only valid split is H(1)+MM(2), hour=1
        # is in range, so this IS resolvable despite being compact.
        r = parse("154pm")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (13, 54)

    def test_154PM_uppercase(self):
        r = parse("154PM")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (13, 54)

    def test_154_space_pm(self):
        r = parse("154 pm")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (13, 54)

    def test_1_54pm_dot_separator(self):
        # locale (European-style) dot separator, same as colon.
        r = parse("1.54pm")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (13, 54)

    def test_13_54_colon_24h(self):
        r = parse("13:54")
        assert r.status in (Status.NORMALIZED, Status.WARN)
        assert (r.hour, r.minute) == (13, 54)

    def test_13_54_dot_24h(self):
        r = parse("13.54")
        assert r.status in (Status.NORMALIZED, Status.WARN)
        assert (r.hour, r.minute) == (13, 54)

    def test_1pm_bare_hour(self):
        r = parse("1pm")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (13, 0)

    def test_1_space_pm_bare_hour(self):
        r = parse("1 pm")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (13, 0)


# ---------------------------------------------------------------------------
# The genuinely ambiguous cousin of the above: bare 3-digit, no meridiem.
# This is the one the brief singles out ("154") -- must NOT silently pick
# a reading.
# ---------------------------------------------------------------------------

class TestBareThreeDigitAmbiguity:
    def test_154_bare_no_meridiem_is_clarify(self):
        r = parse("154")
        assert r.status == Status.CLARIFY
        # Must not smuggle out a canonical hour/minute as if it were safe.
        assert r.hour is None and r.minute is None
        # Should offer the two concrete readings so the follow-up question
        # is answerable, not open-ended.
        assert len(r.candidates) == 2
        hours = sorted(h for (_, h, _m) in r.candidates)
        assert hours == [1, 13]

    def test_154LT_still_ambiguous_on_meridiem_axis(self):
        # LT resolves the *timezone* axis, not the AM/PM axis -- these are
        # orthogonal. Still must CLARIFY.
        r = parse("154LT")
        assert r.status == Status.CLARIFY


# ---------------------------------------------------------------------------
# Malformed AM/PM combinations / 12h-24h collisions
# ---------------------------------------------------------------------------

class TestMalformedMeridiem:
    def test_13_00am_rejected(self):
        r = parse("13:00am")
        assert r.status == Status.REJECT

    def test_25_00_rejected(self):
        r = parse("25:00")
        assert r.status == Status.REJECT

    def test_0_60_rejected(self):
        r = parse("0:60")
        assert r.status == Status.REJECT

    def test_24_00_rejected_not_silently_rolled_to_next_day(self):
        r = parse("24:00")
        assert r.status == Status.REJECT

    def test_single_letter_meridiem_rejected(self):
        # "a" alone is too easily a typo to treat as a meridiem token.
        r = parse("1:00a")
        assert r.status == Status.REJECT

    def test_am_pm_dotted_variants_accepted(self):
        r = parse("1:54p.m.")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (13, 54)

    def test_conflicting_tz_markers_rejected(self):
        r = parse("1354z lt")
        assert r.status == Status.REJECT


# ---------------------------------------------------------------------------
# Whitespace / leading zeros
# ---------------------------------------------------------------------------

class TestWhitespaceAndZeros:
    def test_leading_trailing_whitespace_ignored(self):
        r = parse("   1:54pm   ")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (13, 54)

    def test_leading_zero_bare_hour_24h(self):
        r = parse("0900")
        assert r.status in (Status.NORMALIZED, Status.WARN)
        assert (r.hour, r.minute) == (9, 0)

    def test_leading_zero_with_meridiem(self):
        r = parse("0154pm")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (13, 54)


# ---------------------------------------------------------------------------
# Classic 12-hour-clock edge cases
# ---------------------------------------------------------------------------

class TestNoonMidnightEdgeCases:
    def test_12_00_am_is_midnight(self):
        r = parse("12:00 AM")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (0, 0)

    def test_12_00_pm_is_noon(self):
        r = parse("12:00 PM")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (12, 0)

    def test_00_00_is_midnight(self):
        r = parse("00:00")
        assert r.status in (Status.NORMALIZED, Status.WARN)
        assert (r.hour, r.minute) == (0, 0)

    def test_noon_word(self):
        r = parse("noon")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (12, 0)

    def test_midnight_word(self):
        r = parse("midnight")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (0, 0)

    def test_noon_with_conflicting_meridiem_rejected(self):
        r = parse("noon am")
        assert r.status == Status.REJECT


# ---------------------------------------------------------------------------
# Bare hours -- genuinely ambiguous without a meridiem
# ---------------------------------------------------------------------------

class TestBareHours:
    def test_bare_5_is_clarify(self):
        r = parse("5")
        assert r.status == Status.CLARIFY
        assert len(r.candidates) == 2

    def test_at_5_strips_preposition_still_clarify(self):
        r = parse("at 5")
        assert r.status == Status.CLARIFY

    def test_bare_13_unambiguous_24h_only(self):
        # >12 has no valid 12-hour reading at all, so there is nothing to
        # clarify -- only one interpretation exists.
        r = parse("13")
        assert r.status in (Status.NORMALIZED, Status.WARN)
        assert (r.hour, r.minute) == (13, 0)

    def test_5pm_unambiguous(self):
        r = parse("5pm")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (17, 0)


# ---------------------------------------------------------------------------
# Spelled-out natural-language times
# ---------------------------------------------------------------------------

class TestSpelledOutTimes:
    def test_half_past_two_no_meridiem_is_clarify(self):
        r = parse("half past two")
        assert r.status == Status.CLARIFY

    def test_half_past_two_pm(self):
        r = parse("half past two pm")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (14, 30)

    def test_quarter_to_five_pm(self):
        r = parse("quarter to five pm")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (16, 45)

    def test_quarter_past_five_am(self):
        r = parse("quarter past five am")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (5, 15)

    def test_five_oclock_no_meridiem_is_clarify(self):
        r = parse("five o'clock")
        assert r.status == Status.CLARIFY


# ---------------------------------------------------------------------------
# Locale-specific formats
# ---------------------------------------------------------------------------

class TestLocaleFormats:
    def test_french_style_h_separator(self):
        r = parse("13h54")
        assert r.status in (Status.NORMALIZED, Status.WARN)
        assert (r.hour, r.minute) == (13, 54)

    def test_french_style_hour_only(self):
        r = parse("13h")
        assert r.status in (Status.NORMALIZED, Status.WARN)
        assert (r.hour, r.minute) == (13, 0)

    def test_french_style_low_hour_is_still_unambiguous_24h_by_convention(self):
        # Unlike bare "5" (ambiguous) or colon "5:00" (ambiguous), the 'h'
        # separator is itself a 24-hour-clock convention (French usage), so
        # even a <=12 hour is not ambiguous here.
        r = parse("5h")
        assert r.status in (Status.NORMALIZED, Status.WARN)
        assert (r.hour, r.minute) == (5, 0)


# ---------------------------------------------------------------------------
# Timezone / local-time indicators
# ---------------------------------------------------------------------------

class TestTimezoneIndicators:
    def test_z_suffix_is_utc(self):
        r = parse("1354Z")
        assert r.status == Status.NORMALIZED
        assert r.tz_policy == TzPolicy.EXPLICIT_UTC

    def test_utc_word_suffix(self):
        r = parse("13:54 UTC")
        assert r.status == Status.NORMALIZED
        assert r.tz_policy == TzPolicy.EXPLICIT_UTC

    def test_bare_no_marker_is_unspecified_and_warns(self):
        r = parse("13:54")
        assert r.tz_policy == TzPolicy.UNSPECIFIED
        assert r.status == Status.WARN


# ---------------------------------------------------------------------------
# Full-width / Unicode / non-Latin digits
# ---------------------------------------------------------------------------

class TestUnicodeDigits:
    def test_fullwidth_digits(self):
        # "1354" in fullwidth Unicode digits (U+FF11 etc.)
        r = parse("１３５４")
        assert r.status in (Status.NORMALIZED, Status.WARN)
        assert (r.hour, r.minute) == (13, 54)

    def test_devanagari_digits(self):
        # Devanagari digits for "1354" (१=1 ३=3 ५=5 ४=4)
        r = parse("१३५४")
        assert r.status in (Status.NORMALIZED, Status.WARN)
        assert (r.hour, r.minute) == (13, 54)

    def test_fullwidth_with_pm(self):
        r = parse("１５４pm")
        assert r.status == Status.NORMALIZED
        assert (r.hour, r.minute) == (13, 54)


# ---------------------------------------------------------------------------
# Conflicting expressions within one string
# ---------------------------------------------------------------------------

class TestConflictingExpressions:
    def test_two_different_explicit_times_rejected(self):
        r = parse("13:00 and 1400")
        assert r.status == Status.REJECT

    def test_same_time_twice_is_fine(self):
        # Not a real conflict -- both readings agree.
        r = parse("13:00 / 1300")
        assert r.status in (Status.NORMALIZED, Status.WARN)
        assert (r.hour, r.minute) == (13, 0)


# ---------------------------------------------------------------------------
# Garbage / unparseable input -- must fail closed, never guess
# ---------------------------------------------------------------------------

class TestUnparseable:
    def test_empty_string_rejected(self):
        r = parse("")
        assert r.status == Status.REJECT

    def test_pure_garbage_rejected(self):
        r = parse("banana")
        assert r.status == Status.REJECT

    def test_hour_60_rejected(self):
        r = parse("60:00")
        assert r.status == Status.REJECT


# ---------------------------------------------------------------------------
# Recurring events crossing a DST transition.
#
# America/New_York, 2026: spring-forward is 2026-03-08 (02:00 -> 03:00,
# so 02:00-02:59 does not exist that day); fall-back is 2026-11-01
# (02:00 -> 01:00, so 01:00-01:59 occurs twice). These are real IANA
# tzdata transition dates for that zone/year, not invented.
# ---------------------------------------------------------------------------

class TestRecurringDSTTransitions:
    def test_spring_forward_gap_is_nonexistent_not_silently_shifted(self):
        r = resolve_weekly_occurrence(date(2026, 3, 8), 2, 30, "America/New_York")
        assert r.status == OccurrenceStatus.NONEXISTENT
        assert r.instant_utc is None

    def test_fall_back_overlap_is_ambiguous_not_silently_picked(self):
        r = resolve_weekly_occurrence(date(2026, 11, 1), 1, 30, "America/New_York")
        assert r.status == OccurrenceStatus.AMBIGUOUS
        assert r.instant_utc is None

    def test_ordinary_week_before_transition_resolves_cleanly(self):
        r = resolve_weekly_occurrence(date(2026, 3, 1), 9, 0, "America/New_York")
        assert r.status == OccurrenceStatus.OK
        assert r.instant_utc is not None

    def test_ordinary_week_after_transition_resolves_cleanly(self):
        r = resolve_weekly_occurrence(date(2026, 3, 15), 9, 0, "America/New_York")
        assert r.status == OccurrenceStatus.OK
        assert r.instant_utc is not None

    def test_same_wall_clock_time_is_different_utc_offset_across_transition(self):
        # This is the actual bug real calendar apps hit: a *floating local*
        # 9am shifts by one hour in UTC terms across the transition, which
        # is CORRECT (the point of floating-local storage) -- verify the
        # UTC instants actually differ by an hour, proving we did not
        # accidentally store a fixed UTC instant instead.
        before = resolve_weekly_occurrence(date(2026, 3, 1), 9, 0, "America/New_York")
        after = resolve_weekly_occurrence(date(2026, 3, 15), 9, 0, "America/New_York")
        delta = after.instant_utc - before.instant_utc
        # 14 days minus 1 hour (lost to spring-forward), not exactly 14 days.
        assert delta.total_seconds() == 14 * 86400 - 3600


class TestNextOccurrence:
    """next_occurrence_utc: the "when does this one-shot reminder actually
    fire" resolution used by the Telegram-facing reminder feature -- not
    the recurring-series one above, so there's always exactly one right
    answer (today or tomorrow), never a CLARIFY-worthy ambiguity."""

    def test_time_later_today_fires_today(self):
        now = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)  # 17:00 Asia/Manila (UTC+8)
        r = next_occurrence_utc(20, 0, "Asia/Manila", now_utc=now)  # 20:00 local, still ahead
        assert r.status == OccurrenceStatus.OK
        assert r.instant_utc.astimezone(timezone.utc).date() == date(2026, 6, 1)

    def test_time_already_passed_today_rolls_to_tomorrow(self):
        now = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)  # 17:00 Asia/Manila
        r = next_occurrence_utc(8, 0, "Asia/Manila", now_utc=now)  # 08:00 local already passed
        assert r.status == OccurrenceStatus.OK
        local = r.instant_utc.astimezone(ZoneInfo("Asia/Manila"))
        assert (local.date(), local.hour, local.minute) == (date(2026, 6, 2), 8, 0)

    def test_exact_current_minute_counts_as_passed_rolls_to_tomorrow(self):
        # (hour, minute) <= now is treated as "already happened" -- a
        # reminder for right now would otherwise fire in the past the
        # instant this request finishes processing.
        now = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)  # 17:00 Asia/Manila exactly
        r = next_occurrence_utc(17, 0, "Asia/Manila", now_utc=now)
        local = r.instant_utc.astimezone(ZoneInfo("Asia/Manila"))
        assert local.date() == date(2026, 6, 2)

    def test_spring_forward_gap_retried_on_following_day(self):
        # 2026-03-08 02:30 America/New_York doesn't exist (spring-forward).
        # Unlike a recurring series, a one-shot "next" reminder has no
        # ambiguity about which date was meant -- just try the next day.
        now = datetime(2026, 3, 8, 6, 0, tzinfo=timezone.utc)  # before 02:30 EST that day
        r = next_occurrence_utc(2, 30, "America/New_York", now_utc=now)
        assert r.status == OccurrenceStatus.OK
        local = r.instant_utc.astimezone(ZoneInfo("America/New_York"))
        assert local.date() == date(2026, 3, 9)
