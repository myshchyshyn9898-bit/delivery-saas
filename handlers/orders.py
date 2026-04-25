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


async def _get_lang(user_id: int, tg_code: str) -> str:
    """Мова з БД або TG."""
    tg = (tg_code or 'en').split('-')[0].lower()
    try:
        import database as db_
        ctx = await db_.get_user_context_cached(user_id)
        if ctx:
            saved = ctx['biz'].get('lang') if ctx['role'] == 'owner' else ctx.get('staff', {}).get('lang')
            if saved in ('uk', 'ru', 'pl', 'en'):
                return saved
    except Exception:
        pass
    return tg if tg in ('uk', 'ru', 'pl', 'en') else 'en'


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
    lang = await _get_lang(message.from_user.id, message.from_user.language_code)

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
                # BUG FIX: courier_lang не було визначено — NameError при кожному новому замовленні
                courier_lang = await db.get_courier_lang(int(data["courier_id"]), biz_id)
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
                    status_line=_(courier_lang, 'order_status_active_short'),
                    lang=courier_lang
                )

                builder = InlineKeyboardBuilder()
                if is_pro:
                    builder.button(text=_(courier_lang, 'btn_route'), url=route_url)
                if phone_clean:
                    _btn_t, _call_u = _build_call_url(phone_clean, biz, lang=courier_lang)
                    builder.button(text=_btn_t, url=_call_u)
                if pay_type == "online":
                    builder.button(text=_(courier_lang, 'btn_close_online'), callback_data=f"dispatcher_close_online_{order_id}")
                else:
                    builder.button(text=_(courier_lang, 'btn_close_cash', amount=amount, cur=currency), callback_data=f"dispatcher_close_cash_{order_id}")
                    builder.button(text=_(courier_lang, 'btn_close_terminal', amount=amount, cur=currency), callback_data=f"dispatcher_close_terminal_{order_id}")
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

            # Беремо мову кур'єра
            assign_courier_lang = await db.get_courier_lang(int(courier_id), biz_id)
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
                status_line=_(assign_courier_lang, 'order_status_active_short'),
                lang=assign_courier_lang
            )

            builder = InlineKeyboardBuilder()
            if is_pro:
                builder.button(text=_(assign_courier_lang, 'btn_route'), url=route_url)
            phone_for_call = order_db.get('client_phone', '')
            if phone_for_call:
                _btn_a, _call_a = _build_call_url(phone_for_call, biz, lang=assign_courier_lang)
                builder.button(text=_btn_a, url=_call_a)
            if pay_type == "online":
                builder.button(text=_(assign_courier_lang, 'btn_close_online'), callback_data=f"dispatcher_close_online_{order_id}")
            else:
                builder.button(text=_(assign_courier_lang, 'btn_close_cash', amount=order_db['amount'], cur=currency), callback_data=f"dispatcher_close_cash_{order_id}")
                builder.button(text=_(assign_courier_lang, 'btn_close_terminal', amount=order_db['amount'], cur=currency), callback_data=f"dispatcher_close_terminal_{order_id}")
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

    # ── ВЗЯТТЯ ЗАМОВЛЕННЯ З КАРТИ (WebApp sendData) ──────────────────────────
    elif data.get("action") == "take_order_from_map":
        # ✅ Використовуємо спільне ядро — вся логіка в _process_take_order
        order_id = data.get('order_id')
        if not order_id:
            return
        result = await _process_take_order(
            bot, order_id, user_id, message.from_user.full_name, lang
        )
        if not result["ok"]:
            err = result["error"]
            if err == "order_not_found":
                await message.answer(_(lang, "order_not_found"))
            elif err == "courier_not_in_staff":
                await message.answer(_(lang, "courier_not_in_staff"))
            else:
                await message.answer(_(lang, "order_taken_by_other"))

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
# КУР'ЄР: "📦 Мої замовлення" — відновлення кнопок після перезапуску боту
# ===========================================================================

async def _get_my_active_orders_text(lang: str) -> str:
    """Повертає текст кнопки з texts.py."""
    return _(lang, "btn_my_active_orders")

