"""Tests for database.py — all DB functions with mocked Supabase client."""
import sys
import os
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers to build a fluent Supabase mock
# ---------------------------------------------------------------------------

def _make_chain(data=None, error=None):
    """Build a mock that supports .table().select().eq().execute() etc."""
    result = MagicMock()
    result.data = data if data is not None else []
    result.error = error

    chain = MagicMock()
    chain.execute.return_value = result
    # Support chaining: .eq(), .gte(), .limit(), .neq() all return chain
    chain.eq.return_value = chain
    chain.neq.return_value = chain
    chain.gte.return_value = chain
    chain.lte.return_value = chain
    chain.limit.return_value = chain

    table = MagicMock()
    table.select.return_value = chain
    table.insert.return_value = chain
    table.update.return_value = chain

    client = MagicMock()
    client.table.return_value = table
    return client, chain, result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGetBusinessByOwner:
    def test_returns_first_record_when_found(self):
        biz = {"id": "biz-1", "owner_id": 42, "name": "Test Biz"}
        client, chain, result = _make_chain(data=[biz])

        with patch("database.supabase", client):
            import database
            res = database.get_business_by_owner(42)
        assert res == biz

    def test_returns_none_when_not_found(self):
        client, chain, result = _make_chain(data=[])
        with patch("database.supabase", client):
            import database
            res = database.get_business_by_owner(999)
        assert res is None


class TestGetBusinessById:
    def test_returns_record_when_found(self):
        biz = {"id": "biz-abc", "name": "My Cafe"}
        client, chain, result = _make_chain(data=[biz])
        with patch("database.supabase", client):
            import database
            res = database.get_business_by_id("biz-abc")
        assert res == biz

    def test_returns_none_when_not_found(self):
        client, chain, result = _make_chain(data=[])
        with patch("database.supabase", client):
            import database
            res = database.get_business_by_id("nonexistent")
        assert res is None


class TestRegisterNewBusiness:
    def test_inserts_correct_data(self):
        inserted = [{"id": "new-biz", "owner_id": 1, "plan": "trial"}]
        client, chain, result = _make_chain(data=inserted)

        biz_data = {
            "name": "Pizza Palace",
            "desc": "Best pizza",
            "phone": "+48123456789",
            "location": {"country": "PL", "city": "Rzeszow", "street": "Main St", "lat": 50.0, "lng": 22.0},
            "radius": "10",
            "currency": "zł",
            "payments": ["cash"],
        }

        with patch("database.supabase", client):
            import database
            database.register_new_business(owner_id=1, biz_data=biz_data)

        insert_call_args = client.table.return_value.insert.call_args
        data_arg = insert_call_args[0][0]

        assert data_arg["owner_id"] == 1
        assert data_arg["name"] == "Pizza Palace"
        assert data_arg["plan"] == "trial"
        assert data_arg["is_active"] is True
        assert "subscription_expires_at" in data_arg

    def test_trial_end_is_about_7_days_from_now(self):
        client, chain, result = _make_chain(data=[{"id": "x"}])
        biz_data = {"name": "X", "location": {}}

        with patch("database.supabase", client):
            import database
            database.register_new_business(owner_id=1, biz_data=biz_data)

        data_arg = client.table.return_value.insert.call_args[0][0]
        expires_str = data_arg["subscription_expires_at"]
        expires = datetime.datetime.fromisoformat(expires_str)
        now = datetime.datetime.now(datetime.timezone.utc)
        delta = expires - now
        # Allow between 6 and 8 days to avoid flakiness near midnight
        assert 6 <= delta.days <= 8


class TestGetAllBusinesses:
    def test_returns_list(self):
        businesses = [{"id": "b1"}, {"id": "b2"}]
        client, chain, result = _make_chain(data=businesses)
        with patch("database.supabase", client):
            import database
            res = database.get_all_businesses()
        assert res == businesses

    def test_returns_empty_list_when_none(self):
        client, chain, result = _make_chain(data=[])
        with patch("database.supabase", client):
            import database
            res = database.get_all_businesses()
        assert res == []


class TestUpdateSubscription:
    def test_calls_update_with_correct_params(self):
        client, chain, result = _make_chain()
        with patch("database.supabase", client):
            import database
            database.update_subscription("biz-123", False)

        client.table.assert_called_with("businesses")
        update_call = client.table.return_value.update
        update_call.assert_called_once_with({"is_active": False})
        chain.eq.assert_called_with("id", "biz-123")


