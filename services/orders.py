"""
services/orders.py — чиста бізнес-логіка замовлень.

Цей файл НЕ імпортує нічого з aiogram / Telegram.
Тому його можна викликати з:
  - Telegram bot (handlers/)
  - FastAPI endpoint (якщо колись додаси)
  - Тести (без мок-об'єктів Telegram)
"""

import urllib.parse

from config import BASE_URL
from texts import get_text as _gt


# ===========================================================================
# URL ДЛЯ ДЗВІНКА
# ===========================================================================

def build_call_url(phone: str, biz: dict = None, lang: str = "en") -> tuple:
    """
    Повертає (btn_text, call_url) для кнопки дзвінка.
    - 8 цифр → Uber Call (набирає номер Uber + код)
    - інакше  → звичайний дзвінок
    """
    base = BASE_URL.rstrip('/')
    digits_only = "".join(filter(str.isdigit, phone or ""))

    if len(digits_only) == 8:
        dispatcher = ""
        if biz and biz.get("uber_dispatcher"):
            dispatcher = str(biz["uber_dispatcher"]).strip()
        if dispatcher:
            call_url = (
                f"{base}/call.html"
                f"?code={urllib.parse.quote(digits_only)}"
                f"&dispatcher={urllib.parse.quote(dispatcher)}"
            )
        else:
            call_url = f"{base}/call.html?code={urllib.parse.quote(digits_only)}"
        return "🚖 Uber Call", call_url
    else:
        safe_phone = phone or ""
        call_url = f"{base}/call.html?code={urllib.parse.quote(safe_phone)}"
        return _gt(lang, 'btn_call_regular'), call_url


# ===========================================================================
# ТЕКСТ ЗАМОВЛЕННЯ
# ===========================================================================

def build_order_text(
    short_id, address, details_text, client_name,
    phone, pay_type, amount, currency, comment,
    status_line, source_label="", lang="en"
) -> str:
    """
    Будує уніфікований текст замовлення для БУДЬ-ЯКОГО режиму.
    Не залежить від Telegram — повертає просто рядок.
    """
    if pay_type == "cash":
        pay_line = _gt(lang, 'order_pay_cash_line', amount=amount, cur=currency)
    elif pay_type == "terminal":
        pay_line = _gt(lang, 'order_pay_terminal_line', amount=amount, cur=currency)
    else:
        pay_line = _gt(lang, 'order_pay_online_line')

    order_lbl  = _gt(lang, 'order_label')
    status_lbl = _gt(lang, 'order_status_lbl')
    addr_lbl   = _gt(lang, 'order_address_lbl')
    det_lbl    = _gt(lang, 'order_details_lbl')
    cli_lbl    = _gt(lang, 'order_client_lbl')
    tel_lbl    = _gt(lang, 'order_tel_lbl')
    com_lbl    = _gt(lang, 'order_comment_lbl')

    prefix = f"{source_label}\n" if source_label else ""
    txt = (
        f"{prefix}"
        f"📦 <b>{order_lbl} #{short_id}</b>\n"
        f"➖➖➖➖➖➖\n"
        f"<b>{status_lbl}:</b> {status_line}\n\n"
        f"📍 <b>{addr_lbl}:</b> {address}\n"
    )
    if details_text:
        txt += f"🏢 <b>{det_lbl}:</b> {details_text}\n"
    if client_name and client_name not in ("—", "Клієнт", "Client", "Klient", "Клиент", ""):
        txt += f"👤 <b>{cli_lbl}:</b> {client_name}\n"
    txt += f"📞 <b>{tel_lbl}:</b> {phone or '—'}\n"
    txt += f"{pay_line}\n"
    txt += "➖➖➖➖➖➖"
    if comment:
        txt += f"\n🗣 <b>{com_lbl}:</b> {comment}"
    return txt


