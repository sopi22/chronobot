"""Tests for the Vercel serverless webhook entrypoint (api/index.py).

Uses Flask's test client and mocks telegram.Bot.send_message -- no real
network call to Telegram happens here, same offline-testable spirit as
tests/test_bot.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from api.index import app


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:fake-token-not-real")
    monkeypatch.setenv("WEBHOOK_SECRET_TOKEN", "test-secret")
    monkeypatch.setenv("DEFAULT_TIMEZONE", "Asia/Manila")
    monkeypatch.setenv("CRON_SECRET", "test-cron-secret")


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    return app.test_client()


def _update(text: str, chat_id: int = 999) -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": 0,
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


def _post(client, text: str, secret: str = "test-secret"):
    headers = {}
    if secret is not None:
        headers["X-Telegram-Bot-Api-Secret-Token"] = secret
    return client.post("/api/webhook", json=_update(text), headers=headers)


class TestSecretTokenGuard:
    def test_missing_secret_token_rejected(self, client):
        resp = _post(client, "1pm", secret=None)
        assert resp.status_code == 403

    def test_wrong_secret_token_rejected(self, client):
        resp = _post(client, "1pm", secret="wrong")
        assert resp.status_code == 403


class TestCommands:
    def test_start_command(self, client):
        with patch("api.index.Bot.send_message", new_callable=AsyncMock) as send:
            resp = _post(client, "/start")
            assert resp.status_code == 200
            send.assert_awaited_once()
            args, kwargs = send.await_args
            assert "never guesses" not in "".join(str(a) for a in args)  # sanity: not About text
            assert 999 in args or kwargs.get("chat_id") == 999

    def test_about_command(self, client):
        with patch("api.index.Bot.send_message", new_callable=AsyncMock) as send:
            _post(client, "/about")
            send.assert_awaited_once()

    def test_unknown_command_silently_ignored(self, client):
        with patch("api.index.Bot.send_message", new_callable=AsyncMock) as send:
            resp = _post(client, "/foo")
            assert resp.status_code == 200
            send.assert_not_awaited()


class TestTimeMessages:
    def test_normalized_time_reply(self, client):
        with (
            patch("api.index.Bot.send_message", new_callable=AsyncMock) as send,
            patch("api.index.reminders.store_reminder", new_callable=AsyncMock) as store,
        ):
            _post(client, "5pm call mom")
            send.assert_awaited_once()
            _, kwargs = send.await_args
            text = kwargs.get("text") or send.await_args.args[-1]
            assert "call mom" in text
            assert "17:00" in text
            # A NORMALIZED time is schedulable -- it should have been stored
            # as a reminder, not just echoed back as a confirmation.
            store.assert_awaited_once()
            reminder = store.await_args.args[0]
            assert reminder.chat_id == 999
            assert reminder.description == "call mom"

    def test_clarify_time_reply(self, client):
        with (
            patch("api.index.Bot.send_message", new_callable=AsyncMock) as send,
            patch("api.index.reminders.store_reminder", new_callable=AsyncMock) as store,
        ):
            _post(client, "154")
            send.assert_awaited_once()
            _, kwargs = send.await_args
            text = kwargs.get("text") or send.await_args.args[-1]
            assert "01:54" in text
            assert "13:54" in text
            # Ambiguous -- there is no single time to schedule against.
            store.assert_not_awaited()

    def test_on_and_tomorrow_phrasing_schedules_correctly(self, client):
        with (
            patch("api.index.Bot.send_message", new_callable=AsyncMock) as send,
            patch("api.index.reminders.store_reminder", new_callable=AsyncMock) as store,
        ):
            _post(client, "wake me up at 7am tomorrow")
            send.assert_awaited_once()
            store.assert_awaited_once()
            reminder = store.await_args.args[0]
            assert reminder.description == "wake me up"
            # force_tomorrow means the stored fire time is strictly after
            # "now" by at least several hours no matter when this test
            # actually runs -- a same-day 7am occurrence would be within
            # the next 24h either way, so check it landed on a distinct
            # calendar date from today in Asia/Manila (the test default_tz).
            from datetime import datetime
            from zoneinfo import ZoneInfo

            fire_local = reminder.fire_at_utc.astimezone(ZoneInfo("Asia/Manila"))
            now_local = datetime.now(ZoneInfo("Asia/Manila"))
            assert fire_local.date() > now_local.date()


    @pytest.mark.parametrize(
        "text",
        [
            "remind me to re schedule my appointment at 11:59 pm today",
            "remind me to re schedule my appointment at 2359H today",
        ],
    )
    def test_today_and_hours_suffix_schedule_today(self, client, text):
        # The exact phrasings that failed in real use on 2026-10-25.
        # 23:59 is still ahead "today" unless the suite runs in that minute.
        from datetime import datetime
        from zoneinfo import ZoneInfo

        with (
            patch("api.index.Bot.send_message", new_callable=AsyncMock) as send,
            patch("api.index.reminders.store_reminder", new_callable=AsyncMock) as store,
        ):
            _post(client, text)
            send.assert_awaited_once()
            store.assert_awaited_once()
            reminder = store.await_args.args[0]
            assert reminder.description == "remind me to re schedule my appointment"
            fire_local = reminder.fire_at_utc.astimezone(ZoneInfo("Asia/Manila"))
            assert fire_local.date() == datetime.now(ZoneInfo("Asia/Manila")).date()
            assert (fire_local.hour, fire_local.minute) == (23, 59)

    def test_today_time_already_passed_is_refused(self, client):
        with (
            patch("api.index.Bot.send_message", new_callable=AsyncMock) as send,
            patch("api.index.reminders.store_reminder", new_callable=AsyncMock) as store,
        ):
            _post(client, "call the agent at 12:00 am today")
            store.assert_not_awaited()
            text = send.await_args.kwargs.get("text") or send.await_args.args[-1]
            assert "already passed today" in text


class TestCheckReminders:
    def _hit(self, client, token: str | None = "test-cron-secret"):
        url = "/api/check-reminders" + (f"?token={token}" if token is not None else "")
        return client.post(url)

    def test_secret_in_header_works(self, client):
        with patch("api.index.reminders.pop_due_reminders", new_callable=AsyncMock, return_value=[]):
            resp = client.post("/api/check-reminders", headers={"Authorization": "Bearer test-cron-secret"})
        assert resp.status_code == 200

    def test_wrong_secret_in_header_rejected(self, client):
        resp = client.post("/api/check-reminders", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 403

    def test_missing_token_rejected(self, client):
        resp = self._hit(client, token=None)
        assert resp.status_code == 403

    def test_wrong_token_rejected(self, client):
        resp = self._hit(client, token="wrong")
        assert resp.status_code == 403

    def test_fires_due_reminders_and_reports_count(self, client):
        from reminders import Reminder
        from datetime import datetime, timezone

        due = [
            Reminder(chat_id=111, description="water the plants", fire_at_utc=datetime.now(timezone.utc)),
            Reminder(chat_id=222, description="call mom", fire_at_utc=datetime.now(timezone.utc)),
        ]
        with (
            patch("api.index.Bot.send_message", new_callable=AsyncMock) as send,
            patch("api.index.reminders.pop_due_reminders", new_callable=AsyncMock, return_value=due) as pop,
        ):
            resp = self._hit(client)
            assert resp.status_code == 200
            assert resp.get_json() == {"sent": 2}
            pop.assert_awaited_once()
            assert send.await_count == 2
            sent_chat_ids = {c.args[0] for c in send.await_args_list}
            assert sent_chat_ids == {111, 222}

    def test_nothing_due_sends_nothing(self, client):
        with (
            patch("api.index.Bot.send_message", new_callable=AsyncMock) as send,
            patch("api.index.reminders.pop_due_reminders", new_callable=AsyncMock, return_value=[]),
        ):
            resp = self._hit(client)
            assert resp.status_code == 200
            assert resp.get_json() == {"sent": 0}
            send.assert_not_awaited()

    def test_one_failed_send_does_not_block_the_rest_of_the_batch(self, client):
        # A dead/blocked chat (or any transient Telegram API error) must not
        # crash the whole check -- other due reminders sharing this tick
        # still need to fire, and the reminder was already popped from
        # storage either way, so there's nothing to retry for the failed one.
        from reminders import Reminder
        from datetime import datetime, timezone
        from telegram.error import BadRequest

        due = [
            Reminder(chat_id=111, description="dead chat", fire_at_utc=datetime.now(timezone.utc)),
            Reminder(chat_id=222, description="call mom", fire_at_utc=datetime.now(timezone.utc)),
        ]
        with (
            patch(
                "api.index.Bot.send_message",
                new_callable=AsyncMock,
                side_effect=[BadRequest("Chat not found"), None],
            ) as send,
            patch("api.index.reminders.pop_due_reminders", new_callable=AsyncMock, return_value=due),
        ):
            resp = self._hit(client)
            assert resp.status_code == 200
            # Only the successful send counts -- not a 500, not silently
            # claiming both went out.
            assert resp.get_json() == {"sent": 1}
            assert send.await_count == 2
