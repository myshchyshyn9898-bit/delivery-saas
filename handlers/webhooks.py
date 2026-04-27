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
from config import WHOP_WEBHOOK_SECRET, SUPABASE_URL, SUPABASE_KEY, MAPBOX_TOKEN
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

    # ✅ Перевіряємо підписку — POS замовлення не проходять якщо підписка expired
    actual_plan = await db.get_actual_plan(biz_id)
    if actual_plan == "expired":
        logger.warning(f"[POS:{source}] biz={biz_id} — підписка expired, замовлення відхилено")
        return

    # ✅ FIX #1: Якщо адреса порожня — використовуємо заглушку замість return.
    # ChoiceQR для pickup/dine-in замовлень може не надсилати адресу,
    # але замовлення все одно має дійти менеджеру.
    if not address or not address.strip():
        logger.warning(f"[POS:{source}] biz={biz_id} — замовлення без адреси, використовуємо заглушку")
        address = "— адреса не вказана —"

    delivery_mode = biz.get("delivery_mode", "dispatcher")
    currency = biz.get("currency", "zł")
    pay_icon = "💵" if payment == "cash" else ("💳" if payment == "terminal" else "🌐")
    # Для POS-нотифікацій використовуємо мову бізнесу або 'en' як дефолт
    biz_lang = biz.get("lang", "en")

    # ── DISPATCHER MODE ───────────────────────────────────────────────────────
    if delivery_mode == 'dispatcher':
        base_url = os.environ.get(
            "BASE_URL",
            "https://myshchyshyn9898-bit.github.io/delivery-saas",
        ).rstrip("/")
        form_base_url = f"{base_url}/form.html"
        import html as _ph
        _pe = _ph.escape

        # ✅ ВИПРАВЛЕНО bug #6: зберігаємо POS-замовлення в БД одразу,
        # щоб воно потрапляло в аналітику навіть якщо менеджер не відкриє форму.
        pos_order_id = None
        try:
            saved = await db.create_new_order({
                "biz_id":        biz_id,
                "client_name":   client_name,
                "client_phone":  phone,
                "address":       address or "",
                "amount":        amount,
                "payment":       payment,
                "comment":       comment,
                "courier_id":    None,   # призначається менеджером пізніше
            })
            if saved:
                pos_order_id = saved["id"]
                logger.info(f"[POS:{source}] Замовлення збережено в БД: {pos_order_id}")
        except Exception as exc:
            logger.error(f"[POS:{source}] Не вдалось зберегти в БД: {exc}")

        admin_text = _(biz_lang, 'pos_order_new',
            source=source_label,
            client=_pe(client_name) if client_name else '—',
            phone=_pe(phone) if phone else '—',
            address=_pe(address) if address else '—',
            amount=_pe(str(amount)),
            cur=currency,
        )
        if comment:
            admin_text += _(biz_lang, 'pos_order_comment', comment=_pe(comment))

        markup_base_url = f"{form_base_url}?biz_id={urllib.parse.quote(biz_id)}" \
            f"&address={urllib.parse.quote(address or '')}" \
            f"&phone={urllib.parse.quote(phone or '')}" \
            f"&amount={urllib.parse.quote(str(amount))}" \
            f"&name={urllib.parse.quote(client_name or '')}" \
            f"&comment={urllib.parse.quote(comment or '')}" \
            f"&payment={urllib.parse.quote(payment)}"
        # Якщо замовлення збережено — передаємо order_id щоб форма призначила кур'єра до нього
        if pos_order_id:
            markup_base_url += f"&order_id={urllib.parse.quote(str(pos_order_id))}"

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
                    text=_(biz_lang, 'btn_assign_courier'),
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
        pay_type_str = _(biz_lang, 'pay_' + payment)

        # 3. Надсилаємо в групу через спільний хелпер (з картою + правильними кнопками)
        from handlers.orders import _send_uber_group_message
        await _send_uber_group_message(
            bot=bot, group_id=group_id, biz=biz,
            order_id=order_id, short_id=short_id,
            address=address, details_text="",
            client_name=client_name, phone=phone,
            pay_icon=pay_icon, amount=amount, currency=currency,
            pay_type_str=pay_type_str, pay_type=payment,
            comment=comment, source_label=source_label, lang=biz_lang
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

        # ── Активація підписки ──────────────────────────────────────────
        if event_type == "membership.went_active":
            membership_data = data.get("data", {})
            custom_fields   = membership_data.get("custom_fields") or {}
            biz_id          = custom_fields.get("biz_id")
            tg_user_id      = custom_fields.get("tg_user_id")

            if biz_id:
                # ✅ FIX: читаємо реальну дату закінчення з Whop payload
                expires_at = (
                    membership_data.get("renewal_period_end")
                    or membership_data.get("expires_at")
                    or membership_data.get("expiration_date")
                    or None
                )
                await db.activate_whop_subscription(
                    biz_id, "pro", membership_data.get("id", ""),
                    expires_at_iso=expires_at
                )
                logger.info(f"[Whop] PRO активовано для biz={biz_id}, expires={expires_at or '+30d'}")
                if tg_user_id:
                    db.invalidate_user_cache(int(tg_user_id))
                if tg_user_id:
                    try:
                        owner_lang = "en"
                        try:
                            biz_for_lang = await db.get_business_by_id(biz_id)
                            owner_lang = (biz_for_lang or {}).get("lang", "en")
                        except Exception:
                            pass
                        await bot.send_message(
                            chat_id=int(tg_user_id),
                            text=_(owner_lang, 'pro_activated'),
                            parse_mode="HTML",
                        )
                    except Exception as exc:
                        logger.error(f"[Whop] Помилка відправки {tg_user_id}: {exc}")
            else:
                logger.warning("[Whop] membership.went_active без biz_id")

        # ── Скасування / деактивація підписки ───────────────────────────
        elif event_type in ("membership.went_inactive", "membership.expired", "membership.canceled"):
            membership_data = data.get("data", {})
            custom_fields   = membership_data.get("custom_fields") or {}
            biz_id          = custom_fields.get("biz_id")
            tg_user_id      = custom_fields.get("tg_user_id")

            if biz_id:
                await db.deactivate_whop_subscription(biz_id)
                logger.info(f"[Whop] Підписку деактивовано для biz={biz_id} (event={event_type})")
                if tg_user_id:
                    db.invalidate_user_cache(int(tg_user_id))
                if tg_user_id:
                    try:
                        owner_lang = "en"
                        try:
                            biz_for_lang = await db.get_business_by_id(biz_id)
                            owner_lang = (biz_for_lang or {}).get("lang", "en")
                        except Exception:
                            pass
                        # Надсилаємо власнику повідомлення про закінчення підписки
                        cancel_text = _(owner_lang, 'subscription_expired')
                        await bot.send_message(
                            chat_id=int(tg_user_id),
                            text=cancel_text,
                            parse_mode="HTML",
                        )
                    except Exception as exc:
                        logger.error(f"[Whop] Помилка сповіщення про скасування {tg_user_id}: {exc}")
            else:
                logger.warning(f"[Whop] {event_type} без biz_id у custom_fields")

        else:
            logger.info(f"[Whop] Ігноруємо event={event_type}")

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
            logger.warning(f"[Poster] biz={biz_id} — запит без підпису, відхилено")
            return web.Response(status=403, text="Forbidden: missing signature")

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
                or "—"
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
# WEBHOOK: CHOICEQR (БЕЗ КЛЮЧІВ - ПАСИВНИЙ СЛУХАЧ)
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

        body = await request.read()
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            logger.error(f"[ChoiceQR] biz={biz_id} — невалідний JSON")
            return web.Response(status=400, text="Invalid JSON")

        # ✅ FIX #2: Debug-лог повного payload для діагностики.
        # Допомагає зрозуміти реальні назви полів і event від ChoiceQR.
        # Можна прибрати після підтвердження що інтеграція працює.
        logger.info(
            f"[ChoiceQR DEBUG] biz={biz_id} | "
            f"event={data.get('event')} | type={data.get('type')} | "
            f"keys={list(data.keys())} | "
            f"payload={json.dumps(data, ensure_ascii=False)[:600]}"
        )

        event = data.get("event") or data.get("type", "")
        logger.info(f"[ChoiceQR] biz={biz_id} event={event}")

        # ✅ 1. Опрацювання НОВОГО замовлення
        # Розширений список можливих назв події від різних версій ChoiceQR API
        if event in (
            "order.created", "new_order", "order",
            "ORDER_CREATED", "order_created", "NewOrder",
        ):
            order = data.get("order") or data.get("data") or data

            customer    = order.get("customer") or {}
            client_name = customer.get("name") or order.get("customer_name", "—")
            phone       = customer.get("phone") or order.get("customer_phone", "")

            delivery = order.get("delivery") or {}
            address  = (
                delivery.get("address")
                or delivery.get("full_address")
                or delivery.get("street")
                or order.get("address", "")
            )

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

        # 🚨 2. Опрацювання СКАСУВАННЯ замовлення
        elif event in ("order.cancelled", "order.canceled", "cancelled", "canceled"):
            order = data.get("order") or data.get("data") or data
            choice_order_id = order.get("id") or order.get("display_id", "невідомий")

            logger.warning(f"[ChoiceQR] 🚨 ЗАМОВЛЕННЯ СКАСОВАНО: biz={biz_id}, choice_id={choice_order_id}")
            # P.S. Поки що просто логуємо. Якщо в майбутньому захочеш - тут можна
            # дописати пошук замовлення в БД і відправку повідомлення кур'єру в Telegram.

        else:
            # Невідома подія — логуємо щоб знати що прийшло
            logger.warning(f"[ChoiceQR] biz={biz_id} — невідома подія: '{event}', пропускаємо")

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
                or customer.get("name", "—")
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
            client_name = f"{first} {last}".strip() or "—"
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
            amount = _parse_amount(raw_amount)  # Syrve повертає суму в звичайних одиницях (не в копійках)

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
        # JWT верифікація
        import jwt as _jwt
        from keyboards import JWT_SECRET
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return web.Response(status=403, text="Missing token")
        token_str = auth_header[len("Bearer "):].strip()
        try:
            payload = _jwt.decode(token_str, JWT_SECRET, algorithms=["HS256"])
        except _jwt.ExpiredSignatureError:
            return web.Response(status=403, text="Token expired")
        except _jwt.InvalidTokenError:
            return web.Response(status=403, text="Invalid token")

        data = await request.json()
        lang = data.get("lang", "uk")

        biz_id = data['biz_id']

        # Перевіряємо що token виданий саме для цього biz_id
        # service_role токен (is_boss) може отримати доступ до всіх бізнесів
        token_biz = payload.get("biz_id")
        is_service = payload.get("role") == "service_role"
        if token_biz and not is_service and token_biz != str(biz_id):
            return web.Response(status=403, text="Token biz_id mismatch")

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
            # BUG FIX: courier_lang = lang використовував мову адміна з форми,
            # а не реальну мову кур'єра з БД
            if original_courier_id and original_courier_id != 'unassigned':
                courier_lang = await db.get_courier_lang(int(original_courier_id), biz_id)
            else:
                courier_lang = biz.get('lang', lang)

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

            raw_phone = data.get('client_phone') or ''
            phone_clean = "".join(filter(lambda x: x.isdigit() or x == '+', str(raw_phone)))
            if phone_clean and not phone_clean.startswith('+'):
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
                        comment=data.get('comment', ''), lang=courier_lang
                    )
                    logger.info(f"[uber api] Ручне замовлення {order_id} кинуто в групу {group_id}")
                else:
                    logger.warning(f"Uber mode, але courier_group_id не задано для biz_id={biz_id}")

            # ── DISPATCHER MODE: Відправка конкретному кур'єру ──
            elif original_courier_id != "unassigned":
                # Перевіряємо що courier_id існує в staff цього бізнесу
                courier_check = await db._run(
                    lambda: db.supabase.table('staff')
                        .select('user_id')
                        .eq('user_id', original_courier_id)
                        .eq('business_id', biz_id)
                        .execute()
                )
                if not courier_check.data:
                    logger.warning(f"[api_new_order] courier_id={original_courier_id} не в staff biz={biz_id}")
                    return web.Response(status=400, text="Courier not in staff")
                import html as _wh
                from handlers.orders import _build_order_text, _build_call_url

                pay_type_d = data['payment']
                courier_text = _build_order_text(
                    short_id=short_id,
                    address=_wh.escape(data['address']),
                    details_text=_wh.escape(details_text),
                    client_name=_wh.escape(data.get('client_name', '') or ''),
                    phone=_wh.escape(phone_clean),
                    pay_type=pay_type_d,
                    amount=data['amount'],
                    currency=currency,
                    comment=_wh.escape(data.get('comment', '') or ''),
                    status_line=_(courier_lang, 'order_status_active_short'),
                    lang=courier_lang
                )

                builder = InlineKeyboardBuilder()
                if is_pro:
                    builder.button(text=_(courier_lang, 'btn_route'), url=route_url)
                if phone_clean:
                    _btn_wh, _url_wh = _build_call_url(phone_clean, biz, lang=courier_lang)
                    builder.button(text=_btn_wh, url=_url_wh)
                if pay_type_d == "online":
                    builder.button(text=_(courier_lang, 'btn_close_online'), callback_data=f"dispatcher_close_online_{order_id}")
                else:
                    builder.button(text=_(courier_lang, 'btn_close_cash', amount=data['amount'], cur=currency), callback_data=f"dispatcher_close_cash_{order_id}")
                    builder.button(text=_(courier_lang, 'btn_close_terminal', amount=data['amount'], cur=currency), callback_data=f"dispatcher_close_terminal_{order_id}")
                builder.adjust(1)

                if is_pro:
                    map_filename = await get_route_map_file(biz, data['address'], short_id)
                    try:
                        if map_filename and os.path.exists(map_filename):
                            photo = FSInputFile(map_filename)
                            await bot.send_photo(chat_id=data['courier_id'], photo=photo, caption=courier_text, reply_markup=builder.as_markup(), parse_mode="HTML")
                        else:
                            await bot.send_message(chat_id=data['courier_id'], text=courier_text, reply_markup=builder.as_markup(), parse_mode="HTML")
                    finally:
                        if map_filename and os.path.exists(map_filename):
                            try:
                                os.remove(map_filename)
                            except OSError as e:
                                logger.warning(f"[api_new_order] Не вдалось видалити map файл: {e}")
                else:
                    await bot.send_message(chat_id=data['courier_id'], text=courier_text, reply_markup=builder.as_markup(), parse_mode="HTML")

            return web.Response(text="OK")
        else:
            return web.Response(status=500, text="order_save_error")

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
        {"supabase_url": SUPABASE_URL or "", "supabase_key": SUPABASE_KEY or "", "mapbox_token": MAPBOX_TOKEN or ""},
        headers=CORS_HEADERS,
    )


