import datetime
import logging

import database as db
from bot_setup import bot

logger = logging.getLogger(__name__)


# ==========================================
# ⏱ ФОНОВИЙ ПРОЦЕС: ТАЙМЕР ЗАПІЗНЕНЬ (5 ХВ)
# ==========================================
async def check_late_orders():
    """Фонова задача, яка перевіряє всі активні замовлення і повідомляє про запізнення"""
    try:
        # Шукаємо незавершені замовлення
        res = db.supabase.table("orders").select("*").eq("status", "pending").execute()
        if not res.data: return
        
        now = datetime.datetime.now(datetime.timezone.utc)
        
        for order in res.data:
            # Якщо вже сповіщали про це замовлення - пропускаємо
            if order.get("is_late_notified"): continue
            
            created_at = datetime.datetime.fromisoformat(order["created_at"].replace("Z", "+00:00"))
            est_time = order.get("est_time", 30)
            
            # Дедлайн = час створення + заявлений час + 5 хв "буфера"
            deadline = created_at + datetime.timedelta(minutes=est_time + 5)
            
            if now > deadline:
                # 1. Помічаємо в базі, щоб не спамити
                db.supabase.table("orders").update({"is_late_notified": True}).eq("id", order["id"]).execute()
                
                biz_id = order["business_id"]
                short_id = str(order["id"])[:6].upper()
                
                # 2. Визначаємо, хто везе
                c_name = "Не призначено (На карті)"
                if order.get("courier_id"):
                    c_res = db.supabase.table("staff").select("name").eq("user_id", order["courier_id"]).execute()
                    if c_res.data:
                        c_name = c_res.data[0]["name"]
                        
                late_mins = int((now - created_at).total_seconds() / 60) - est_time
                
                # 3. Формуємо тривожне повідомлення
                msg = (
                    f"🚨 **ЗАПІЗНЕННЯ ЗАМОВЛЕННЯ!**\n\n"
                    f"📦 Замовлення `#{short_id}`\n"
                    f"📍 Адреса: {order.get('address')}\n"
                    f"📞 Тел: {order.get('client_phone')}\n"
                    f"🛵 Кур'єр: {c_name}\n\n"
                    f"⚠️ Запізнення вже на **{late_mins} хв**!"
                )
                
                # 4. Відправляємо всім менеджерам закладу
                managers_res = db.supabase.table("staff").select("user_id").eq("business_id", biz_id).eq("role", "manager").execute()
                if managers_res.data:
                    for m in managers_res.data:
                        try:
                            await bot.send_message(chat_id=m["user_id"], text=msg, parse_mode="Markdown")
                        except Exception as e:
                            logger.error(f"Помилка відправки сповіщення про запізнення менеджеру {m['user_id']}: {e}")
    except Exception as e:
        logger.error(f"Помилка перевірки запізнень: {e}")
