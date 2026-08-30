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
import os

from flask import Flask, abort, request
from telegram import Bot, Update

from bot.bot import ABOUT_TEXT, START_TEXT, _format_reply, _split_description_and_time
from chrono_engine.parser import parse_time

app = Flask(__name__)


@app.post("/")
@app.post("/api/index")
@app.post("/api/webhook")
def webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != os.environ["WEBHOOK_SECRET_TOKEN"]:
        abort(403)

    bot = Bot(os.environ["TELEGRAM_BOT_TOKEN"])
    update = Update.de_json(request.get_json(force=True), bot)
    if update.message and update.message.text:
        asyncio.run(_handle(update, bot))
    return "", 200


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
    result = parse_time(time_part, default_tz=default_tz)
    await bot.send_message(chat_id, _format_reply(result, description))