async def health_handler(request: web.Request) -> web.Response:
    """GET /health — health check для Railway/UptimeRobot."""
    return web.json_response({"status": "ok"}, headers=CORS_HEADERS)


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


async def invalidate_cache_handler(request: web.Request) -> web.Response:
    """POST /api/invalidate_cache — скидає in-memory кеш для юзера після зміни в staff.

    ✅ ВИПРАВЛЕНО bug #2: при видаленні/зміні персоналу через JS-дашборд
    бот не знав про це і ще 60 сек показував видаленому кур'єру активне меню.
    Тепер JS викликає цей endpoint після будь-якої зміни в staff.

    Авторизація: Bearer JWT токен (той самий що генерує keyboards.py).
    """
    try:
        import jwt as _jwt
        from keyboards import JWT_SECRET

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return web.Response(status=403, text="Missing token")
        token_str = auth_header[len("Bearer "):].strip()
        try:
            payload = _jwt.decode(token_str, JWT_SECRET, algorithms=["HS256"])
        except _jwt.ExpiredSignatureError:
            return web.Response(status=403, text="Token expired")
        except _jwt.InvalidTokenError:
            return web.Response(status=403, text="Invalid token")

        data    = await request.json()
        user_id = data.get("user_id")
        biz_id  = data.get("biz_id")

        if not user_id and not biz_id:
            return web.Response(status=400, text="Missing user_id or biz_id")

        if user_id:
            db.invalidate_user_cache(int(user_id))
            logger.info(f"[cache] Скинуто user кеш: user_id={user_id}")

        # ✅ FIX: скидаємо кеш по biz_id — потрібно після зміни delivery_mode
        if biz_id:
            db.invalidate_biz_cache(str(biz_id))
            logger.info(f"[cache] Скинуто biz кеш: biz_id={biz_id}")

        return web.json_response({"ok": True}, headers=CORS_HEADERS)

    except Exception as exc:
        logger.error(f"[cache] Помилка: {exc}")
        return web.Response(status=500, text="Internal Server Error")

