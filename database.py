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
    """Записуємо кур'єра або менеджера в базу"""
    data = {
        "user_id": user_id,
        "name": name,
        "business_id": biz_id,
        "role": role
    }
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
    data = {
        "business_id": order_data['biz_id'],
        "courier_id": order_data['courier_id'],
        "client_name": order_data.get('client_name'),
        "client_phone": order_data.get('client_phone'),
        "address": order_data.get('address'),
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
    """Генерує дані для звіту адміна за поточний день"""
    today = datetime.datetime.now(timezone.utc).date()
    start_of_day = datetime.datetime(today.year, today.month, today.day, tzinfo=timezone.utc).isoformat()

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

    for o in orders:
        c_id = str(o['courier_id'])
        if c_id not in report:
            report[c_id] = {'count': 0, 'cash': 0.0, 'term': 0.0, 'online': 0, 'name': staff_dict.get(c_id, "Невідомий")}
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
            # online — сума вже сплачена, окремо рахуємо кількість
            report[c_id]['online'] += 1
            total_online += 1

    return report, total_cash, total_term, total_online
