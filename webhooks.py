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
from config import WHOP_WEBHOOK_SECRET, SUPABASE_URL, SUPABASE_KEY
from keyboards import generate_token
from aiogram.types import FSInputFile
from texts import get_text as _
from handlers.map_service import get_route_map_file

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


def _parse_payment(raw: str) -> str:
    """
    Нормалізує тип оплати з POS-систем до одного з: cash | terminal | online.
    Підтримує різні назви від Poster, ChoiceQR, GoPOS, Syrve.
    """
    if not raw:
        return "cash"
    r = str(raw).strip().lower()
    CASH    = {'cash', 'готівка', 'наличные', 'gotówka', 'наличка',
               'cash_on_delivery', 'cod', '1', 'cash_courier'}
    TERM    = {'terminal', 'термінал', 'терминал', 'card', 'карта',
               'card_on_delivery', 'pos', 'безготівка', '2', 'card_courier'}
    ONLINE  = {'online', 'онлайн', 'prepaid', 'liqpay', 'monobank',
               'fondy', 'wayforpay', 'stripe', 'internet', 'paid_online',
               'electronic', '3', 'card_online'}
    if r in CASH:   return "cash"
    if r in TERM:   return "terminal"
    if r in ONLINE: return "online"
    return "cash"  # fallback


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
# Єдиний хелпер: відправити повідомлення менеджерам/власнику (ОНОВЛЕНО ДЛЯ UBER)
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
    Надсилає картку нового POS-замовлення.
    dispatcher-mode: менеджерам з кнопкою WebApp (стара логіка).
    uber-mode: одразу створює замовлення і кидає в групу кур'єрів.
    """
    source_labels = {
        "poster":   "🔶 POSTER",
        "choiceqr": "🟢 CHOICEQR",
        "gopos":    "🔵 GOPOS",
        "syrve":    "🟣 SYRVE",
    }
    source_label = source_labels.get(source, f"📦 {source.upper()}")

    biz = await db.get_business_by_id(biz_id)
    if not biz:
        return

    delivery_mode = biz.get("delivery_mode", "dispatcher")
    currency = biz.get("currency", "zł")
    pay_icon = "💵" if payment == "cash" else ("💳" if payment == "terminal" else "🌐")

    # ── DISPATCHER MODE (стара незмінна логіка) ───────────────────
    if delivery_mode == 'dispatcher':
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

    # ── UBER MODE (логіка вільної каси — з картою та кнопками в групі) ────
    elif delivery_mode == 'uber':
        group_id = biz.get('courier_group_id')
        if not group_id:
            logger.warning(f"[uber] biz_id={biz_id}: courier_group_id не задано, пропускаємо")
            return

        # 1. Зберігаємо замовлення в БД
        new_order_payload = {
            "biz_id": biz_id,
            "client_name": client_name,
            "client_phone": phone,
            "address": address,
            "amount": amount,
            "payment": payment,
            "comment": comment,
            "courier_id": None
        }

        new_order = await db.create_new_order(new_order_payload)
        if not new_order:
            logger.error(f"[uber] Не вдалося створити замовлення в БД для biz_id={biz_id}")
            return

        order_id = new_order['id']
        short_id = str(order_id)[:6].upper()

        # 2. Локалізований тип оплати
        pay_type_str = {"cash": "Готівка", "terminal": "Термінал", "online": "Онлайн"}.get(payment, payment)

        # 3. Надсилаємо в групу через спільний хелпер (з картою + правильними кнопками)
        from handlers.orders import _send_uber_group_message
        await _send_uber_group_message(
            bot=bot, group_id=group_id, biz=biz,
            order_id=order_id, short_id=short_id,
            address=address, details_text="",
            client_name=client_name, phone=phone,
            pay_icon=pay_icon, amount=amount, currency=currency,
            pay_type_str=pay_type_str, pay_type=payment,
            comment=comment, source_label=source_label
        )
        logger.info(f"[uber POS] Замовлення {order_id} кинуто в групу {group_id}")


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
            expected = _hmac_sha256(poster_token, body)
            if not _safe_hmac_equal(header_sig, expected):
                logger.warning(f"[Poster] biz={biz_id} — невірний X-Poster-Signature")
                return web.Response(status=403, text="Forbidden")
        elif verify_param:
            timestamp = request.query.get("timestamp", "")
            raw = f"{poster_token};{timestamp}" if timestamp else poster_token
            expected_md5 = hashlib.md5(raw.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(verify_param, expected_md5):
                logger.warning(f"[Poster] biz={biz_id} — невірний verify hash")
                return web.Response(status=403, text="Forbidden")
        else:
            logger.info(f"[Poster] biz={biz_id} — запит без підпису (дозволено для тестів)")

        def _decode_body(b: bytes) -> str:
            for enc in ("utf-8", "cp1251", "latin-1"):
                try:
                    return b.decode(enc)
                except UnicodeDecodeError:
                    continue
            return b.decode("utf-8", errors="replace")

        try:
            data = json.loads(_decode_body(body))
        except (json.JSONDecodeError, ValueError):
            parsed = urllib.parse.parse_qs(_decode_body(body))
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

            raw_sum = order_data.get("total_sum") or order_data.get("sum", 0)
            amount  = _parse_amount(raw_sum, divisor=100)

            raw_pay = (
                order_data.get("pay_type")
                or order_data.get("payment_method")
                or order_data.get("payment")
                or ""
            )
            payment = _parse_payment(str(raw_pay))

            asyncio.create_task(
                _notify_managers_new_pos_order(
                    biz_id=biz_id,
                    source="poster",
                    client_name=client_name,
                    phone=phone,
                    address=address,
                    amount=amount,
                    comment=comment,
                    payment=payment,
                )
            )

        return web.Response(text="OK")

    except Exception as exc:
        logger.error(f"[Poster] Невідома помилка: {exc}", exc_info=True)
        return web.Response(status=500, text="Internal Server Error")


# ---------------------------------------------------------------------------
# WEBHOOK: CHOICEQR
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

            raw_pay = (
                order.get("payment_method")
                or order.get("payment_type")
                or order.get("payment")
                or (order.get("payment_info") or {}).get("type", "")
                or ""
            )
            payment = _parse_payment(str(raw_pay))

            asyncio.create_task(
                _notify_managers_new_pos_order(
                    biz_id=biz_id,
                    source="choiceqr",
                    client_name=client_name,
                    phone=phone,
                    address=address,
                    amount=amount,
                    comment=comment,
                    payment=payment,
                )
            )

        return web.Response(text="OK")

    except Exception as exc:
        logger.error(f"[ChoiceQR] Невідома помилка: {exc}", exc_info=True)
        return web.Response(status=500, text="Internal Server Error")


# ---------------------------------------------------------------------------
# WEBHOOK: GOPOS
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

            raw_pay = (
                order.get("payment_method")
                or order.get("payment_type")
                or order.get("payment")
                or ""
            )
            payment = _parse_payment(str(raw_pay))

            asyncio.create_task(
                _notify_managers_new_pos_order(
                    biz_id=biz_id,
                    source="gopos",
                    client_name=client_name,
                    phone=phone,
                    address=address,
                    amount=amount,
                    comment=comment,
                    payment=payment,
                )
            )

        return web.Response(text="OK")

    except Exception as exc:
        logger.error(f"[GoPOS] Невідома помилка: {exc}", exc_info=True)
        return web.Response(status=500, text="Internal Server Error")


# ---------------------------------------------------------------------------
# WEBHOOK: SYRVE (iiko)
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
            amount = _parse_amount(raw_amount, divisor=100)

            raw_pay = ""
            payments_list = order.get("payments") or []
            if payments_list and isinstance(payments_list, list):
                first_pay = payments_list[0] if payments_list else {}
                pay_type_obj = first_pay.get("paymentType") or {}
                raw_pay = (
                    pay_type_obj.get("name", "")
                    or pay_type_obj.get("code", "")
                    or first_pay.get("paymentTypeKind", "")
                )
            if not raw_pay:
                raw_pay = order.get("paymentType") or order.get("payment") or ""
            payment = _parse_payment(str(raw_pay))

            asyncio.create_task(
                _notify_managers_new_pos_order(
                    biz_id=biz_id,
                    source="syrve",
                    client_name=client_name,
                    phone=phone,
                    address=address,
                    amount=amount,
                    comment=comment,
                    payment=payment,
                )
            )

        return web.Response(text="OK")

    except Exception as exc:
        logger.error(f"[Syrve] Невідома помилка: {exc}", exc_info=True)
        return web.Response(status=500, text="Internal Server Error")

# ---------------------------------------------------------------------------
# WEBHOOK: API NEW ORDER (FROM WEBAPP FORM)
# ---------------------------------------------------------------------------

async def api_new_order_handler(request: web.Request) -> web.Response:
    """POST /api/new_order"""
    try:
        data = await request.json()
        lang = data.get("lang", "uk")
        
        biz_id = data['biz_id']
        actual_plan = await db.get_actual_plan(biz_id)
        if actual_plan == "expired":
            return web.Response(status=403, text="expired")

        original_courier_id = data.get('courier_id')
        if original_courier_id == "unassigned":
            data['courier_id'] = None

        # Створюємо замовлення в базі
        new_order = await db.create_new_order(data)

        if new_order:
            order_id = new_order['id']
            short_id = str(order_id)[:6].upper()
            biz = await db.get_business_by_id(biz_id)
            currency = biz.get('currency', 'zł')
            delivery_mode = biz.get('delivery_mode', 'dispatcher')
            is_pro = actual_plan in ['pro', 'trial']
            courier_lang = lang

            pay_type_str = _(courier_lang, 'pay_' + data['payment'])
            pay_icon = "💵" if data['payment'] == "cash" else ("💳" if data['payment'] == "terminal" else "🌐")

            details_parts = []
            if data.get('apt'):
                details_parts.append(_(courier_lang, 'apt_prefix', apt=data['apt']))
            if data.get('code'):
                details_parts.append(_(courier_lang, 'code_prefix', code=data['code']))
            details_text = _(courier_lang, 'details_prefix', details=', '.join(details_parts)) if details_parts else ""

            address_query = urllib.parse.quote(data['address'])
            route_url = f"https://www.google.com/maps/dir/?api=1&destination={address_query}"

            phone_clean = "".join(filter(lambda x: x.isdigit() or x == '+', data.get('client_phone', '')))
            if not phone_clean.startswith('+') and phone_clean:
                phone_clean = '+' + phone_clean

            # ── UBER MODE: Відправка в загальну групу (з картою + кнопками) ──
            if original_courier_id == "unassigned" and delivery_mode == 'uber':
                group_id = biz.get('courier_group_id')
                if group_id:
                    from handlers.orders import _send_uber_group_message
                    await _send_uber_group_message(
                        bot=bot, group_id=group_id, biz=biz,
                        order_id=order_id, short_id=short_id,
                        address=data['address'], details_text=details_text,
                        client_name=data.get('client_name', '—'), phone=phone_clean,
                        pay_icon=pay_icon, amount=data['amount'], currency=currency,
                        pay_type_str=pay_type_str, pay_type=data['payment'],
                        comment=data.get('comment', '')
                    )
                    logger.info(f"[uber api] Ручне замовлення {order_id} кинуто в групу {group_id}")
                else:
                    logger.warning(f"Uber mode, але courier_group_id не задано для biz_id={biz_id}")

            # ── DISPATCHER MODE: Відправка конкретному кур'єру (стара логіка) ──
            elif original_courier_id != "unassigned":
                status_active = _(courier_lang, 'status_active_full')

                courier_text = _(courier_lang, 'order_new',
                                 short_id=short_id, status=status_active, address=data['address'],
                                 details_text=details_text, phone=phone_clean, client_name=data.get('client_name', ''),
                                 pay_icon=pay_icon, amount=data['amount'], cur=currency, pay_type=pay_type_str)

                if data.get('comment'):
                    courier_text += _(courier_lang, 'comment_prefix', comment=data['comment'])

                builder = InlineKeyboardBuilder()
                if is_pro:
                    builder.button(text=_(courier_lang, 'btn_route'), url=route_url)
                builder.button(text=_(courier_lang, 'btn_finish'), callback_data=f"finish_order_{order_id}")
                builder.adjust(1)

                if is_pro:
                    map_filename = await get_route_map_file(biz, data['address'], short_id)
                    if map_filename and os.path.exists(map_filename):
                        photo = FSInputFile(map_filename)
                        await bot.send_photo(chat_id=data['courier_id'], photo=photo, caption=courier_text, reply_markup=builder.as_markup(), parse_mode="Markdown")
                        os.remove(map_filename)
                    else:
                        await bot.send_message(chat_id=data['courier_id'], text=courier_text, reply_markup=builder.as_markup(), parse_mode="Markdown")
                else:
                    await bot.send_message(chat_id=data['courier_id'], text=courier_text, reply_markup=builder.as_markup(), parse_mode="Markdown")

            return web.Response(text="OK")
        else:
            return web.Response(status=500, text="Помилка збереження замовлення")
            
    except Exception as exc:
        logger.error(f"[API New Order] Помилка: {exc}", exc_info=True)
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
    app.router.add_post("/api/new_order",      api_new_order_handler)

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