@router.message(F.text.in_(["📦 Мої замовлення", "📦 Мои заказы", "📦 Moje zamówienia", "📦 My Orders"]))
async def cmd_my_active_orders(message: types.Message, bot: Bot):
    """Кур'єр отримує свої активні delivering замовлення з кнопками."""
    lang = await _get_lang(message.from_user.id, message.from_user.language_code)
    ctx = await db.get_user_context_cached(message.from_user.id)
    if not ctx or ctx["role"] not in ("courier", "manager", "owner"):
        await message.answer(_(lang, "no_access"))
        return

    biz_id  = ctx["biz"]["id"]
    user_id = message.from_user.id

    res = await db._run(
        lambda: db.supabase.table("orders")
            .select("*")
            .eq("courier_id", user_id)
            .eq("business_id", biz_id)
            .eq("status", "delivering")
            .execute()
    )
    orders_list = res.data or []

    if not orders_list:
        await message.answer(_(lang, "no_active_orders"))
        return

    biz      = ctx["biz"]
    currency = biz.get("currency", "zł")

    import html as _hmy
    import urllib.parse as _ulmy

    for order in orders_list:
        order_id = order["id"]
        short_id = str(order_id)[:6].upper()
        pay_type = order.get("pay_type", "cash")
        amount   = order.get("amount", "0")
        address  = _hmy.escape(order.get("address", "—"))
        details  = _hmy.escape(order.get("details", "") or "")
        client   = _hmy.escape(order.get("client_name", "") or "")
        phone    = _hmy.escape(order.get("client_phone", "—") or "—")
        comment  = _hmy.escape(order.get("comment", "") or "")
        status_line = _(lang, "order_status_delivering", courier=_hmy.escape(message.from_user.full_name))

        text = _build_order_text(short_id, address, details, client,
                                  phone, pay_type, amount, currency,
                                  comment, status_line, lang=lang)

        raw_phone = order.get("client_phone", "") or ""
        route_url = f"https://www.google.com/maps/dir/?api=1&destination={_ulmy.quote(order.get('address', ''))}"
        builder   = InlineKeyboardBuilder()
        builder.button(text=_(lang, "btn_route"), url=route_url)
        if raw_phone:
            _btn, _url = _build_call_url(raw_phone, biz, lang=lang)
            builder.button(text=_btn, url=_url)
        if pay_type == "online":
            builder.button(text=_(lang, "btn_close_online"), callback_data=f"uber_close_online_{order_id}")
        else:
            builder.button(text=_(lang, "btn_close_cash", amount=amount, cur=currency), callback_data=f"uber_close_cash_{order_id}")
            builder.button(text=_(lang, "btn_close_terminal", amount=amount, cur=currency), callback_data=f"uber_close_terminal_{order_id}")
        builder.adjust(1)

        try:
            await message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        except Exception as e:
            logger.error(f"[my_orders] {e}")


# ===========================================================================
# СКАСУВАННЯ ЗАМОВЛЕННЯ (адмін/менеджер через callback)
# ===========================================================================

@router.callback_query(F.data.startswith("cancel_order_"))
async def cancel_order_handler(callback: types.CallbackQuery, bot: Bot):
    lang     = (callback.from_user.language_code or "en").split("-")[0].lower()
    order_id = callback.data.replace("cancel_order_", "")

    ctx = await db.get_user_context_cached(callback.from_user.id)
    if not ctx or ctx["role"] not in ("owner", "manager"):
        await callback.answer(_(lang, "no_access"), show_alert=True)
        return

    try:
        await callback.answer("⏳", show_alert=False)
    except Exception:
        pass

    res = await db._run(
        lambda: db.supabase.table("orders").select("*").eq("id", order_id).execute()
    )
    if not res.data:
        await callback.message.answer(_(lang, "order_not_found"))
        return

    order = res.data[0]
    if order["status"] in ("completed", "cancelled"):
        await callback.message.answer(_(lang, "order_already_closed"))
        return

    await db._run(
        lambda: db.supabase.table("orders")
            .update({"status": "cancelled"})
            .eq("id", order_id)
            .execute()
    )

    short_id = str(order_id)[:6].upper()
    biz = await db.get_business_by_id(order["business_id"])
    currency = biz.get("currency", "zł") if biz else "zł"

    import html as _hcan
    cancelled_text = (
        f"❌ <b>{_(lang, 'order_cancelled').replace('❌ ', '')} #{short_id}</b>\n"
        f"👤 {_hcan.escape(order.get('client_name', '') or '')}\n"
        f"📞 {_hcan.escape(order.get('client_phone', '—') or '—')}"
    )

    try:
        if callback.message.caption is not None:
            await callback.message.edit_caption(caption=cancelled_text, reply_markup=None, parse_mode="HTML")
        else:
            await callback.message.edit_text(text=cancelled_text, reply_markup=None, parse_mode="HTML")
    except Exception:
        await callback.message.answer(cancelled_text, parse_mode="HTML")

    # Сповіщаємо кур'єра якщо призначений
    courier_id = order.get("courier_id")
    if courier_id:
        try:
            # BUG FIX: кур'єр отримував повідомлення мовою адміна — тепер беремо його власну мову
            courier_notify_lang = await db.get_courier_lang(int(courier_id), order.get("business_id"))
            await bot.send_message(
                chat_id=int(courier_id),
                text=_(courier_notify_lang, "order_cancelled_admin", short_id=short_id),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"[cancel_order] courier notify: {e}")

    # Оновлюємо групове повідомлення
    try:
        group_msg_id  = order.get("group_message_id")
        group_chat_id = biz.get("courier_group_id") if biz else None
        if group_msg_id and group_chat_id and str(callback.message.chat.id) != str(group_chat_id):
            try:
                await bot.edit_message_caption(
                    chat_id=group_chat_id, message_id=group_msg_id,
                    caption=cancelled_text, reply_markup=None, parse_mode="HTML"
                )
            except Exception:
                try:
                    await bot.edit_message_text(
                        chat_id=group_chat_id, message_id=group_msg_id,
                        text=cancelled_text, reply_markup=None, parse_mode="HTML"
                    )
                except Exception as eg:
                    logger.warning(f"[cancel_order] group: {eg}")
    except Exception:
        pass


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
# СПІЛЬНЕ ЯДРО: взяття замовлення (викликається з усіх трьох шляхів)
# ===========================================================================

