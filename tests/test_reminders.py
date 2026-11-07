"""Tests for reminders.py's storage logic, with Redis faked out."""
import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import reminders


def _member(chat_id, text):
    return json.dumps(
        {"chat_id": chat_id, "description": text, "fire_at_utc": "2026-11-04T15:00:00+00:00"}
    )


def _fake_redis(members, zrem_results):
    calls = []
    zrem = iter(zrem_results)

    async def command(client, url, token, *args):
        calls.append(args)
        if args[0] == "ZRANGEBYSCORE":
            return members
        if args[0] == "ZREM":
            return next(zrem)
        raise AssertionError(args)

    return command, calls


def _run(**kwargs):
    env = {"UPSTASH_REDIS_REST_URL": "https://example.invalid", "UPSTASH_REDIS_REST_TOKEN": "x"}
    with patch.dict("os.environ", env, clear=False):
        return asyncio.run(
            reminders.pop_due_reminders(now_utc=datetime(2026, 11, 4, 15, 8, tzinfo=timezone.utc), **kwargs)
        )


def test_asks_redis_for_at_most_the_limit():
    command, calls = _fake_redis([], [])
    with patch("reminders._redis_command", new=command):
        assert _run(limit=3) == []
    assert calls[0][-3:] == ("LIMIT", 0, 3)


def test_default_limit_keeps_one_check_short():
    command, calls = _fake_redis([], [])
    with patch("reminders._redis_command", new=command):
        _run()
    assert calls[0][-1] == reminders._MAX_PER_CHECK


def test_returns_reminders_it_removed():
    members = [_member(1, "tea"), _member(2, "call mom")]
    command, _ = _fake_redis(members, [1, 1])
    with patch("reminders._redis_command", new=command):
        due = _run()
    assert [(r.chat_id, r.description) for r in due] == [(1, "tea"), (2, "call mom")]


def test_skips_reminders_another_check_already_took():
    members = [_member(1, "tea"), _member(2, "call mom")]
    command, _ = _fake_redis(members, [0, 1])
    with patch("reminders._redis_command", new=command):
        due = _run()
    assert [r.description for r in due] == ["call mom"]


def test_every_redis_call_has_a_timeout():
    assert reminders._HTTP_TIMEOUT.read is not None
    assert reminders._HTTP_TIMEOUT.read <= 10
