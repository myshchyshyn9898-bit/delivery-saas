"""
Tests for NEW database.py functions added during refactoring:
  - get_courier_lang
  - create_staff with lang param
  - get_user_context with multi-business courier (active biz priority)
  - get_actual_plan edge cases
"""
import sys
import os
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chain(data=None):
    """Build a fluent Supabase mock chain."""
    result = MagicMock(data=data if data is not None else [])
    c = MagicMock()
    c.execute.return_value = result
    c.eq.return_value = c
    c.neq.return_value = c
    c.limit.return_value = c
    c.single.return_value = c
    table = MagicMock()
    table.select.return_value = c
    table.insert.return_value = c
    table.update.return_value = c
    table.upsert.return_value = c
    client = MagicMock()
    client.table.return_value = table
    return client, c, result


# ---------------------------------------------------------------------------
# get_courier_lang
# ---------------------------------------------------------------------------

class TestGetCourierLang:
    """Tests for database.get_courier_lang()"""

    @pytest.mark.asyncio
    async def test_returns_lang_from_db(self):
        client, chain, result = _chain(data=[{"lang": "pl"}])
        with patch("database.supabase", client):
            import database
            lang = await database.get_courier_lang(user_id=42, biz_id="biz-1")
        assert lang == "pl"

    @pytest.mark.asyncio
    async def test_returns_en_when_no_record(self):
        client, chain, result = _chain(data=[])
        with patch("database.supabase", client):
            import database
            lang = await database.get_courier_lang(user_id=99)
        assert lang == "en"

    @pytest.mark.asyncio
    async def test_returns_en_for_unsupported_lang(self):
        """Lang stored as 'de' → fallback to 'en'."""
        client, chain, result = _chain(data=[{"lang": "de"}])
        with patch("database.supabase", client):
            import database
            lang = await database.get_courier_lang(user_id=1)
        assert lang == "en"

    @pytest.mark.asyncio
    async def test_returns_en_when_lang_field_is_none(self):
        client, chain, result = _chain(data=[{"lang": None}])
        with patch("database.supabase", client):
            import database
            lang = await database.get_courier_lang(user_id=1)
        assert lang == "en"

    @pytest.mark.asyncio
    async def test_all_supported_langs_pass_through(self):
        """uk, ru, pl, en — всі повертаються без зміни."""
        for expected in ("uk", "ru", "pl", "en"):
            client, chain, result = _chain(data=[{"lang": expected}])
            with patch("database.supabase", client):
                import database
                lang = await database.get_courier_lang(user_id=1)
            assert lang == expected

    @pytest.mark.asyncio
    async def test_returns_en_on_exception(self):
        client = MagicMock()
        client.table.side_effect = Exception("DB down")
        with patch("database.supabase", client):
            import database
            lang = await database.get_courier_lang(user_id=1)
        assert lang == "en"


# ---------------------------------------------------------------------------
# create_staff with lang
# ---------------------------------------------------------------------------

class TestCreateStaffWithLang:
    """create_staff must save the lang field."""

    @pytest.mark.asyncio
    async def test_saves_lang_field_on_new_record(self):
        client, chain, result = _chain(data=[])  # no existing record
        # upsert returns new record
        with patch("database.supabase", client):
            import database
            await database.create_staff(
                user_id=10, name="Іван", biz_id="biz-1",
                role="courier", lang="uk"
            )
        # Find the insert/upsert call and check lang
        upsert_calls = client.table.return_value.upsert.call_args_list
        insert_calls = client.table.return_value.insert.call_args_list
        all_calls = upsert_calls + insert_calls
        assert len(all_calls) > 0
        # Check lang in one of the calls
        found_lang = False
        for call in all_calls:
            args = call[0]
            if args and isinstance(args[0], dict) and args[0].get("lang") == "uk":
                found_lang = True
        # Also check update path
        update_calls = client.table.return_value.update.call_args_list
        for call in update_calls:
            args = call[0]
            if args and isinstance(args[0], dict) and "lang" in args[0]:
                found_lang = True
        assert found_lang, "lang='uk' not saved in any DB call"

    @pytest.mark.asyncio
    async def test_default_lang_is_en(self):
        """If lang not specified, defaults to 'en'."""
        client, chain, result = _chain(data=[])
        with patch("database.supabase", client):
            import database
            await database.create_staff(user_id=11, name="Bob", biz_id="biz-2")
        # Check that 'en' appears in insert data somewhere
        insert_calls = client.table.return_value.insert.call_args_list
        upsert_calls = client.table.return_value.upsert.call_args_list
        update_calls = client.table.return_value.update.call_args_list
        all_calls = insert_calls + upsert_calls + update_calls
        en_found = any(
            call[0] and isinstance(call[0][0], dict) and call[0][0].get("lang") == "en"
            for call in all_calls
        )
        update_en = any(
            call[0] and isinstance(call[0][0], dict) and call[0][0].get("lang") == "en"
            for call in update_calls
        )
        assert en_found or update_en


# ---------------------------------------------------------------------------
# get_user_context — multi-business courier active biz priority
# ---------------------------------------------------------------------------

