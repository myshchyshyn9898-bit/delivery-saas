import asyncio
import json
import logging
import urllib.parse
import os

from aiogram import Router, types, F, Bot
from aiogram.types import FSInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import SUPER_ADMIN_IDS, BASE_URL
import database as db
from texts import get_text as _
from handlers.map_service import get_route_map_file

logger = logging.getLogger(__name__)
router = Router()


# ===========================================================================
# ХЕЛПЕР: будує текст групового повідомлення (uber-режим)
# ===========================================================================

def _build_call_url(phone: str, biz: dict = None, lang: str = "en") -> tuple:
    """
    Повертає (btn_text, call_url) для кнопки дзвінка.
    - 8 цифр → Uber Call (набирає номер Uber + код)
    - інакше → звичайний дзвінок
    dispatcher береться з biz['uber_dispatcher'] або дефолтний польський
    """
    from texts import get_text as _gt
    base = BASE_URL.rstrip('/')
    digits_only = "".join(filter(str.isdigit, phone or ""))

    if len(digits_only) == 8:
        # Uber-код — потрібен номер диспетчера
        dispatcher = ""
        if biz and biz.get("uber_dispatcher"):
            dispatcher = str(biz["uber_dispatcher"]).strip()
        if dispatcher:
            call_url = f"{base}/call.html?code={urllib.parse.quote(digits_only)}&dispatcher={urllib.parse.quote(dispatcher)}"
        else:
            call_url = f"{base}/call.html?code={urllib.parse.quote(digits_only)}"
        return "🚖 Uber Call", call_url
    else:
        # Звичайний номер
        safe_phone = phone or ""
        call_url = f"{base}/call.html?code={urllib.parse.quote(safe_phone)}"
        return _gt(lang, 'btn_call_regular'), call_url


def _build_order_text(short_id, address, details_text, client_name,
                       phone, pay_type, amount, currency, comment,
                       status_line, source_label="", lang="en"):
    """
    Будує уніфікований текст замовлення для БУДЬ-ЯКОГО режиму.
    lang — мова для локалізації підписів полів.
    """
    from texts import get_text as _gt

    if pay_type == "cash":
        pay_line = _gt(lang, 'order_pay_cash_line', amount=amount, cur=currency)
    elif pay_type == "terminal":
        pay_line = _gt(lang, 'order_pay_terminal_line', amount=amount, cur=currency)
    else:
        pay_line = _gt(lang, 'order_pay_online_line')

    order_lbl  = _gt(lang, 'order_label')
    status_lbl = _gt(lang, 'order_status_lbl')
    addr_lbl   = _gt(lang, 'order_address_lbl')
    det_lbl    = _gt(lang, 'order_details_lbl')
    cli_lbl    = _gt(lang, 'order_client_lbl')
    tel_lbl    = _gt(lang, 'order_tel_lbl')
    com_lbl    = _gt(lang, 'order_comment_lbl')

    prefix = f"{source_label}\n" if source_label else ""
    txt = (
        f"{prefix}"
        f"📦 <b>{order_lbl} #{short_id}</b>\n"
        f"➖➖➖➖➖➖\n"
        f"<b>{status_lbl}:</b> {status_line}\n\n"
        f"📍 <b>{addr_lbl}:</b> {address}\n"
    )
    if details_text:
        txt += f"🏢 <b>{det_lbl}:</b> {details_text}\n"
    if client_name and client_name not in ("—", "Клієнт", "Client", "Klient", "Клиент", ""):
        txt += f"👤 <b>{cli_lbl}:</b> {client_name}\n"
    txt += f"📞 <b>{tel_lbl}:</b> {phone or '—'}\n"
    txt += f"{pay_line}\n"
    txt += "➖➖➖➖➖➖"
    if comment:
        txt += f"\n🗣 <b>{com_lbl}:</b> {comment}"
    return txt


# Зворотна сумісність — старе ім'я
def _build_uber_group_text(short_id, source_label, address, details_text,
                            client_name, phone, pay_icon, amount, currency,
                            pay_type_str, comment, status_line, lang="en"):
    # Визначаємо pay_type з pay_icon
    if pay_icon == "💵":
        pay_type = "cash"
    elif pay_icon == "💳" or pay_icon == "🏧":
        pay_type = "terminal"
    else:
        pay_type = "online"
    return _build_order_text(short_id, address, details_text, client_name,
                              phone, pay_type, amount, currency, comment,
                              status_line, source_label, lang)


def _build_uber_keyboard(order_id, route_url, phone, pay_type, amount, currency, state="pending", lang="en"):
    """
    Будує inline-клавіатуру для групового повідомлення:
    - state='pending'    → кнопка "Взяти замовлення"
    - state='delivering' → кнопки Маршрут + Подзвонити + Закрити (готівка/термінал/онлайн)
    - state='completed'  → порожня клавіатура (None)
    """
    if state == "completed":
        return None

    builder = InlineKeyboardBuilder()

    if state == "pending":
        builder.button(text=_(lang, 'btn_take_order'), callback_data=f"take_order_{order_id}")
        builder.adjust(1)
        return builder.as_markup()

    # delivering
    builder.button(text=_(lang, 'btn_route'), url=route_url)

    if phone:
        _btn_text, _call_url = _build_call_url(phone, lang=lang)
        builder.button(text=_btn_text, url=_call_url)

    if pay_type == "cash":
        builder.button(text=_(lang, 'btn_close_cash', amount=amount, cur=currency), callback_data=f"uber_close_cash_{order_id}")
    elif pay_type == "terminal":
        builder.button(text=_(lang, 'btn_close_terminal', amount=amount, cur=currency), callback_data=f"uber_close_terminal_{order_id}")
    else:
        builder.button(text=_(lang, 'btn_close_online'), callback_data=f"uber_close_online_{order_id}")

    builder.adjust(2, 1)
    return builder.as_markup()