# ---------------------------------------------------------------------------
# WEBHOOK: /api/take_order — кур'єр бере замовлення з карти (НЕ закриває WebApp)
# ---------------------------------------------------------------------------

async def api_take_order_handler(request: web.Request) -> web.Response:
    """
    POST /api/take_order
    Викликається з map.html — WebApp НЕ закривається.
    Делегує всю логіку в handlers.orders._process_take_order.

    Body JSON: { order_id, courier_id, courier_name, lang }
    Auth: Bearer JWT
    """
    try:
        import jwt as _jwt
        from keyboards import JWT_SECRET
        from handlers.orders import _build_order_text, _build_call_url, _build_uber_keyboard

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return web.json_response({"ok": False, "error": "Missing token"}, status=403, headers=CORS_HEADERS)
        token_str = auth_header[len("Bearer "):].strip()
        try:
            payload = _jwt.decode(token_str, JWT_SECRET, algorithms=["HS256"])
        except (_jwt.ExpiredSignatureError, _jwt.InvalidTokenError) as e:
            return web.json_response({"ok": False, "error": str(e)}, status=403, headers=CORS_HEADERS)

        data         = await request.json()
        order_id     = data.get("order_id")
        courier_id   = data.get("courier_id")
        courier_name = data.get("courier_name", "Кур'єр")
        lang         = data.get("lang", "en")

        if not order_id or not courier_id:
            return web.json_response({"ok": False, "error": "Missing order_id or courier_id"}, status=400, headers=CORS_HEADERS)

        if str(payload.get("user_id", "")) != str(courier_id):
            return web.json_response({"ok": False, "error": "Token user_id mismatch"}, status=403, headers=CORS_HEADERS)

        # ✅ Делегуємо в спільне ядро.
        # already_captured=True — map.html вже зробив UPDATE в Supabase перед цим викликом,
        # тому пропускаємо повторне захоплення і йдемо одразу до оновлення групи і розсилки.
        from handlers.orders import _process_take_order
        result = await _process_take_order(
            bot, order_id, int(courier_id), courier_name, lang,
            already_captured=True
        )

        if not result["ok"]:
            err = result["error"]
            status = 409 if err == "order_already_assigned" else (404 if "not_found" in err else 403)
            return web.json_response({"ok": False, "error": err}, status=status, headers=CORS_HEADERS)

        db.invalidate_user_cache(int(courier_id))
        logger.info(f"[api_take_order] Замовлення {order_id} взяв кур'єр {courier_id}")
        return web.json_response({"ok": True}, headers=CORS_HEADERS)

    except Exception as exc:
        logger.error(f"[api_take_order] Помилка: {exc}", exc_info=True)
        return web.json_response({"ok": False, "error": str(exc)}, status=500, headers=CORS_HEADERS)


