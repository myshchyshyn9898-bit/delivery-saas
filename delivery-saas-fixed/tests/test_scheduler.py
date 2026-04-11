"""Tests for handlers/scheduler.py — check_late_orders()."""
import sys
import os
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call


def _make_order(
    order_id: str,
    created_minutes_ago: int,
    est_time: int = 30,
    is_late_notified: bool = False,
    courier_id=None,
    address: str = "Test St 1",
    client_phone: str = "+48000000000",
    business_id: str = "biz-1",
):
    """Build a fake order dict."""
    created_at = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(minutes=created_minutes_ago)
    ).isoformat()
    return {
        "id": order_id,
        "created_at": created_at,
        "est_time": est_time,
        "is_late_notified": is_late_notified,
        "courier_id": courier_id,
        "address": address,
        "client_phone": client_phone,
        "business_id": business_id,
    }


class TestCheckLateOrders:
    @pytest.mark.asyncio
    async def test_does_nothing_when_no_pending_orders(self):
        """No pending orders → no notifications sent."""
        orders_result = MagicMock(data=[])
        chain = MagicMock()
        chain.eq.return_value = chain
        chain.execute.return_value = orders_result
        client = MagicMock()
        client.table.return_value.select.return_value = chain

        with patch("database.supabase", client), \
             patch("handlers.scheduler.bot") as mock_bot:
            mock_bot.send_message = AsyncMock()
            from handlers import scheduler
            await scheduler.check_late_orders()

        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_already_notified_orders(self):
        """Orders with is_late_notified=True must not trigger a notification."""
        order = _make_order("ord-1", created_minutes_ago=60, est_time=30, is_late_notified=True)
        orders_result = MagicMock(data=[order])
        chain = MagicMock()
        chain.eq.return_value = chain
        chain.execute.return_value = orders_result
        client = MagicMock()
        client.table.return_value.select.return_value = chain

        with patch("database.supabase", client), \
             patch("handlers.scheduler.bot") as mock_bot:
            mock_bot.send_message = AsyncMock()
            from handlers import scheduler
            await scheduler.check_late_orders()

        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_notification_for_late_order(self):
        """An overdue order (not yet notified) should trigger a send_message call."""
        # Created 45 min ago with est_time=30 → deadline was 35 min ago → late
        order = _make_order("ord-2", created_minutes_ago=45, est_time=30, is_late_notified=False)

        managers = [{"user_id": 555}]

        call_count = [0]

        def fake_execute():
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: pending orders
                return MagicMock(data=[order])
            elif call_count[0] == 2:
                # Second call: update is_late_notified (UPDATE path)
                return MagicMock(data=[])
            else:
                # Third call: managers (courier_id is None so staff lookup is skipped)
                return MagicMock(data=managers)

        chain = MagicMock()
        chain.eq.return_value = chain
        chain.update.return_value = chain
        chain.execute.side_effect = lambda: fake_execute()
        client = MagicMock()
        client.table.return_value.select.return_value = chain
        client.table.return_value.update.return_value = chain

        with patch("database.supabase", client), \
             patch("handlers.scheduler.bot") as mock_bot:
            mock_bot.send_message = AsyncMock()
            from handlers import scheduler
            await scheduler.check_late_orders()

        mock_bot.send_message.assert_called_once()
        call_kwargs = mock_bot.send_message.call_args.kwargs
        assert call_kwargs.get("chat_id") == 555

    @pytest.mark.asyncio
    async def test_does_not_notify_for_on_time_order(self):
        """An order that is within its delivery window should NOT trigger a notification."""
        # Created 10 min ago with est_time=30 → deadline is 25 min in the future
        order = _make_order("ord-3", created_minutes_ago=10, est_time=30, is_late_notified=False)
        orders_result = MagicMock(data=[order])
        chain = MagicMock()
        chain.eq.return_value = chain
        chain.execute.return_value = orders_result
        client = MagicMock()
        client.table.return_value.select.return_value = chain

        with patch("database.supabase", client), \
             patch("handlers.scheduler.bot") as mock_bot:
            mock_bot.send_message = AsyncMock()
            from handlers import scheduler
            await scheduler.check_late_orders()

        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_marks_order_as_notified_in_db(self):
        """After sending, the order should be flagged is_late_notified=True in the DB."""
        order = _make_order("ord-4", created_minutes_ago=50, est_time=30, is_late_notified=False)

        update_calls = []
        call_count = [0]

        def fake_execute():
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(data=[order])
            return MagicMock(data=[])

        chain = MagicMock()
        chain.eq.return_value = chain
        chain.execute.side_effect = lambda: fake_execute()
        client = MagicMock()
        client.table.return_value.select.return_value = chain
        update_chain = MagicMock()
        update_chain.eq.return_value = update_chain
        update_chain.execute.return_value = MagicMock(data=[])
        client.table.return_value.update.return_value = update_chain

        with patch("database.supabase", client), \
             patch("handlers.scheduler.bot") as mock_bot:
            mock_bot.send_message = AsyncMock()
            from handlers import scheduler
            await scheduler.check_late_orders()

        # Verify update was called with is_late_notified=True
        client.table.return_value.update.assert_called_with({"is_late_notified": True})
