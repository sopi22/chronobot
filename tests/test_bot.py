"""Tests for the bot's pure logic only (message splitting, reply formatting).
No live Telegram connection is needed or used here."""
from __future__ import annotations

from chrono_engine.canonical import Candidate, ParsedTime, Status, TzPolicy
from chrono_engine.parser import parse_time
from bot.bot import _format_reply, _split_description_and_time


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