class TestGetUserContext:
    def test_returns_owner_when_business_found(self):
        biz = {"id": "b1", "owner_id": 10, "name": "Test"}
        client = MagicMock()

        # First call: check businesses table for owner
        owner_result = MagicMock(data=[biz])
        # Second call should not be reached for owner path
        client.table.return_value.select.return_value.eq.return_value.execute.return_value = owner_result

        with patch("database.supabase", client):
            import database
            ctx = database.get_user_context(10)

        assert ctx["role"] == "owner"
        assert ctx["biz"] == biz

    def test_returns_none_when_not_found(self):
        client = MagicMock()
        empty = MagicMock(data=[])
        client.table.return_value.select.return_value.eq.return_value.execute.return_value = empty

        with patch("database.supabase", client):
            import database
            ctx = database.get_user_context(99)

        assert ctx is None

    def test_returns_staff_context_when_not_owner(self):
        staff_record = {"user_id": 5, "business_id": "biz-5", "role": "courier"}
        biz = {"id": "biz-5", "name": "Biz Five"}

        call_count = [0]

        def fake_execute():
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: businesses by owner_id → empty
                return MagicMock(data=[])
            elif call_count[0] == 2:
                # Second call: staff by user_id → found
                return MagicMock(data=[staff_record])
            else:
                # Third call: businesses by id → found
                return MagicMock(data=[biz])

        chain = MagicMock()
        chain.eq.return_value = chain
        chain.execute.side_effect = lambda: fake_execute()
        client = MagicMock()
        client.table.return_value.select.return_value = chain

        with patch("database.supabase", client):
            import database
            ctx = database.get_user_context(5)

        assert ctx is not None
        assert ctx["role"] == "courier"
        assert ctx["biz"] == biz


class TestCreateNewOrder:
    def test_inserts_correct_fields(self):
        new_order = {"id": "order-1", "business_id": "biz-1"}
        client, chain, result = _make_chain(data=[new_order])

        order_data = {
            "biz_id": "biz-1",
            "courier_id": 7,
            "client_name": "John",
            "client_phone": "+48111222333",
            "address": "Street 1",
            "amount": 99.5,
            "payment": "cash",
            "comment": "No pickles",
            "lat": 50.0,
            "lon": 22.0,
            "est_time": 45,
        }

        with patch("database.supabase", client):
            import database
            res = database.create_new_order(order_data)

        data_arg = client.table.return_value.insert.call_args[0][0]
        assert data_arg["business_id"] == "biz-1"
        assert data_arg["courier_id"] == 7
        assert data_arg["client_name"] == "John"
        assert data_arg["status"] == "pending"
        assert data_arg["est_time"] == 45
        assert res == new_order

    def test_returns_none_when_insert_returns_empty(self):
        client, chain, result = _make_chain(data=[])
        with patch("database.supabase", client):
            import database
            res = database.create_new_order({"biz_id": "x", "courier_id": 1})
        assert res is None


class TestGetActualPlan:
    def _future_iso(self, days=5):
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
        return future.isoformat()

    def _past_iso(self, days=2):
        past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        return past.isoformat()

    def test_returns_expired_when_no_data(self):
        client, chain, result = _make_chain(data=[])
        with patch("database.supabase", client):
            import database
            plan = database.get_actual_plan("biz-x")
        assert plan == "expired"

    def test_returns_plan_when_not_expired(self):
        biz = {"plan": "trial", "subscription_expires_at": self._future_iso()}
        client, chain, result = _make_chain(data=[biz])
        with patch("database.supabase", client):
            import database
            plan = database.get_actual_plan("biz-x")
        assert plan == "trial"

    def test_returns_expired_when_subscription_elapsed(self):
        biz = {"plan": "pro", "subscription_expires_at": self._past_iso()}
        client, chain, result = _make_chain(data=[biz])
        with patch("database.supabase", client):
            import database
            plan = database.get_actual_plan("biz-x")
        assert plan == "expired"

    def test_returns_expired_status_as_is_when_already_expired(self):
        biz = {"plan": "expired", "subscription_expires_at": self._future_iso()}
        client, chain, result = _make_chain(data=[biz])
        with patch("database.supabase", client):
            import database
            plan = database.get_actual_plan("biz-x")
        assert plan == "expired"


class TestActivateWhopSubscription:
    def test_updates_correct_fields(self):
        client, chain, result = _make_chain()
        with patch("database.supabase", client):
            import database
            database.activate_whop_subscription("biz-1", "pro", "membership-abc")

        update_call = client.table.return_value.update
        data_arg = update_call.call_args[0][0]
        assert data_arg["plan"] == "pro"
        assert data_arg["whop_membership_id"] == "membership-abc"
        assert "subscription_expires_at" in data_arg
