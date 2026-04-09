"""
handlers/webhooks.py — POS інтеграції та службові endpoints.

Підтримувані POS-системи:
  • Poster POS  — verify query-param (MD5) або X-Poster-Signature (HMAC-SHA256)
  • ChoiceQR    — Authorization: Bearer <token>
  • GoPOS       — X-GoPOS-Signature (HMAC-SHA256)
  • Syrve/iiko  — X-Syrve-Signature (HMAC-SHA256)

Whop — webhook підписок (HMAC-SHA256 через X-Whop-Signature).

Всі endpoint-и повертають HTTP 200 "OK" одразу після верифікації,
щоб POS-системи не робили повторних спроб (retry). Фактична
обробка (відправка в Telegram) виконується після відповіді через
asyncio.ensure_future.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import urllib.parse

from aiohttp import web
from aiogram import types
from aiogram.utils.keyboard import InlineKeyboardBuilder

import database as db
from bot_setup import bot
from config import WHOP_WEBHOOK_SECRET, SUPABASE_URL, SUPABASE_KEY, POSTER_WEBHOOK_SECRET
from keyboards import generate_token

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Допоміжні функції
# ---------------------------------------------------------------------------

def _hmac_sha256(secret: str, body: bytes) -> str:
    """Обчислює HMAC-SHA256 підпис, повертає hex-рядок."""
    return hmac.HMAC(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()


def _safe_hmac_equal(a: str, b: str) -> bool:
    """Порівнює два рядки без timing-атаки."""
    return hmac.compare_digest(a.lower(), b.lower())


def _parse_amount(raw, divisor: int = 1) -> str:
    """
    Перетворює raw-суму в рядок із двома знаками після коми.
    divisor=100 для систем, що зберігають суму в копійках.
    """
    try:
        return str(round(float(raw) / divisor, 2))
    except (ValueError, TypeError, ZeroDivisionError):
        return str(raw or 0)


def _build_gopos_address(delivery) -> str:
    """Збирає адресу з dict-структури GoPOS."""
    if isinstance(delivery, dict):
        street = delivery.get("street", "")
        house  = delivery.get("building_number", "")
        city   = delivery.get("city", "")
        address = f"{street} {house}".strip()
        if city:
            address = f"{address}, {city}" if address else city
        return address
    return str(delivery or "")


# ---------------------------------------------------------------------------
# Єдиний хелпер: відправити повідомлення менеджерам/власнику
# ---------------------------------------------------------------------------

async def _notify_managers_new_pos_order(
    biz_id: str,
    source: str,
    client_name: str,
    phone: str,
    address: str,
    amount: str,
    comment: str,
    payment: str = "cash",
):
    """
    Надсилає менеджерам (або власнику) картку нового POS-замовлення
    з кнопкою WebApp для призначення кур'єра.
    """
    source_labels = {
        "poster":   "🔶 POSTER",
        "choiceqr": "🟢 CHOICEQR",
        "gopos":    "🔵 GOPOS",
        "syrve":    "🟣 SYRVE",
    }
    source_label = source_labels.get(source, f"📦 {source.upper()}")

    # Отримуємо бізнес для валюти та BASE_URL форми
    biz = await db.get_business_by_id(biz_id)
    currency = (biz or {}).get("currency", "zł")

    base_url = os.environ.get(
        "BASE_URL",
        "https://myshchyshyn9898-bit.github.io/delivery-saas",
    ).rstrip("/")
    form_base_url = f"{base_url}/form.html"

    admin_text = (
        f"🔥 <b>НОВЕ ЗАМОВЛЕННЯ З {source_label}!</b>\n\n"
        f"👤 <b>Клієнт:</b> {client_name or '—'}\n"
        f"📞 <b>Телефон:</b> {phone or '—'}\n"
        f"📍 <b>Адреса:</b> {address or '—'}\n"
        f"💰 <b>Сума:</b> {amount} {currency}\n"
    )
    if comment:
        admin_text += f"\n💬 <b>Коментар:</b> <i>{comment}</i>"

    markup_base_url = f"{form_base_url}?biz_id={urllib.parse.quote(biz_id)}" \
        f"&address={urllib.parse.quote(address or '')}" \
        f"&phone={urllib.parse.quote(phone or '')}" \
        f"&amount={urllib.parse.quote(str(amount))}" \
        f"&name={urllib.parse.quote(client_name or '')}" \
        f"&comment={urllib.parse.quote(comment or '')}" \
        f"&payment={urllib.parse.quote(payment)}"

    managers_res = await db._run(
        lambda: db.supabase.table("staff")
            .select("user_id")
            .eq("business_id", biz_id)
            .eq("role", "manager")
            .execute()
    )

    recipients = []
    if managers_res.data:
        recipients = [int(m["user_id"]) for m in managers_res.data]
    elif biz and biz.get("owner_id"):
        recipients = [int(biz["owner_id"])]

    if not recipients:
        logger.warning(f"[POS:{source}] biz={biz_id} — нікому відправляти замовлення!")
        return

    for uid in recipients:
        try:
            # Генеруємо персональний JWT для кожного менеджера —
            # role="authenticated" + sub=uid, сумісний з RLS Supabase
            personal_token = generate_token(biz_id=biz_id, user_id=uid)
            form_url = f"{markup_base_url}&token={urllib.parse.quote(personal_token)}"

            builder = InlineKeyboardBuilder()
            builder.button(
                text="🛵 Призначити кур'єра",
                web_app=types.WebAppInfo(url=form_url),
            )
            await bot.send_message(
                chat_id=uid,
                text=admin_text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML",
            )
            logger.info(f"[POS:{source}] Замовлення надіслано менеджеру {uid} (biz={biz_id})")
        except Exception as exc:
            logger.error(f"[POS:{source}] Помилка відправки менеджеру {uid}: {exc}")


# ---------------------------------------------------------------------------
# WEBHOOK: WHOP (підписки)
# Верифікація: X-Whop-Signature = HMAC-SHA256(WHOP_WEBHOOK_SECRET, body)
# ---------------------------------------------------------------------------

async def whop_webhook_handler(request: web.Request) -> web.Response:
    """POST /webhook/whop"""
    try:
        if not WHOP_WEBHOOK_SECRET:
            logger.error("[Whop] WHOP_WEBHOOK_SECRET не налаштовано")
            return web.Response(status=500, text="Webhook secret not configured")

        body = await request.read()

        signature = request.headers.get("X-Whop-Signature", "")
        if not signature:
            logger.warning("[Whop] Відсутній заголовок X-Whop-Signature")
            return web.Response(status=403, text="Missing signature")

        expected = _hmac_sha256(WHOP_WEBHOOK_SECRET, body)
        if not _safe_hmac_equal(signature, expected):
            logger.warning("[Whop] Невірний підпис")
            return web.Response(status=403, text="Forbidden")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            logger.error("[Whop] Невалідний JSON")
            return web.Response(status=400, text="Invalid JSON")

        event_type = data.get("event_type", "")
        logger.info(f"[Whop] event={event_type}")

        if event_type == "membership.went_active":
            membership_data = data.get("data", {})
            custom_fields   = membership_data.get("custom_fields") or {}
            biz_id          = custom_fields.get("biz_id")
            tg_user_id      = custom_fields.get("tg_user_id")

            if biz_id:
                await db.activate_whop_subscription(
                    biz_id, "pro", membership_data.get("id", "")
                )
                logger.info(f"[Whop] Підписку PRO активовано для biz={biz_id}")

                if tg_user_id:
                    try:
                        await bot.send_message(
                            chat_id=int(tg_user_id),
                            text=(
                                "🎉 <b>Вітаємо! Оплата успішна!</b>\n"
                                "Тариф <b>PRO</b> активовано. Дякуємо за довіру! 🚀"
                            ),
                            parse_mode="HTML",
                        )
                    except Exception as exc:
                        logger.error(f"[Whop] Помилка відправки користувачу {tg_user_id}: {exc}")
            else:
                logger.warning("[Whop] membership.went_active без biz_id у custom_fields")

        return web.Response(text="OK")

    except Exception as exc:
        logger.error(f"[Whop] Невідома помилка: {exc}", exc_info=True)
        return web.Response(status=500, text="Internal Server Error")


# ---------------------------------------------------------------------------
# WEBHOOK: POSTER POS
# Документація: https://dev.joinposter.com/docs/api/pos-system/webhooks
#
# Верифікація (підтримуємо два варіанти):
#   1. ?verify=<md5>  — MD5(account_secret + ';' + timestamp) або MD5(account_secret)
#   2. X-Poster-Signature — HMAC-SHA256(poster_token, body)
#   Якщо жодного немає — пропускаємо (Poster іноді не підписує тестові запити).
# URL: POST /webhook/poster?biz_id=<uuid>
# ---------------------------------------------------------------------------

async def poster_webhook_handler(request: web.Request) -> web.Response:
    """POST /webhook/poster?biz_id=<uuid>"""
    try:
        biz_id = request.query.get("biz_id", "").strip()
        if not biz_id:
            return web.Response(status=400, text="Missing biz_id")

        biz = await db.get_business_by_id(biz_id)
        if not biz:
            return web.Response(status=404, text="Business not found")

        poster_token = (biz.get("poster_token") or "").strip()
        if not poster_token:
            logger.warning(f"[Poster] biz={biz_id} — poster_token не налаштовано")
            return web.Response(status=403, text="Integration not configured")

        body = await request.read()

        verify_param = request.query.get("verify", "").strip()
        header_sig   = request.headers.get("X-Poster-Signature", "").strip()

        if header_sig:
            # Варіант 2: HMAC-SHA256
            expected = _hmac_sha256(poster_token, body)
            if not _safe_hmac_equal(header_sig, expected):
                logger.warning(f"[Poster] biz={biz_id} — невірний X-Poster-Signature")
                return web.Response(status=403, text="Forbidden")

        elif verify_param:
            # Варіант 1: MD5(secret + ';' + timestamp) або MD5(secret)
            timestamp = request.query.get("timestamp", "")
            raw = f"{poster_token};{timestamp}" if timestamp else poster_token
            expected_md5 = hashlib.md5(raw.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(verify_param, expected_md5):
                logger.warning(f"[Poster] biz={biz_id} — невірний verify hash")
                return web.Response(status=403, text="Forbidden")
        else:
            logger.info(f"[Poster] biz={biz_id} — запит без підпису (дозволено для тестів)")

        # Парсинг: JSON або form-urlencoded
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            parsed = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"))
            data = {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}

        object_type = str(data.get("object", ""))
        action      = str(data.get("action", ""))

        logger.info(f"[Poster] biz={biz_id} object={object_type} action={action}")

        is_new_order = (
            (object_type == "incoming_order" and action == "added")
            or (object_type == "order" and action == "added")
        )

        if is_new_order:
            order_data = data.get("data") or {}
            if not order_data:
                order_data = data

            client_name = (
                order_data.get("client_name")
                or order_data.get("firstname")
                or "Клієнт"
            )
            phone   = order_data.get("phone") or order_data.get("client_phone", "")
            address = order_data.get("address") or order_data.get("delivery_address", "")
            comment = order_data.get("comment", "")

            # Poster зберігає суму в копійках (×100)
            raw_sum = order_data.get("total_sum") or order_data.get("sum", 0)
            amount  = _parse_amount(raw_sum, divisor=100)

            asyncio.create_task(
                _notify_managers_new_pos_order(
                    biz_id=biz_id,
                    source="poster",
                    client_name=client_name,
                    phone=phone,
                    address=address,
                    amount=amount,
                    comment=comment,
                )
            )

        return web.Response(text="OK")

    except Exception as exc:
        logger.error(f"[Poster] Невідома помилка: {exc}", exc_info=True)
        return web.Response(status=500, text="Internal Server Error")


# ---------------------------------------------------------------------------
# WEBHOOK: CHOICEQR
# Документація: https://docs.choiceqr.com/integrations/webhooks
#
# Верифікація: Authorization: Bearer <choice_token>
# Якщо заголовку немає або токен невірний — відхиляємо.
# URL: POST /webhook/choiceqr?biz_id=<uuid>
# ---------------------------------------------------------------------------

async def choiceqr_webhook_handler(request: web.Request) -> web.Response:
    """POST /webhook/choiceqr?biz_id=<uuid>"""
    try:
        biz_id = request.query.get("biz_id", "").strip()
        if not biz_id:
            return web.Response(status=400, text="Missing biz_id")

        biz = await db.get_business_by_id(biz_id)
        if not biz:
            return web.Response(status=404, text="Business not found")

        stored_token = (biz.get("choice_token") or "").strip()
        if not stored_token:
            logger.warning(f"[ChoiceQR] biz={biz_id} — choice_token не налаштовано")
            return web.Response(status=403, text="Integration not configured")

        # Верифікація: Bearer токен обов'язковий
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            logger.warning(f"[ChoiceQR] biz={biz_id} — відсутній Authorization Bearer")
            return web.Response(status=403, text="Forbidden")

        incoming_token = auth_header[len("Bearer "):].strip()
        if not hmac.compare_digest(incoming_token, stored_token):
            logger.warning(f"[ChoiceQR] biz={biz_id} — невірний Bearer токен")
            return web.Response(status=403, text="Forbidden")

        body = await request.read()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            logger.error(f"[ChoiceQR] biz={biz_id} — невалідний JSON")
            return web.Response(status=400, text="Invalid JSON")

        event = data.get("event") or data.get("type", "")
        logger.info(f"[ChoiceQR] biz={biz_id} event={event}")

        if event in ("order.created", "new_order", "order"):
            order = data.get("order") or data.get("data") or data

            customer    = order.get("customer") or {}
            client_name = customer.get("name") or order.get("customer_name", "Клієнт")
            phone       = customer.get("phone") or order.get("customer_phone", "")

            delivery = order.get("delivery") or {}
            address  = delivery.get("address") or order.get("address", "")

            comment    = order.get("comment") or order.get("notes", "")
            raw_amount = (
                order.get("total")
                or order.get("total_price")
                or order.get("amount", 0)
            )
            amount = _parse_amount(raw_amount)

            asyncio.create_task(
                _notify_managers_new_pos_order(
                    biz_id=biz_id,
                    source="choiceqr",
                    client_name=client_name,
                    phone=phone,
                    address=address,
                    amount=amount,
                    comment=comment,
                )
            )

        return web.Response(text="OK")

    except Exception as exc:
        logger.error(f"[ChoiceQR] Невідома помилка: {exc}", exc_info=True)
        return web.Response(status=500, text="Internal Server Error")


# ---------------------------------------------------------------------------
# WEBHOOK: GOPOS
# Документація: https://gopos.pl/api-docs
#
# Верифікація: X-GoPOS-Signature = HMAC-SHA256(gopos_token, body)
# Заголовок обов'язковий — без нього відхиляємо.
# URL: POST /webhook/gopos?biz_id=<uuid>
# ---------------------------------------------------------------------------

async def gopos_webhook_handler(request: web.Request) -> web.Response:
    """POST /webhook/gopos?biz_id=<uuid>"""
    try:
        biz_id = request.query.get("biz_id", "").strip()
        if not biz_id:
            return web.Response(status=400, text="Missing biz_id")

        biz = await db.get_business_by_id(biz_id)
        if not biz:
            return web.Response(status=404, text="Business not found")

        stored_token = (biz.get("gopos_token") or "").strip()
        if not stored_token:
            logger.warning(f"[GoPOS] biz={biz_id} — gopos_token не налаштовано")
            return web.Response(status=403, text="Integration not configured")

        body = await request.read()

        incoming_sig = request.headers.get("X-GoPOS-Signature", "").strip()
        if not incoming_sig:
            logger.warning(f"[GoPOS] biz={biz_id} — відсутній X-GoPOS-Signature")
            return web.Response(status=403, text="Forbidden")

        expected = _hmac_sha256(stored_token, body)
        if not _safe_hmac_equal(incoming_sig, expected):
            logger.warning(f"[GoPOS] biz={biz_id} — невірний підпис")
            return web.Response(status=403, text="Forbidden")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            logger.error(f"[GoPOS] biz={biz_id} — невалідний JSON")
            return web.Response(status=400, text="Invalid JSON")

        event = data.get("event") or data.get("event_type", "")
        logger.info(f"[GoPOS] biz={biz_id} event={event}")

        if event in ("order.created", "delivery.created", "new_order"):
            order    = data.get("order") or data.get("data") or data
            customer = order.get("customer") or {}

            client_name = (
                order.get("customer_name")
                or order.get("client_name")
                or customer.get("name", "Клієнт")
            )
            phone = (
                order.get("customer_phone")
                or order.get("phone")
                or customer.get("phone", "")
            )

            raw_delivery = order.get("delivery") or order.get("delivery_address") or {}
            address = _build_gopos_address(raw_delivery) or order.get("address", "")

            comment    = order.get("comment") or order.get("notes", "")
            raw_amount = order.get("total") or order.get("price") or order.get("amount", 0)
            amount     = _parse_amount(raw_amount)

            asyncio.create_task(
                _notify_managers_new_pos_order(
                    biz_id=biz_id,
                    source="gopos",
                    client_name=client_name,
                    phone=phone,
                    address=address,
                    amount=amount,
                    comment=comment,
                )
            )

        return web.Response(text="OK")

    except Exception as exc:
        logger.error(f"[GoPOS] Невідома помилка: {exc}", exc_info=True)
        return web.Response(status=500, text="Internal Server Error")


# ---------------------------------------------------------------------------
# WEBHOOK: SYRVE (iiko)
# Документація: https://ru.iiko.help/articles/#!api-documentations
#
# Верифікація: X-Syrve-Signature = HMAC-SHA256(syrve_token, body)
# Подія: DeliveryOrderStatusChanged з orderStatus == "New"
# URL: POST /webhook/syrve?biz_id=<uuid>
# ---------------------------------------------------------------------------

async def syrve_webhook_handler(request: web.Request) -> web.Response:
    """POST /webhook/syrve?biz_id=<uuid>"""
    try:
        biz_id = request.query.get("biz_id", "").strip()
        if not biz_id:
            return web.Response(status=400, text="Missing biz_id")

        biz = await db.get_business_by_id(biz_id)
        if not biz:
            return web.Response(status=404, text="Business not found")

        stored_token = (biz.get("syrve_token") or "").strip()
        if not stored_token:
            logger.warning(f"[Syrve] biz={biz_id} — syrve_token не налаштовано")
            return web.Response(status=403, text="Integration not configured")

        body = await request.read()

        incoming_sig = request.headers.get("X-Syrve-Signature", "").strip()
        if not incoming_sig:
            logger.warning(f"[Syrve] biz={biz_id} — відсутній X-Syrve-Signature")
            return web.Response(status=403, text="Forbidden")
        expected = _hmac_sha256(stored_token, body)
        if not _safe_hmac_equal(incoming_sig, expected):
            logger.warning(f"[Syrve] biz={biz_id} — невірний підпис")
            return web.Response(status=403, text="Forbidden")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            logger.error(f"[Syrve] biz={biz_id} — невалідний JSON")
            return web.Response(status=400, text="Invalid JSON")

        event = data.get("eventType") or data.get("event", "")
        logger.info(f"[Syrve] biz={biz_id} event={event}")

        # Нове замовлення доставки
        is_new = (
            event == "DeliveryOrderStatusChanged"
            and data.get("orderStatus") == "New"
        ) or event in ("order.created", "delivery.created")

        if is_new:
            order    = data.get("order") or data.get("deliveryOrder") or data
            customer = order.get("customer") or {}

            first = customer.get("name") or customer.get("firstName", "")
            last  = customer.get("lastName", "")
            client_name = f"{first} {last}".strip() or "Клієнт"
            phone = customer.get("cellPhone") or customer.get("phone", "")

            # Складання адреси з вкладеної структури Syrve
            addr_obj = order.get("address") or order.get("deliveryAddress") or {}
            if isinstance(addr_obj, dict):
                street_obj = addr_obj.get("street") or {}
                street_name = (
                    street_obj.get("name", "")
                    if isinstance(street_obj, dict)
                    else str(street_obj)
                )
                house = addr_obj.get("house", "")
                flat  = addr_obj.get("flat", "")
                city_obj  = addr_obj.get("city") or {}
                city_name = (
                    city_obj.get("name", "")
                    if isinstance(city_obj, dict)
                    else str(city_obj)
                )
                parts = [p for p in [street_name, house, flat, city_name] if p]
                address = ", ".join(parts)
            else:
                address = str(addr_obj or "")

            comment    = order.get("comment", "")
            raw_amount = order.get("sum") or order.get("total") or order.get("amount", 0)
            # Syrve зберігає суму в копійках
            amount = _parse_amount(raw_amount, divisor=100)

            asyncio.create_task(
                _notify_managers_new_pos_order(
                    biz_id=biz_id,
                    source="syrve",
                    client_name=client_name,
                    phone=phone,
                    address=address,
                    amount=amount,
                    comment=comment,
                )
            )

        return web.Response(text="OK")

    except Exception as exc:
        logger.error(f"[Syrve] Невідома помилка: {exc}", exc_info=True)
        return web.Response(status=500, text="Internal Server Error")


# ---------------------------------------------------------------------------
# CONFIG endpoint
# ---------------------------------------------------------------------------

CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
}


async def config_handler(request: web.Request) -> web.Response:
    """
    GET /config — повертає Supabase anon-key фронтенду.
    Ніколи не повертає service_role ключ.
    """
    return web.json_response(
        {"supabase_url": SUPABASE_URL or "", "supabase_key": SUPABASE_KEY or ""},
        headers=CORS_HEADERS,
    )


async def cors_preflight_handler(request: web.Request) -> web.Response:
    """OPTIONS — відповідь на preflight-запити браузера."""
    return web.Response(status=204, headers=CORS_HEADERS)


# ---------------------------------------------------------------------------
# Middleware: CORS для всіх відповідей
# ---------------------------------------------------------------------------

@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        return await cors_preflight_handler(request)
    response = await handler(request)
    for k, v in CORS_HEADERS.items():
        response.headers[k] = v
    return response


# ---------------------------------------------------------------------------
# Реєстрація маршрутів та запуск сервера
# ---------------------------------------------------------------------------

async def start_webhook_server() -> None:
    """Створює aiohttp-додаток, реєструє всі маршрути і запускає сервер."""
    app = web.Application(middlewares=[cors_middleware])

    # Службові
    app.router.add_get("/config",              config_handler)
    app.router.add_options("/{path_info:.*}",  cors_preflight_handler)

    # Whop — підписки
    app.router.add_post("/webhook/whop",       whop_webhook_handler)

    # POS-системи
    app.router.add_post("/webhook/poster",     poster_webhook_handler)
    app.router.add_post("/webhook/choiceqr",   choiceqr_webhook_handler)
    app.router.add_post("/webhook/gopos",      gopos_webhook_handler)
    app.router.add_post("/webhook/syrve",      syrve_webhook_handler)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", 8000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info(f"🌐 Webhook сервер запущено на порту {port}")
    logger.info("   Зареєстровані маршрути:")
    logger.info("   GET  /config")
    logger.info("   POST /webhook/whop       (Whop підписки)")
    logger.info("   POST /webhook/poster     (Poster POS)")
    logger.info("   POST /webhook/choiceqr   (ChoiceQR)")
    logger.info("   POST /webhook/gopos      (GoPOS)")
    logger.info("   POST /webhook/syrve      (Syrve / iiko)")
