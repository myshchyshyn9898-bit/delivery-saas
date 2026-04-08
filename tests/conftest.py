"""Shared fixtures for the test suite."""
import sys
import os
import types as stdlib_types
from unittest.mock import MagicMock, AsyncMock

import pytest

# ---------------------------------------------------------------------------
# Ensure the project root is on the path so we can import project modules
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---------------------------------------------------------------------------
# Set required environment variables before any project module is imported
# ---------------------------------------------------------------------------
os.environ.setdefault("BOT_TOKEN", "test_bot_token")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test_jwt_secret")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test_supabase_key")

# ---------------------------------------------------------------------------
# Stub heavy third-party packages BEFORE any project module is imported
# ---------------------------------------------------------------------------

def _make_supabase_stub():
    """Return a minimal stub for the supabase package."""
    supabase_mod = stdlib_types.ModuleType("supabase")
    client_mod = stdlib_types.ModuleType("supabase.client")

    class Client:
        pass

    def create_client(url, key):
        return MagicMock()

    supabase_mod.create_client = create_client
    supabase_mod.Client = Client
    client_mod.create_client = create_client
    client_mod.Client = Client
    sys.modules.setdefault("supabase", supabase_mod)
    sys.modules.setdefault("supabase.client", client_mod)


def _make_aiogram_stub():
    """Minimal aiogram stub so project modules can be imported without a real bot."""
    if "aiogram" in sys.modules:
        return

    aiogram_mod = stdlib_types.ModuleType("aiogram")

    class Bot:
        def __init__(self, token=None):
            pass

    class Dispatcher:
        def __init__(self):
            pass

    class Router:
        def __init__(self):
            pass

        def message(self, *a, **kw):
            def decorator(fn):
                return fn
            return decorator

        def callback_query(self, *a, **kw):
            def decorator(fn):
                return fn
            return decorator

    class F:
        @staticmethod
        def __getattr__(item):
            return MagicMock()

    aiogram_mod.Bot = Bot
    aiogram_mod.Dispatcher = Dispatcher
    aiogram_mod.Router = Router
    aiogram_mod.F = F()

    # aiogram.types
    types_mod = stdlib_types.ModuleType("aiogram.types")

    class ReplyKeyboardMarkup:
        def __init__(self, keyboard=None, resize_keyboard=False, **kw):
            self.keyboard = keyboard or []
            self.resize_keyboard = resize_keyboard

    class KeyboardButton:
        def __init__(self, text="", web_app=None, **kw):
            self.text = text
            self.web_app = web_app

    class WebAppInfo:
        def __init__(self, url=""):
            self.url = url

    class Message:
        def __init__(self, user_id=1, language_code="uk", text=""):
            self.from_user = MagicMock(id=user_id, language_code=language_code)
            self.text = text
            self.answer = AsyncMock()
            self.reply = AsyncMock()

    class CallbackQuery:
        def __init__(self, data="", user_id=1, language_code="uk"):
            self.data = data
            self.from_user = MagicMock(id=user_id, language_code=language_code)
            self.message = Message(user_id=user_id, language_code=language_code)
            self.answer = AsyncMock()

    types_mod.ReplyKeyboardMarkup = ReplyKeyboardMarkup
    types_mod.KeyboardButton = KeyboardButton
    types_mod.WebAppInfo = WebAppInfo
    types_mod.Message = Message
    types_mod.CallbackQuery = CallbackQuery

    # aiogram.types.web_app_info
    web_app_mod = stdlib_types.ModuleType("aiogram.types.web_app_info")
    web_app_mod.WebAppInfo = WebAppInfo
    types_mod.web_app_info = web_app_mod

    # aiogram.filters
    filters_mod = stdlib_types.ModuleType("aiogram.filters")

    class Command:
        def __init__(self, *a, **kw):
            pass

    filters_mod.Command = Command

    # aiogram.fsm
    fsm_mod = stdlib_types.ModuleType("aiogram.fsm")
    fsm_context_mod = stdlib_types.ModuleType("aiogram.fsm.context")
    fsm_state_mod = stdlib_types.ModuleType("aiogram.fsm.state")

    class FSMContext:
        pass

    class State:
        pass

    class StatesGroup:
        pass

    fsm_context_mod.FSMContext = FSMContext
    fsm_state_mod.State = State
    fsm_state_mod.StatesGroup = StatesGroup

    # aiogram.utils.keyboard
    utils_mod = stdlib_types.ModuleType("aiogram.utils")
    kb_mod = stdlib_types.ModuleType("aiogram.utils.keyboard")

    class InlineKeyboardBuilder:
        def __init__(self):
            self._buttons = []

        def button(self, **kw):
            self._buttons.append(kw)

        def adjust(self, *a):
            pass

        def as_markup(self):
            return MagicMock()

    kb_mod.InlineKeyboardBuilder = InlineKeyboardBuilder

    # Register all sub-modules
    sys.modules["aiogram"] = aiogram_mod
    sys.modules["aiogram.types"] = types_mod
    sys.modules["aiogram.types.web_app_info"] = web_app_mod
    sys.modules["aiogram.filters"] = filters_mod
    sys.modules["aiogram.fsm"] = fsm_mod
    sys.modules["aiogram.fsm.context"] = fsm_context_mod
    sys.modules["aiogram.fsm.state"] = fsm_state_mod
    sys.modules["aiogram.utils"] = utils_mod
    sys.modules["aiogram.utils.keyboard"] = kb_mod

    # Attach attributes for "from aiogram import Router, types, F"
    aiogram_mod.types = types_mod
    aiogram_mod.filters = filters_mod
    aiogram_mod.fsm = fsm_mod
    aiogram_mod.utils = utils_mod


def _make_apscheduler_stub():
    if "apscheduler" in sys.modules:
        return
    ap = stdlib_types.ModuleType("apscheduler")
    schedulers = stdlib_types.ModuleType("apscheduler.schedulers")
    asyncio_mod = stdlib_types.ModuleType("apscheduler.schedulers.asyncio")

    class AsyncIOScheduler:
        def __init__(self, **kw):
            pass

    asyncio_mod.AsyncIOScheduler = AsyncIOScheduler
    sys.modules["apscheduler"] = ap
    sys.modules["apscheduler.schedulers"] = schedulers
    sys.modules["apscheduler.schedulers.asyncio"] = asyncio_mod


def _make_dotenv_stub():
    if "dotenv" in sys.modules:
        return
    dotenv_mod = stdlib_types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = lambda: None
    sys.modules["dotenv"] = dotenv_mod


# Install all stubs
_make_supabase_stub()
_make_aiogram_stub()
_make_apscheduler_stub()
_make_dotenv_stub()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_supabase():
    """Return a MagicMock that mimics a Supabase client."""
    client = MagicMock()
    # Default: table().select().eq().execute() → empty data
    client.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    client.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[])
    client.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    return client


@pytest.fixture
def mock_bot():
    """Return an AsyncMock that mimics an aiogram Bot."""
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    bot.send_photo = AsyncMock()
    return bot


@pytest.fixture
def mock_message():
    """Return a fake aiogram Message object."""
    from aiogram.types import Message
    return Message(user_id=123456, language_code="uk", text="/start")


@pytest.fixture
def mock_callback():
    """Return a fake aiogram CallbackQuery object."""
    from aiogram.types import CallbackQuery
    return CallbackQuery(data="manage_biz_test-biz-id", user_id=123456, language_code="uk")
