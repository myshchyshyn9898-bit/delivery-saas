"""
Tests for:
  - handlers/commands.py: FSM state cleared on /start
  - texts.py: всі 149 ключів присутні в 4 мовах, нові ключі покриті
"""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ===========================================================================
# texts.py — нові ключі
# ===========================================================================

from texts import get_text, TEXTS

EXPECTED_LANGS = ("uk", "ru", "pl", "en")

# Нові ключі додані під час рефакторингу
NEW_KEYS = [
    "start_menu_hint",
    "order_taken_by_other",
    "order_take_failed",
    "late_header",
    "late_order_lbl",
    "late_addr_lbl",
    "late_phone_lbl",
    "late_courier_lbl",
    "late_mins_msg",
]


class TestNewTextKeys:
    """Нові i18n ключі є в усіх мовах і повертають непорожній рядок."""

    @pytest.mark.parametrize("lang", EXPECTED_LANGS)
    @pytest.mark.parametrize("key", NEW_KEYS)
    def test_key_exists_in_all_langs(self, lang, key):
        assert key in TEXTS[lang], f"Key '{key}' missing in lang '{lang}'"

    @pytest.mark.parametrize("lang", EXPECTED_LANGS)
    @pytest.mark.parametrize("key", NEW_KEYS)
    def test_key_returns_non_empty_string(self, lang, key):
        result = get_text(lang, key)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_late_mins_msg_formats_correctly(self):
        """late_mins_msg містить {mins} і форматується правильно."""
        for lang in EXPECTED_LANGS:
            result = get_text(lang, "late_mins_msg", mins=15)
            assert "15" in result, f"mins not formatted in lang '{lang}'"
            assert "{mins}" not in result

    def test_all_langs_have_same_key_count(self):
        """Всі 4 мови повинні мати однакову кількість ключів."""
        counts = {lang: len(TEXTS[lang]) for lang in EXPECTED_LANGS}
        assert len(set(counts.values())) == 1, \
            f"Key count mismatch between langs: {counts}"

    def test_total_keys_at_least_149(self):
        """Має бути мінімум 149 ключів після всіх змін."""
        for lang in EXPECTED_LANGS:
            assert len(TEXTS[lang]) >= 149, \
                f"Lang '{lang}' has only {len(TEXTS[lang])} keys, expected >= 149"

    def test_fallback_to_en_for_new_keys(self):
        """Невідома мова → en для нових ключів."""
        for key in NEW_KEYS:
            result = get_text("xx", key)
            assert result == TEXTS["en"][key]

    def test_start_menu_hint_not_hardcoded_ua(self):
        """EN та PL версія start_menu_hint не містить Ukrainian."""
        for lang in ("en", "pl", "ru"):
            result = TEXTS[lang]["start_menu_hint"]
            # Не повинно бути кириличних букв специфічних для UA
            assert "Натисніть" not in result, \
                f"UA hardcoded in {lang}: {result}"

    def test_order_taken_by_other_contains_warning(self):
        """Всі мовні версії order_taken_by_other містять попередження."""
        for lang in EXPECTED_LANGS:
            result = TEXTS[lang]["order_taken_by_other"]
            assert "⚠️" in result or "!" in result


# ===========================================================================
# handlers/commands.py — FSM clear on /start
# ===========================================================================

