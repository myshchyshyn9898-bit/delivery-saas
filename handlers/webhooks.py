import hmac
import logging
import os
import urllib.parse

from aiohttp import web
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import types

import database as db
from bot_setup import bot
from config import WHOP_WEBHOOK_SECRET, POSTER_WEBHOOK_SECRET, SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)


# ==========================================
# WEBHOOKS (WHOP + POSTER)
# ==========================================
async def whop_webhook_handler(request):
    try:
        if WHOP_WEBHOOK_SECRET:
            provided = (
                request.headers.get("X-Whop-Signature")
                if "X-Whop-Signature" in request.headers
                else request.headers.get("Authorization", "")
            )
            if not hmac.compare_digest(provided or "", WHOP_WEBHOOK_SECRET):
                return web.Response(status=403, text="Forbidden")
        else:
            logger.warning("WHOP_WEBHOOK_SECRET is not set — accepting request without verification")

        data = await request.json()
        if data.get("event_type") == "membership.went_active":
            membership_data = data.get("data", {})
            biz_id, tg_user_id = membership_data.get("custom_fields", {}).get("biz_id"), membership_data.get("custom_fields", {}).get("tg_user_id")
            if biz_id:
                db.activate_whop_subscription(biz_id, "pro", membership_data.get("id"))
                if tg_user_id:
                    try: await bot.send_message(chat_id=int(tg_user_id), text="🎉 **Вітаємо! Оплата успішна!**\nТариф **PRO** активовано.", parse_mode="Markdown")
                    except Exception as e:
                        logger.error(f"Помилка відправки повідомлення Whop: {e}")
        return web.Response(text="OK")
    except Exception as e:
        logger.error(f"Помилка обробки Whop webhook: {e}")
        return web.Response(status=500, text="Error")


async def poster_webhook_handler(request):
    try:
        if POSTER_WEBHOOK_SECRET:
            provided = (
                request.query.get("secret")
                if "secret" in request.query
                else request.headers.get("X-Poster-Secret", "")
            )
            if not hmac.compare_digest(provided or "", POSTER_WEBHOOK_SECRET):
                return web.Response(status=403, text="Forbidden")
        else:
            logger.warning("POSTER_WEBHOOK_SECRET is not set — accepting request without verification")

        biz_id = request.query.get("biz_id")
        if not biz_id: return web.Response(status=400, text="Missing biz_id")
        data = await request.json()
        if data.get("object") == "incoming_order" and data.get("action") == "added":
            order_data = data.get("data", {})
            client_name, phone, address, amount, comment = order_data.get("client_name", "Клієнт"), order_data.get("phone", ""), order_data.get("address", ""), str(float(order_data.get("total_sum", 0)) / 100), order_data.get("comment", "")
            managers_res = db.supabase.table("staff").select("user_id").eq("business_id", biz_id).eq("role", "manager").execute()
            admin_text = f"🔥 <b>НОВЕ ЗАМОВЛЕННЯ З POSTER!</b>\n\n👤 <b>Клієнт:</b> {client_name}\n📞 <b>Телефон:</b> {phone}\n📍 <b>Адреса:</b> {address}\n💰 <b>Сума:</b> {amount}\n"
            if comment: admin_text += f"\n💬 <b>Коментар:</b> <i>{comment}</i>"
            form_url = f"https://myshchyshyn9898-bit.github.io/delivery-saas/form.html?biz_id={biz_id}&address={urllib.parse.quote(address)}&phone={urllib.parse.quote(phone)}&amount={urllib.parse.quote(amount)}&name={urllib.parse.quote(client_name)}&comment={urllib.parse.quote(comment)}"
            builder = InlineKeyboardBuilder()
            builder.button(text="🛵 Призначити кур'єра", web_app=types.WebAppInfo(url=form_url))
            if managers_res.data:
                for manager in managers_res.data:
                    try: await bot.send_message(chat_id=manager['user_id'], text=admin_text, reply_markup=builder.as_markup(), parse_mode="HTML")
                    except Exception as e:
                        logger.error(f"Помилка відправки Poster менеджеру: {e}")
        return web.Response(text="OK")
    except Exception as e:
        logger.error(f"Помилка обробки Poster webhook: {e}")
        return web.Response(status=500, text="Error")


async def config_handler(request):
    cors_headers = {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET',
    }
    return web.json_response(
        {'supabase_url': SUPABASE_URL or '', 'supabase_key': SUPABASE_KEY or ''},
        headers=cors_headers,
    )


async def start_webhook_server():
    app = web.Application()
    app.router.add_get('/config', config_handler)
    app.router.add_post('/webhook/whop', whop_webhook_handler)
    app.router.add_post('/webhook/poster', poster_webhook_handler) 
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"🌐 Webhook сервер запущено на порту {port}")
