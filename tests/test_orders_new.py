"""
Tests for handlers/orders.py:
  - Cancelled status blocks dispatcher_close and uber_close
  - courier_lang is fetched before building dispatcher message
  - take_order race condition (already taken)
"""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_callback(data="", user_id=100, lang="uk"):
    cb = MagicMock()
    cb.data = data
    cb.from_user = MagicMock(id=user_id, language_code=lang)
    cb.message = MagicMock()
    cb.message.chat = MagicMock(id=user_id)
    cb.message.answer = AsyncMock()
    cb.message.edit_text = AsyncMock()
    cb.message.edit_caption = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def _mock_db_order(status="delivering", courier_id=100):
    db = MagicMock()
    order = {
        "id": "order-1",
        "status": status,
        "courier_id": courier_id,
        "business_id": "biz-1",
        "amount": 55.0,
        "pay_type": "cash",
        "address": "вул. Франка 1",
        "client_phone": "+48111222333",
        "client_name": "Тест",
    }
    db.get_order_by_id = AsyncMock(return_value=order)
    db.get_business_by_id = AsyncMock(return_value={
        "id": "biz-1", "currency": "zł", "plan": "pro",
        "delivery_mode": "dispatcher", "base_url": "https://test.com"
    })
    db.get_actual_plan = AsyncMock(return_value="pro")
    db.get_courier_lang = AsyncMock(return_value="pl")
    db._run = AsyncMock(return_value=MagicMock(data=[{"status": "completed"}]))
    return db, order


# ---------------------------------------------------------------------------
# Cancelled status — dispatcher_close
# ---------------------------------------------------------------------------

class TestDispatcherCloseCancelledStatus:
    """Закрити замовлення зі статусом 'cancelled' → помилка 'вже закрито'."""

    @pytest.mark.asyncio
    async def test_cancelled_order_blocks_cash_close(self):
        db, order = _mock_db_order(status="cancelled", courier_id=100)
        callback = _make_callback(
            data="dispatcher_close_cash_order-1",
            user_id=100, lang="uk"
        )

        with patch("handlers.orders.db", db), \
             patch("handlers.orders.bot", AsyncMock()):
            from handlers import orders
            await orders.dispatcher_close_cash(callback)

        # Повинно відповісти що вже закрито/скасовано
        callback.message.answer.assert_called_once()
        call_text = callback.message.answer.call_args[0][0]
        assert "закрит" in call_text.lower() or "скасован" in call_text.lower() or \
               "already" in call_text.lower() or "closed" in call_text.lower()

    @pytest.mark.asyncio
    async def test_cancelled_order_blocks_terminal_close(self):
        db, order = _mock_db_order(status="cancelled", courier_id=100)
        callback = _make_callback(
            data="dispatcher_close_terminal_order-1",
            user_id=100, lang="en"
        )

        with patch("handlers.orders.db", db), \
             patch("handlers.orders.bot", AsyncMock()):
            from handlers import orders
            await orders.dispatcher_close_terminal(callback)

        callback.message.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_completed_order_also_blocked(self):
        db, order = _mock_db_order(status="completed", courier_id=100)
        callback = _make_callback(
            data="dispatcher_close_cash_order-1",
            user_id=100, lang="uk"
        )

        with patch("handlers.orders.db", db), \
             patch("handlers.orders.bot", AsyncMock()):
            from handlers import orders
            await orders.dispatcher_close_cash(callback)

        callback.message.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_wrong_courier_blocked(self):
        """Інший кур'єр не може закрити замовлення."""
        db, order = _mock_db_order(status="delivering", courier_id=999)
        callback = _make_callback(
            data="dispatcher_close_cash_order-1",
            user_id=100,  # не той кур'єр
            lang="uk"
        )

        with patch("handlers.orders.db", db), \
             patch("handlers.orders.bot", AsyncMock()):
            from handlers import orders
            await orders.dispatcher_close_cash(callback)

        callback.message.answer.assert_called_once()
        call_text = callback.message.answer.call_args[0][0]
        assert "замовлення" in call_text.lower() or "order" in call_text.lower()


# ---------------------------------------------------------------------------
# Cancelled status — uber_close
# ---------------------------------------------------------------------------

