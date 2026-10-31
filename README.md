# chronobot

A Telegram bot that turns a text message into a scheduled time — and refuses to guess when the time you gave it is genuinely ambiguous. `1354pm` gets rejected outright (13 isn't a valid 12-hour hour); bare `154` gets a clarifying question instead of a silent 50/50 guess. It exists because most "smart" schedulers quietly pick a wrong time when your input is ambiguous, and a wrong reminder is worse than no reminder.

## Why a Telegram bot, not an app

This targets low-bandwidth, install-averse use: Telegram is usually already on the phone, and every interaction is one small text message — no APK download, no app-store account, no update cycle. A native Android app (reusing the same parsing engine) is a planned later phase, not built yet.

## Tech stack

- **Python 3.12**, stdlib only for the actual time-parsing engine (`re`, `datetime`, `zoneinfo`, `unicodedata`) — no heavy dependencies in the part that has to be correct.
- **[python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) 21.6** for the bot itself.
- **pytest** for tests (dev-only, not shipped).
- **Docker** — optional, for running the bot without setting up a Python environment by hand. Built and run end-to-end on 2026-08-24: image builds clean, container starts, authenticates with Telegram (`getMe` → 200 OK), and stays up polling.

## Inspired by

UX ideas only, no shared code: [Etar Calendar](https://github.com/Etar-Group/Etar-Calendar) (GPL-3.0). This project is a fresh, from-scratch codebase specifically so it can be MIT-licensed — a GPL-3.0 fork can't legally be relicensed to MIT, so nothing here is derived from Etar's source.

## Features

- Predictable time parsing (the same message always gives the same answer) — see [`chrono_engine/`](chrono_engine/) — never silently guesses AM/PM or a timezone.
- Handles 12h and 24h formats, compact digits (`1354`, `154pm`), spelled-out times (`noon`, `half past two`), maritime `LT`/`Z`/`UTC` markers, locale variants (`13.54`, `13h54`), and full-width/Devanagari Unicode digits.
- Recurring-event DST safety: occurrences are resolved against real IANA timezone data and flagged, never silently shifted, if a specific date's local time is nonexistent (spring-forward) or ambiguous (fall-back).
- On ambiguity, the bot asks — with the concrete candidate times, not an open-ended "please clarify."
- Description can go before or after the time — `call mom at 154` and `154 call mom` both work, `on` works the same as `at`. Only decides *where the time is*, never guesses what it means.
- An explicit trailing `tomorrow` ("wake me up at 7am tomorrow") is understood as a date, not more unparseable text.
- Reminders actually fire — on the Vercel deployment, a NORMALIZED/WARN time is stored and the bot messages that chat again the moment it's due, like a Telegram-native alarm. Multiple reminders are tracked independently. See "Reminders that actually message you later" below.
- 108 passing unit tests (the first set was written before the code).

## Setup & run

### Option A — plain Python

```bash
git clone <this-repo-url> chronobot
cd chronobot
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Get a bot token: open Telegram, message **@BotFather**, send `/newbot`, follow the prompts, copy the token it gives you into `.env`:

```
TELEGRAM_BOT_TOKEN=your-real-token-here
DEFAULT_TIMEZONE=Asia/Manila
```

Run it:

```bash
python -m bot.bot
```

The bot uses long-polling (no public URL/webhook needed), so it works from any machine with outbound internet — a laptop, a Raspberry Pi, a small VPS. **It's only reachable while this process is running** — closing the terminal stops it. For a machine you want to keep it running unattended on, use `tmux`/`screen`, a `systemd` service, or a process manager, and see "Where this should actually run" below.

Run the tests:

```bash
pip install -r requirements-dev.txt
pytest
```

### Option B — Docker

```bash
docker build -t chronobot .
docker run -d --name chronobot --env-file .env chronobot
```

Installs `requirements.txt`, copies `chrono_engine/` and `bot/`, runs `python -m bot.bot`, with `.env` passed at run time (never baked into the image). Verified end-to-end on 2026-09-01 (rebuilt against the current `requirements.txt`, including `tzdata`/`httpx`): `docker logs chronobot` should show `Application started` after a successful `getMe` call to Telegram's API.

**Warning if you also have the Vercel webhook deployed:** `python-telegram-bot`'s `run_polling()` calls `deleteWebhook()` automatically on startup. Running this container against the *same bot token* as an active Vercel deployment will silently take the live webhook offline the moment the container starts — confirmed the hard way while polishing this README. Use a second, disposable bot token (a fresh `/newbot` from @BotFather) for any local/Docker testing once you have a real webhook running in production, and re-run the `setWebhook` step in "Deploying on Vercel" below if this ever happens to your real bot.

### Where this should actually run

Option A/B above (long-polling) is for local development and testing only. This WSL machine sleeping, or Docker Desktop not running, takes the bot fully offline with no auto-recovery — confirmed the hard way on 2026-08-24, when a WSL restart silently killed the container for hours with no crash trace, and every message sent in that window (including `/about`) got no reply at all.

For an always-on bot that doesn't depend on this laptop being on, see **Deploying on Vercel** below — a genuinely free, no-credit-card host, using a webhook instead of long-polling so there's no process that needs to stay running on your own machine.

### Deploying on Vercel (free, always-on, no credit card)

This uses `api/index.py` — a small Flask function, separate from `bot/bot.py` — because Vercel's free tier only runs your code in response to an incoming HTTP request (no long-running process for long-polling to live in). It's not a rewrite of the parsing logic: `api/index.py` imports and reuses the exact same `chrono_engine` and the same tested helper functions from `bot/bot.py`, just receives updates via Telegram's webhook protocol instead of `getUpdates`.

**One-time setup (needs a human — I can't create accounts or click through a browser login):**

1. Go to [vercel.com](https://vercel.com) and sign up with **"Continue with GitHub"** — no credit card anywhere in this flow.
2. **Import this repo**: New Project → pick `sopi22/chronobot` from your GitHub repos → Deploy. Vercel auto-detects the Python function in `api/`.
3. In the project's **Settings → Environment Variables**, add:
   - `TELEGRAM_BOT_TOKEN` — same value as your local `.env`.
   - `DEFAULT_TIMEZONE` — same value as your local `.env` (e.g. `Asia/Manila`).
   - `WEBHOOK_SECRET_TOKEN` — same value as your local `.env`'s `WEBHOOK_SECRET_TOKEN` (generate one first if you haven't — see `.env.example`).
4. Redeploy (Vercel does this automatically after saving env vars, or click **Redeploy**).
5. Copy the deployment's URL (looks like `https://chronobot-<something>.vercel.app`) and tell me — I'll register it with Telegram (a one-time `setWebhook` API call) without needing the token pasted into chat, since I already have it locally in `.env`.

After that, the bot responds to messages the moment Vercel receives them from Telegram — no laptop, no Docker, no polling loop required. If you ever want to go back to local testing, Telegram only allows one active mode (webhook *or* long-polling) at a time — deleting the webhook (`deleteWebhook`) switches back to `python -m bot.bot` working again, and re-registering it switches back to Vercel.

### Reminders that actually message you later (Vercel deployment only)

A NORMALIZED or WARN time isn't just echoed back — it's stored, and the bot messages that chat again the moment it's due ("⏰ Reminder: ..."), like a Telegram-native alarm. Say "wake me up at 7am tomorrow" now, then immediately say "remind me to check my notes on 10:15 pm" — both are tracked independently; neither has to wait for the other.

This needs two extra pieces beyond the base webhook setup, since a serverless function has no memory between requests and Vercel's own free-tier cron only runs once a day (nowhere near precise enough for "at 5pm" — see [Vercel's docs](https://vercel.com/docs/cron-jobs/usage-and-pricing)):

1. **Somewhere to store pending reminders** — add the **Upstash for Redis** integration from Vercel's dashboard: your project → **Storage** tab → **Browse Marketplace** → Upstash for Redis → Create (free tier: 500K commands/month). This automatically injects `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` (or `KV_REST_API_URL` / `KV_REST_API_TOKEN`, depending on which product name Vercel shows) into your project's environment variables — no manual copy-paste needed on Vercel's side.
2. **Something to check "is anything due yet?" every minute** — a free external scheduler hitting `/api/check-reminders?token=<CRON_SECRET>`:
   - Add `CRON_SECRET` to Vercel's env vars (same value as your local `.env`'s `CRON_SECRET` — see `.env.example`).
   - Sign up at [cron-job.org](https://cron-job.org) (free, no card) and create a job: URL = `https://<your-deployment>.vercel.app/api/check-reminders?token=<your CRON_SECRET>`, method POST, schedule every 1 minute.

Once both are in place, a reminder fires within a minute of its target time, independent of whether the laptop's on, Docker's running, or you've sent any other message since.

## Example conversation

Generated by calling the bot's own reply-formatting code directly against real inputs — the exact same code path a live message takes, just without going through Telegram's servers for this particular transcript.

```
You:  call mom at 1pm
Bot:  ✅ 'call mom' at 13:00 (Asia/Manila)

You:  154
Bot:  ❓ '154' could mean 01:54 (AM) or 13:54 (PM) -- please specify am/pm or use 24-hour format.
  • AM: 01:54
  • PM: 13:54

You:  1354pm
Bot:  ❌ '1354pm': hour 13 is not valid with am/pm (must be 1-12)

You:  half past two pm
Bot:  ✅ 'That' at 14:30 (Asia/Manila)

You:  5pm call mom
Bot:  ✅ 'call mom' at 17:00 (Asia/Manila)
```

On the Vercel deployment, a NORMALIZED/WARN reply also schedules a real reminder (see "Reminders that actually message you later" above) — the confirmation gets an extra line, and a second message arrives on its own once the time comes, with no further input:

```
You:  wake me up at 7am tomorrow
Bot:  ✅ 'wake me up' at 07:00 (Asia/Manila)

      I'll message you here the moment it's time.

[... next morning, unprompted ...]
Bot:  ⏰ Reminder: wake me up
```

## Dependencies & licenses

Checked for MIT compatibility, not assumed:

| Dependency | License | Compatible? |
|---|---|---|
| `python-telegram-bot` 21.6 | LGPL-3.0 | Yes — used as an unmodified library dependency (`pip install` + `import`, no vendored/modified source). LGPL explicitly permits this without requiring the using application to also be LGPL. |
| `flask` 3.1.3 | BSD-3-Clause | Yes — permissive, MIT-compatible. Only used by `api/index.py` (Vercel webhook entrypoint). |
| `tzdata` | Apache-2.0 | Yes — permissive, MIT-compatible. IANA timezone database as a pip package; only actually needed on Vercel's Python runtime, which doesn't ship the system tzdata that `zoneinfo` (`chrono_engine/dst.py`) relies on. |
| `httpx` | BSD-3-Clause | Yes — permissive, MIT-compatible. Already pulled in transitively by `python-telegram-bot`; declared directly here because `reminders.py` also calls it explicitly, to talk to Upstash Redis's REST API. |
| `pytest` | MIT | Yes — dev/test-only, not distributed as part of the running application either way. |
| `python:3.12-slim` (Docker base image) | Debian/various (base OS) | Not a licensing concern — used only as a runtime environment, not incorporated into this project's source. |

Remaining transitive dependencies pulled in by `python-telegram-bot` (`httpcore`, `h11`, `anyio`, `certifi`, `idna`) are standard, widely-used permissive or weak-copyleft (file-level, e.g. `certifi`'s MPL-2.0) packages common throughout the Python ecosystem — spot-checked, no conflicts found.

## License

MIT — see [LICENSE](LICENSE).

Copyright (c) 2026 Jhoana Sophia Munar
