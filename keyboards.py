from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.types.web_app_info import WebAppInfo
import time  # <-- ДОДАЛИ ДЛЯ ГЕНЕРАЦІЇ УНІКАЛЬНОГО ЧАСУ

# Головне посилання на твій GitHub Pages
URL = "https://myshchyshyn9898-bit.github.io/delivery-saas/"

# --- 1. МЕНЮ РЕЄСТРАЦІЇ (Для нових юзерів) ---
# Ця кнопка запускає наш новий крутий додаток!
reg_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🚀 Відкрити DelivePro", web_app=WebAppInfo(url=f"{URL}delivepro.html"))]
    ],
    resize_keyboard=True
)

# --- 2. МЕНЮ ВЛАСНИКА ---
def get_owner_kb(biz_id):
    t = int(time.time()) # Генеруємо унікальне число (секунди)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Дашборд", web_app=WebAppInfo(url=f"{URL}dashboard.html?biz_id={biz_id}&v={t}"))],
            [KeyboardButton(text="📊 Зробити звіт")], # <-- ДОДАВ КОМУ
            [KeyboardButton(text="⚙️ Налаштування бізнесу"), KeyboardButton(text="👥 Персонал")]
        ],
        resize_keyboard=True
    )

# --- 3. МЕНЮ МЕНЕДЖЕРА ---
def get_manager_kb(biz_id):
    t = int(time.time()) # Кеш більше не пройде!
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Нове замовлення", web_app=WebAppInfo(url=f"{URL}form.html?biz_id={biz_id}&v={t}"))],
            [KeyboardButton(text="📊 Зробити звіт")], # <-- ДОДАВ КОМУ
            [KeyboardButton(text="📂 Активні замовлення", web_app=WebAppInfo(url=f"{URL}orders.html?biz_id={biz_id}&v={t}"))] # ЗМІНИЛИ НА orders.html
        ],
        resize_keyboard=True
    )

# --- 4. МЕНЮ КУР'ЄРА ---
def get_courier_kb(biz_id):
    t = int(time.time()) # Кеш більше не пройде!
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📦 Мої доставки", web_app=WebAppInfo(url=f"{URL}orders.html?biz_id={biz_id}&v={t}"))] # ЗМІНИЛИ НА orders.html
        ],
        resize_keyboard=True
    )