async def _process_take_order(
    bot: Bot,
    order_id: str,
    taker_id: int,
    taker_name: str,
    lang: str,
    already_captured: bool = False,
) -> dict:
    """
    Захоплення замовлення + оновлення повідомлень.
    Повертає dict: { ok, error?, text?, markup? }

    already_captured=True — map.html вже зробив UPDATE в Supabase сам,
    пропускаємо кроки захоплення і йдемо одразу до розсилки.

    Викликається з:
      - take_order_handler (callback кнопки в групі) → already_captured=False
      - take_order_from_map (WebApp sendData)         → already_captured=False
      - api_take_order_handler (REST з map.html)      → already_captured=True
    """
    import html as _h
    import urllib.parse as _ul

    # 1. Читаємо замовлення (завжди)
    res = await db._run(
        lambda: db.supabase.table("orders").select("*").eq("id", order_id).execute()
    )
    if not res.data:
        return {"ok": False, "error": "order_not_found"}

    order = res.data[0]

    if already_captured:
        # map.html вже зробив UPDATE — просто перевіряємо що саме ми захопили
        if str(order.get("courier_id", "")) != str(taker_id):
            return {"ok": False, "error": "order_already_assigned"}
    else:
        # 2. Перевірка статусу
        if order["status"] != "pending":
            return {"ok": False, "error": "order_already_assigned"}

        # 3. Перевірка що кур'єр є в staff
        staff_check = await db._run(
            lambda: db.supabase.table("staff")
                .select("user_id")
                .eq("user_id", taker_id)
                .eq("business_id", order["business_id"])
                .execute()
        )
        if not staff_check.data:
            return {"ok": False, "error": "courier_not_in_staff"}

        # 4. Атомарне захоплення — UPDATE тільки якщо статус ще pending
        take_res = await db._run(
            lambda: db.supabase.table("orders")
                .update({"status": "delivering", "courier_id": taker_id})
                .eq("id", order_id)
                .eq("status", "pending")
                .execute()
        )
        if not take_res.data:
            verify = await db._run(
                lambda: db.supabase.table("orders")
                    .select("courier_id, status")
                    .eq("id", order_id)
                    .execute()
            )
            if not verify.data or str(verify.data[0].get("courier_id")) != str(taker_id):
                return {"ok": False, "error": "order_already_assigned"}

    # 5. Будуємо текст і клавіатуру
    short_id = str(order_id)[:6].upper()
    biz_id   = order["business_id"]
    biz      = await db.get_business_by_id(biz_id)
    currency = biz.get("currency", "zł") if biz else "zł"

    pay_type  = order.get("pay_type", "cash")
    amount    = order.get("amount", "0")
    raw_phone = order.get("client_phone", "") or ""
    address   = order.get("address", "")

    safe_name    = _h.escape(taker_name)
    safe_address = _h.escape(address)
    safe_details = _h.escape(order.get("details", "") or "")
    safe_client  = _h.escape(order.get("client_name", "") or "")
    safe_phone   = _h.escape(raw_phone or "—")
    safe_comment = _h.escape(order.get("comment", "") or "")

    status_line = _(lang, "order_status_delivering", courier=safe_name)
    text = _build_order_text(
        short_id, safe_address, safe_details, safe_client,
        safe_phone, pay_type, amount, currency,
        safe_comment, status_line, lang=lang
    )
    if len(text) > 1024:
        text = text[:1020] + "..."

    route_url = f"https://www.google.com/maps/dir/?api=1&destination={_ul.quote(address)}"
    builder   = InlineKeyboardBuilder()
    builder.button(text=_(lang, "btn_route"), url=route_url)
    if raw_phone:
        _bt, _cu = _build_call_url(raw_phone, biz, lang=lang)
        builder.button(text=_bt, url=_cu)
    if pay_type == "online":
        builder.button(text=_(lang, "btn_close_online"), callback_data=f"uber_close_online_{order_id}")
    else:
        builder.button(text=_(lang, "btn_close_cash", amount=amount, cur=currency), callback_data=f"uber_close_cash_{order_id}")
        builder.button(text=_(lang, "btn_close_terminal", amount=amount, cur=currency), callback_data=f"uber_close_terminal_{order_id}")
    if raw_phone and pay_type != "online":
        builder.adjust(2, 2)
    elif raw_phone:
        builder.adjust(2, 1)
    else:
        builder.adjust(1) if pay_type == "online" else builder.adjust(1, 2)

    markup = builder.as_markup()

    # 6. Надсилаємо кур'єру особисте повідомлення з кнопками
    try:
        await bot.send_message(chat_id=taker_id, text=text, reply_markup=markup, parse_mode="HTML")
    except Exception as ep:
        logger.warning(f"[take_order] особисте кур'єру {taker_id}: {ep}")

    # 7. Оновлюємо групове повідомлення — тільки маршрут (закриття — через особисте)
    group_msg_id  = order.get("group_message_id")
    group_chat_id = biz.get("courier_group_id") if biz else None
    if group_msg_id and group_chat_id:
        group_builder = InlineKeyboardBuilder()
        group_builder.button(text=_(lang, "btn_route"), url=route_url)
        group_markup = group_builder.as_markup()
        try:
            await bot.edit_message_caption(
                chat_id=group_chat_id, message_id=group_msg_id,
                caption=text, reply_markup=group_markup, parse_mode="HTML"
            )
        except Exception:
            try:
                await bot.edit_message_text(
                    chat_id=group_chat_id, message_id=group_msg_id,
                    text=text, reply_markup=group_markup, parse_mode="HTML"
                )
            except Exception as eg:
                logger.warning(f"[take_order] group msg: {eg}")

    # 8. Нотифікація менеджерів/власника
    try:
        biz_res = await db._run(
            lambda bid=biz_id: db.supabase.table("businesses").select("owner_id,lang").eq("id", bid).execute()
        )
        if biz_res.data:
            owner_id_n = biz_res.data[0].get("owner_id")
            mgr_res = await db._run(
                lambda bid=biz_id: db.supabase.table("staff").select("user_id")
                    .eq("business_id", bid).eq("role", "manager").execute()
            )
            notify_uids = [int(m["user_id"]) for m in (mgr_res.data or [])]
            if owner_id_n and int(owner_id_n) not in notify_uids:
                notify_uids.append(int(owner_id_n))
            # BUG FIX: hardcoded Ukrainian — використовуємо мову власника бізнесу
            owner_lang = biz_res.data[0].get("lang", "en") if biz_res.data else "en"
            notify_text = _(owner_lang, "order_taken_notify", name=safe_name, short_id=short_id)
            for nuid in notify_uids:
                if nuid != taker_id:
                    try:
                        await bot.send_message(chat_id=nuid, text=notify_text, parse_mode="HTML")
                    except Exception:
                        pass
    except Exception as en:
        logger.warning(f"[take_order] notify: {en}")

    return {"ok": True, "text": text, "markup": markup}


