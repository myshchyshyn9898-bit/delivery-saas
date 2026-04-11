"""Tests for texts.py — get_text() function."""
import sys
import os

# Ensure project root is importable (conftest already does this, but be explicit)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from texts import get_text, TEXTS


class TestGetText:
    """Tests for the get_text() i18n helper."""

    # ------------------------------------------------------------------
    # Language resolution
    # ------------------------------------------------------------------

    def test_returns_ukrainian_text(self):
        result = get_text("uk", "dont_understand")
        assert result == TEXTS["uk"]["dont_understand"]

    def test_returns_russian_text(self):
        result = get_text("ru", "dont_understand")
        assert result == TEXTS["ru"]["dont_understand"]

    def test_returns_polish_text(self):
        result = get_text("pl", "dont_understand")
        assert result == TEXTS["pl"]["dont_understand"]

    def test_returns_english_text(self):
        result = get_text("en", "dont_understand")
        assert result == TEXTS["en"]["dont_understand"]

    def test_falls_back_to_english_for_unknown_lang(self):
        result = get_text("xx", "dont_understand")
        assert result == TEXTS["en"]["dont_understand"]

    def test_falls_back_to_english_for_none_lang(self):
        result = get_text(None, "dont_understand")
        assert result == TEXTS["en"]["dont_understand"]

    def test_falls_back_to_english_for_empty_string(self):
        result = get_text("", "dont_understand")
        assert result == TEXTS["en"]["dont_understand"]

    # ------------------------------------------------------------------
    # Missing key behaviour
    # ------------------------------------------------------------------

    def test_returns_key_when_not_in_any_language(self):
        result = get_text("uk", "nonexistent_key_xyz")
        assert result == "nonexistent_key_xyz"

    def test_returns_key_for_unknown_lang_and_missing_key(self):
        result = get_text("xx", "nonexistent_key_xyz")
        assert result == "nonexistent_key_xyz"

    # ------------------------------------------------------------------
    # Formatting with kwargs
    # ------------------------------------------------------------------

    def test_formats_single_kwarg(self):
        result = get_text("uk", "owner_panel", name="Тест")
        assert "Тест" in result
        assert "{name}" not in result

    def test_formats_multiple_kwargs(self):
        result = get_text("uk", "invite_welcome", role="Кур'єр", biz_name="Кафе Ромашка")
        assert "Кур'єр" in result
        assert "Кафе Ромашка" in result

    def test_formats_biz_created_with_kwargs(self):
        result = get_text("uk", "biz_created", biz_name="Піцерія", plan="trial")
        assert "Піцерія" in result
        assert "trial" in result

    def test_no_kwargs_returns_raw_template(self):
        raw = get_text("uk", "owner_panel")
        assert "{name}" in raw

    def test_formats_with_english_fallback(self):
        """Unknown lang falls back to English, then formats correctly."""
        result = get_text("xx", "owner_panel", name="Boss")
        assert "Boss" in result

    # ------------------------------------------------------------------
    # All four languages contain the same set of keys
    # ------------------------------------------------------------------

    def test_all_languages_have_common_keys(self):
        common_keys = {"dont_understand", "sub_expired", "btn_open_app", "btn_report"}
        for lang in ("uk", "ru", "pl", "en"):
            for key in common_keys:
                assert key in TEXTS[lang], f"Key '{key}' missing in lang '{lang}'"
