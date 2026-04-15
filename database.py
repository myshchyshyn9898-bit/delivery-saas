import asyncio
import datetime
import logging
from datetime import timedelta, timezone
from supabase import create_client, Client

from config import SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_KEY

logger = logging.getLogger(__name__)

if not SUPABASE_URL:
    raise RuntimeError("SUPABASE_URL environment variable is not set. Bot cannot start without it.")

_db_key = SUPABASE_SERVICE_KEY or SUPABASE_KEY
if not _db_key:
    raise RuntimeError("Neither SUPABASE_SERVICE_KEY nor SUPABASE_KEY is set. Bot cannot start.")

supabase: Client = create_client(SUPABASE_URL, _db_key)

# ==========================================
# ХЕЛПЕР: запускає синхронний DB-виклик
# в окремому потоці, не блокуючи event loop
# ==========================================

async def _run(fn):
    """Виконує синхронний supabase запит у thread pool."""
    return await asyncio.to_thread(fn)

# ==========================================
# ФУНКЦІЇ ДЛЯ БІЗНЕСУ ТА АДМІНІСТРУВАННЯ
# ==========================================

async def get_business_by_owner(owner_id: int):
    res = await _run(lambda: supabase.table("businesses").select("*").eq("owner_id", owner_id).execute())
    return res.data[0] if res.data else None

async def get_business_by_id(biz_id: str):
    """Отримуємо інфо про бізнес за його UUID"""
    res = await _run(lambda: supabase.table("businesses").select("*").eq("id", biz_id).execute())
    return res.data[0] if res.data else None

async def register_new_business(owner_id: int, biz_data: dict):
    """Розширена реєстрація з Web App (з координатами та Тріалом на 7 днів)"""
    now = datetime.datetime.now(timezone.utc)
    trial_end = now + timedelta(days=7)
    data = {
        "owner_id": owner_id,
        "name": biz_data.get("name"),
        "description": biz_data.get("desc"),
        "phone": biz_data.get("phone"),
        "country": biz_data.get("location", {}).get("country"),
        "city": biz_data.get("location", {}).get("city"),
        "street": biz_data.get("location", {}).get("street"),
        "lat": biz_data.get("location", {}).get("lat"),
        "lng": biz_data.get("location", {}).get("lng"),
        "radius_km": int(biz_data.get("radius", 5)),
        "currency": biz_data.get("currency", "zł"),
        "payments": biz_data.get("payments", []),
        # ✅ ВИПРАВЛЕНО: зберігаємо режим доставки (dispatcher/uber)
        "delivery_mode": biz_data.get("delivery_mode", "dispatcher"),
        # ✅ ВИПРАВЛЕНО: зберігаємо ID групи кур'єрів для uber-режиму
        "courier_group_id": biz_data.get("courier_group_id"),
        "plan": "trial",
        "subscription_expires_at": trial_end.isoformat(),
        "is_active": True
    }
    return await _run(lambda: supabase.table("businesses").insert(data).execute())

async def get_all_businesses():
    """Для панелі супер-адміна"""
    res = await _run(lambda: supabase.table("businesses").select("*").execute())
    return res.data

async def update_subscription(biz_id: str, is_active: bool):
    """Для панелі супер-адміна"""
    await _run(lambda: supabase.table("businesses").update({"is_active": is_active}).eq("id", biz_id).execute())

# ==========================================
# ФУНКЦІЇ ДЛЯ ПІДПИСОК ТА WHOP
# ==========================================

async def get_actual_plan(biz_id: str) -> str:
    """
    Розумна перевірка тарифу.
    Якщо час вийшов — автоматично переводить в 'expired'.
    """
    res = await _run(lambda: supabase.table("businesses").select("plan, subscription_expires_at").eq("id", biz_id).execute())
    if not res.data:
        return "expired"

    biz = res.data[0]
    plan = biz.get("plan", "expired")
    expires_at_str = biz.get("subscription_expires_at")

    if not expires_at_str or plan == "expired":
        return plan

    expires_at = datetime.datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
    now = datetime.datetime.now(timezone.utc)

    if now > expires_at:
        await _run(lambda: supabase.table("businesses").update({"plan": "expired"}).eq("id", biz_id).execute())
        return "expired"

    return plan