# ===========================================================================
# UBER MODE: кнопка "Взяти замовлення" в групі
# ===========================================================================

@router.callback_query(F.data.startswith("take_order_"))
async def take_order_handler(callback: types.CallbackQuery, bot: Bot):
    """Кур'єр натиснув 'Взяти замовлення' в груповому чаті."""
    try:
        await callback.answer("⏳...", show_alert=False)
    except Exception:
        pass

    order_id   = callback.data.replace("take_order_", "")
    taker_id   = callback.from_user.id
    taker_name = callback.from_user.full_name
    lang       = (callback.from_user.language_code or "en").split("-")[0].lower()

    result = await _process_take_order(bot, order_id, taker_id, taker_name, lang)

    if not result["ok"]:
        err = result["error"]
        if err == "order_not_found":
            await callback.message.answer(_(lang, "order_not_found"))
        elif err == "courier_not_in_staff":
            await callback.message.answer(_(lang, "courier_not_in_staff"))
        else:
            await callback.message.answer(f"⚡️ {_(lang, 'order_already_assigned')}")
        return

    # Оновлюємо поточне повідомлення (в групі де натиснули кнопку)
    text   = result["text"]
    markup = result["markup"]
    try:
        if callback.message.caption is not None:
            await callback.message.edit_caption(caption=text, reply_markup=markup, parse_mode="HTML")
        else:
            await callback.message.edit_text(text=text, reply_markup=markup, parse_mode="HTML")
    except Exception as edit_err:
        logger.warning(f"[take_order] edit: {edit_err}")

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

    lang = await _get_lang(callback.from_user.id, callback.from_user.language_code)

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

        if order["status"] in ("completed", "cancelled"):
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
    lang = await _get_lang(callback.from_user.id, callback.from_user.language_code)
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

        if order["status"] in ("completed", "cancelled"):
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