async def _send_uber_group_message(bot: Bot, group_id, biz, order_id, short_id,
                                    address, details_text, client_name, phone,
                                    pay_icon, amount, currency, pay_type_str,
                                    pay_type, comment, source_label="", lang="en"):
    """
    Надсилає замовлення в групу кур'єрів (uber-режим) з картою або без.
    Повертає message_id надісланого повідомлення або None.
    """
    import html as _hug
    _e = _hug.escape
    status_line = _(lang, 'order_status_active')
    text = _build_order_text(
        short_id=short_id,
        address=_e(address),
        details_text=_e(details_text),
        client_name=_e(client_name) if client_name else "",
        phone=_e(phone) if phone else "—",
        pay_type=pay_type,
        amount=amount,
        currency=currency,
        comment=_e(comment) if comment else "",
        status_line=status_line,
        source_label=source_label,
        lang=lang
    )

    address_query = urllib.parse.quote(address)
    route_url = f"https://www.google.com/maps/dir/?api=1&destination={address_query}"
    kb = _build_uber_keyboard(order_id, route_url, phone, pay_type, amount, currency, state="pending", lang=lang)

    sent = None
    # ✅ ВИПРАВЛЕНО bug #5: map_filename тепер завжди видаляється через try/finally,
    # навіть якщо send_photo кидає виняток — файли більше не накопичуються на диску.
    map_filename = await get_route_map_file(biz, address, short_id)
    try:
        if map_filename and os.path.exists(map_filename):
            photo = FSInputFile(map_filename)
            try:
                sent = await bot.send_photo(
                    chat_id=group_id, photo=photo,
                    caption=text, reply_markup=kb, parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"[uber] Помилка send_photo в групу {group_id}: {e}")
                sent = await bot.send_message(
                    chat_id=group_id, text=text,
                    reply_markup=kb, parse_mode="HTML"
                )
        else:
            try:
                sent = await bot.send_message(
                    chat_id=group_id, text=text,
                    reply_markup=kb, parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"[uber] Критична помилка надсилання в групу: {e}")
    finally:
        # Видаляємо тимчасовий файл карти в будь-якому випадку
        if map_filename and os.path.exists(map_filename):
            try:
                os.remove(map_filename)
            except OSError as e:
                logger.warning(f"[uber] Не вдалось видалити map файл {map_filename}: {e}")

    # Зберігаємо message_id в БД щоб потім оновлювати повідомлення
    if sent:
        try:
            await db._run(
                lambda: db.supabase.table("orders")
                    .update({"group_message_id": sent.message_id})
                    .eq("id", order_id)
                    .execute()
            )
        except Exception as e:
            logger.error(f"[uber] Не вдалось зберегти group_message_id: {e}")

    return sent.message_id if sent else None


# ===========================================================================
# ОБРОБКА ДАНИХ З WEB APP
# ===========================================================================

