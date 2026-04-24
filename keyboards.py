import os
import time
import uuid
import jwt
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.types.web_app_info import WebAppInfo
from texts import get_text as _
from config import BASE_URL

# Дістаємо секрет з Railway
JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("SUPABASE_JWT_SECRET environment variable is not set. Bot cannot start without it.")

def generate_token(biz_id=None, user_id=None, is_boss=False):
    """Генерує безпечну криптографічну перепустку, прив'язану до юзера"""
    payload = {
        "exp": int(time.time()) + 86400  # Токен живе 24 години
    }
    
    # 🔒 ДОДАНО: Прив'язуємо токен до конкретного користувача (захист від крадіжки URL)
    if user_id:
        # ✅ FIX: Supabase вимагає UUID у полі sub — генеруємо детермінований UUID з Telegram ID
        # uuid5 дає один і той самий UUID для одного tg_id завжди
        _NS = uuid.UUID('6ba7b810-9dad-11d1-80b4-00c04fd430c8')
        payload["sub"] = str(uuid.uuid5(_NS, str(user_id)))
        payload["tg_id"] = str(user_id)  # зберігаємо оригінальний Telegram ID окремо

    if is_boss:
        payload["role"] = "service_role" # Токен Бога для Супер-Адміна
    else:
        payload["role"] = "authenticated"
        if biz_id:
            payload["biz_id"] = str(biz_id)  # Даємо доступ ТІЛЬКИ до одного закладу
        
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

# --- 1. МЕНЮ РЕЄСТРАЦІЇ ---
def get_reg_kb(lang='uk'):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=_(lang, 'btn_open_app'), web_app=WebAppInfo(url=f"{BASE_URL}delivepro.html"))]
        ],
        resize_keyboard=True
    )

# --- 2. МЕНЮ ВЛАСНИКА ---
def get_owner_kb(biz_id, user_id, lang='uk'):
    t = int(time.time())
    token = generate_token(biz_id=biz_id, user_id=user_id)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=_(lang, 'btn_dashboard'), web_app=WebAppInfo(url=f"{BASE_URL}dashboard.html?biz_id={biz_id}&tg_id={user_id}&v={t}&token={token}"))],
            [KeyboardButton(text=_(lang, 'btn_map'), web_app=WebAppInfo(url=f"{BASE_URL}map.html?biz_id={biz_id}&tg_id={user_id}&v={t}&token={token}"))],
            [KeyboardButton(text=_(lang, 'btn_report')), KeyboardButton(text=_(lang, 'btn_shift_report'))],
            [KeyboardButton(text=_(lang, 'btn_settings')), KeyboardButton(text=_(lang, 'btn_staff'))]
        ],
        resize_keyboard=True
    )

# --- 3. МЕНЮ МЕНЕДЖЕРА ---
def get_manager_kb(biz_id, user_id, lang='uk'):
    t = int(time.time())
    token = generate_token(biz_id=biz_id, user_id=user_id)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=_(lang, 'btn_dashboard'), web_app=WebAppInfo(url=f"{BASE_URL}dashboard.html?biz_id={biz_id}&tg_id={user_id}&v={t}&token={token}"))],
            [KeyboardButton(text=_(lang, 'btn_new_order'), web_app=WebAppInfo(url=f"{BASE_URL}form.html?biz_id={biz_id}&tg_id={user_id}&v={t}&token={token}"))],
            [KeyboardButton(text=_(lang, 'btn_map'), web_app=WebAppInfo(url=f"{BASE_URL}map.html?biz_id={biz_id}&tg_id={user_id}&v={t}&token={token}"))],
            [KeyboardButton(text=_(lang, 'btn_report')), KeyboardButton(text=_(lang, 'btn_shift_report'))],
            [KeyboardButton(text=_(lang, 'btn_active_orders'), web_app=WebAppInfo(url=f"{BASE_URL}orders.html?biz_id={biz_id}&tg_id={user_id}&v={t}&token={token}"))],
            [KeyboardButton(text=_(lang, 'btn_schedule'), web_app=WebAppInfo(url=f"{BASE_URL}schedule.html?biz_id={biz_id}&tg_id={user_id}&v={t}&token={token}"))]
        ],
        resize_keyboard=True
    )

# --- 4. МЕНЮ КУР'ЄРА ---
def get_courier_kb(biz_id, user_id, lang='uk', shift_active=False):
    t = int(time.time())
    token = generate_token(biz_id=biz_id, user_id=user_id)
    shift_btn = KeyboardButton(text=_(lang, 'btn_close_shift')) if shift_active else KeyboardButton(text=_(lang, 'btn_start_shift'))
    return ReplyKeyboardMarkup(
        keyboard=[
            [shift_btn],
            # ✅ Кнопка "Мої замовлення" — відновлення кнопок після перезапуску боту
            [KeyboardButton(text=_(lang, 'btn_my_active_orders'))],
            [KeyboardButton(text=_(lang, 'btn_map'), web_app=WebAppInfo(url=f"{BASE_URL}map.html?biz_id={biz_id}&tg_id={user_id}&v={t}&token={token}"))],
            [KeyboardButton(text=_(lang, 'btn_my_deliveries'), web_app=WebAppInfo(url=f"{BASE_URL}orders.html?biz_id={biz_id}&tg_id={user_id}&v={t}&token={token}"))],
            [KeyboardButton(text=_(lang, 'btn_schedule'), web_app=WebAppInfo(url=f"{BASE_URL}schedule.html?biz_id={biz_id}&tg_id={user_id}&v={t}&token={token}"))]
        ],
        resize_keyboard=True
    )

# --- 5. МЕНЮ СУПЕР-АДМІНА ---
def get_superadmin_kb(user_id, lang='uk'):
    t = int(time.time())
    token = generate_token(user_id=user_id, is_boss=True) # 🔒 Передаємо user_id
    web_app_url = f"{BASE_URL}boss.html?tg_id={user_id}&v={t}&token={token}"
    
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=_(lang, 'btn_boss_panel'), web_app=WebAppInfo(url=web_app_url))]
        ],
        resize_keyboard=True
    )
