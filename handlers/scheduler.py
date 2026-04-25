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
                .limit(500)
                .execute()
        )
        if not res.data:
            return

        # ✅ FIX: кешуємо плани по biz_id — один запит на бізнес, а не на кожне замовлення
        _plans_cache: dict = {}

        for order in res.data:
            if order.get("is_late_notified"):
                continue

            # ✅ ВИПРАВЛЕНО bug #3: pending замовлення без кур'єра в dispatcher-режимі
            # не є запізненням — їх ще не призначили. Алерт тільки якщо:
            # - статус delivering (кур'єр вже їде), або
            # - статус pending АЛЕ є courier_id (uber-режим: кур'єр не взяв)
            if order["status"] == "pending" and not order.get("courier_id"):
                continue  # ще не призначено — пропускаємо

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

            # ✅ FIX N+1: план береться з кешу, а не окремим запитом
            if biz_id not in _plans_cache:
                _plans_cache[biz_id] = await db.get_actual_plan(biz_id)
            if _plans_cache[biz_id] == "expired":
                continue

            # Ім'я кур'єра
            c_name = None  # буде підставлено після отримання biz_lang нижче
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

            late_mins = max(1, int((now - created_at).total_seconds() / 60) - est_time)

            # Екрануємо спецсимволи Markdown у даних від користувача
            def _md(s): return str(s).replace('_', r'\_').replace('*', r'\*').replace('`', r'\`').replace('[', r'\[')

            # Беремо мову бізнесу для сповіщення
            biz_lang_res = await db._run(
                lambda bid=biz_id: db.supabase.table("businesses").select("lang").eq("id", bid).execute()
            )
            biz_lang = (biz_lang_res.data[0].get("lang") or "uk") if biz_lang_res.data else "uk"
            from texts import get_text as _tl
            if c_name is None:
                c_name = _tl(biz_lang, 'late_unassigned')
            msg = (
                f"🚨 **{_tl(biz_lang, 'late_header')}**\n\n"
                f"📦 {_tl(biz_lang, 'late_order_lbl')} `#{short_id}`\n"
                f"📍 {_tl(biz_lang, 'late_addr_lbl')}: {_md(order.get('address', '—'))}\n"
                f"📞 {_tl(biz_lang, 'late_phone_lbl')}: {_md(order.get('client_phone', '—'))}\n"
                f"🛵 {_tl(biz_lang, 'late_courier_lbl')}: {_md(c_name)}\n\n"
                f"⚠️ {_tl(biz_lang, 'late_mins_msg', mins=late_mins)}"
            )

            # Знаходимо менеджерів + власника без зайвого запиту
            managers_res = await db._run(
                lambda bid=biz_id: db.supabase.table("staff")
                    .select("user_id")
                    .eq("business_id", bid)
                    .eq("role", "manager")
                    .execute()
            )
            notify_ids = [int(m["user_id"]) for m in managers_res.data] if managers_res.data else []

            if not notify_ids:
                # Власника беремо напряму без get_business_by_id (уникаємо N+1)
                biz_res = await db._run(
                    lambda bid=biz_id: db.supabase.table("businesses")
                        .select("owner_id")
                        .eq("id", bid)
                        .execute()
                )
                if biz_res.data and biz_res.data[0].get("owner_id"):
                    notify_ids = [int(biz_res.data[0]["owner_id"])]

            for uid in notify_ids:
                try:
                    await bot.send_message(chat_id=uid, text=msg, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Помилка відправки сповіщення про запізнення {uid}: {e}")

    except Exception as e:
        logger.error(f"Помилка перевірки запізнень: {e}")
