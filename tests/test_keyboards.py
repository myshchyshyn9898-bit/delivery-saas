"""Tests for keyboards.py — generate_token() and keyboard builder functions."""
import sys
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import jwt
import pytest
from unittest.mock import patch

# keyboards.py reads JWT_SECRET at module import time, so we patch the env
# variable before importing.
os.environ.setdefault("SUPABASE_JWT_SECRET", "test_secret_for_keyboards")

import keyboards
from keyboards import (
    generate_token,
    get_reg_kb,
    get_owner_kb,
    get_manager_kb,
    get_courier_kb,
    get_superadmin_kb,
    JWT_SECRET,
)
from aiogram.types import ReplyKeyboardMarkup


class TestGenerateToken:
    """Tests for generate_token()."""

    def test_returns_string(self):
        token = generate_token()
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_has_exp_field(self):
        token = generate_token()
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        assert "exp" in payload
        # Expiry should be roughly 24 h from now
        assert payload["exp"] > int(time.time())
        assert payload["exp"] <= int(time.time()) + 86401

    def test_default_role_is_authenticated(self):
        token = generate_token()
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        assert payload["role"] == "authenticated"

    def test_boss_token_role_is_service_role(self):
        token = generate_token(is_boss=True)
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        assert payload["role"] == "service_role"

    def test_user_id_sets_sub_and_tg_id(self):
        token = generate_token(user_id=42)
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        assert payload["sub"] == "42"
        assert payload["tg_id"] == "42"

    def test_biz_id_included_in_payload(self):
        token = generate_token(biz_id="my-biz-123", user_id=7)
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        assert payload["biz_id"] == "my-biz-123"

    def test_boss_token_has_no_biz_id(self):
        token = generate_token(is_boss=True, biz_id="ignored-biz")
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        assert "biz_id" not in payload

    def test_no_user_id_means_no_sub_or_tg_id(self):
        token = generate_token()
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        assert "sub" not in payload
        assert "tg_id" not in payload

    def test_different_calls_produce_different_tokens(self):
        t1 = generate_token(user_id=1)
        t2 = generate_token(user_id=2)
        assert t1 != t2


class TestGetRegKb:
    def test_returns_reply_keyboard_markup(self):
        kb = get_reg_kb()
        assert isinstance(kb, ReplyKeyboardMarkup)

    def test_has_one_row_with_one_button(self):
        kb = get_reg_kb()
        assert len(kb.keyboard) == 1
        assert len(kb.keyboard[0]) == 1

    def test_button_has_web_app(self):
        kb = get_reg_kb()
        btn = kb.keyboard[0][0]
        assert btn.web_app is not None

    def test_web_app_url_contains_delivepro(self):
        kb = get_reg_kb()
        url = kb.keyboard[0][0].web_app.url
        assert "delivepro.html" in url


class TestGetOwnerKb:
    def test_returns_reply_keyboard_markup(self):
        kb = get_owner_kb(biz_id="biz1", user_id=99)
        assert isinstance(kb, ReplyKeyboardMarkup)

    def test_has_four_rows(self):
        kb = get_owner_kb(biz_id="biz1", user_id=99)
        assert len(kb.keyboard) == 4

    def test_first_row_web_app_contains_biz_id(self):
        kb = get_owner_kb(biz_id="biz-abc", user_id=5)
        url = kb.keyboard[0][0].web_app.url
        assert "biz_id=biz-abc" in url

    def test_first_row_web_app_contains_tg_id(self):
        kb = get_owner_kb(biz_id="biz1", user_id=77)
        url = kb.keyboard[0][0].web_app.url
        assert "tg_id=77" in url

    def test_first_row_web_app_contains_token(self):
        kb = get_owner_kb(biz_id="biz1", user_id=77)
        url = kb.keyboard[0][0].web_app.url
        assert "token=" in url

    def test_resize_keyboard_true(self):
        kb = get_owner_kb(biz_id="biz1", user_id=1)
        assert kb.resize_keyboard is True


class TestGetManagerKb:
    def test_returns_reply_keyboard_markup(self):
        kb = get_manager_kb(biz_id="biz1", user_id=10)
        assert isinstance(kb, ReplyKeyboardMarkup)

    def test_has_four_rows(self):
        kb = get_manager_kb(biz_id="biz1", user_id=10)
        assert len(kb.keyboard) == 4

    def test_first_button_url_contains_form(self):
        kb = get_manager_kb(biz_id="biz1", user_id=10)
        url = kb.keyboard[0][0].web_app.url
        assert "form.html" in url

    def test_url_contains_biz_id_and_tg_id(self):
        kb = get_manager_kb(biz_id="biz-xyz", user_id=55)
        url = kb.keyboard[0][0].web_app.url
        assert "biz_id=biz-xyz" in url
        assert "tg_id=55" in url


class TestGetCourierKb:
    def test_returns_reply_keyboard_markup(self):
        kb = get_courier_kb(biz_id="biz1", user_id=20)
        assert isinstance(kb, ReplyKeyboardMarkup)

    def test_has_two_rows(self):
        kb = get_courier_kb(biz_id="biz1", user_id=20)
        assert len(kb.keyboard) == 2

    def test_map_button_url_contains_map(self):
        kb = get_courier_kb(biz_id="biz1", user_id=20)
        url = kb.keyboard[0][0].web_app.url
        assert "map.html" in url

    def test_url_contains_biz_id_and_tg_id(self):
        kb = get_courier_kb(biz_id="biz-q", user_id=99)
        url = kb.keyboard[0][0].web_app.url
        assert "biz_id=biz-q" in url
        assert "tg_id=99" in url


class TestGetSuperadminKb:
    def test_returns_reply_keyboard_markup(self):
        kb = get_superadmin_kb(user_id=1)
        assert isinstance(kb, ReplyKeyboardMarkup)

    def test_has_one_row(self):
        kb = get_superadmin_kb(user_id=1)
        assert len(kb.keyboard) == 1

    def test_url_contains_boss_html(self):
        kb = get_superadmin_kb(user_id=8)
        url = kb.keyboard[0][0].web_app.url
        assert "boss.html" in url

    def test_token_in_url_is_service_role(self):
        kb = get_superadmin_kb(user_id=8)
        url = kb.keyboard[0][0].web_app.url
        # Extract the token parameter
        token = url.split("token=")[-1].split("&")[0]
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        assert payload["role"] == "service_role"

    def test_url_contains_tg_id(self):
        kb = get_superadmin_kb(user_id=42)
        url = kb.keyboard[0][0].web_app.url
        assert "tg_id=42" in url
