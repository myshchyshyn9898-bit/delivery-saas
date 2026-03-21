from supabase import create_client, Client
import os

# Дані беремо з налаштувань (пізніше винесемо в змінні середовища)
SUPABASE_URL = "https://kvanzkcwpwmfexsmldvx.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imt2YW56a2N3cHdtZmV4c21sZHZ4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQwMzgyMzksImV4cCI6MjA4OTYxNDIzOX0.ZHXB9-PwJhH07LzPGpxK0HD-BkLGlf5w2L4WbgrX4JA" # Твій повний ключ

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- ФУНКЦІЇ ДЛЯ БІЗНЕСУ ---

def get_business_by_owner(owner_id: int):
    res = supabase.table("businesses").select("*").eq("owner_id", owner_id).execute()
    return res.data[0] if res.data else None

def register_new_business(owner_id: int, biz_data: dict):
    # Пакуємо всі дані з Web App у формат для бази
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
        "plan": biz_data.get("plan", "pro"),
        "is_active": True
    }
    return supabase.table("businesses").insert(data).execute()

# --- ФУНКЦІЇ ДЛЯ КУР'ЄРІВ ---

def get_courier(tg_id: int):
    res = supabase.table("couriers").select("*").eq("tg_id", tg_id).execute()
    return res.data[0] if res.data else None

def add_courier(tg_id: int, name: str, business_id: str):
    data = {
        "tg_id": tg_id,
        "name": name,
        "business_id": business_id
    }
    return supabase.table("couriers").insert(data).execute()
# database.py (додай ці функції до існуючих)

def get_user_context(tg_id: int):
    """
    Повертає роль юзера та дані його бізнесу.
    Спочатку шукаємо чи він Власник, потім чи він Персонал.
    """
    # 1. Перевіряємо чи він Owner
    biz = supabase.table("businesses").select("*").eq("owner_id", tg_id).execute()
    if biz.data:
        return {"role": "owner", "biz": biz.data[0]}

    # 2. Якщо не власник, шукаємо в таблиці персоналу (couriers)
    staff = supabase.table("couriers").select("*, businesses(*)").eq("tg_id", tg_id).execute()
    if staff.data:
        role = staff.data[0]['role'] # 'manager' або 'courier'
        return {"role": role, "biz": staff.data[0]['businesses']}

    return None
# database.py (добавь эти функции)

def get_business_by_id(biz_id: str):
    """Получаем инфо о бизнесе по его UUID"""
    res = supabase.table("businesses").select("*").eq("id", biz_id).execute()
    return res.data[0] if res.data else None

def create_staff(tg_id: int, name: str, biz_id: str, role: str = 'courier'):
    """Записываем курьера или менеджера в базу"""
    data = {
        "tg_id": tg_id,
        "name": name,
        "business_id": biz_id,
        "role": role
    }
    return supabase.table("couriers").insert(data).execute()
# database.py

def get_business_by_id(biz_id: str):
    """Отримуємо інфо про бізнес за його UUID"""
    res = supabase.table("businesses").select("*").eq("id", biz_id).execute()
    return res.data[0] if res.data else None

def create_staff(tg_id: int, name: str, biz_id: str, role: str = 'courier'):
    """Записуємо кур'єра або менеджера в базу"""
    data = {
        "tg_id": tg_id,
        "name": name,
        "business_id": biz_id,
        "role": role
    }
    return supabase.table("couriers").insert(data).execute()
def create_new_order(order_data: dict):
    # Пакуємо дані з форми для бази
    data = {
        "business_id": order_data['biz_id'],
        "courier_id": order_data['courier_id'],
        "client_name": order_data.get('client_name'),
        "client_phone": order_data.get('client_phone'),
        "address": order_data.get('address'),
        "amount": order_data.get('amount'),
        "pay_type": order_data.get('payment'),
        "comment": order_data.get('comment'),
        "status": "pending" # Статус: очікує прийняття кур'єром
    }
    
    # Записуємо і повертаємо результат (щоб отримати ID нового замовлення)
    result = supabase.table("orders").insert(data).execute()
    return result.data[0] if result.data else None
