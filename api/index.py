"""Vercel serverless entrypoint: receives Telegram's webhook POSTs.

Deliberately does NOT use telegram.ext.Application/Updater's built-in
webhook server (that's for bot/bot.py's long-running local/Docker polling
process). A serverless function has no persistent event loop to host it,
and PTB's run_webhook() always calls Bot.set_webhook() with a URL derived
from listen/port -- fine for a real server, actively wrong here, since it
would silently overwrite the real webhook with an unreachable local one on
every cold start. So this talks to telegram.Bot directly, one Update per
invocation, and reuses only the pure, framework-agnostic logic already
covered by tests/test_bot.py.

Registering the webhook itself (pointing Telegram at this function's real
public URL) is a separate, one-time step done after deploy -- see README's
Vercel section -- not something this file does at import or request time.
"""
from __future__ import annotations

import asyncio
import logging
import os

from flask import Flask, abort, request
from telegram import Bot, Update
from telegram.error import TelegramError

import reminders
from bot.bot import ABOUT_TEXT, START_TEXT, _extract_tomorrow, _format_reply, _split_description_and_time
from chrono_engine.canonical import Status
from chrono_engine.dst import OccurrenceStatus, next_occurrence_utc
from chrono_engine.parser import parse_time

logger = logging.getLogger(__name__)

app = Flask(__name__)


@app.post("/")
@app.post("/api/index")
@app.post("/api/webhook")
@app.post("/api/check-reminders")
def entrypoint():
    """Single dispatcher for both jobs this function does, deliberately not
    branching on request.path: Vercel's rewrite ("/(.*)"  ->  "/api/index" in
    vercel.json) routes every incoming path to whichever view function is
    bound to "/api/index" -- confirmed via `vercel logs`, a request actually
    sent to /api/check-reminders was logged and handled as /api/index. Two
    separately-routed view functions would mean one of them silently never
    runs. Branching on which credential is present (a `token` query param
    vs. Telegram's secret-token header) works regardless of what the
    platform does with the path, and is also literally who's calling: only
    cron-job.org sends `token`, only Telegram sends that header."""
    if request.args.get("token") is not None:
        return _check_reminders()
    return _webhook()


def _webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != os.environ["WEBHOOK_SECRET_TOKEN"]:
        abort(403)

    bot = Bot(os.environ["TELEGRAM_BOT_TOKEN"])
    update = Update.de_json(request.get_json(force=True), bot)
    if update.message and update.message.text:
        asyncio.run(_handle(update, bot))
    return "", 200


def _check_reminders():
    """Fire every due reminder. Not user-facing -- meant to be hit on a
    schedule by an external trigger (e.g. cron-job.org), since Vercel's own
    Hobby-plan cron jobs only run once a day, nowhere near precise enough
    for "message me at 5pm"."""
    if request.args.get("token") != os.environ["CRON_SECRET"]:
        abort(403)

    bot = Bot(os.environ["TELEGRAM_BOT_TOKEN"])
    sent = asyncio.run(_fire_due_reminders(bot))
    return {"sent": sent}, 200


async def _fire_due_reminders(bot: Bot) -> int:
    """Send every popped-due reminder, isolating failures per-reminder: one
    bad send (blocked chat, transient Telegram API error) must not abort the
    rest of the batch -- the reminder is already popped from storage by the
    time send is attempted either way, so there's nothing to retry for it,
    but *other* due reminders sharing this same check-reminders tick still
    need to go out."""
    due = await reminders.pop_due_reminders()
    sent = 0
    for reminder in due:
        try:
            await bot.send_message(reminder.chat_id, f"⏰ Reminder: {reminder.description}")
            sent += 1
        except TelegramError:
            logger.exception("Failed to send reminder to chat_id=%s", reminder.chat_id)
    return sent


async def _handle(update: Update, bot: Bot) -> None:
    text = update.message.text
    chat_id = update.message.chat_id
    default_tz = os.environ.get("DEFAULT_TIMEZONE", "UTC")

    if text.startswith("/"):
        if text.startswith("/start"):
            await bot.send_message(chat_id, START_TEXT)
        elif text.startswith("/about"):
            await bot.send_message(chat_id, ABOUT_TEXT)
        # Any other command: silently ignored, same as the polling bot
        # (no handler registered for it there either).
        return

    description, time_part = _split_description_and_time(text, default_tz=default_tz)
    time_part, force_tomorrow = _extract_tomorrow(time_part)
    result = parse_time(time_part, default_tz=default_tz)
    reply = _format_reply(result, description)

    if result.status in (Status.NORMALIZED, Status.WARN):
        occurrence = next_occurrence_utc(
            result.hour, result.minute, result.tz_name, force_tomorrow=force_tomorrow
        )
        if occurrence.status == OccurrenceStatus.OK:
            await reminders.store_reminder(
                reminders.Reminder(
                    chat_id=chat_id,
                    description=description or text,
                    fire_at_utc=occurrence.instant_utc,
                )
            )
            reply += "\n\nI'll message you here the moment it's time."
        else:
            reply += f"\n\n⚠️ Couldn't schedule this as a reminder: {occurrence.message}"

    await bot.send_message(chat_id, reply)
