from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.types.web_app_info import WebAppInfo

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
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Дашборд", web_app=WebAppInfo(url=f"{URL}dashboard.html?biz_id={biz_id}"))],
            [KeyboardButton(text="⚙️ Налаштування бізнесу"), KeyboardButton(text="👥 Персонал")]
        ],
        resize_keyboard=True
    )

# --- 3. МЕНЮ МЕНЕДЖЕРА ---
def get_manager_kb(biz_id):
    return ReplyKeyboardMarkup(
        keyboard=[
            # ДОДАЛИ v=2& ОСЬ ТУТ 👇 ЩОБ ВБИТИ КЕШ!
            [KeyboardButton(text="📝 Нове замовлення", web_app=WebAppInfo(url=f"{URL}form.html?v=2&biz_id={biz_id}"))],
            [KeyboardButton(text="📂 Активні замовлення", web_app=WebAppInfo(url=f"{URL}archive.html?biz_id={biz_id}"))]
        ],
        resize_keyboard=True
    )

# --- 4. МЕНЮ КУР'ЄРА ---
def get_courier_kb(biz_id):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📦 Мої доставки", web_app=WebAppInfo(url=f"{URL}archive.html?biz_id={biz_id}"))]
        ],
        resize_keyboard=True
    )
