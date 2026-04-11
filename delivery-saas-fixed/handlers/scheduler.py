import datetime
import logging

import database as db
from bot_setup import bot

logger = logging.getLogger(__name__)

# ==========================================
# ФОНОВИЙ ПРОЦЕС: ТАЙМЕР ЗАПІЗНЕНЬ
# ==========================================

async def check_late_orders():
    """Фонова задача, яка перевіряє активні замовлення і повідомляє про запізнення"""
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        since = (now - datetime.timedelta(hours=24)).isoformat()

        res = await db._run(
            lambda: db.supabase.table("orders")
                .select("*")
                .in_("status", ["pending", "delivering"])
                .gte("created_at", since)
                .execute()
        )
        if not res.data:
            return

        for order in res.data:
            if order.get("is_late_notified"):
                continue

            created_at = datetime.datetime.fromisoformat(order["created_at"].replace("Z", "+00:00"))
            est_time   = order.get("est_time", 30)
            deadline   = created_at + datetime.timedelta(minutes=est_time + 5)

            if now <= deadline:
                continue

            # ✅ ВИПРАВЛЕНО: default-аргументи в lambda щоб уникнути closure bug
            oid = order["id"]
            await db._run(
                lambda oid=oid: db.supabase.table("orders")
                    .update({"is_late_notified": True})
                    .eq("id", oid)
                    .execute()
            )

            biz_id   = order["business_id"]
            short_id = str(oid)[:6].upper()

            # Ім'я кур'єра
            c_name = "Не призначено"
            courier_id = order.get("courier_id")
            if courier_id:
                # ✅ ВИПРАВЛЕНО: default-аргумент щоб уникнути closure bug
                c_res = await db._run(
                    lambda cid=courier_id: db.supabase.table("staff")
                        .select("name")
                        .eq("user_id", cid)
                        .execute()
                )
                if c_res.data:
                    c_name = c_res.data[0]["name"]

            late_mins = int((now - created_at).total_seconds() / 60) - est_time

            msg = (
                f"🚨 **ЗАПІЗНЕННЯ ЗАМОВЛЕННЯ!**\n\n"
                f"📦 Замовлення `#{short_id}`\n"
                f"📍 Адреса: {order.get('address', '—')}\n"
                f"📞 Тел: {order.get('client_phone', '—')}\n"
                f"🛵 Кур'єр: {c_name}\n\n"
                f"⚠️ Запізнення вже на **{late_mins} хв**!"
            )

            # Знаходимо менеджерів
            managers_res = await db._run(
                lambda bid=biz_id: db.supabase.table("staff")
                    .select("user_id")
                    .eq("business_id", bid)
                    .eq("role", "manager")
                    .execute()
            )

            notify_ids = [int(m["user_id"]) for m in managers_res.data] if managers_res.data else []

            # ✅ ВИПРАВЛЕНО: якщо немає менеджерів — нотифікуємо власника
            if not notify_ids:
                biz = await db.get_business_by_id(biz_id)
                if biz and biz.get("owner_id"):
                    notify_ids = [int(biz["owner_id"])]

            for uid in notify_ids:
                try:
                    await bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Помилка відправки сповіщення про запізнення {uid}: {e}")

    except Exception as e:
        logger.error(f"Помилка перевірки запізнень: {e}")