# ---------------------------------------------------------------------------
# Реєстрація маршрутів та запуск сервера
# ---------------------------------------------------------------------------


async def broadcast_handler(request: web.Request) -> web.Response:
    """POST /api/broadcast — розсилка всім власникам (тільки для boss)."""
    # ✅ Inline JWT verification (same pattern as api_take_order_handler)
    try:
        from keyboards import JWT_SECRET
        import jwt as _jwt
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return web.json_response({"ok": False, "error": "Unauthorized"}, status=401, headers=CORS_HEADERS)
        token_str = auth_header[len("Bearer "):].strip()
        payload = _jwt.decode(token_str, JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=401, headers=CORS_HEADERS)

    # Перевіряємо що це boss (SUPER_ADMIN)
    from config import SUPER_ADMIN_IDS
    uid = payload.get("user_id")
    if not uid or int(uid) not in SUPER_ADMIN_IDS:
        return web.json_response({"ok": False, "error": "Forbidden"}, status=403, headers=CORS_HEADERS)

    try:
        data = await request.json()
        text = (data.get("text") or "").strip()
        if not text:
            return web.json_response({"ok": False, "error": "Empty message"}, status=400, headers=CORS_HEADERS)

        # Беремо всіх власників
        owners_res = await db._run(
            lambda: db.supabase.table("businesses").select("owner_id,lang").execute()
        )
        owners = owners_res.data or []

        import asyncio as _aio
        from texts import get_text as _
        sent, failed = 0, 0
        for biz in owners:
            owner_id = biz.get("owner_id")
            if not owner_id:
                continue
            lang = biz.get("lang") or "uk"
            msg = _(lang, "broadcast_msg", text=text)
            try:
                await bot.send_message(chat_id=int(owner_id), text=msg, parse_mode="HTML")
                sent += 1
            except Exception:
                failed += 1
            await _aio.sleep(0.05)  # ~20 msg/sec щоб не флудити

        logger.info(f"[broadcast] sent={sent} failed={failed}")
        return web.json_response({"ok": True, "sent": sent, "failed": failed}, headers=CORS_HEADERS)

    except Exception as e:
        logger.error(f"[broadcast] {e}")
        return web.json_response({"ok": False, "error": str(e)}, status=500, headers=CORS_HEADERS)


async def start_webhook_server() -> None:
    """Створює aiohttp-додаток, реєструє всі маршрути і запускає сервер."""
    app = web.Application(middlewares=[cors_middleware])

    # Службові
    app.router.add_get("/config",              config_handler)
    app.router.add_get("/health",              health_handler)
    app.router.add_options("/{path_info:.*}",  cors_preflight_handler)

    # Whop — підписки
    app.router.add_post("/webhook/whop",       whop_webhook_handler)

    # POS-системи
    app.router.add_post("/webhook/poster",     poster_webhook_handler)
    app.router.add_post("/webhook/choiceqr",   choiceqr_webhook_handler)
    app.router.add_post("/webhook/gopos",      gopos_webhook_handler)
    app.router.add_post("/webhook/syrve",      syrve_webhook_handler)
    app.router.add_post("/api/new_order",      api_new_order_handler)
    app.router.add_post("/api/take_order",     api_take_order_handler)
    app.router.add_post("/api/invalidate_cache", invalidate_cache_handler)
    app.router.add_post("/api/broadcast",        broadcast_handler)

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