async def activate_whop_subscription(biz_id: str, plan_name: str, membership_id: str):
    """Функція для Webhook: активує підписку після успішної оплати на Whop."""
    now = datetime.datetime.now(timezone.utc)
    next_month = now + timedelta(days=30)
    data = {
        "plan": plan_name,
        "subscription_expires_at": next_month.isoformat(),
        "whop_membership_id": membership_id
    }
    return await _run(lambda: supabase.table("businesses").update(data).eq("id", biz_id).execute())

# ==========================================
# ФУНКЦІЇ ДЛЯ КОРИСТУВАЧІВ ТА ПЕРСОНАЛУ
# ==========================================

async def get_user_context(user_id: int):
    """
    Повертає роль юзера та дані його бізнесу.
    Спочатку шукаємо чи він Власник, потім чи він Персонал.
    """
    biz = await _run(lambda: supabase.table("businesses").select("*").eq("owner_id", user_id).execute())
    if biz.data:
        return {"role": "owner", "biz": biz.data[0]}

    staff = await _run(lambda: supabase.table("staff").select("*").eq("user_id", user_id).execute())
    if staff.data:
        s = staff.data[0]
        biz_info = await _run(lambda: supabase.table("businesses").select("*").eq("id", s["business_id"]).execute())
        if biz_info.data:
            return {"role": s["role"], "biz": biz_info.data[0]}

    return None

async def create_staff(user_id: int, name: str, biz_id: str, role: str = 'courier'):
    """Записуємо кур'єра або менеджера в базу.

    ✅ ВИПРАВЛЕНО bug #1: раніше upsert on_conflict='user_id' перезаписував весь запис,
    тому кур'єр міг бути тільки в одному бізнесі — при повторній реєстрації він
    'зникав' з попереднього закладу. Тепер перевіряємо пару (user_id, business_id):
    якщо такий запис вже є — оновлюємо ім'я/роль, якщо немає — вставляємо новий.
    """
    # Перевіряємо чи вже є запис для цієї пари (user_id + business_id)
    existing = await _run(
        lambda: supabase.table("staff")
            .select("id")
            .eq("user_id", user_id)
            .eq("business_id", biz_id)
            .execute()
    )
    data = {
        "user_id": user_id,
        "name": name,
        "business_id": biz_id,
        "role": role
    }
    if existing.data:
        # Запис є — оновлюємо ім'я та роль
        record_id = existing.data[0]["id"]
        return await _run(
            lambda: supabase.table("staff").update({"name": name, "role": role}).eq("id", record_id).execute()
        )
    else:
        # Нового кур'єра — просто вставляємо
        return await _run(lambda: supabase.table("staff").insert(data).execute())

async def get_courier(user_id: int):
    """Отримуємо інфо про конкретного працівника"""
    res = await _run(lambda: supabase.table("staff").select("*").eq("user_id", user_id).execute())
    return res.data[0] if res.data else None

# ==========================================
# ФУНКЦІЇ ДЛЯ ЗАМОВЛЕНЬ
# ==========================================

async def create_new_order(order_data: dict):
    """Записуємо нове замовлення в таблицю orders"""
    # Збираємо деталі (квартира/поверх/домофон) якщо передані окремо
    details = order_data.get('details', '')
    if not details:
        parts = []
        if order_data.get('apt'):
            parts.append(f"Кв/Оф: {order_data['apt']}")
        if order_data.get('floor'):
            parts.append(f"Пов: {order_data['floor']}")
        if order_data.get('code'):
            parts.append(f"Домофон: {order_data['code']}")
        details = ', '.join(parts)

    data = {
        "business_id": order_data['biz_id'],
        "courier_id": order_data['courier_id'],
        "client_name": order_data.get('client_name'),
        "client_phone": order_data.get('client_phone'),
        "address": order_data.get('address'),
        "details": details,
        "amount": order_data.get('amount'),
        "pay_type": order_data.get('payment'),
        "comment": order_data.get('comment'),
        "lat": order_data.get('lat'),
        "lon": order_data.get('lon'),
        "est_time": int(order_data.get('est_time') or 30),
        "status": "pending"
    }
    result = await _run(lambda: supabase.table("orders").insert(data).execute())
    return result.data[0] if result.data else None