@router.message(F.web_app_data)
async def handle_web_app_data(message: types.Message, bot: Bot):
    data = json.loads(message.web_app_data.data)
    user_id = message.from_user.id
    lang = message.from_user.language_code

    # ── РЕЄСТРАЦІЯ БІЗНЕСУ ──────────────────────────────────────────────────
    if data.get("action") == "register_business":
        try:
            # Перевіряємо чи вже є бізнес у цього власника
            existing_biz = await db.get_business_by_owner(user_id)
            if existing_biz:
                db.invalidate_user_cache(user_id)
                context = await db.get_user_context_cached(user_id)
                import keyboards as kb
                await message.answer(_(lang, 'biz_already_exists'), reply_markup=kb.get_owner_kb(existing_biz['id'], user_id, lang), parse_mode="Markdown")
                return
            await db.register_new_business(user_id, data)
            db.invalidate_user_cache(user_id)
            context = await db.get_user_context_cached(user_id)
            biz = context['biz']
            import keyboards as kb
            await message.answer(
                _(lang, 'biz_created', biz_name=biz['name'], plan=biz['plan'].upper()),
                reply_markup=kb.get_owner_kb(biz['id'], user_id, lang),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Помилка реєстрації бізнесу: {e}")
            await message.answer(_(lang, 'reg_error'))

    # ── НОВЕ ЗАМОВЛЕННЯ ──────────────────────────────────────────────────────
    elif data.get("action") == "new_order":
        try:
            biz_id = data['biz_id']

            # ✅ БЕЗПЕКА: перевіряємо що відправник є частиною цього бізнесу
            ctx = await db.get_user_context_cached(user_id)
            if not ctx or ctx['role'] not in ('owner', 'manager') or str(ctx['biz']['id']) != str(biz_id):
                logger.warning(f"[new_order] Несанкціонований доступ: user={user_id} biz={biz_id}")
                await message.answer(_(lang, 'no_access'))
                return

            actual_plan = await db.get_actual_plan(biz_id)
            if actual_plan == "expired":
                await message.answer(_(lang, 'expired_no_orders'))
                return

            original_courier_id = data.get('courier_id')
            if original_courier_id == "unassigned":
                data['courier_id'] = None

            new_order = await db.create_new_order(data)
            if not new_order:
                await message.answer(_(lang, 'order_save_err'))
                return

            order_id = new_order['id']
            short_id = str(order_id)[:6].upper()
            biz = await db.get_business_by_id(biz_id)
            currency = biz.get('currency', 'zł')
            is_pro = actual_plan in ['pro', 'trial']
            delivery_mode = biz.get('delivery_mode', 'dispatcher')

            pay_type = data['payment']
            pay_type_str = _(lang, 'pay_' + pay_type)
            pay_icon = "💵" if pay_type == "cash" else ("💳" if pay_type == "terminal" else "🌐")

            details_parts = []
            if data.get('apt'):
                details_parts.append(_(lang, 'apt_prefix', apt=data['apt']))
            if data.get('code'):
                details_parts.append(_(lang, 'code_prefix', code=data['code']))
            details_text = _(lang, 'details_prefix', details=', '.join(details_parts)) if details_parts else ""

            address = data['address']
            address_query = urllib.parse.quote(address)
            route_url = f"https://www.google.com/maps/dir/?api=1&destination={address_query}"

            phone_raw = data.get('client_phone', '') or ''
            phone_clean = "".join(filter(lambda x: x.isdigit() or x == '+', phone_raw))
            if phone_clean and not phone_clean.startswith('+'):
                phone_clean = '+' + phone_clean

            amount = data['amount']
            comment = data.get('comment', '')
            client_name = data.get('client_name', '—')

            # ── UBER MODE ────────────────────────────────────────────────────
            if original_courier_id == "unassigned" and delivery_mode == 'uber':
                group_id = biz.get('courier_group_id')
                if group_id:
                    await _send_uber_group_message(
                        bot=bot, group_id=group_id, biz=biz,
                        order_id=order_id, short_id=short_id,
                        address=address, details_text=details_text,
                        client_name=client_name, phone=phone_clean,
                        pay_icon=pay_icon, amount=amount, currency=currency,
                        pay_type_str=pay_type_str, pay_type=pay_type,
                        comment=comment, lang=lang
                    )
                    logger.info(f"[uber webapp] Замовлення {order_id} кинуто в групу {group_id}")
                else:
                    logger.warning(f"Uber mode, але courier_group_id не задано для biz_id={biz_id}")

            # ── DISPATCHER MODE + UNASSIGNED: повідомляємо менеджерів вручну призначити ──
            elif original_courier_id == "unassigned" and delivery_mode == 'dispatcher':
                managers_res = await db._run(
                    lambda: db.supabase.table('staff').select('user_id')
                        .eq('business_id', biz_id).eq('role', 'manager').execute()
                )
                recipients = [int(m['user_id']) for m in managers_res.data] if managers_res.data else []
                if not recipients and biz.get('owner_id'):
                    recipients = [int(biz['owner_id'])]
                import html as _hfree
                free_text = _(lang, 'free_order_manager_notify',
                    short_id=short_id,
                    address=_hfree.escape(address),
                    client_name=_hfree.escape(client_name),
                    phone=_hfree.escape(phone_clean or '—'),
                    amount=amount, cur=currency
                )
                for uid in recipients:
                    try:
                        await bot.send_message(chat_id=uid, text=free_text, parse_mode='HTML')
                    except Exception as e:
                        logger.error(f'Помилка нотифікації менеджера {uid}: {e}')

            # ── DISPATCHER MODE ──────────────────────────────────────────────
            elif original_courier_id != "unassigned":
                import html as _hd
                courier_text = _build_order_text(
                    short_id=short_id,
                    address=_hd.escape(address),
                    details_text=_hd.escape(details_text),
                    client_name=_hd.escape(client_name),
                    phone=_hd.escape(phone_clean),
                    pay_type=pay_type,
                    amount=amount,
                    currency=currency,
                    comment=_hd.escape(comment),
                    status_line=_(lang, 'order_status_active_short'),
                    lang=lang
                )

                builder = InlineKeyboardBuilder()
                if is_pro:
                    builder.button(text=_(lang, 'btn_route'), url=route_url)
                if phone_clean:
                    _btn_t, _call_u = _build_call_url(phone_clean, biz, lang=lang)
                    builder.button(text=_btn_t, url=_call_u)
                if pay_type == "online":
                    builder.button(text=_(lang, 'btn_close_online'), callback_data=f"dispatcher_close_online_{order_id}")
                else:
                    builder.button(text=_(lang, 'btn_close_cash', amount=amount, cur=currency), callback_data=f"dispatcher_close_cash_{order_id}")
                    builder.button(text=_(lang, 'btn_close_terminal', amount=amount, cur=currency), callback_data=f"dispatcher_close_terminal_{order_id}")
                builder.adjust(1)

                if is_pro:
                    # ✅ ВИПРАВЛЕНО bug #5: try/finally гарантує видалення map файлу
                    map_filename = await get_route_map_file(biz, address, short_id)
                    try:
                        if map_filename and os.path.exists(map_filename):
                            photo = FSInputFile(map_filename)
                            await bot.send_photo(
                                chat_id=data['courier_id'], photo=photo,
                                caption=courier_text, reply_markup=builder.as_markup(),
                                parse_mode="HTML"
                            )
                        else:
                            await bot.send_message(
                                chat_id=data['courier_id'], text=courier_text,
                                reply_markup=builder.as_markup(), parse_mode="HTML"
                            )
                    finally:
                        if map_filename and os.path.exists(map_filename):
                            try:
                                os.remove(map_filename)
                            except OSError as e:
                                logger.warning(f"[dispatcher] Не вдалось видалити map файл: {e}")
                else:
                    await bot.send_message(
                        chat_id=data['courier_id'], text=courier_text,
                        reply_markup=builder.as_markup(), parse_mode="HTML"
                    )

            tracking_link = f"{BASE_URL}track.html?id={order_id}"
            if original_courier_id == "unassigned":
                await message.answer(_(lang, 'free_order_created', short_id=short_id, tracking_link=tracking_link), parse_mode="Markdown")
            else:
                admin_text = _(lang, 'order_sent', short_id=short_id)
                await message.answer(f"{admin_text}\n\n" + _(lang, 'tracking_link_label', tracking_link=tracking_link), parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Помилка створення замовлення: {e}")
            await message.answer(_(lang, 'order_send_err'))

    # ── ПРИЗНАЧЕННЯ ЗАМОВЛЕННЯ З КАРТИ (dispatcher) ──────────────────────────
    elif data.get("action") == "assign_order":
        try:
            order_id = data['order_id']
            courier_id = data['courier_id']

            res = await db._run(lambda: db.supabase.table("orders").select("*").eq("id", order_id).execute())
            if not res.data:
                return
            order_db = res.data[0]

            # ✅ БЕЗПЕКА: перевіряємо що відправник є owner/manager саме цього бізнесу
            ctx = await db.get_user_context_cached(user_id)
            if not ctx or ctx['role'] not in ('owner', 'manager') or str(ctx['biz']['id']) != str(order_db['business_id']):
                logger.warning(f"[assign_order] Несанкціонований доступ: user={user_id} order={order_id}")
                await message.answer(_(lang, 'no_access'))
                return

            # Перевіряємо що замовлення ще не призначено (немає кур'єра і статус pending)
            if order_db.get('status') not in ('pending', None) or order_db.get('courier_id'):
                await message.answer(_(lang, 'order_already_assigned'))
                return

            # ✅ ВИПРАВЛЕНО bug #7: перевіряємо що courier_id існує в staff цього бізнесу
            biz_id = order_db['business_id']
            courier_check = await db._run(
                lambda: db.supabase.table('staff')
                    .select('user_id, role')
                    .eq('user_id', courier_id)
                    .eq('business_id', biz_id)
                    .execute()
            )
            if not courier_check.data:
                logger.warning(f"[assign_order] courier_id={courier_id} не в staff biz={biz_id}")
                await message.answer(_(lang, 'courier_not_in_staff'))
                return

            # Оновлюємо статус і кур'єра в БД
            await db._run(
                lambda: db.supabase.table('orders')
                    .update({'courier_id': courier_id, 'status': 'delivering'})
                    .eq('id', order_id)
                    .execute()
            )

            short_id = str(order_id)[:6].upper()
            biz = await db.get_business_by_id(biz_id)
            currency = biz.get('currency', 'zł')
            is_pro = await db.get_actual_plan(biz_id) in ['pro', 'trial']

            pay_type = order_db['pay_type']
            pay_type_str = _(lang, 'pay_' + pay_type)
            pay_icon = "💵" if pay_type == "cash" else ("💳" if pay_type == "terminal" else "🌐")

            address = order_db['address']
            address_query = urllib.parse.quote(address)
            route_url = f"https://www.google.com/maps/dir/?api=1&destination={address_query}"

            import html as _ha
            courier_text = _build_order_text(
                short_id=short_id,
                address=_ha.escape(address),
                details_text=_ha.escape(order_db.get("details", "") or ""),
                client_name=_ha.escape(order_db.get("client_name", "") or ""),
                phone=_ha.escape(order_db.get("client_phone", "—") or "—"),
                pay_type=pay_type,
                amount=order_db["amount"],
                currency=currency,
                comment=_ha.escape(order_db.get("comment", "") or ""),
                status_line=_(lang, 'order_status_active_short'),
                lang=lang
            )

            builder = InlineKeyboardBuilder()
            if is_pro:
                builder.button(text=_(lang, 'btn_route'), url=route_url)
            phone_for_call = order_db.get('client_phone', '')
            if phone_for_call:
                _btn_a, _call_a = _build_call_url(phone_for_call, biz, lang=lang)
                builder.button(text=_btn_a, url=_call_a)
            if pay_type == "online":
                builder.button(text=_(lang, 'btn_close_online'), callback_data=f"dispatcher_close_online_{order_id}")
            else:
                builder.button(text=_(lang, 'btn_close_cash', amount=order_db['amount'], cur=currency), callback_data=f"dispatcher_close_cash_{order_id}")
                builder.button(text=_(lang, 'btn_close_terminal', amount=order_db['amount'], cur=currency), callback_data=f"dispatcher_close_terminal_{order_id}")
            builder.adjust(1)

            if is_pro:
                # ✅ ВИПРАВЛЕНО bug #5: try/finally гарантує видалення map файлу
                map_filename = await get_route_map_file(biz, address, short_id)
                try:
                    if map_filename and os.path.exists(map_filename):
                        photo = FSInputFile(map_filename)
                        await bot.send_photo(
                            chat_id=courier_id, photo=photo,
                            caption=courier_text, reply_markup=builder.as_markup(),
                            parse_mode="HTML"
                        )
                    else:
                        await bot.send_message(
                            chat_id=courier_id, text=courier_text,
                            reply_markup=builder.as_markup(), parse_mode="HTML"
                        )
                finally:
                    if map_filename and os.path.exists(map_filename):
                        try:
                            os.remove(map_filename)
                        except OSError as e:
                            logger.warning(f"[assign_order] Не вдалось видалити map файл: {e}")
            else:
                await bot.send_message(
                    chat_id=courier_id, text=courier_text,
                    reply_markup=builder.as_markup(), parse_mode="HTML"
                )

            await message.answer(_(lang, 'order_assigned_success', short_id=short_id))

        except Exception as e:
            logger.error(f"Помилка призначення з карти: {e}")
            await message.answer(_(lang, 'order_assign_error'))

    # ── РОЗСИЛКА (super admin) ───────────────────────────────────────────────
    elif data.get("action") == "broadcast":
        if user_id not in SUPER_ADMIN_IDS:
            return
        msg_text = data.get("text", "")
        # Екрануємо спецсимволи Markdown щоб не зламати форматування
        import re as _re
        msg_text_safe = _re.sub(r'([_*`\[])', r'\\\1', msg_text)
        businesses = await db.get_all_businesses()
        owner_ids = set([int(b['owner_id']) for b in businesses if b.get('owner_id')])
        if not owner_ids:
            return
        await message.answer(_(lang, 'broadcast_start', count=len(owner_ids)))
        sent_count = 0
        for oid in owner_ids:
            try:
                await bot.send_message(chat_id=oid, text=_(lang, 'broadcast_msg', text=msg_text_safe), parse_mode="Markdown")
                sent_count += 1
                await asyncio.sleep(0.1)  # ✅ ВИПРАВЛЕНО: 10 msg/sec щоб не впертись в Telegram rate limit
            except Exception as e:
                logger.error(f"Помилка розсилки користувачу {oid}: {e}")
        await message.answer(_(lang, 'broadcast_done', sent=sent_count, total=len(owner_ids)))

    # ── ВЗЯТТЯ ЗАМОВЛЕННЯ З КАРТИ (uber) ─────────────────────────────────────
    elif data.get("action") == "take_order_from_map":
        try:
            order_id = data.get('order_id')
            if not order_id:
                return

            # Читаємо замовлення
            res = await db._run(
                lambda: db.supabase.table("orders").select("*").eq("id", order_id).execute()
            )
            if not res.data:
                return
            order = res.data[0]

            biz_id = order["business_id"]
            biz = await db.get_business_by_id(biz_id)
            if not biz:
                return

            group_id = biz.get("courier_group_id")
            if not group_id:
                return

            currency = biz.get("currency", "zł")
            short_id = str(order_id)[:6].upper()
            pay_type = order.get("pay_type", "cash")
            amount = order.get("amount", "0")

            # Ім'я кур'єра що взяв
            taker_name = message.from_user.full_name
            import html as _hmap
            safe_name = _hmap.escape(taker_name)
            status_line = _(lang, 'order_status_delivering', courier=safe_name)

            # Будуємо оновлений текст
            import urllib.parse as _ul
            safe_address = _hmap.escape(order.get("address", "—"))
            safe_details = _hmap.escape(order.get("details", "") or "")
            safe_client = _hmap.escape(order.get("client_name", "") or "")
            safe_phone = _hmap.escape(order.get("client_phone", "—") or "—")
            safe_comment = _hmap.escape(order.get("comment", "") or "")

            updated_text = _build_order_text(
                short_id=short_id,
                address=safe_address,
                details_text=safe_details,
                client_name=safe_client,
                phone=safe_phone,
                pay_type=pay_type,
                amount=amount,
                currency=currency,
                comment=safe_comment,
                status_line=status_line,
                lang=lang
            )

            # Кнопки для кур'єра що взяв
            route_url = f"https://www.google.com/maps/dir/?api=1&destination={_ul.quote(order.get('address', ''))}"
            raw_phone = order.get("client_phone", "") or ""
            builder = InlineKeyboardBuilder()
            builder.button(text=_(lang, 'btn_route'), url=route_url)
            if raw_phone:
                _bt, _cu = _build_call_url(raw_phone, biz, lang=lang)
                builder.button(text=_bt, url=_cu)
            if pay_type == "online":
                builder.button(text=_(lang, 'btn_close_online'), callback_data=f"uber_close_online_{order_id}")
            else:
                builder.button(text=_(lang, 'btn_close_cash', amount=amount, cur=currency), callback_data=f"uber_close_cash_{order_id}")
                builder.button(text=_(lang, 'btn_close_terminal', amount=amount, cur=currency), callback_data=f"uber_close_terminal_{order_id}")
            builder.adjust(2, 2) if raw_phone and pay_type != "online" else builder.adjust(2, 1) if raw_phone else builder.adjust(1)

            # Відправляємо кур'єру ОСОБИСТЕ повідомлення з кнопками
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=updated_text,
                    reply_markup=builder.as_markup(),
                    parse_mode="HTML"
                )
            except Exception as ep:
                logger.error(f"[take_order_from_map] Не вдалось відправити особисте: {ep}")

            # Оновлюємо повідомлення в ГРУПІ
            if group_id:
                grp_res = await db._run(
                    lambda: db.supabase.table("orders")
                        .select("group_message_id")
                        .eq("id", order_id)
                        .execute()
                )
                group_msg_id = grp_res.data[0].get("group_message_id") if grp_res.data else None

                if group_msg_id:
                    try:
                        await bot.edit_message_caption(
                            chat_id=group_id, message_id=group_msg_id,
                            caption=updated_text, reply_markup=builder.as_markup(), parse_mode="HTML"
                        )
                    except Exception:
                        try:
                            await bot.edit_message_text(
                                chat_id=group_id, message_id=group_msg_id,
                                text=updated_text, reply_markup=builder.as_markup(), parse_mode="HTML"
                            )
                        except Exception as eg:
                            logger.warning(f"[take_order_from_map] Не вдалось оновити group msg: {eg}")

        except Exception as e:
            logger.error(f"[take_order_from_map] Помилка: {e}")

    # ── ТІКЕТ ПІДТРИМКИ ──────────────────────────────────────────────────────
    elif data.get("action") == "support_ticket":
        try:
            import html as _esc
            biz_id = data.get("biz_id", "?")
            # ✅ ВИПРАВЛЕНО: екрануємо всі поля від юзера перед вставкою в HTML
            reason  = _esc.escape(str(data.get('reason',  '?')))
            topic   = _esc.escape(str(data.get('topic',   '?')))
            msg_txt = _esc.escape(str(data.get('message', '?')))
            # Адмінське повідомлення завжди англійською (для підтримки)
            admin_msg = _(  'en', 'ticket_admin_msg',
                biz_id=biz_id, user_id=user_id,
                reason=reason, topic=topic, message=msg_txt
            )
            for admin_id in SUPER_ADMIN_IDS:
                try:
                    await bot.send_message(chat_id=admin_id, text=admin_msg, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Помилка відправки тікету адміну {admin_id}: {e}")
            await message.answer(_(lang, 'ticket_sent'), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Помилка обробки тікету: {e}")


# ===========================================================================
# DISPATCHER MODE: кнопка "Завершити замовлення" (особисте повідомлення)
# ✅ ВИПРАВЛЕНО: finish_order_ більше не генерується ніде в коді.
# Цей обробник залишений як запасний alias → перенаправляє на dispatcher_close_cash_
# щоб не «загубити» старі повідомлення якщо вони ще є у кур'єрів.
# ===========================================================================

@router.callback_query(F.data.startswith("finish_order_"))
async def finish_order_handler(callback: types.CallbackQuery, bot: Bot):
    """
    Застарілий callback — редіректить на dispatcher_close_cash_.
    Нові повідомлення використовують dispatcher_close_{pay_type}_{order_id}.
    """
    order_id = callback.data.replace("finish_order_", "")
    # Перенаправляємо як натискання кнопки «Готівка» (найпоширеніший варіант)
    callback.data = f"dispatcher_close_cash_{order_id}"
    await dispatcher_close_handler(callback, bot)


# ===========================================================================
# UBER MODE: кнопка "Взяти замовлення" в групі
# ===========================================================================

@router.callback_query(F.data.startswith("take_order_"))
async def take_order_handler(callback: types.CallbackQuery, bot: Bot):
    import html as _html
    import urllib.parse as _ul

    # 1. Одразу відповідаємо Telegram — кнопка перестає крутитись
    try:
        await callback.answer("⏳...", show_alert=False)
    except Exception:
        pass

    order_id  = callback.data.replace("take_order_", "")
    taker_id  = callback.from_user.id
    taker_name = callback.from_user.full_name
    lang = callback.from_user.language_code or "en"

    try:
        # 2. Свіжі дані з БД
        res = await db._run(
            lambda: db.supabase.table("orders").select("*").eq("id", order_id).execute()
        )
        if not res.data:
            await callback.message.answer(_(lang, 'order_not_found'))
            return

        order = res.data[0]

        # 3. Перевірка що ще pending
        if order["status"] != "pending":
            await callback.message.answer(
                f"⚡️ #{str(order_id)[:6].upper()} — {_(lang, 'order_already_assigned')}"
            )
            return

        # 4. Перевіряємо чи кур'єр є в staff
        staff_check = await db._run(
            lambda: db.supabase.table("staff")
                .select("user_id, business_id")
                .eq("user_id", taker_id)
                .eq("business_id", order["business_id"])
                .execute()
        )
        if not staff_check.data:
            await callback.message.answer(_(lang, 'courier_not_in_staff'))
            return

        # 5. Атомарне захоплення: UPDATE тільки якщо статус ще 'pending'
        # Supabase повертає оновлені рядки — якщо список порожній, хтось встиг раніше
        take_res = await db._run(
            lambda: db.supabase.table("orders")
                .update({"status": "delivering", "courier_id": taker_id})
                .eq("id", order_id)
                .eq("status", "pending")   # ← умовний UPDATE (pseudo-atomic)
                .execute()
        )

        # ✅ ВИПРАВЛЕНО race condition: перевіряємо по courier_id що саме ми захопили.
        # Якщо take_res.data порожній — UPDATE не відпрацював (інший кур'єр встиг першим).
        # Додатково читаємо свіжий стан щоб не покладатись на порожній список від supabase-py.
        if not take_res.data:
            verify = await db._run(
                lambda: db.supabase.table("orders")
                    .select("courier_id, status")
                    .eq("id", order_id)
                    .execute()
            )
            if not verify.data or str(verify.data[0].get("courier_id")) != str(taker_id):
                await callback.message.answer(f"⚡️ {_(lang, 'order_already_assigned')}")
                return

        # 6. Будуємо текст З НУЛЯ — стиль як в старому боті
        import html as _h
        import datetime as _dt

        short_id  = str(order_id)[:6].upper()
        biz_id    = order["business_id"]
        biz       = await db.get_business_by_id(biz_id)
        currency  = biz.get("currency", "zł") if biz else "zł"

        pay_type  = order.get("pay_type", "cash")
        amount    = order.get("amount", "0")
        address   = _h.escape(order.get("address", "—"))
        details   = _h.escape(order.get("details", "") or "")
        client    = _h.escape(order.get("client_name", "") or "")
        phone     = _h.escape(order.get("client_phone", "—") or "—")
        comment   = _h.escape(order.get("comment", "") or "")
        safe_name = _h.escape(taker_name)
        status_line = _(lang, 'order_status_delivering', courier=safe_name)

        text = _build_order_text(short_id, address, details, client,
                                  phone, pay_type, amount, currency,
                                  comment, status_line, lang=lang)

        if len(text) > 1024:
            text = text[:1020] + "..."

        # 7. Кнопки: Маршрут + Подзвонити + Готівка/Термінал (обидві) або Онлайн
        builder = InlineKeyboardBuilder()
        route_url = f"https://www.google.com/maps/dir/?api=1&destination={_ul.quote(order.get('address', ''))}"
        raw_phone = order.get("client_phone", "") or ""
        amount    = order.get("amount", "0")

        builder.button(text=_(lang, 'btn_route'), url=route_url)
        if raw_phone:
            _btn_tk, _call_tk = _build_call_url(raw_phone, biz, lang=lang)
            builder.button(text=_btn_tk, url=_call_tk)

        if pay_type == "online":
            builder.button(text=_(lang, 'btn_close_online'), callback_data=f"uber_close_online_{order_id}")
        else:
            builder.button(text=_(lang, 'btn_close_cash', amount=amount, cur=currency), callback_data=f"uber_close_cash_{order_id}")
            builder.button(text=_(lang, 'btn_close_terminal', amount=amount, cur=currency), callback_data=f"uber_close_terminal_{order_id}")

        if pay_type == "online":
            builder.adjust(2, 1) if raw_phone else builder.adjust(1)
        else:
            # Маршрут+Телефон в першому рядку, Готівка+Термінал в другому
            builder.adjust(2, 2) if raw_phone else builder.adjust(1, 2)

        # 8. Оновлюємо ПОТОЧНЕ повідомлення (в групі — якщо натиснули кнопку в групі)
        try:
            if callback.message.caption is not None:
                await callback.message.edit_caption(
                    caption=text, reply_markup=builder.as_markup(), parse_mode="HTML"
                )
            else:
                await callback.message.edit_text(
                    text=text, reply_markup=builder.as_markup(), parse_mode="HTML"
                )
        except Exception as edit_err:
            logger.warning(f"[take_order] edit поточного повідомлення: {edit_err}")

        # 9. Якщо замовлення взяли з КАРТИ — треба також оновити повідомлення в групі
        # (group_message_id зберігається в БД при відправці в групу)
        try:
            fresh_order = await db._run(
                lambda: db.supabase.table("orders")
                    .select("group_message_id, courier_group_id")
                    .eq("id", order_id)
                    .execute()
            )
            if fresh_order.data:
                group_msg_id = fresh_order.data[0].get("group_message_id")
                group_chat_id = biz.get("courier_group_id") if biz else None

                # Якщо це повідомлення в групі (не те, яке ми вже редагували)
                if group_msg_id and group_chat_id and str(callback.message.chat.id) != str(group_chat_id):
                    try:
                        await bot.edit_message_caption(
                            chat_id=group_chat_id,
                            message_id=group_msg_id,
                            caption=text,
                            reply_markup=builder.as_markup(),
                            parse_mode="HTML"
                        )
                    except Exception:
                        # Можливо це текстове повідомлення (без фото)
                        try:
                            await bot.edit_message_text(
                                chat_id=group_chat_id,
                                message_id=group_msg_id,
                                text=text,
                                reply_markup=builder.as_markup(),
                                parse_mode="HTML"
                            )
                        except Exception as eg:
                            logger.warning(f"[take_order] Не вдалось оновити group msg: {eg}")
        except Exception as eg2:
            logger.warning(f"[take_order] Помилка оновлення group msg: {eg2}")

    except Exception as e:
        logger.error(f"КРИТИЧНА ПОМИЛКА take_order {order_id}: {e}")
        await callback.message.answer(_(lang, 'generic_error', error=str(e)))

# ===========================================================================
# DISPATCHER MODE: закриття замовлення з типом оплати
# ===========================================================================

@router.callback_query(F.data.startswith("dispatcher_close_"))
async def dispatcher_close_handler(callback: types.CallbackQuery, bot: Bot):
    """
    Формат: dispatcher_close_{pay_type}_{order_id}
    pay_type: cash | terminal | online
    """
    import html as _html

    lang = callback.from_user.language_code or "en"

    try:
        await callback.answer("✅...", show_alert=False)
    except Exception:
        pass

    parts = callback.data.replace("dispatcher_close_", "").split("_", 1)
    if len(parts) != 2:
        await callback.message.answer(_(lang, 'order_format_error'))
        return

    pay_type_closed, order_id = parts[0], parts[1]
    courier_name = callback.from_user.full_name

    try:
        res = await db._run(
            lambda: db.supabase.table("orders").select("*").eq("id", order_id).execute()
        )
        if not res.data:
            await callback.message.answer(_(lang, 'order_not_found'))
            return

        order = res.data[0]

        if order["status"] == "completed":
            await callback.message.answer(_(lang, 'order_already_closed'))
            return

        # ✅ ВИПРАВЛЕНО: блокуємо і якщо courier_id є None (замовлення без кур'єра не можна закрити кнопкою)
        if not order.get("courier_id") or str(order["courier_id"]) != str(callback.from_user.id):
            await callback.message.answer(_(lang, 'order_wrong_courier'))
            return

        # Закриваємо в БД з реальним типом оплати який натиснув кур'єр
        await db.update_order_status(order_id, "completed", actual_pay_type=pay_type_closed)

        short_id = str(order_id)[:6].upper()
        biz_id   = order["business_id"]
        biz      = await db.get_business_by_id(biz_id)
        currency = biz.get("currency", "zł") if biz else "zł"

        pay_type     = pay_type_closed  # Реальний тип що натиснув кур'єр
        pay_icon     = "💵" if pay_type == "cash" else ("💳" if pay_type == "terminal" else "🌐")
        pay_type_str = _(lang, 'pay_' + pay_type)

        safe_courier = _html.escape(courier_name)

        # Будуємо фінальний текст — стиль старого боту
        import datetime as _dt2
        from config import BUSINESS_TZ
        time_str2    = _dt2.datetime.now(BUSINESS_TZ).strftime("%H:%M")
        pay_icon_d   = "💵" if pay_type == "cash" else ("🏧" if pay_type == "terminal" else "✅")
        status_line2 = _(lang, 'order_status_closed', time=time_str2, courier=safe_courier, pay_icon=pay_icon_d)

        import html as _hdc
        safe_addr2    = _hdc.escape(order.get("address", "—"))
        safe_client2  = _hdc.escape(order.get("client_name", "") or "")
        safe_phone2   = _hdc.escape(order.get("client_phone", "—") or "—")
        safe_comment2 = _hdc.escape(order.get("comment", "") or "")
        details2      = _hdc.escape(order.get("details", "") or "")

        done_text = _build_order_text(short_id, safe_addr2, details2,
                                       safe_client2, safe_phone2,
                                       pay_type, order.get("amount", "0"),
                                       currency, safe_comment2, status_line2,
                                       lang=lang)
        if len(done_text) > 1024:
            done_text = done_text[:1020] + "..."

        try:
            if callback.message.caption is not None:
                await callback.message.edit_caption(caption=done_text, reply_markup=None, parse_mode="HTML")
            else:
                await callback.message.edit_text(text=done_text, reply_markup=None, parse_mode="HTML")
        except Exception as e:
            logger.error(f"[dispatcher_close] edit поточного повідомлення: {e}")

        # Оновлюємо групове повідомлення якщо є (uber-режим або взяли через карту)
        try:
            group_msg_id = order.get("group_message_id")
            group_chat_id = biz.get("courier_group_id") if biz else None
            if group_msg_id and group_chat_id and str(callback.message.chat.id) != str(group_chat_id):
                try:
                    await bot.edit_message_caption(
                        chat_id=group_chat_id, message_id=group_msg_id,
                        caption=done_text, reply_markup=None, parse_mode="HTML"
                    )
                except Exception:
                    try:
                        await bot.edit_message_text(
                            chat_id=group_chat_id, message_id=group_msg_id,
                            text=done_text, reply_markup=None, parse_mode="HTML"
                        )
                    except Exception as eg:
                        logger.warning(f"[dispatcher_close] Не вдалось оновити group msg: {eg}")
        except Exception as eg2:
            logger.warning(f"[dispatcher_close] Помилка оновлення group msg: {eg2}")

        # Нотифікація адміну/менеджеру
        _safe_cn = courier_name.translate(str.maketrans({'_': r'\_', '*': r'\*', '`': r'\`'}))
        notify_text = _(lang, "finish_notify",
                        short_id=short_id,
                        amount=order.get("amount", "0"),
                        cur=currency,
                        courier_name=_safe_cn)

        managers_res = await db._run(
            lambda: db.supabase.table("staff").select("user_id")
                .eq("business_id", biz_id).eq("role", "manager").execute()
        )
        notify_ids = [int(m["user_id"]) for m in managers_res.data] if managers_res.data else []
        if not notify_ids and biz and biz.get("owner_id"):
            notify_ids = [int(biz["owner_id"])]

        for uid in notify_ids:
            try:
                await bot.send_message(chat_id=uid, text=notify_text, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Помилка нотифікації {uid}: {e}")

    except Exception as e:
        logger.error(f"КРИТИЧНА ПОМИЛКА dispatcher_close {order_id}: {e}")
        await callback.message.answer(_(lang, 'generic_error', error=str(e)))


@router.callback_query(F.data.startswith("uber_close_"))
async def uber_close_handler(callback: types.CallbackQuery, bot: Bot):
    """
    Формат callback_data: uber_close_{pay_type}_{order_id}
    pay_type: cash | terminal | online
    """
    import html as _html

    # 1. Одразу відповідаємо Telegram
    lang = callback.from_user.language_code or "en"
    try:
        await callback.answer(_(lang, 'closing_order'), show_alert=False)
    except Exception:
        pass

    parts = callback.data.replace("uber_close_", "").split("_", 1)
    if len(parts) != 2:
        await callback.message.answer(_(lang, 'order_format_error'))
        return

    pay_type_closed, order_id = parts[0], parts[1]
    courier_name = callback.from_user.full_name

    try:
        # 2. Читаємо замовлення
        res = await db._run(
            lambda: db.supabase.table("orders").select("*").eq("id", order_id).execute()
        )
        if not res.data:
            await callback.message.answer(_(lang, 'order_not_found'))
            return

        order = res.data[0]

        # 3. Перевірка що закриває той хто взяв
        if not order.get("courier_id") or str(order["courier_id"]) != str(callback.from_user.id):
            await callback.message.answer(_(lang, 'order_wrong_courier'))
            return

        if order["status"] == "completed":
            await callback.message.answer(_(lang, 'order_already_closed'))
            return

        # 4. Закриваємо в БД з реальним типом оплати який натиснув кур'єр
        await db.update_order_status(order_id, "completed", actual_pay_type=pay_type_closed)

        short_id = str(order_id)[:6].upper()
        biz_id   = order["business_id"]
        biz      = await db.get_business_by_id(biz_id)
        currency = biz.get("currency", "zł") if biz else "zł"

        pay_type     = pay_type_closed  # Використовуємо реальний тип що натиснув кур'єр
        pay_icon     = "💵" if pay_type == "cash" else ("💳" if pay_type == "terminal" else "🌐")
        pay_type_str = _(lang, 'pay_' + pay_type)

        safe_courier = _html.escape(courier_name)
        safe_address = _html.escape(order.get("address", "—"))
        safe_client  = _html.escape(order.get("client_name", "—") or "—")
        safe_phone   = _html.escape(order.get("client_phone", "—") or "—")
        safe_comment = _html.escape(order.get("comment", "") or "")

        # 5. Будуємо фінальний текст — стиль старого боту
        import datetime as _dt
        from config import BUSINESS_TZ
        time_str    = _dt.datetime.now(BUSINESS_TZ).strftime("%H:%M")
        pay_icon_cl = "💵" if pay_type == "cash" else ("🏧" if pay_type == "terminal" else "✅")
        status_line = _(lang, 'order_status_closed', time=time_str, courier=safe_courier, pay_icon=pay_icon_cl)

        safe_details = _html.escape(order.get("details", "") or "")
        final_text = _build_order_text(short_id, safe_address, safe_details,
                                        safe_client, safe_phone,
                                        pay_type, order.get("amount", "0"),
                                        currency, safe_comment, status_line,
                                        lang=lang)

        if len(final_text) > 1024:
            final_text = final_text[:1020] + "..."

        # 6. Оновлюємо ПОТОЧНЕ повідомлення — прибираємо кнопки
        try:
            if callback.message.caption is not None:
                await callback.message.edit_caption(
                    caption=final_text, reply_markup=None, parse_mode="HTML"
                )
            else:
                await callback.message.edit_text(
                    text=final_text, reply_markup=None, parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"[uber_close] edit поточного повідомлення: {e}")

        # 6б. Якщо кур'єр закривав з особистого повідомлення — оновлюємо також групове
        try:
            group_msg_id = order.get("group_message_id")
            group_chat_id = biz.get("courier_group_id") if biz else None
            if group_msg_id and group_chat_id and str(callback.message.chat.id) != str(group_chat_id):
                try:
                    await bot.edit_message_caption(
                        chat_id=group_chat_id, message_id=group_msg_id,
                        caption=final_text, reply_markup=None, parse_mode="HTML"
                    )
                except Exception:
                    try:
                        await bot.edit_message_text(
                            chat_id=group_chat_id, message_id=group_msg_id,
                            text=final_text, reply_markup=None, parse_mode="HTML"
                        )
                    except Exception as eg:
                        logger.warning(f"[uber_close] Не вдалось оновити group msg: {eg}")
        except Exception as eg2:
            logger.warning(f"[uber_close] Помилка оновлення group msg: {eg2}")

        # 7. Нотифікація менеджерів / власника
        _safe_ucn = courier_name.translate(str.maketrans({'_': r'\_', '*': r'\*', '`': r'\`'}))
        notify_text = _(lang, "finish_notify",
                        short_id=short_id,
                        amount=order.get("amount", "0"),
                        cur=currency,
                        courier_name=_safe_ucn)

        managers_res = await db._run(
            lambda: db.supabase.table("staff").select("user_id")
                .eq("business_id", biz_id).eq("role", "manager").execute()
        )
        notify_ids = [int(m["user_id"]) for m in managers_res.data] if managers_res.data else []
        if not notify_ids and biz and biz.get("owner_id"):
            notify_ids = [int(biz["owner_id"])]

        for uid in notify_ids:
            try:
                await bot.send_message(chat_id=uid, text=notify_text, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Помилка нотифікації {uid}: {e}")

    except Exception as e:
        logger.error(f"КРИТИЧНА ПОМИЛКА uber_close {order_id}: {e}")
        await callback.message.answer(_(lang, 'generic_error', error=str(e)))
