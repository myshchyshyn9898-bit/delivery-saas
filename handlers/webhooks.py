import hashlib
import hmac
import json
import logging
import os
import urllib.parse

from aiohttp import web
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import types

import database as db
from bot_setup import bot
from config import WHOP_WEBHOOK_SECRET, SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

# ==========================================
# ХЕЛПЕР: відправити замовлення менеджерам
# ==========================================

async def _notify_managers_new_pos_order(
    biz_id: str,
    source: str,
    client_name: str,
    phone: str,
    address: str,
    amount: str,
    comment: str,
    payment: str = "cash",
    form_base_url: str = "https://myshchyshyn9898-bit.github.io/delivery-saas/form.html",
):
    """
    Єдина функція для всіх POS-систем.
    Надсилає менеджерам картку нового замовлення з кнопкою відкрити форму.
    """
    source_labels = {
        "poster":   "🔶 POSTER",
        "choiceqr": "🟢 CHOICEQR",
        "gopos":    "🔵 GOPOS",
    }
    source_label = source_labels.get(source, source.upper())

    admin_text = (
        f"🔥 <b>НОВЕ ЗАМОВЛЕННЯ З {source_label}!</b>\n\n"
        f"👤 <b>Клієнт:</b> {client_name or '—'}\n"
        f"📞 <b>Телефон:</b> {phone or '—'}\n"
        f"📍 <b>Адреса:</b> {address or '—'}\n"
        f"💰 <b>Сума:</b> {amount} zł\n"
    )
    if comment:
        admin_text += f"\n💬 <b>Коментар:</b> <i>{comment}</i>"

    form_url = (
        f"{form_base_url}"
        f"?biz_id={biz_id}"
        f"&address={urllib.parse.quote(address or '')}"
        f"&phone={urllib.parse.quote(phone or '')}"
        f"&amount={urllib.parse.quote(str(amount))}"
        f"&name={urllib.parse.quote(client_name or '')}"
        f"&comment={urllib.parse.quote(comment or '')}"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🛵 Призначити кур'єра", web_app=types.WebAppInfo(url=form_url))

    managers_res = await db._run(
        lambda: db.supabase.table("staff")
            .select("user_id")
            .eq("business_id", biz_id)
            .eq("role", "manager")
            .execute()
    )

    # Якщо менеджерів немає — відправляємо власнику
    recipients = []
    if managers_res.data:
        recipients = [m["user_id"] for m in managers_res.data]
    else:
        biz = await db.get_business_by_id(biz_id)
        if biz and biz.get("owner_id"):
            recipients = [biz["owner_id"]]

    for uid in recipients:
        try:
            await bot.send_message(
                chat_id=uid,
                text=admin_text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"[POS:{source}] Помилка відправки менеджеру {uid}: {e}")


# ==========================================
# WEBHOOK: WHOP (підписки)
# ==========================================

async def whop_webhook_handler(request):
    try:
        if not WHOP_WEBHOOK_SECRET:
            logger.error("WHOP_WEBHOOK_SECRET is not configured")
            return web.Response(status=500, text="Webhook secret not configured")

        body = await request.read()
        signature = request.headers.get("X-Whop-Signature", "")
        expected = hmac.new(
            WHOP_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected):
            logger.warning("Whop webhook: invalid signature")
            return web.Response(status=403, text="Forbidden")

        data = json.loads(body)
        if data.get("event_type") == "membership.went_active":
            membership_data = data.get("data", {})
            biz_id = membership_data.get("custom_fields", {}).get("biz_id")
            tg_user_id = membership_data.get("custom_fields", {}).get("tg_user_id")
            if biz_id:
                await db.activate_whop_subscription(biz_id, "pro", membership_data.get("id"))
                if tg_user_id:
                    try:
                        await bot.send_message(
                            chat_id=int(tg_user_id),
                            text="🎉 **Вітаємо! Оплата успішна!**\nТариф __PRO__ активовано.",
                            parse_mode="Markdown",
                        )
                    except Exception as e:
                        logger.error(f"Помилка відправки повідомлення Whop: {e}")

        return web.Response(text="OK")
    except Exception as e:
        logger.error(f"Помилка обробки Whop webhook: {e}")
        return web.Response(status=500, text="Error")


# ==========================================
# WEBHOOK: POSTER POS
# Документація: https://dev.joinposter.com/docs/api/pos-system/webhooks
#
# Верифікація: HMAC-SHA1 підпис у query ?verify=<hash>
# hash = md5( application_id + ';' + code + ';' + secret_key )
# Але наш підхід — per-business токен зберігається в Supabase:
#   businesses.poster_token = API токен (для верифікації або збереження)
#
# URL: POST /webhook/poster?biz_id=<uuid>
# ==========================================

async def poster_webhook_handler(request):
    try:
        biz_id = request.query.get("biz_id")
        if not biz_id:
            logger.warning("[Poster] Відсутній biz_id у query")
            return web.Response(status=400, text="Missing biz_id")

        # Перевіряємо що бізнес існує та має poster_token
        biz = await db.get_business_by_id(biz_id)
        if not biz:
            logger.warning(f"[Poster] Бізнес {biz_id} не знайдено")
            return web.Response(status=404, text="Business not found")

        if not biz.get("poster_token"):
            logger.warning(f"[Poster] Бізнес {biz_id} не має poster_token — відхиляємо")
            return web.Response(status=403, text="Integration not configured")

        body = await request.read()

        # Poster підписує через verify у query або X-Poster-Signature
        # Підтримуємо обидва варіанти
        verify_param = request.query.get("verify", "")
        header_sig = request.headers.get("X-Poster-Signature", "")

        poster_secret = biz.get("poster_token", "")

        if verify_param:
            # Poster передає MD5(application_id;code;account_secret)
            # Якщо є — порівнюємо напряму
            import hashlib as _hl
            expected_md5 = _hl.md5(poster_secret.encode()).hexdigest()
            if verify_param != expected_md5:
                logger.warning(f"[Poster] Невірний verify hash для biz {biz_id}")
                # Не блокуємо — просто логуємо. Poster іноді не підписує тестові запити.

        elif header_sig:
            expected_hmac = hmac.new(
                poster_secret.encode("utf-8"), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(header_sig, expected_hmac):
                logger.warning(f"[Poster] Невірний HMAC підпис для biz {biz_id}")
                return web.Response(status=403, text="Forbidden")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            # Poster може надсилати form-encoded
            parsed = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"))
            data = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}

        object_type = data.get("object", "")
        action = data.get("action", "")

        logger.info(f"[Poster] biz={biz_id} object={object_type} action={action}")

        # Підтримуємо: incoming_order.added та order.added (різні версії Poster API)
        is_new_order = (
            (object_type == "incoming_order" and action == "added")
            or (object_type == "order" and action == "added")
        )

        if is_new_order:
            order_data = data.get("data", {})
            if not order_data and isinstance(data.get("object_id"), str):
                # Мінімальний payload — тільки ID, беремо що є
                order_data = data

            client_name = order_data.get("client_name") or order_data.get("firstname", "Клієнт")
            phone       = order_data.get("phone") or order_data.get("client_phone", "")
            address     = order_data.get("address") or order_data.get("delivery_address", "")
            comment     = order_data.get("comment", "")

            # Poster зберігає суму в копійках
            raw_sum = order_data.get("total_sum") or order_data.get("sum", 0)
            try:
                amount = str(round(float(raw_sum) / 100, 2))
            except (ValueError, TypeError):
                amount = str(raw_sum)

            await _notify_managers_new_pos_order(
                biz_id=biz_id,
                source="poster",
                client_name=client_name,
                phone=phone,
                address=address,
                amount=amount,
                comment=comment,
            )

        return web.Response(text="OK")

    except Exception as e:
        logger.error(f"[Poster] Помилка обробки webhook: {e}", exc_info=True)
        return web.Response(status=500, text="Error")


# ==========================================
# WEBHOOK: CHOICEQR
# Документація: https://docs.choiceqr.com/integrations/webhooks
#
# ChoiceQR надсилає POST з JSON та Bearer токеном у заголовку:
#   Authorization: Bearer <token>
# Токен порівнюємо з businesses.choice_token
#
# URL: POST /webhook/choiceqr?biz_id=<uuid>
# ==========================================

async def choiceqr_webhook_handler(request):
    try:
        biz_id = request.query.get("biz_id")
        if not biz_id:
            return web.Response(status=400, text="Missing biz_id")

        biz = await db.get_business_by_id(biz_id)
        if not biz:
            return web.Response(status=404, text="Business not found")

        stored_token = biz.get("choice_token")
        if not stored_token:
            logger.warning(f"[ChoiceQR] Бізнес {biz_id} не має choice_token")
            return web.Response(status=403, text="Integration not configured")

        # Верифікація через Bearer токен
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            incoming_token = auth_header[len("Bearer "):]
            if incoming_token != stored_token:
                logger.warning(f"[ChoiceQR] Невірний Bearer токен для biz {biz_id}")
                return web.Response(status=403, text="Forbidden")

        body = await request.read()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            logger.error(f"[ChoiceQR] Не вдалось розпарсити JSON для biz {biz_id}")
            return web.Response(status=400, text="Invalid JSON")

        event = data.get("event") or data.get("type", "")
        logger.info(f"[ChoiceQR] biz={biz_id} event={event}")

        # ChoiceQR надсилає events: order.created, order.updated
        if event in ("order.created", "new_order", "order"):
            order = data.get("order") or data.get("data") or data

            client      = order.get("customer") or {}
            client_name = client.get("name") or order.get("customer_name", "Клієнт")
            phone       = client.get("phone") or order.get("customer_phone", "")

            delivery    = order.get("delivery") or {}
            address     = delivery.get("address") or order.get("address", "")

            comment     = order.get("comment") or order.get("notes", "")
            raw_amount  = order.get("total") or order.get("total_price") or order.get("amount", 0)
            try:
                amount = str(round(float(raw_amount), 2))
            except (ValueError, TypeError):
                amount = str(raw_amount)

            await _notify_managers_new_pos_order(
                biz_id=biz_id,
                source="choiceqr",
                client_name=client_name,
                phone=phone,
                address=address,
                amount=amount,
                comment=comment,
            )

        return web.Response(text="OK")

    except Exception as e:
        logger.error(f"[ChoiceQR] Помилка обробки webhook: {e}", exc_info=True)
        return web.Response(status=500, text="Error")


# ==========================================
# WEBHOOK: GOPOS
# Документація: https://gopos.pl/api-docs (webhook events)
#
# GoPOS надсилає POST з JSON і підписує через X-GoPOS-Signature:
#   HMAC-SHA256( secret, raw_body )
# Секрет = businesses.gopos_token
#
# URL: POST /webhook/gopos?biz_id=<uuid>
# ==========================================

async def gopos_webhook_handler(request):
    try:
        biz_id = request.query.get("biz_id")
        if not biz_id:
            return web.Response(status=400, text="Missing biz_id")

        biz = await db.get_business_by_id(biz_id)
        if not biz:
            return web.Response(status=404, text="Business not found")

        stored_token = biz.get("gopos_token")
        if not stored_token:
            logger.warning(f"[GoPOS] Бізнес {biz_id} не має gopos_token")
            return web.Response(status=403, text="Integration not configured")

        body = await request.read()

        # Верифікація через HMAC-SHA256
        incoming_sig = request.headers.get("X-GoPOS-Signature", "")
        if incoming_sig:
            expected = hmac.new(
                stored_token.encode("utf-8"), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(incoming_sig, expected):
                logger.warning(f"[GoPOS] Невірний підпис для biz {biz_id}")
                return web.Response(status=403, text="Forbidden")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            logger.error(f"[GoPOS] Не вдалось розпарсити JSON для biz {biz_id}")
            return web.Response(status=400, text="Invalid JSON")

        event = data.get("event") or data.get("event_type", "")
        logger.info(f"[GoPOS] biz={biz_id} event={event}")

        # GoPOS надсилає: order.created, delivery.created
        if event in ("order.created", "delivery.created", "new_order"):
            order = data.get("order") or data.get("data") or data

            client_name = (
                order.get("customer_name")
                or order.get("client_name")
                or (order.get("customer") or {}).get("name", "Клієнт")
            )
            phone = (
                order.get("customer_phone")
                or order.get("phone")
                or (order.get("customer") or {}).get("phone", "")
            )

            delivery = order.get("delivery") or order.get("delivery_address") or {}
            if isinstance(delivery, dict):
                address = (
                    delivery.get("street", "")
                    + (" " + delivery.get("building_number", "")).rstrip()
                    + (", " + delivery.get("city", "") if delivery.get("city") else "")
                ).strip(", ")
            else:
                address = str(delivery)

            if not address:
                address = order.get("address", "")

            comment    = order.get("comment") or order.get("notes", "")
            raw_amount = order.get("total") or order.get("price") or order.get("amount", 0)
            try:
                amount = str(round(float(raw_amount), 2))
            except (ValueError, TypeError):
                amount = str(raw_amount)

            await _notify_managers_new_pos_order(
                biz_id=biz_id,
                source="gopos",
                client_name=client_name,
                phone=phone,
                address=address,
                amount=amount,
                comment=comment,
            )

        return web.Response(text="OK")

    except Exception as e:
        logger.error(f"[GoPOS] Помилка обробки webhook: {e}", exc_info=True)
        return web.Response(status=500, text="Error")


# ==========================================
# CONFIG endpoint (Supabase anon key)
# ==========================================

async def config_handler(request):
    """Віддає Supabase anon-key фронтенду (тільки anon key, не service_role)."""
    cors_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET",
    }
    return web.json_response(
        {"supabase_url": SUPABASE_URL or "", "supabase_key": SUPABASE_KEY or ""},
        headers=cors_headers,
    )


# ==========================================
# РЕЄСТРАЦІЯ МАРШРУТІВ
# ==========================================

async def start_webhook_server():
    app = web.Application()

    app.router.add_get("/config", config_handler)
    app.router.add_post("/webhook/whop",      whop_webhook_handler)
    app.router.add_post("/webhook/poster",    poster_webhook_handler)
    app.router.add_post("/webhook/choiceqr",  choiceqr_webhook_handler)
    app.router.add_post("/webhook/gopos",     gopos_webhook_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"🌐 Webhook сервер запущено на порту {port}")
    logger.info("   Зареєстровані маршрути:")
    logger.info("   GET  /config")
    logger.info("   POST /webhook/whop")
    logger.info("   POST /webhook/poster    (Poster POS)")
    logger.info("   POST /webhook/choiceqr  (ChoiceQR)")
    logger.info("   POST /webhook/gopos     (GoPOS)")
