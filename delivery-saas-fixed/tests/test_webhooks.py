"""Tests for handlers/webhooks.py."""
import sys
import os
import json
import hmac
import hashlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from aiohttp import web


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_WHOP_SECRET = "test-whop-secret"
TEST_POSTER_SECRET = "test-poster-secret"


def _make_hmac_sig(secret: str, body: bytes) -> str:
    """Обчислює HMAC-SHA256 підпис для тестів."""
    return hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()


def _make_whop_request(body: dict, secret: str = TEST_WHOP_SECRET):
    """Build a fake aiohttp Request-like object with a valid Whop HMAC signature."""
    body_bytes = json.dumps(body).encode('utf-8')
    sig = _make_hmac_sig(secret, body_bytes)
    req = MagicMock()
    req.read = AsyncMock(return_value=body_bytes)
    req.headers = {"X-Whop-Signature": sig}
    return req


def _make_poster_request(body: dict, query_params: dict = None, secret: str = TEST_POSTER_SECRET):
    """Build a fake aiohttp Request-like object with a valid Poster HMAC signature."""
    body_bytes = json.dumps(body).encode('utf-8')
    sig = _make_hmac_sig(secret, body_bytes)
    req = MagicMock()
    req.read = AsyncMock(return_value=body_bytes)
    req.headers = {"X-Poster-Signature": sig}
    req.query = query_params or {}
    return req


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWhopWebhookHandler:
    @pytest.mark.asyncio
    async def test_activates_subscription_on_membership_went_active(self):
        body = {
            "event_type": "membership.went_active",
            "data": {
                "id": "mem-123",
                "custom_fields": {
                    "biz_id": "biz-abc",
                    "tg_user_id": "77777",
                },
            },
        }
        req = _make_whop_request(body)

        with patch("database.activate_whop_subscription") as mock_activate, \
             patch("handlers.webhooks.bot") as mock_bot, \
             patch("handlers.webhooks.WHOP_WEBHOOK_SECRET", TEST_WHOP_SECRET):
            mock_bot.send_message = AsyncMock()
            from handlers import webhooks
            response = await webhooks.whop_webhook_handler(req)

        mock_activate.assert_called_once_with("biz-abc", "pro", "mem-123")
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_sends_message_to_user_on_activation(self):
        body = {
            "event_type": "membership.went_active",
            "data": {
                "id": "mem-456",
                "custom_fields": {
                    "biz_id": "biz-xyz",
                    "tg_user_id": "12345",
                },
            },
        }
        req = _make_whop_request(body)

        with patch("database.activate_whop_subscription"), \
             patch("handlers.webhooks.bot") as mock_bot, \
             patch("handlers.webhooks.WHOP_WEBHOOK_SECRET", TEST_WHOP_SECRET):
            mock_bot.send_message = AsyncMock()
            from handlers import webhooks
            await webhooks.whop_webhook_handler(req)

        mock_bot.send_message.assert_called_once()
        call_kwargs = mock_bot.send_message.call_args.kwargs
        assert call_kwargs.get("chat_id") == 12345

    @pytest.mark.asyncio
    async def test_ignores_other_event_types(self):
        body = {"event_type": "membership.went_inactive", "data": {}}
        req = _make_whop_request(body)

        with patch("database.activate_whop_subscription") as mock_activate, \
             patch("handlers.webhooks.bot"), \
             patch("handlers.webhooks.WHOP_WEBHOOK_SECRET", TEST_WHOP_SECRET):
            from handlers import webhooks
            response = await webhooks.whop_webhook_handler(req)

        mock_activate.assert_not_called()
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_returns_500_when_secret_not_configured(self):
        """Якщо WHOP_WEBHOOK_SECRET не задано — повертати 500."""
        body = {"event_type": "membership.went_active", "data": {}}
        req = _make_whop_request(body)

        with patch("handlers.webhooks.WHOP_WEBHOOK_SECRET", ""):
            from handlers import webhooks
            response = await webhooks.whop_webhook_handler(req)

        assert response.status == 500

    @pytest.mark.asyncio
    async def test_returns_403_on_invalid_signature(self):
        """Невірний підпис — повертати 403."""
        body = {"event_type": "membership.went_active", "data": {}}
        body_bytes = json.dumps(body).encode('utf-8')
        req = MagicMock()
        req.read = AsyncMock(return_value=body_bytes)
        req.headers = {"X-Whop-Signature": "invalid-signature"}

        with patch("handlers.webhooks.WHOP_WEBHOOK_SECRET", TEST_WHOP_SECRET):
            from handlers import webhooks
            response = await webhooks.whop_webhook_handler(req)

        assert response.status == 403

    @pytest.mark.asyncio
    async def test_returns_500_on_exception(self):
        req = MagicMock()
        req.read = AsyncMock(side_effect=Exception("Boom"))
        req.headers = {"X-Whop-Signature": "any"}

        with patch("handlers.webhooks.WHOP_WEBHOOK_SECRET", TEST_WHOP_SECRET):
            from handlers import webhooks
            response = await webhooks.whop_webhook_handler(req)

        assert response.status == 500

    @pytest.mark.asyncio
    async def test_handles_missing_biz_id_gracefully(self):
        """If biz_id is absent, should NOT call activate_whop_subscription."""
        body = {
            "event_type": "membership.went_active",
            "data": {
                "id": "mem-789",
                "custom_fields": {},
            },
        }
        req = _make_whop_request(body)

        with patch("database.activate_whop_subscription") as mock_activate, \
             patch("handlers.webhooks.bot"), \
             patch("handlers.webhooks.WHOP_WEBHOOK_SECRET", TEST_WHOP_SECRET):
            from handlers import webhooks
            response = await webhooks.whop_webhook_handler(req)

        mock_activate.assert_not_called()
        assert response.status == 200


