"""Tests for handlers/webhooks.py."""
import sys
import os
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from aiohttp import web


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(body: dict, query_params: dict = None):
    """Build a fake aiohttp Request-like object."""
    req = MagicMock()
    req.json = AsyncMock(return_value=body)
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
        req = _make_request(body)

        with patch("database.activate_whop_subscription") as mock_activate, \
             patch("handlers.webhooks.bot") as mock_bot:
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
        req = _make_request(body)

        with patch("database.activate_whop_subscription"), \
             patch("handlers.webhooks.bot") as mock_bot:
            mock_bot.send_message = AsyncMock()
            from handlers import webhooks
            await webhooks.whop_webhook_handler(req)

        mock_bot.send_message.assert_called_once()
        call_kwargs = mock_bot.send_message.call_args.kwargs
        assert call_kwargs.get("chat_id") == 12345

    @pytest.mark.asyncio
    async def test_ignores_other_event_types(self):
        body = {"event_type": "membership.went_inactive", "data": {}}
        req = _make_request(body)

        with patch("database.activate_whop_subscription") as mock_activate, \
             patch("handlers.webhooks.bot"):
            from handlers import webhooks
            response = await webhooks.whop_webhook_handler(req)

        mock_activate.assert_not_called()
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_returns_500_on_exception(self):
        req = MagicMock()
        req.json = AsyncMock(side_effect=Exception("Boom"))

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
        req = _make_request(body)

        with patch("database.activate_whop_subscription") as mock_activate, \
             patch("handlers.webhooks.bot"):
            from handlers import webhooks
            response = await webhooks.whop_webhook_handler(req)

        mock_activate.assert_not_called()
        assert response.status == 200


class TestPosterWebhookHandler:
    @pytest.mark.asyncio
    async def test_returns_400_when_biz_id_missing(self):
        req = _make_request({}, query_params={})

        from handlers import webhooks
        response = await webhooks.poster_webhook_handler(req)

        assert response.status == 400

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
        req = _make_request(body, query_params={"biz_id": "biz-1"})

        managers_result = MagicMock(data=[{"user_id": 111}, {"user_id": 222}])
        mock_table = MagicMock()
        mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = managers_result

        with patch("database.supabase") as mock_supabase, \
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
        req = _make_request(body, query_params={"biz_id": "biz-1"})

        with patch("database.supabase") as mock_supabase, \
             patch("handlers.webhooks.bot") as mock_bot:
            mock_bot.send_message = AsyncMock()
            from handlers import webhooks
            response = await webhooks.poster_webhook_handler(req)

        mock_bot.send_message.assert_not_called()
        assert response.status == 200

    @pytest.mark.asyncio
    async def test_returns_500_on_exception(self):
        req = MagicMock()
        req.query = {"biz_id": "biz-1"}
        req.json = AsyncMock(side_effect=Exception("DB error"))

        from handlers import webhooks
        response = await webhooks.poster_webhook_handler(req)

        assert response.status == 500
