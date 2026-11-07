"""Reminder persistence on Upstash Redis's REST API.

A Vercel serverless function has no memory between invocations, so an
actual "remind me later" alarm needs somewhere durable to put the target
time until a separate trigger checks "is anything due yet?" (see
api/index.py's /api/check-reminders route). Talks to Upstash's REST API
directly over httpx instead of adding a Redis client dependency -- the
REST API IS the storage layer here; there's no separate TCP Redis to
connect to from a serverless function.

Deliberately outside chrono_engine: this is Telegram-shaped storage
(chat_id), not the offline-testable temporal-parsing core.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

_KEY = "chronobot:reminders"

# cron-job.org gives up on a call after 30 seconds and emails a failure.
# Every Redis call gets a short timeout, and one check handles at most
# _MAX_PER_CHECK reminders (the rest wait for the next check, one minute
# later), so a single check always finishes well inside that limit.
_HTTP_TIMEOUT = httpx.Timeout(5.0)
_MAX_PER_CHECK = 10


class RedisNotConfigured(RuntimeError):
    pass


def _rest_config() -> tuple[str, str]:
    url = os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN")
    if not url or not token:
        raise RedisNotConfigured(
            "No Redis REST credentials found. Expected UPSTASH_REDIS_REST_URL/"
            "UPSTASH_REDIS_REST_TOKEN or KV_REST_API_URL/KV_REST_API_TOKEN in the "
            "environment -- add the Upstash for Redis integration in Vercel's "
            "Marketplace (or Vercel KV), which injects these automatically."
        )
    return url, token


@dataclass(frozen=True)
class Reminder:
    chat_id: int
    description: str
    fire_at_utc: datetime


async def _redis_command(client: httpx.AsyncClient, url: str, token: str, *args: object) -> object:
    resp = await client.post(url, headers={"Authorization": f"Bearer {token}"}, json=list(args))
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"Redis error: {data['error']}")
    return data.get("result")


async def store_reminder(reminder: Reminder) -> None:
    url, token = _rest_config()
    member = json.dumps(
        {
            "chat_id": reminder.chat_id,
            "description": reminder.description,
            "fire_at_utc": reminder.fire_at_utc.isoformat(),
        }
    )
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        await _redis_command(client, url, token, "ZADD", _KEY, reminder.fire_at_utc.timestamp(), member)


async def pop_due_reminders(
    *, now_utc: datetime | None = None, limit: int = _MAX_PER_CHECK
) -> list[Reminder]:
    """Return up to `limit` reminders whose fire time has passed, removing
    each from storage as it's read off -- a reminder is popped, not peeked,
    so a retried/overlapping check-reminders call can't double-send it.
    A reminder only counts as ours if ZREM actually removed it (returns 1);
    if an overlapping call removed it first, that call sends it instead."""
    url, token = _rest_config()
    now_utc = now_utc or datetime.now(timezone.utc)

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        raw_members = await _redis_command(
            client, url, token, "ZRANGEBYSCORE", _KEY, 0, now_utc.timestamp(), "LIMIT", 0, limit
        )
        due: list[Reminder] = []
        for raw in raw_members or []:
            removed = await _redis_command(client, url, token, "ZREM", _KEY, raw)
            if not removed:
                continue
            data = json.loads(raw)
            due.append(
                Reminder(
                    chat_id=data["chat_id"],
                    description=data["description"],
                    fire_at_utc=datetime.fromisoformat(data["fire_at_utc"]),
                )
            )
    return due