async def update_order_status(order_id: str, new_status: str, actual_pay_type: str = None):
    """Оновлює статус замовлення в базі та фіксує час завершення.
    actual_pay_type — реальний тип оплати який натиснув кур'єр (може відрізнятись від вибраного адміном)
    """
    data = {"status": new_status}
    if new_status == "completed":
        data["completed_at"] = datetime.datetime.now(timezone.utc).isoformat()
    # ✅ Зберігаємо реальний тип оплати якщо кур'єр змінив
    if actual_pay_type:
        data["pay_type"] = actual_pay_type
    await _run(lambda: supabase.table("orders").update(data).eq("id", order_id).execute())

async def get_daily_report(biz_id: str):
    """Генерує дані для звіту адміна за поточний день.

    ✅ ВИПРАВЛЕНО bug #4: раніше використовувалась глобальна BUSINESS_TZ,
    тому бізнеси в різних містах рахували день неправильно.
    Тепер timezone береться з поля businesses.timezone якщо є,
    інакше fallback до глобальної BUSINESS_TZ.
    """
    import zoneinfo as _zi
    from config import BUSINESS_TZ

    # Намагаємось дістати timezone бізнесу. Якщо колонки timezone ще немає в БД
    # або будь-яка інша помилка — мовчки падаємо на глобальний BUSINESS_TZ.
    biz_tz = BUSINESS_TZ
    try:
        tz_res = await _run(
            lambda: supabase.table("businesses").select("timezone").eq("id", biz_id).execute()
        )
        if tz_res.data and tz_res.data[0].get("timezone"):
            biz_tz = _zi.ZoneInfo(tz_res.data[0]["timezone"])
    except Exception:
        pass  # колонки немає або невалідна tz — fallback до BUSINESS_TZ

    now_local = datetime.datetime.now(biz_tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day = start_local.astimezone(timezone.utc).isoformat()

    res_orders = await _run(
        lambda: supabase.table("orders")
            .select("courier_id, amount, pay_type")
            .eq("business_id", biz_id)
            .eq("status", "completed")
            .gte("created_at", start_of_day)
            .limit(5000)
            .execute()
    )
    orders = res_orders.data if res_orders.data else []

    res_staff = await _run(
        lambda: supabase.table("staff").select("user_id, name").eq("business_id", biz_id).execute()
    )
    staff_dict = {str(s['user_id']): s['name'] for s in (res_staff.data if res_staff.data else [])}

    report = {}
    total_cash = 0
    total_term = 0
    total_online = 0
    total_online_sum = 0.0  # ✅ ВИПРАВЛЕНО bug #10: рахуємо суму онлайн-замовлень

    for o in orders:
        if not o.get('courier_id'):
            continue  # пропускаємо замовлення без призначеного кур'єра
        c_id = str(o['courier_id'])
        if c_id not in report:
            report[c_id] = {'count': 0, 'cash': 0.0, 'term': 0.0, 'online': 0, 'online_sum': 0.0, 'name': staff_dict.get(c_id, "Невідомий")}
        report[c_id]['count'] += 1
        amt = float(o['amount'])
        pay = o['pay_type']
        if pay == 'cash':
            report[c_id]['cash'] += amt
            total_cash += amt
        elif pay == 'terminal':
            report[c_id]['term'] += amt
            total_term += amt
        else:
            # online — сума вже сплачена онлайн
            report[c_id]['online'] += 1
            report[c_id]['online_sum'] += amt
            total_online += 1
            total_online_sum += amt

    return report, total_cash, total_term, total_online, total_online_sum

# ==========================================
# ✅ ВИПРАВЛЕНО: IN-MEMORY КЕШ для get_user_context
# Зменшує кількість DB запитів з 2-3 до 0 протягом 60 сек
# ==========================================

import time as _time
from typing import Optional

_context_cache: dict = {}
_CACHE_TTL = 60  # секунд

async def get_user_context_cached(user_id: int) -> Optional[dict]:
    """
    Кешована версія get_user_context.
    Перший виклик іде в БД, наступні 60 сек — з пам'яті.
    """
    now = _time.monotonic()
    cached = _context_cache.get(user_id)

    if cached:
        ts, data = cached
        if now - ts < _CACHE_TTL:
            return data

    context = await get_user_context(user_id)
    _context_cache[user_id] = (now, context)
    return context

def invalidate_user_cache(user_id: int):
    """
    Скидає кеш для юзера. Викликати після зміни ролі,
    реєстрації бізнесу або додавання персоналу.
    """
    _context_cache.pop(user_id, None)

# ==========================================
# ФУНКЦІЇ ДЛЯ ЗМІН КУР'ЄРІВ (SHIFTS)
# ==========================================

async def get_active_shift(courier_id: int, biz_id: str):
    """Повертає активну зміну кур'єра або None."""
    res = await _run(
        lambda: supabase.table("shifts")
            .select("*")
            .eq("courier_id", courier_id)
            .eq("business_id", biz_id)
            .is_("ended_at", "null")
            .execute()
    )
    return res.data[0] if res.data else None

async def open_shift(courier_id: int, biz_id: str, start_km: int, start_photo_id: str):
    """Відкриває нову зміну."""
    data = {
        "courier_id": courier_id,
        "business_id": biz_id,
        "start_km": start_km,
        "start_photo_id": start_photo_id,
        "started_at": datetime.datetime.now(timezone.utc).isoformat(),
    }
    res = await _run(lambda: supabase.table("shifts").insert(data).execute())
    return res.data[0] if res.data else None

async def close_shift(shift_id: str, end_km: int, end_photo_id: str):
    """Закриває зміну — записує кінцевий км і час."""
    data = {
        "end_km": end_km,
        "end_photo_id": end_photo_id,
        "ended_at": datetime.datetime.now(timezone.utc).isoformat(),
    }
    await _run(lambda: supabase.table("shifts").update(data).eq("id", shift_id).execute())

async def get_shift_orders_stats(courier_id: int, biz_id: str, since_iso: str):
    """Повертає кількість замовлень, готівку, термінал за зміну."""
    res = await _run(
        lambda: supabase.table("orders")
            .select("amount, pay_type")
            .eq("courier_id", courier_id)
            .eq("business_id", biz_id)
            .eq("status", "completed")
            .gte("completed_at", since_iso)
            .execute()
    )
    orders = res.data or []
    count = len(orders)
    cash = sum(float(o["amount"]) for o in orders if o.get("pay_type") == "cash")
    term = sum(float(o["amount"]) for o in orders if o.get("pay_type") == "terminal")
    return count, cash, term

async def get_km_rate(biz_id: str) -> float:
    """Повертає ціну за км з налаштувань бізнесу."""
    res = await _run(
        lambda: supabase.table("businesses").select("km_rate").eq("id", biz_id).execute()
    )
    if res.data and res.data[0].get("km_rate"):
        return float(res.data[0]["km_rate"])
    return 0.0

async def get_today_shifts_report(biz_id: str):
    """Повертає всі закриті зміни за сьогодні для адмін-звіту."""
    import zoneinfo as _zi
    from config import BUSINESS_TZ
    biz_tz = BUSINESS_TZ
    try:
        tz_res = await _run(
            lambda: supabase.table("businesses").select("timezone").eq("id", biz_id).execute()
        )
        if tz_res.data and tz_res.data[0].get("timezone"):
            biz_tz = _zi.ZoneInfo(tz_res.data[0]["timezone"])
    except Exception:
        pass

    now_local = datetime.datetime.now(biz_tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_day = start_local.astimezone(timezone.utc).isoformat()

    res = await _run(
        lambda: supabase.table("shifts")
            .select("*")
            .eq("business_id", biz_id)
            .gte("started_at", start_of_day)
            .execute()
    )
    return res.data or []