def build_order_text_from_pay_icon(
    short_id, source_label, address, details_text,
    client_name, phone, pay_icon, amount, currency,
    pay_type_str, comment, status_line, lang="en"
) -> str:
    """
    Зворотна сумісність — визначає pay_type з emoji і делегує в build_order_text.
    """
    if pay_icon == "💵":
        pay_type = "cash"
    elif pay_icon in ("💳", "🏧"):
        pay_type = "terminal"
    else:
        pay_type = "online"
    return build_order_text(
        short_id, address, details_text, client_name,
        phone, pay_type, amount, currency, comment,
        status_line, source_label, lang
    )


# ===========================================================================
# МАРШРУТ
# ===========================================================================

def build_route_url(address: str) -> str:
    """Google Maps посилання для маршруту."""
    return f"https://www.google.com/maps/dir/?api=1&destination={urllib.parse.quote(address)}"


# ===========================================================================
# НОРМАЛІЗАЦІЯ ТЕЛЕФОНУ
# ===========================================================================

def normalize_phone(phone_raw: str) -> str:
    """
    Прибирає все крім цифр і '+'.
    Якщо починається з цифри — додає '+'.
    """
    phone_clean = "".join(filter(lambda x: x.isdigit() or x == '+', phone_raw or ""))
    if phone_clean and not phone_clean.startswith('+'):
        phone_clean = '+' + phone_clean
    return phone_clean


# ===========================================================================
# ЗАХОПЛЕННЯ ЗАМОВЛЕННЯ — чиста DB логіка (без Telegram)
# ===========================================================================

async def capture_order(
    order_id: str,
    taker_id: int,
    already_captured: bool = False,
) -> dict:
    """
    Перевіряє і атомарно захоплює замовлення в БД.

    Повертає:
      {"ok": True,  "order": {...}, "biz": {...}}  — успіх
      {"ok": False, "error": "order_not_found"}    — немає замовлення
      {"ok": False, "error": "courier_not_in_staff"} — кур'єр не в команді
      {"ok": False, "error": "order_already_assigned"} — вже захоплено

    already_captured=True — map.html вже зробив UPDATE сам,
    пропускаємо кроки захоплення і лише перевіряємо.

    НЕ відправляє жодних Telegram повідомлень — це робота handlers/.
    """
    import database as db

    # 1. Читаємо замовлення
    res = await db._run(
        lambda: db.supabase.table("orders").select("*").eq("id", order_id).execute()
    )
    if not res.data:
        return {"ok": False, "error": "order_not_found"}

    order = res.data[0]

    if already_captured:
        # map.html вже зробив UPDATE — перевіряємо що саме ми захопили
        if str(order.get("courier_id", "")) != str(taker_id):
            return {"ok": False, "error": "order_already_assigned"}
    else:
        # 2. Перевірка статусу
        if order["status"] != "pending":
            return {"ok": False, "error": "order_already_assigned"}

        # 3. Перевірка що кур'єр є в staff цього бізнесу
        staff_check = await db._run(
            lambda: db.supabase.table("staff")
                .select("user_id")
                .eq("user_id", taker_id)
                .eq("business_id", order["business_id"])
                .execute()
        )
        if not staff_check.data:
            return {"ok": False, "error": "courier_not_in_staff"}

        # 4. Атомарне захоплення — UPDATE тільки якщо статус ще pending
        take_res = await db._run(
            lambda: db.supabase.table("orders")
                .update({"status": "delivering", "courier_id": taker_id})
                .eq("id", order_id)
                .eq("status", "pending")
                .execute()
        )
        if not take_res.data:
            # Хтось встиг раніше — перевіряємо хто саме
            verify = await db._run(
                lambda: db.supabase.table("orders")
                    .select("courier_id, status")
                    .eq("id", order_id)
                    .execute()
            )
            if not verify.data or str(verify.data[0].get("courier_id")) != str(taker_id):
                return {"ok": False, "error": "order_already_assigned"}

    # 5. Підтягуємо бізнес (потрібен для currency, group_id тощо)
    biz = await db.get_business_by_id(order["business_id"])

    return {"ok": True, "order": order, "biz": biz}
