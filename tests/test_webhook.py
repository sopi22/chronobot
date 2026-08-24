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
        with patch("api.index.Bot.send_message", new_callable=AsyncMock) as send:
            _post(client, "5pm call mom")
            send.assert_awaited_once()
            _, kwargs = send.await_args
            text = kwargs.get("text") or send.await_args.args[-1]
            assert "call mom" in text
            assert "17:00" in text

    def test_clarify_time_reply(self, client):
        with patch("api.index.Bot.send_message", new_callable=AsyncMock) as send:
            _post(client, "154")
            send.assert_awaited_once()
            _, kwargs = send.await_args
            text = kwargs.get("text") or send.await_args.args[-1]
            assert "01:54" in text
            assert "13:54" in text