class TestUberCloseCancelledStatus:

    @pytest.mark.asyncio
    async def test_cancelled_order_blocks_uber_cash_close(self):
        db, order = _mock_db_order(status="cancelled", courier_id=100)
        callback = _make_callback(
            data="uber_close_cash_order-1",
            user_id=100, lang="pl"
        )

        with patch("handlers.orders.db", db), \
             patch("handlers.orders.bot", AsyncMock()):
            from handlers import orders
            await orders.uber_close_cash(callback)

        callback.message.answer.assert_called_once()


# ---------------------------------------------------------------------------
# Courier language in dispatcher mode
# ---------------------------------------------------------------------------

class TestCourierLangInDispatcher:
    """get_courier_lang повинна викликатись при відправці повідомлення кур'єру."""

    @pytest.mark.asyncio
    async def test_get_courier_lang_called_for_new_order(self):
        """При новому замовленні в dispatcher режимі — мова кур'єра береться з БД."""
        mock_db = MagicMock()
        mock_db.get_courier_lang = AsyncMock(return_value="pl")
        mock_db.get_business_by_id = AsyncMock(return_value={
            "id": "biz-1", "currency": "zł", "delivery_mode": "dispatcher",
            "plan": "pro", "base_url": "https://test.com",
            "courier_group_id": None
        })
        mock_db.get_actual_plan = AsyncMock(return_value="pro")
        mock_db.get_user_context_cached = AsyncMock(return_value={
            "role": "owner", "biz": {"id": "biz-1"}
        })
        mock_db.create_new_order = AsyncMock(return_value={
            "id": "order-new", "business_id": "biz-1"
        })
        mock_db._run = AsyncMock(return_value=MagicMock(data=[{"user_id": 200}]))

        # Simulates that courier is in staff
        mock_db._run.return_value.data = [{"user_id": 200}]

        mock_bot = AsyncMock()

        data = {
            "action": "new_order",
            "biz_id": "biz-1",
            "courier_id": 200,
            "address": "Street 1",
            "amount": 100,
            "payment": "cash",
            "client_name": "Test",
            "client_phone": "+48111222333",
        }

        message = MagicMock()
        message.from_user = MagicMock(id=1, language_code="uk")
        message.answer = AsyncMock()

        import json
        message.web_app_data = MagicMock(data=json.dumps(data))

        with patch("handlers.orders.db", mock_db), \
             patch("handlers.orders.bot", mock_bot):
            from handlers import orders
            await orders.handle_web_app_data(message)

        # get_courier_lang must have been called with courier_id=200
        mock_db.get_courier_lang.assert_called()
        call_args = mock_db.get_courier_lang.call_args
        assert 200 in call_args[0] or call_args[1].get("user_id") == 200


# ---------------------------------------------------------------------------
# Take order — race condition
# ---------------------------------------------------------------------------

class TestTakeOrderRaceCondition:

    @pytest.mark.asyncio
    async def test_already_taken_shows_message(self):
        """Якщо UPDATE повертає порожній список (хтось встиг) — показати повідомлення."""
        db = MagicMock()
        db._run = AsyncMock(side_effect=[
            MagicMock(data=[{  # get order
                "id": "ord-1", "status": "delivering",  # already taken!
                "business_id": "biz-1", "courier_id": 999,
                "address": "Test", "amount": 50, "pay_type": "cash",
                "client_name": "X", "client_phone": "+48100"
            }]),
            MagicMock(data=[{"user_id": 100}]),  # staff check OK
            MagicMock(data=[]),  # UPDATE returns empty → race condition lost
        ])
        db.get_business_by_id = AsyncMock(return_value={
            "id": "biz-1", "currency": "zł", "delivery_mode": "uber",
            "plan": "pro", "courier_group_id": -100
        })
        db.get_actual_plan = AsyncMock(return_value="pro")

        callback = _make_callback(
            data="take_order_ord-1",
            user_id=100, lang="uk"
        )

        with patch("handlers.orders.db", db), \
             patch("handlers.orders.bot", AsyncMock()):
            from handlers import orders
            await orders.take_order_handler(callback)

        # Кур'єр повинен отримати повідомлення "вже взято"
        callback.message.answer.assert_called()
