import os
from dotenv import load_dotenv

# Завантажуємо змінні з файлу .env
load_dotenv()

# Тепер змінні безпечно підтягуються з середовища
API_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Посилання на твій бот (не забудь замінити на реальне!)
BOT_USERNAME = "@deliprobot" 

# Головне посилання на твій GitHub Pages
BASE_URL = "https://myshchyshyn9898-bit.github.io/delivery-saas"

# Твій ID та ID помічника
SUPER_ADMIN_IDS = [6889016268] 
