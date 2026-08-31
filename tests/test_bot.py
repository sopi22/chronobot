"""Tests for the bot's pure logic only (message splitting, reply formatting).
No live Telegram connection is needed or used here."""
from __future__ import annotations

from chrono_engine.canonical import Candidate, ParsedTime, Status, TzPolicy
from chrono_engine.parser import parse_time
from bot.bot import _extract_tomorrow, _format_reply, _split_description_and_time


class TestSplitDescriptionAndTime:
    def test_with_description(self):
        desc, time_part = _split_description_and_time("call mom at 154")
        assert desc == "call mom"
        assert time_part == "154"

    def test_without_description(self):
        desc, time_part = _split_description_and_time("1354")
        assert desc is None
        assert time_part == "1354"

    def test_case_insensitive_at(self):
        desc, time_part = _split_description_and_time("call mom AT 1pm")
        assert desc == "call mom"
        assert time_part == "1pm"

    def test_time_first_no_at(self):
        desc, time_part = _split_description_and_time("10:24am remind me to call Sophie")
        assert desc == "remind me to call Sophie"
        assert time_part == "10:24am"

    def test_time_first_bare_ambiguous(self):
        desc, time_part = _split_description_and_time("154 remind me to call mom")
        assert desc == "remind me to call mom"
        assert time_part == "154"

    def test_time_first_multiword_lexicon(self):
        desc, time_part = _split_description_and_time("half past two pm call mom")
        assert desc == "call mom"
        assert time_part == "half past two pm"

    def test_no_time_anywhere_falls_back_to_whole_text(self):
        desc, time_part = _split_description_and_time("how are you doing today")
        assert desc is None
        assert time_part == "how are you doing today"

    def test_at_still_wins_over_leading_time_heuristic(self):
        desc, time_part = _split_description_and_time("call mom at 5pm")
        assert desc == "call mom"
        assert time_part == "5pm"

    def test_on_works_same_as_at(self):
        desc, time_part = _split_description_and_time("remind me to check my notes on 10:15 pm")
        assert desc == "remind me to check my notes"
        assert time_part == "10:15 pm"

    def test_rightmost_separator_wins_when_both_present(self):
        # "on" earlier in the description shouldn't be mistaken for the
        # time-introducing separator -- only the last "at"/"on" is.
        desc, time_part = _split_description_and_time("put the report on my desk at 5pm")
        assert desc == "put the report on my desk"
        assert time_part == "5pm"


class TestExtractTomorrow:
    def test_strips_trailing_tomorrow(self):
        time_part, tomorrow = _extract_tomorrow("7am tomorrow")
        assert time_part == "7am"
        assert tomorrow is True

    def test_case_insensitive(self):
        time_part, tomorrow = _extract_tomorrow("7am TOMORROW")
        assert time_part == "7am"
        assert tomorrow is True

    def test_no_tomorrow_present(self):
        time_part, tomorrow = _extract_tomorrow("7am")
        assert time_part == "7am"
        assert tomorrow is False

    def test_tomorrow_mid_string_not_stripped(self):
        # Only a trailing "tomorrow" is a date modifier; elsewhere in the
        # text it's just part of the words being parsed (and chrono_engine
        # will REJECT it as a time on its own merits, not this function's
        # job to decide).
        time_part, tomorrow = _extract_tomorrow("tomorrow at 7am")
        assert time_part == "tomorrow at 7am"
        assert tomorrow is False


class TestFormatReply:
    def test_normalized_reply(self):
        result = parse_time("1pm", default_tz="Asia/Manila")
        text = _format_reply(result, "call mom")
        assert "call mom" in text
        assert "13:00" in text
        assert "✅" in text

    def test_clarify_reply_lists_candidates(self):
        result = parse_time("154", default_tz="Asia/Manila")
        text = _format_reply(result, None)
        assert "❓" in text
        assert "01:54" in text
        assert "13:54" in text

    def test_reject_reply(self):
        result = parse_time("25:00", default_tz="Asia/Manila")
        text = _format_reply(result, None)
        assert "❌" in text

    def test_warn_reply_mentions_default_tz(self):
        result = parse_time("13:54", default_tz="Asia/Manila")
        text = _format_reply(result, None)
        assert "⚠️" in text
        assert "Asia/Manila" in text
