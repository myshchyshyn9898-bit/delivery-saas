from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.types.web_app_info import WebAppInfo
import time
from texts import get_text as _ # <--- ІМПОРТ СЛОВНИКА

# Головне посилання на твій GitHub Pages
URL = "https://myshchyshyn9898-bit.github.io/delivery-saas/"

# --- 1. МЕНЮ РЕЄСТРАЦІЇ ---
def get_reg_kb(lang='uk'):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=_(lang, 'btn_open_app'), web_app=WebAppInfo(url=f"{URL}delivepro.html"))]
        ],
        resize_keyboard=True
    )

# --- 2. МЕНЮ ВЛАСНИКА ---
def get_owner_kb(biz_id, user_id, lang='uk'):
    t = int(time.time())
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=_(lang, 'btn_dashboard'), web_app=WebAppInfo(url=f"{URL}dashboard.html?biz_id={biz_id}&tg_id={user_id}&v={t}"))],
            [KeyboardButton(text=_(lang, 'btn_report'))],
            [KeyboardButton(text=_(lang, 'btn_settings')), KeyboardButton(text=_(lang, 'btn_staff'))]
        ],
        resize_keyboard=True
    )

# --- 3. МЕНЮ МЕНЕДЖЕРА ---
def get_manager_kb(biz_id, user_id, lang='uk'):
    t = int(time.time())
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=_(lang, 'btn_new_order'), web_app=WebAppInfo(url=f"{URL}form.html?biz_id={biz_id}&tg_id={user_id}&v={t}"))],
            [KeyboardButton(text=_(lang, 'btn_report'))],
            [KeyboardButton(text=_(lang, 'btn_active_orders'), web_app=WebAppInfo(url=f"{URL}orders.html?biz_id={biz_id}&tg_id={user_id}&v={t}"))]
        ],
        resize_keyboard=True
    )

# --- 4. МЕНЮ КУР'ЄРА ---
def get_courier_kb(biz_id, user_id, lang='uk'):
    t = int(time.time())
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=_(lang, 'btn_my_deliveries'), web_app=WebAppInfo(url=f"{URL}orders.html?biz_id={biz_id}&tg_id={user_id}&v={t}"))]
        ],
        resize_keyboard=True
    )

# --- 5. МЕНЮ СУПЕР-АДМІНА (ВЛАСНИКА БОТА) ---
def get_superadmin_kb(user_id, lang='uk'):
    t = int(time.time())
    web_app_url = f"{URL}boss.html?tg_id={user_id}&v={t}"
    
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=_(lang, 'btn_boss_panel'), web_app=WebAppInfo(url=web_app_url))]
        ],
        resize_keyboard=True
    )
    return keyboard