class TestGetUserContextMultiBiz:
    """Courier in multiple businesses: should get first ACTIVE one."""

    @pytest.mark.asyncio
    async def test_returns_active_biz_over_inactive(self):
        """
        Кур'єр в 2 бізнесах: перший неактивний, другий активний.
        Має повернутись активний бізнес.
        """
        staff_records = [
            {"user_id": 5, "business_id": "biz-inactive", "role": "courier"},
            {"user_id": 5, "business_id": "biz-active", "role": "courier"},
        ]
        inactive_biz = {"id": "biz-inactive", "name": "Old Cafe", "is_active": False}
        active_biz = {"id": "biz-active", "name": "New Cafe", "is_active": True}

        call_count = [0]

        def fake_execute():
            call_count[0] += 1
            n = call_count[0]
            if n == 1:
                return MagicMock(data=[])           # not owner
            elif n == 2:
                return MagicMock(data=staff_records) # staff records
            elif n == 3:
                return MagicMock(data=[inactive_biz]) # first biz — inactive
            elif n == 4:
                return MagicMock(data=[active_biz])   # second biz — active
            return MagicMock(data=[])

        chain = MagicMock()
        chain.eq.return_value = chain
        chain.execute.side_effect = lambda: fake_execute()
        client = MagicMock()
        client.table.return_value.select.return_value = chain

        with patch("database.supabase", client):
            import database
            ctx = await database.get_user_context_cached(5)

        assert ctx is not None
        assert ctx["biz"]["id"] == "biz-active"

    @pytest.mark.asyncio
    async def test_returns_first_biz_when_none_active(self):
        """
        Всі бізнеси неактивні — повертаємо перший.
        """
        staff_records = [
            {"user_id": 6, "business_id": "biz-1", "role": "courier"},
            {"user_id": 6, "business_id": "biz-2", "role": "courier"},
        ]
        biz1 = {"id": "biz-1", "name": "Closed1", "is_active": False}
        biz2 = {"id": "biz-2", "name": "Closed2", "is_active": False}

        call_count = [0]

        def fake_execute():
            call_count[0] += 1
            n = call_count[0]
            if n == 1: return MagicMock(data=[])
            elif n == 2: return MagicMock(data=staff_records)
            elif n == 3: return MagicMock(data=[biz1])
            elif n == 4: return MagicMock(data=[biz2])
            elif n == 5: return MagicMock(data=[biz1])  # fallback first
            return MagicMock(data=[])

        chain = MagicMock()
        chain.eq.return_value = chain
        chain.execute.side_effect = lambda: fake_execute()
        client = MagicMock()
        client.table.return_value.select.return_value = chain

        with patch("database.supabase", client):
            import database
            ctx = await database.get_user_context_cached(6)

        assert ctx is not None
        assert ctx["biz"]["id"] == "biz-1"


# ---------------------------------------------------------------------------
# get_actual_plan — нові edge cases
# ---------------------------------------------------------------------------

class TestGetActualPlanEdgeCases:

    def _future(self, days=5):
        return (datetime.datetime.now(datetime.timezone.utc) +
                datetime.timedelta(days=days)).isoformat()

    def _past(self, days=2):
        return (datetime.datetime.now(datetime.timezone.utc) -
                datetime.timedelta(days=days)).isoformat()

    @pytest.mark.asyncio
    async def test_trial_active_returns_trial(self):
        client, chain, result = _chain(
            data=[{"plan": "trial", "subscription_expires_at": self._future()}]
        )
        with patch("database.supabase", client):
            import database
            plan = await database.get_actual_plan("biz-1")
        assert plan == "trial"

    @pytest.mark.asyncio
    async def test_pro_active_returns_pro(self):
        client, chain, result = _chain(
            data=[{"plan": "pro", "subscription_expires_at": self._future(30)}]
        )
        with patch("database.supabase", client):
            import database
            plan = await database.get_actual_plan("biz-1")
        assert plan == "pro"

    @pytest.mark.asyncio
    async def test_expired_flag_overrides_future_date(self):
        """plan='expired' в БД завжди expired, навіть якщо дата в майбутньому."""
        client, chain, result = _chain(
            data=[{"plan": "expired", "subscription_expires_at": self._future()}]
        )
        with patch("database.supabase", client):
            import database
            plan = await database.get_actual_plan("biz-1")
        assert plan == "expired"

    @pytest.mark.asyncio
    async def test_trial_expired_by_date_returns_expired(self):
        client, chain, result = _chain(
            data=[{"plan": "trial", "subscription_expires_at": self._past()}]
        )
        with patch("database.supabase", client):
            import database
            plan = await database.get_actual_plan("biz-1")
        assert plan == "expired"

    @pytest.mark.asyncio
    async def test_no_expiry_date_returns_expired(self):
        client, chain, result = _chain(
            data=[{"plan": "trial", "subscription_expires_at": None}]
        )
        with patch("database.supabase", client):
            import database
            plan = await database.get_actual_plan("biz-1")
        assert plan == "expired"
