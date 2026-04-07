"""Tests for handlers/admin.py."""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from aiogram.types import Message, CallbackQuery


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_message(user_id: int, language_code: str = "uk"):
    msg = MagicMock()
    msg.from_user = MagicMock(id=user_id, language_code=language_code)
    msg.answer = AsyncMock()
    msg.reply = AsyncMock()
    return msg


def _make_callback(data: str, user_id: int, language_code: str = "uk"):
    cb = MagicMock()
    cb.data = data
    cb.from_user = MagicMock(id=user_id, language_code=language_code)
    cb.message = _make_message(user_id, language_code)
    cb.answer = AsyncMock()
    return cb


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSuperAdminPanel:
    @pytest.mark.asyncio
    async def test_non_super_admin_is_ignored(self):
        """Non-admin user: handler returns immediately without calling message.answer."""
        msg = _make_message(user_id=999)

        with patch("config.SUPER_ADMIN_IDS", [6889016268]):
            from handlers import admin
            await admin.super_admin_panel(msg)

        msg.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_super_admin_sees_empty_message_when_no_businesses(self):
        msg = _make_message(user_id=6889016268)

        with patch("config.SUPER_ADMIN_IDS", [6889016268]), \
             patch("database.get_all_businesses", return_value=[]):
            from handlers import admin
            await admin.super_admin_panel(msg)

        msg.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_super_admin_sees_inline_keyboard_with_businesses(self):
        businesses = [
            {"id": "biz-1", "name": "Cafe One", "is_active": True},
            {"id": "biz-2", "name": "Cafe Two", "is_active": False},
        ]
        msg = _make_message(user_id=6889016268)

        with patch("config.SUPER_ADMIN_IDS", [6889016268]), \
             patch("database.get_all_businesses", return_value=businesses):
            from handlers import admin
            await admin.super_admin_panel(msg)

        msg.answer.assert_called_once()
        call_kwargs = msg.answer.call_args
        # The reply_markup keyword should be present
        assert "reply_markup" in (call_kwargs.kwargs or {}) or len(call_kwargs.args) >= 2


class TestManageBiz:
    @pytest.mark.asyncio
    async def test_toggles_subscription_status_from_active_to_inactive(self):
        biz = {"id": "biz-1", "name": "Test", "is_active": True}
        cb = _make_callback(data="manage_biz_biz-1", user_id=6889016268)

        with patch("config.SUPER_ADMIN_IDS", [6889016268]), \
             patch("database.get_business_by_id", return_value=biz), \
             patch("database.update_subscription") as mock_update, \
             patch("database.get_all_businesses", return_value=[biz]):
            from handlers import admin
            await admin.manage_biz(cb)

        # Should have called update with the toggled value
        mock_update.assert_called_once_with("biz-1", False)

    @pytest.mark.asyncio
    async def test_toggles_subscription_status_from_inactive_to_active(self):
        biz = {"id": "biz-2", "name": "Test", "is_active": False}
        cb = _make_callback(data="manage_biz_biz-2", user_id=6889016268)

        with patch("config.SUPER_ADMIN_IDS", [6889016268]), \
             patch("database.get_business_by_id", return_value=biz), \
             patch("database.update_subscription") as mock_update, \
             patch("database.get_all_businesses", return_value=[biz]):
            from handlers import admin
            await admin.manage_biz(cb)

        mock_update.assert_called_once_with("biz-2", True)

    @pytest.mark.asyncio
    async def test_callback_answer_is_called(self):
        biz = {"id": "biz-1", "name": "T", "is_active": True}
        cb = _make_callback(data="manage_biz_biz-1", user_id=6889016268)

        with patch("config.SUPER_ADMIN_IDS", [6889016268]), \
             patch("database.get_business_by_id", return_value=biz), \
             patch("database.update_subscription"), \
             patch("database.get_all_businesses", return_value=[biz]):
            from handlers import admin
            await admin.manage_biz(cb)

        cb.answer.assert_called_once()
