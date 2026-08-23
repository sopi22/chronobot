"""Thin Telegram bot: message handling and reply formatting only.

No time-parsing logic lives here. Every message is routed straight into
chrono_engine.parser.parse_time(); this file only decides what to say back
based on the returned Status. If you're looking for how "1354pm" gets
rejected or "154" gets a clarify question, that's in chrono_engine, not
here.
"""
from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from chrono_engine.canonical import Status
from chrono_engine.parser import parse_time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ABOUT_TEXT = (
    "chronobot -- a Telegram bot that never guesses your time.\n\n"
    "Built by Jhoana Sophia Munar.\n"
    "License: MIT.\n"
    "Inspired by Etar (github.com/Etar-Group/Etar-Calendar) for UX ideas only -- "
    "no Etar code is used (Etar is GPL-3.0; this project is a fresh MIT codebase)."
)

START_TEXT = (
    "Tell me a time, e.g. '1pm', '13:54', 'half past two pm', 'quarter to five', 'noon'.\n\n"
    "Add a description before ' at ' if you like: 'call mom at 154'.\n\n"
    "If your time is ambiguous (like '5' or '154' with no am/pm), I'll ask -- "
    "I never guess. /about for details."
)


def _load_dotenv(path: str = ".env") -> None:
    """Tiny manual .env loader -- avoids adding python-dotenv as a dependency
    for something this small."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def _split_description_and_time(text: str) -> tuple[str | None, str]:
    """Purely syntactic split on the last ' at ' -- not intent extraction,
    not LLM-based. If absent, the whole message is treated as the time
    expression."""
    lowered = text.lower()
    idx = lowered.rfind(" at ")
    if idx == -1:
        return None, text
    description = text[:idx].strip()
    time_part = text[idx + len(" at "):].strip()
    return (description or None), time_part


def _format_reply(result, description: str | None) -> str:
    label = f"'{description}'" if description else "That"
    if result.status == Status.NORMALIZED:
        tz_note = f" ({result.tz_name})" if result.tz_name else ""
        return f"✅ {label} at {result.hour:02d}:{result.minute:02d}{tz_note}"
    if result.status == Status.WARN:
        return (
            f"⚠️ {label} at {result.hour:02d}:{result.minute:02d} -- "
            f"no timezone given, defaulted to {result.tz_name}. "
            "Add 'LT' or a timezone if that's wrong."
        )
    if result.status == Status.CLARIFY:
        lines = [f"❓ {result.message}"]
        for c in result.candidates:
            lines.append(f"  • {c.label}: {c.hour:02d}:{c.minute:02d}")
        return "\n".join(lines)
    return f"❌ {result.message}"  # REJECT


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(START_TEXT)


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(ABOUT_TEXT)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    description, time_part = _split_description_and_time(text)
    default_tz = context.bot_data["default_tz"]
    result = parse_time(time_part, default_tz=default_tz)
    await update.message.reply_text(_format_reply(result, description))


def build_application() -> Application:
    _load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and fill it in "
            "(get a token from @BotFather on Telegram)."
        )
    default_tz = os.environ.get("DEFAULT_TIMEZONE", "UTC")

    application = Application.builder().token(token).build()
    application.bot_data["default_tz"] = default_tz
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return application


def main() -> None:
    app = build_application()
    logger.info("chronobot starting (long-polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()
