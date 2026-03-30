import datetime
from datetime import timedelta, timezone
from supabase import create_client, Client

# Імпортуємо вже готові змінні, які config.py дістав із системних змінних Railway
from config import SUPABASE_URL, SUPABASE_KEY 

# Тепер ініціалізуємо клієнта, використовуючи імпортовані дані
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==========================================
# ФУНКЦІЇ ДЛЯ БІЗНЕСУ ТА АДМІНІСТРУВАННЯ
# ==========================================

def get_business_by_owner(owner_id: int):
    res = supabase.table("businesses").select("*").eq("owner_id", owner_id).execute()
    return res.data[0] if res.data else None

def get_business_by_id(biz_id: str):
    """Отримуємо інфо про бізнес за його UUID"""
    res = supabase.table("businesses").select("*").eq("id", biz_id).execute()
    return res.data[0] if res.data else None

def register_new_business(owner_id: int, biz_data: dict):
    """Розширена реєстрація з Web App (з координатами та Тріалом на 7 днів)"""
    
    # Розраховуємо дату закінчення безкоштовного тріалу (сьогодні + 7 днів)
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
        "plan": "trial",  # Всі нові заклади отримують статус trial
        "subscription_expires_at": trial_end.isoformat(), # Дата закінчення
        "is_active": True
    }
    return supabase.table("businesses").insert(data).execute()

def get_all_businesses():
    """Для панелі супер-адміна"""
    res = supabase.table("businesses").select("*").execute()
    return res.data

def update_subscription(biz_id: str, is_active: bool):
    """Для панелі супер-адміна"""
    supabase.table("businesses").update({"is_active": is_active}).eq("id", biz_id).execute()

# ==========================================
# ФУНКЦІЇ ДЛЯ ПІДПИСОК ТА WHOP (ОХОРОНЕЦЬ)
# ==========================================

def get_actual_plan(biz_id: str) -> str:
    """
    Розумна перевірка тарифу. 
    Якщо час вийшов — автоматично переводить в 'expired'.
    """
    res = supabase.table("businesses").select("plan, subscription_expires_at").eq("id", biz_id).execute()
    if not res.data:
        return "expired"
        
    biz = res.data[0]
    plan = biz.get("plan", "expired")
    expires_at_str = biz.get("subscription_expires_at")
    
    # Якщо немає дати, або статус вже expired, повертаємо як є
    if not expires_at_str or plan == "expired":
        return plan
        
    # Конвертуємо рядок з бази в об'єкт datetime
    expires_at = datetime.datetime.fromisoformat(expires_at_str.replace('Z', '+00:00'))
    now = datetime.datetime.now(timezone.utc)
    
    # Перевіряємо, чи не закінчився час (тріалу або підписки)
    if now > expires_at:
        # Час вийшов! Оновлюємо базу
        supabase.table("businesses").update({"plan": "expired"}).eq("id", biz_id).execute()
        return "expired"
        
    return plan

def activate_whop_subscription(biz_id: str, plan_name: str, membership_id: str):
    """
    Функція для Webhook: активує підписку після успішної оплати на Whop.
    """
    now = datetime.datetime.now(timezone.utc)
    next_month = now + timedelta(days=30) # Додаємо 30 днів доступу
    
    data = {
        "plan": plan_name,
        "subscription_expires_at": next_month.isoformat(),
        "whop_membership_id": membership_id
    }
    return supabase.table("businesses").update(data).eq("id", biz_id).execute()

# ==========================================
# ФУНКЦІЇ ДЛЯ КОРИСТУВАЧІВ ТА ПЕРСОНАЛУ
# ==========================================

def get_user_context(user_id: int):
    """
    Повертає роль юзера та дані його бізнесу.
    Спочатку шукаємо чи він Власник, потім чи він Персонал (в таблиці staff).
    """
    # 1. Перевіряємо чи він Owner
    biz = supabase.table("businesses").select("*").eq("owner_id", user_id).execute()
    if biz.data:
        return {"role": "owner", "biz": biz.data[0]}

    # 2. Якщо не власник, шукаємо в таблиці персоналу (staff)
    staff = supabase.table("staff").select("*").eq("user_id", user_id).execute()
    if staff.data:
        s = staff.data[0]
        # Отримуємо дані бізнесу, до якого він прив'язаний
        biz_info = supabase.table("businesses").select("*").eq("id", s["business_id"]).execute()
        if biz_info.data:
            return {"role": s["role"], "biz": biz_info.data[0]}

    return None

def create_staff(user_id: int, name: str, biz_id: str, role: str = 'courier'):
    """Записуємо кур'єра або менеджера в базу (таблиця staff)"""
    data = {
        "user_id": user_id,
        "name": name,
        "business_id": biz_id,
        "role": role
    }
    return supabase.table("staff").insert(data).execute()

def get_courier(user_id: int):
    """Отримуємо інфо про конкретного працівника"""
    res = supabase.table("staff").select("*").eq("user_id", user_id).execute()
    return res.data[0] if res.data else None

# ==========================================
# ФУНКЦІЇ ДЛЯ ЗАМОВЛЕНЬ
# ==========================================

def create_new_order(order_data: dict):
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
        "est_time": int(order_data.get('est_time') or 30), # <--- 🔴 ДОДАНО: ЧАС ДОСТАВКИ
        "status": "pending" # Статус: очікує прийняття кур'єром
    }
    
    # Записуємо і повертаємо результат (щоб отримати ID нового замовлення)
    result = supabase.table("orders").insert(data).execute()
    return result.data[0] if result.data else None

def update_order_status(order_id: str, new_status: str):
    """Оновлює статус замовлення в базі та фіксує час завершення"""
    data = {"status": new_status}
    
    # Якщо статус "completed", записуємо поточний час
    if new_status == "completed":
        data["completed_at"] = datetime.datetime.now(timezone.utc).isoformat()
        
    supabase.table("orders").update(data).eq("id", order_id).execute()

def get_daily_report(biz_id: str):
    """Генерує дані для звіту адміна за поточний день"""
    today = datetime.datetime.utcnow().date()
    start_of_day = datetime.datetime(today.year, today.month, today.day).isoformat()
    
    # Отримуємо всі закриті замовлення за сьогодні
    res_orders = supabase.table("orders").select("*").eq("business_id", biz_id).eq("status", "completed").gte("created_at", start_of_day).limit(5000).execute()
    orders = res_orders.data if res_orders.data else []
    
    # Отримуємо імена персоналу
    res_staff = supabase.table("staff").select("user_id, name").eq("business_id", biz_id).execute()
    staff_dict = {str(s['user_id']): s['name'] for s in (res_staff.data if res_staff.data else [])}
    
    report = {}
    total_cash = 0
    total_term = 0
    
    for o in orders:
        c_id = str(o['courier_id'])
        if c_id not in report:
            report[c_id] = {'count': 0, 'cash': 0.0, 'term': 0.0, 'name': staff_dict.get(c_id, "Невідомий")}
        
        report[c_id]['count'] += 1
        amt = float(o['amount'])
        if o['pay_type'] == 'cash':
            report[c_id]['cash'] += amt
            total_cash += amt
        else: # term або online
            report[c_id]['term'] += amt
            total_term += amt
            
    return report, total_cash, total_term