class TestFSMClearOnStart:
    """При /start з активним FSM станом — стан повинен скидатись."""

    @pytest.mark.asyncio
    async def test_start_clears_active_fsm_state(self):
        """Якщо FSM активний і немає інвайт-токена — state.clear() викликається."""
        message = MagicMock()
        message.from_user = MagicMock(id=555, language_code="uk")
        message.answer = AsyncMock()
        message.text = "/start"

        command = MagicMock()
        command.args = None  # no invite token

        state = AsyncMock()
        state.get_state = AsyncMock(return_value="ShiftOpen:waiting_photo")
        state.clear = AsyncMock()
        state.get_data = AsyncMock(return_value={})

        mock_db = MagicMock()
        mock_db.get_user_context_cached = AsyncMock(return_value={
            "role": "courier",
            "biz": {"id": "biz-1", "name": "Тест", "plan": "pro",
                    "is_active": True, "delivery_mode": "dispatcher",
                    "base_url": "https://test.com"}
        })
        mock_db.get_actual_plan = AsyncMock(return_value="pro")
        mock_db.get_active_shift = AsyncMock(return_value=None)

        with patch("handlers.commands.db", mock_db), \
             patch("handlers.commands.bot", AsyncMock()):
            from handlers import commands
            await commands.cmd_start(message, command, state)

        state.clear.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_does_not_clear_fsm_when_no_state(self):
        """Якщо FSM не активний — state.clear() НЕ викликається."""
        message = MagicMock()
        message.from_user = MagicMock(id=556, language_code="en")
        message.answer = AsyncMock()

        command = MagicMock()
        command.args = None

        state = AsyncMock()
        state.get_state = AsyncMock(return_value=None)  # no active state
        state.clear = AsyncMock()
        state.get_data = AsyncMock(return_value={})

        mock_db = MagicMock()
        mock_db.get_user_context_cached = AsyncMock(return_value={
            "role": "owner",
            "biz": {"id": "biz-1", "name": "Test", "plan": "trial",
                    "is_active": True, "delivery_mode": "dispatcher",
                    "base_url": "https://test.com"}
        })
        mock_db.get_actual_plan = AsyncMock(return_value="trial")

        with patch("handlers.commands.db", mock_db), \
             patch("handlers.commands.bot", AsyncMock()):
            from handlers import commands
            await commands.cmd_start(message, command, state)

        state.clear.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_with_invite_token_does_not_clear_fsm(self):
        """При /start c_<token> FSM не скидається (реєстрація продовжується)."""
        message = MagicMock()
        message.from_user = MagicMock(id=557, language_code="pl")
        message.answer = AsyncMock()

        command = MagicMock()
        command.args = "c_abc123token"  # invite token

        state = AsyncMock()
        state.get_state = AsyncMock(return_value="SomeState:waiting")
        state.clear = AsyncMock()
        state.get_data = AsyncMock(return_value={})
        state.set_state = AsyncMock()
        state.update_data = AsyncMock()

        mock_db = MagicMock()
        mock_db.get_user_context_cached = AsyncMock(return_value=None)
        mock_db.get_staff_by_invite_token = AsyncMock(return_value={
            "biz_id": "biz-1", "role": "courier"
        })

        with patch("handlers.commands.db", mock_db), \
             patch("handlers.commands.bot", AsyncMock()):
            from handlers import commands
            await commands.cmd_start(message, command, state)

        state.clear.assert_not_called()


# ===========================================================================
# Integration: lang fallback chain
# ===========================================================================

class TestLangFallbackChain:
    """Перевірка ланцюжка fallback мови у всіх нових ключах."""

    def test_german_falls_back_to_english(self):
        for key in NEW_KEYS:
            if key == "late_mins_msg":
                result = get_text("de", key, mins=5)
                assert "5" in result
            else:
                result = get_text("de", key)
                assert result == TEXTS["en"][key]

    def test_none_lang_falls_back_to_english(self):
        for key in NEW_KEYS:
            if key == "late_mins_msg":
                result = get_text(None, key, mins=10)
                assert "10" in result
            else:
                result = get_text(None, key)
                assert result == TEXTS["en"][key]

    def test_all_new_keys_differ_between_languages(self):
        """Переклади повинні відрізнятись між мовами (не копія одного)."""
        for key in NEW_KEYS:
            if key == "late_mins_msg":
                continue  # шаблон однаковий структурно
            values = set(TEXTS[lang][key] for lang in EXPECTED_LANGS)
            assert len(values) > 1, \
                f"Key '{key}' has identical values across all languages: {values}"