class TestPosterWebhookHandler:
    """
    Poster token береться з БД (biz["poster_token"]), НЕ з env.
    Всі тести мокають db.get_business_by_id для контролю токена.
    """

    def _mock_biz(self, poster_token=TEST_POSTER_SECRET):
        return {"poster_token": poster_token, "currency": "zł", "owner_id": 999}

    @pytest.mark.asyncio
    async def test_returns_400_when_biz_id_missing(self):
        req = _make_poster_request({}, query_params={})
        from handlers import webhooks
        response = await webhooks.poster_webhook_handler(req)
        assert response.status == 400

    @pytest.mark.asyncio
    async def test_returns_404_when_business_not_found(self):
        body = {}
        req = _make_poster_request(body, query_params={"biz_id": "unknown-biz"})

        with patch("database.get_business_by_id", new_callable=AsyncMock, return_value=None):
            from handlers import webhooks
            response = await webhooks.poster_webhook_handler(req)

        assert response.status == 404

    @pytest.mark.asyncio
    async def test_returns_403_when_poster_token_not_configured(self):
        """Якщо biz не має poster_token — повертати 403."""
        body = {}
        req = _make_poster_request(body, query_params={"biz_id": "biz-1"})

        biz_no_token = {"poster_token": "", "currency": "zł", "owner_id": 999}
        with patch("database.get_business_by_id", new_callable=AsyncMock, return_value=biz_no_token):
            from handlers import webhooks
            response = await webhooks.poster_webhook_handler(req)

        assert response.status == 403

    @pytest.mark.asyncio
    async def test_sends_message_to_managers_on_new_order(self):
        body = {
            "object": "incoming_order",
            "action": "added",
            "data": {
                "client_name": "Alice",
                "phone": "+48100200300",
                "address": "Street 1",
                "total_sum": 5000,
                "comment": "Ring doorbell",
            },
        }
        req = _make_poster_request(body, query_params={"biz_id": "biz-1"})

        managers_result = MagicMock(data=[{"user_id": 111}, {"user_id": 222}])
        mock_table = MagicMock()
        mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = managers_result

        with patch("database.get_business_by_id", new_callable=AsyncMock, return_value=self._mock_biz()), \
             patch("database.supabase") as mock_supabase, \
             patch("handlers.webhooks.bot") as mock_bot:
            mock_supabase.table.return_value = mock_table
            mock_bot.send_message = AsyncMock()
            from handlers import webhooks
            response = await webhooks.poster_webhook_handler(req)

        assert response.status == 200
        assert mock_bot.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_ignores_non_new_order_events(self):
        body = {"object": "incoming_order", "action": "updated", "data": {}}
        req = _make_poster_request(body, query_params={"biz_id": "biz-1"})

        with patch("database.get_business_by_id", new_callable=AsyncMock, return_value=self._mock_biz()), \
             patch("handlers.webhooks.bot") as mock_bot:
            mock_bot.send_message = AsyncMock()
            from handlers import webhooks
            response = await webhooks.poster_webhook_handler(req)

        mock_bot.send_message.assert_not_called()
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_returns_403_on_invalid_signature(self):
        """Невірний підпис — повертати 403."""
        body = {"object": "incoming_order", "action": "added", "data": {}}
        body_bytes = json.dumps(body).encode('utf-8')
        req = MagicMock()
        req.read = AsyncMock(return_value=body_bytes)
        req.headers = {"X-Poster-Signature": "invalid-signature"}
        req.query = {"biz_id": "biz-1"}

        with patch("database.get_business_by_id", new_callable=AsyncMock, return_value=self._mock_biz()):
            from handlers import webhooks
            response = await webhooks.poster_webhook_handler(req)

        assert response.status == 403

    @pytest.mark.asyncio
    async def test_returns_500_on_exception(self):
        req = MagicMock()
        req.query = {"biz_id": "biz-1"}
        req.read = AsyncMock(side_effect=Exception("DB error"))
        req.headers = {"X-Poster-Signature": "any"}

        with patch("database.get_business_by_id", new_callable=AsyncMock, side_effect=Exception("DB error")):
            from handlers import webhooks
            response = await webhooks.poster_webhook_handler(req)

        assert response.status == 500
