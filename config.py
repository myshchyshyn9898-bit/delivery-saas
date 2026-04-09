import os
from dotenv import load_dotenv

# Завантажуємо змінні з файлу .env
load_dotenv()

# Тепер змінні безпечно підтягуються з середовища
API_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN")

# Посилання на твій бот (не забудь замінити на реальне!)
BOT_USERNAME = os.getenv("BOT_USERNAME", "@deliprobot")

# Головне посилання на твій GitHub Pages
_base_url = os.getenv("BASE_URL", "https://myshchyshyn9898-bit.github.io/delivery-saas")
BASE_URL = _base_url if _base_url.endswith("/") else _base_url + "/"

# Твій ID та ID помічника
_super_admin_ids_raw = os.getenv("SUPER_ADMIN_IDS", "")
try:
    SUPER_ADMIN_IDS = [int(x) for x in _super_admin_ids_raw.split(",") if x.strip()] if _super_admin_ids_raw.strip() else []
except ValueError as e:
    raise ValueError(f"SUPER_ADMIN_IDS contains non-numeric values. Expected comma-separated integers, got: {_super_admin_ids_raw!r}") from e

# Секрети для верифікації вебхуків (опціонально, але рекомендовано)
WHOP_WEBHOOK_SECRET = os.getenv("WHOP_WEBHOOK_SECRET")
POSTER_WEBHOOK_SECRET = os.getenv("POSTER_WEBHOOK_SECRET")
