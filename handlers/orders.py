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

def _build_order_text(short_id, address, details_text, client_name,
                       phone, pay_type, amount, currency, comment,
                       status_line, source_label=""):
    """
    Будує уніфікований текст замовлення для БУДЬ-ЯКОГО режиму.
    Формат: як в старому боті — з роздільниками і чітким статусом.

    status_line приклади:
        "🟢 Активний"
        "🟡 В дорозі (Везтиме: Іван)"
        "🔴 Закрито (14:32, Іван - 💵)"
    """
    import html as _h
    import datetime

    # Тип оплати — рядок і сума
    if pay_type == "cash":
        pay_line = f"💵 Готівка: {amount} {currency}"
    elif pay_type == "terminal":
        pay_line = f"🏧 Термінал: {amount} {currency}"
    else:
        pay_line = f"💳 Оплата: ОНЛАЙН (Сплачено)"

    prefix = f"{source_label}\n" if source_label else ""
    txt = (
        f"{prefix}"
        f"📦 <b>ЗАМОВЛЕННЯ #{short_id}</b>\n"
        f"➖➖➖➖➖➖\n"
        f"<b>Статус:</b> {status_line}\n\n"
        f"📍 <b>Адреса:</b> {address}\n"
    )
    if details_text:
        txt += f"🏢 <b>Деталі:</b> {details_text}\n"
    if client_name and client_name not in ("—", "Клієнт", ""):
        txt += f"👤 <b>Клієнт:</b> {client_name}\n"
    txt += f"📞 <b>Тел:</b> {phone or '—'}\n"
    txt += f"{pay_line}\n"
    txt += "➖➖➖➖➖➖"
    if comment:
        txt += f"\n🗣 <b>Коментар:</b> {comment}"
    return txt


# Зворотна сумісність — старе ім'я
def _build_uber_group_text(short_id, source_label, address, details_text,
                            client_name, phone, pay_icon, amount, currency,
                            pay_type_str, comment, status_line):
    # Визначаємо pay_type з pay_icon
    if pay_icon == "💵":
        pay_type = "cash"
    elif pay_icon == "💳" or pay_icon == "🏧":
        pay_type = "terminal"
    else:
        pay_type = "online"
    return _build_order_text(short_id, address, details_text, client_name,
                              phone, pay_type, amount, currency, comment,
                              status_line, source_label)


def _build_uber_keyboard(order_id, route_url, phone, pay_type, amount, currency, state="pending"):
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
        builder.button(text="✅ Взяти замовлення", callback_data=f"take_order_{order_id}")
        builder.adjust(1)
        return builder.as_markup()

    # delivering
    builder.button(text="🗺 Маршрут", url=route_url)

    if phone:
        digits_only = "".join(filter(str.isdigit, phone))
        btn_text = "🚖 Uber Call" if len(digits_only) == 8 else "📞 Подзвонити"
        call_url = f"{BASE_URL.rstrip('/')}/call.html?code={urllib.parse.quote(phone)}"
        builder.button(text=btn_text, url=call_url)

    if pay_type == "cash":
        builder.button(text=f"💵 Готівка — {amount} {currency}", callback_data=f"uber_close_cash_{order_id}")
    elif pay_type == "terminal":
        builder.button(text=f"🏧 Термінал — {amount} {currency}", callback_data=f"uber_close_terminal_{order_id}")
    else:
        builder.button(text="✅ Закрити (Онлайн оплачено)", callback_data=f"uber_close_online_{order_id}")

    builder.adjust(2, 1)
    return builder.as_markup()


async def _send_uber_group_message(bot: Bot, group_id, biz, order_id, short_id,
                                    address, details_text, client_name, phone,
                                    pay_icon, amount, currency, pay_type_str,
                                    pay_type, comment, source_label=""):
    """
    Надсилає замовлення в групу кур'єрів (uber-режим) з картою або без.
    Повертає message_id надісланого повідомлення або None.
    """
    import html as _hug
    _e = _hug.escape
    status_line = "🟢 Активний — хто перший, той і везе!"
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
        source_label=source_label
    )

    address_query = urllib.parse.quote(address)
    route_url = f"https://www.google.com/maps/dir/?api=1&destination={address_query}"
    kb = _build_uber_keyboard(order_id, route_url, phone, pay_type, amount, currency, state="pending")

    sent = None
    try:
        # Спробуємо надіслати з картою
        map_filename = await get_route_map_file(biz, address, short_id)
        if map_filename and os.path.exists(map_filename):
            photo = FSInputFile(map_filename)
            sent = await bot.send_photo(
                chat_id=group_id, photo=photo,
                caption=text, reply_markup=kb, parse_mode="HTML"
            )
            os.remove(map_filename)
        else:
            sent = await bot.send_message(
                chat_id=group_id, text=text,
                reply_markup=kb, parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"[uber] Помилка надсилання в групу {group_id}: {e}")
        try:
            sent = await bot.send_message(
                chat_id=group_id, text=text,
                reply_markup=kb, parse_mode="HTML"
            )
        except Exception as e2:
            logger.error(f"[uber] Критична помилка надсилання в групу: {e2}")

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
            await db.register_new_business(user_id, data)
            context = await db.get_user_context(user_id)
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
                        comment=comment
                    )
                    logger.info(f"[uber webapp] Замовлення {order_id} кинуто в групу {group_id}")
                else:
                    logger.warning(f"Uber mode, але courier_group_id не задано для biz_id={biz_id}")

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
                    status_line="🟢 Активний"
                )

                builder = InlineKeyboardBuilder()
                if is_pro:
                    builder.button(text=_(lang, 'btn_route'), url=route_url)
                if phone_clean:
                    digits_only = "".join(filter(str.isdigit, phone_clean))
                    btn_text = "🚖 Uber Call" if len(digits_only) == 8 else "📞 Подзвонити"
                    call_url = f"{BASE_URL.rstrip('/')}/call.html?code={urllib.parse.quote(phone_clean)}"
                    builder.button(text=btn_text, url=call_url)
                if pay_type == "online":
                    builder.button(text="✅ Закрити (Онлайн оплачено)", callback_data=f"dispatcher_close_online_{order_id}")
                else:
                    # Показуємо ОБИДВІ — адмін міг вибрати готівку, але клієнт платить карткою
                    builder.button(text=f"💵 Готівка — {amount} {currency}", callback_data=f"dispatcher_close_cash_{order_id}")
                    builder.button(text=f"🏧 Термінал — {amount} {currency}", callback_data=f"dispatcher_close_terminal_{order_id}")
                builder.adjust(1)

                if is_pro:
                    map_filename = await get_route_map_file(biz, address, short_id)
                    if map_filename and os.path.exists(map_filename):
                        photo = FSInputFile(map_filename)
                        await bot.send_photo(
                            chat_id=data['courier_id'], photo=photo,
                            caption=courier_text, reply_markup=builder.as_markup(),
                            parse_mode="HTML"
                        )
                        os.remove(map_filename)
                    else:
                        await bot.send_message(
                            chat_id=data['courier_id'], text=courier_text,
                            reply_markup=builder.as_markup(), parse_mode="HTML"
                        )
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

            biz_id = order_db['business_id']
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
                details_text="",
                client_name=_ha.escape(order_db.get("client_name", "") or ""),
                phone=_ha.escape(order_db.get("client_phone", "—") or "—"),
                pay_type=pay_type,
                amount=order_db["amount"],
                currency=currency,
                comment=_ha.escape(order_db.get("comment", "") or ""),
                status_line="🟢 Активний"
            )

            builder = InlineKeyboardBuilder()
            if is_pro:
                builder.button(text=_(lang, 'btn_route'), url=route_url)
            phone_for_call = order_db.get('client_phone', '')
            if phone_for_call:
                digits_only = "".join(filter(str.isdigit, phone_for_call))
                btn_text = "🚖 Uber Call" if len(digits_only) == 8 else "📞 Подзвонити"
                call_url = f"{BASE_URL.rstrip('/')}/call.html?code={urllib.parse.quote(phone_for_call)}"
                builder.button(text=btn_text, url=call_url)
            if pay_type == "online":
                builder.button(text="✅ Закрити (Онлайн оплачено)", callback_data=f"dispatcher_close_online_{order_id}")
            else:
                builder.button(text=f"💵 Готівка — {order_db['amount']} {currency}", callback_data=f"dispatcher_close_cash_{order_id}")
                builder.button(text=f"🏧 Термінал — {order_db['amount']} {currency}", callback_data=f"dispatcher_close_terminal_{order_id}")
            builder.adjust(1)

            if is_pro:
                map_filename = await get_route_map_file(biz, address, short_id)
                if map_filename and os.path.exists(map_filename):
                    photo = FSInputFile(map_filename)
                    await bot.send_photo(
                        chat_id=courier_id, photo=photo,
                        caption=courier_text, reply_markup=builder.as_markup(),
                        parse_mode="HTML"
                    )
                    os.remove(map_filename)
                else:
                    await bot.send_message(
                        chat_id=courier_id, text=courier_text,
                        reply_markup=builder.as_markup(), parse_mode="HTML"
                    )
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
        msg_text = data.get("text")
        businesses = await db.get_all_businesses()
        owner_ids = set([int(b['owner_id']) for b in businesses if b.get('owner_id')])
        if not owner_ids:
            return
        await message.answer(_(lang, 'broadcast_start', count=len(owner_ids)))
        sent_count = 0
        for oid in owner_ids:
            try:
                await bot.send_message(chat_id=oid, text=_('uk', 'broadcast_msg', text=msg_text), parse_mode="Markdown")
                sent_count += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.error(f"Помилка розсилки користувачу {oid}: {e}")
        await message.answer(_(lang, 'broadcast_done', sent=sent_count, total=len(owner_ids)))

    # ── ТІКЕТ ПІДТРИМКИ ──────────────────────────────────────────────────────
    elif data.get("action") == "support_ticket":
        try:
            biz_id = data.get("biz_id", "?")
            admin_msg = (
                f"🆘 <b>НОВИЙ ТІКЕТ ПІДТРИМКИ</b>\n\n"
                f"🏢 <b>Бізнес ID:</b> <code>{biz_id}</code>\n"
                f"👤 <b>Від:</b> <a href='tg://user?id={user_id}'>Клієнт (ID: {user_id})</a>\n"
                f"🏷 <b>Категорія:</b> {data.get('reason', '?')}\n"
                f"📌 <b>Тема:</b> {data.get('topic', '?')}\n"
                f"〰️〰️〰️〰️〰️〰️〰️〰️\n"
                f"💬 <b>Повідомлення:</b>\n<i>{data.get('message', '?')}</i>"
            )
            for admin_id in SUPER_ADMIN_IDS:
                try:
                    await bot.send_message(chat_id=admin_id, text=admin_msg, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"Помилка відправки тікету адміну {admin_id}: {e}")
            await message.answer(
                "✅ <b>Тікет успішно відправлено!</b> Наша служба підтримки зв'яжеться з вами найближчим часом.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Помилка обробки тікету: {e}")


# ===========================================================================
# DISPATCHER MODE: кнопка "Завершити замовлення" (особисте повідомлення)
# ===========================================================================

@router.callback_query(F.data.startswith("finish_order_"))
async def finish_order_handler(callback: types.CallbackQuery, bot: Bot):
    order_id = callback.data.replace("finish_order_", "")
    lang = callback.from_user.language_code
    try:
        res = await db._run(lambda: db.supabase.table("orders").select("*").eq("id", order_id).execute())
        await db.update_order_status(order_id, "completed")

        # Оновлюємо статус — будуємо з нуля в новому стилі
        import html as _hf
        import datetime as _dtf

        if res.data:
            ord_info   = res.data[0]
            courier_fn = callback.from_user.full_name
            time_str_f = _dtf.datetime.now().strftime("%H:%M")
            pay_tp     = ord_info.get("pay_type", "cash")
            pay_icon_f = "💵" if pay_tp == "cash" else ("🏧" if pay_tp == "terminal" else "✅")
            biz_fn     = await db.get_business_by_id(ord_info["business_id"])
            cur_fn     = biz_fn.get("currency", "zł") if biz_fn else "zł"
            short_fn   = str(ord_info["id"])[:6].upper()

            status_fn  = f"🔴 Закрито ({time_str_f}, {_hf.escape(courier_fn)} - {pay_icon_f})"

            new_msg = _build_order_text(
                short_id=short_fn,
                address=_hf.escape(ord_info.get("address", "—")),
                details_text=_hf.escape(ord_info.get("details", "") or ""),
                client_name=_hf.escape(ord_info.get("client_name", "") or ""),
                phone=_hf.escape(ord_info.get("client_phone", "—") or "—"),
                pay_type=pay_tp,
                amount=ord_info.get("amount", "0"),
                currency=cur_fn,
                comment=_hf.escape(ord_info.get("comment", "") or ""),
                status_line=status_fn
            )
        else:
            new_msg = "✅ Замовлення закрито."

        if len(new_msg) > 1024:
            new_msg = new_msg[:1020] + "..."

        try:
            if callback.message.caption is not None:
                await callback.message.edit_caption(caption=new_msg, reply_markup=None, parse_mode="HTML")
            else:
                await callback.message.edit_text(text=new_msg, reply_markup=None, parse_mode="HTML")
        except Exception as e:
            logger.error(f"[finish_order] edit failed: {e}")

        await callback.answer(_(lang, 'finish_success'))

        # Нотифікація менеджерів / власника
        if res.data:
            order_info = res.data[0]
            biz_id = order_info['business_id']
            short_id = str(order_info['id'])[:6].upper()
            biz = await db.get_business_by_id(biz_id)
            currency = biz.get('currency', 'zł') if biz else 'zł'

            notify_text = _(lang, 'finish_notify',
                            short_id=short_id,
                            amount=order_info['amount'],
                            cur=currency,
                            courier_name=callback.from_user.full_name)

            managers_res = await db._run(
                lambda: db.supabase.table("staff").select("user_id")
                    .eq("business_id", biz_id).eq("role", "manager").execute()
            )
            notify_ids = [int(m['user_id']) for m in managers_res.data] if managers_res.data else []
            if not notify_ids and biz and biz.get('owner_id'):
                notify_ids = [int(biz['owner_id'])]

            for uid in notify_ids:
                try:
                    await bot.send_message(chat_id=uid, text=notify_text, parse_mode="Markdown")
                except Exception as e:
                    logger.error(f"Помилка нотифікації {uid}: {e}")

    except Exception as e:
        logger.error(f"Помилка закриття замовлення {order_id}: {e}")
        await callback.answer(_(lang, 'finish_err'), show_alert=True)


# ===========================================================================
# UBER MODE: кнопка "Взяти замовлення" в групі
# ===========================================================================

@router.callback_query(F.data.startswith("take_order_"))
async def take_order_handler(callback: types.CallbackQuery, bot: Bot):
    import html as _html
    import urllib.parse as _ul

    # 1. Одразу відповідаємо Telegram — кнопка перестає крутитись
    try:
        await callback.answer("⏳ Беремо замовлення...", show_alert=False)
    except Exception:
        pass

    order_id  = callback.data.replace("take_order_", "")
    taker_id  = callback.from_user.id
    taker_name = callback.from_user.full_name

    try:
        # 2. Свіжі дані з БД
        res = await db._run(
            lambda: db.supabase.table("orders").select("*").eq("id", order_id).execute()
        )
        if not res.data:
            await callback.message.answer("❌ Замовлення не знайдено в базі.")
            return

        order = res.data[0]

        # 3. Перевірка що ще pending
        if order["status"] != "pending":
            await callback.message.answer(
                f"⚡️ Замовлення #{str(order_id)[:6].upper()} вже забрав інший кур'єр!"
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
            await callback.message.answer(
                "⛔️ Вас немає в персоналі цього закладу!\n\n"
                "Попросіть адміна додати вас через посилання-запрошення."
            )
            return

        # 5. Атомарне захоплення
        await db._run(
            lambda: db.supabase.table("orders")
                .update({"status": "delivering", "courier_id": taker_id})
                .eq("id", order_id)
                .eq("status", "pending")
                .execute()
        )

        # 6. Перевірка що саме ми захопили
        verify = await db._run(
            lambda: db.supabase.table("orders")
                .select("courier_id")
                .eq("id", order_id)
                .execute()
        )
        if not verify.data or str(verify.data[0].get("courier_id")) != str(taker_id):
            await callback.message.answer("⚡️ Хтось був швидшим!")
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

        status_line = f"🟡 В дорозі (Везтиме: {safe_name})"

        text = _build_order_text(short_id, address, details, client,
                                  phone, pay_type, amount, currency,
                                  comment, status_line)

        if len(text) > 1024:
            text = text[:1020] + "..."

        # 7. Кнопки: Маршрут + Подзвонити + Готівка/Термінал (обидві) або Онлайн
        builder = InlineKeyboardBuilder()
        route_url = f"https://www.google.com/maps/dir/?api=1&destination={_ul.quote(order.get('address', ''))}"
        raw_phone = order.get("client_phone", "") or ""
        amount    = order.get("amount", "0")

        builder.button(text="🗺 Маршрут", url=route_url)
        if raw_phone:
            digits_only = "".join(filter(str.isdigit, raw_phone))
            btn_text = "🚖 Uber Call" if len(digits_only) == 8 else "📞 Подзвонити"
            call_url = f"{BASE_URL.rstrip('/')}/call.html?code={urllib.parse.quote(raw_phone)}"
            builder.button(text=btn_text, url=call_url)

        if pay_type == "online":
            builder.button(text="✅ Закрити (Онлайн оплачено)", callback_data=f"uber_close_online_{order_id}")
        else:
            # Завжди показуємо ОБИДВІ кнопки — готівка і термінал
            builder.button(text=f"💵 Готівка — {amount} {currency}", callback_data=f"uber_close_cash_{order_id}")
            builder.button(text=f"🏧 Термінал — {amount} {currency}", callback_data=f"uber_close_terminal_{order_id}")

        builder.adjust(2, 2, 1) if pay_type != "online" and raw_phone else builder.adjust(1)

        # 8. Оновлюємо повідомлення
        if callback.message.caption is not None:
            await callback.message.edit_caption(
                caption=text, reply_markup=builder.as_markup(), parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                text=text, reply_markup=builder.as_markup(), parse_mode="HTML"
            )

    except Exception as e:
        logger.error(f"КРИТИЧНА ПОМИЛКА take_order {order_id}: {e}")
        await callback.message.answer(f"❌ Системна помилка при взятті замовлення: {e}")

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

    try:
        await callback.answer("✅ Закриваємо...", show_alert=False)
    except Exception:
        pass

    parts = callback.data.replace("dispatcher_close_", "").split("_", 1)
    if len(parts) != 2:
        await callback.message.answer("❌ Помилка формату.")
        return

    pay_type_closed, order_id = parts[0], parts[1]
    courier_name = callback.from_user.full_name
    lang = callback.from_user.language_code or "uk"

    try:
        res = await db._run(
            lambda: db.supabase.table("orders").select("*").eq("id", order_id).execute()
        )
        if not res.data:
            await callback.message.answer("❌ Замовлення не знайдено.")
            return

        order = res.data[0]

        if order["status"] == "completed":
            await callback.message.answer("✅ Замовлення вже закрите.")
            return

        # Закриваємо в БД з реальним типом оплати який натиснув кур'єр
        await db.update_order_status(order_id, "completed", actual_pay_type=pay_type_closed)

        short_id = str(order_id)[:6].upper()
        biz_id   = order["business_id"]
        biz      = await db.get_business_by_id(biz_id)
        currency = biz.get("currency", "zł") if biz else "zł"

        pay_type     = pay_type_closed  # Реальний тип що натиснув кур'єр
        pay_icon     = "💵" if pay_type == "cash" else ("💳" if pay_type == "terminal" else "🌐")
        pay_type_str = {"cash": "Готівка", "terminal": "Термінал", "online": "Онлайн"}.get(pay_type, pay_type)

        safe_courier = _html.escape(courier_name)

        # Будуємо фінальний текст — стиль старого боту
        import datetime as _dt2
        time_str2    = _dt2.datetime.now().strftime("%H:%M")
        pay_icon_d   = "💵" if pay_type == "cash" else ("🏧" if pay_type == "terminal" else "✅")
        status_line2 = f"🔴 Закрито ({time_str2}, {safe_courier} - {pay_icon_d})"

        safe_addr2    = __import__("html").escape(order.get("address", "—"))
        safe_client2  = __import__("html").escape(order.get("client_name", "") or "")
        safe_phone2   = __import__("html").escape(order.get("client_phone", "—") or "—")
        safe_comment2 = __import__("html").escape(order.get("comment", "") or "")
        details2      = __import__("html").escape(order.get("details", "") or "")

        done_text = _build_order_text(short_id, safe_addr2, details2,
                                       safe_client2, safe_phone2,
                                       pay_type, order.get("amount", "0"),
                                       currency, safe_comment2, status_line2)
        if len(done_text) > 1024:
            done_text = done_text[:1020] + "..."

        try:
            if callback.message.caption is not None:
                await callback.message.edit_caption(caption=done_text, reply_markup=None, parse_mode="HTML")
            else:
                await callback.message.edit_text(text=done_text, reply_markup=None, parse_mode="HTML")
        except Exception as e:
            logger.error(f"[dispatcher_close] edit failed: {e}")

        # Нотифікація адміну/менеджеру
        notify_text = _(lang, "finish_notify",
                        short_id=short_id,
                        amount=order.get("amount", "0"),
                        cur=currency,
                        courier_name=courier_name)

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
        await callback.message.answer(f"❌ Помилка: {e}")


@router.callback_query(F.data.startswith("uber_close_"))
async def uber_close_handler(callback: types.CallbackQuery, bot: Bot):
    """
    Формат callback_data: uber_close_{pay_type}_{order_id}
    pay_type: cash | terminal | online
    """
    import html as _html

    # 1. Одразу відповідаємо Telegram
    try:
        await callback.answer("✅ Закриваємо замовлення...", show_alert=False)
    except Exception:
        pass

    parts = callback.data.replace("uber_close_", "").split("_", 1)
    if len(parts) != 2:
        await callback.message.answer("❌ Помилка формату callback.")
        return

    pay_type_closed, order_id = parts[0], parts[1]
    courier_name = callback.from_user.full_name

    try:
        # 2. Читаємо замовлення
        res = await db._run(
            lambda: db.supabase.table("orders").select("*").eq("id", order_id).execute()
        )
        if not res.data:
            await callback.message.answer("❌ Замовлення не знайдено.")
            return

        order = res.data[0]

        # 3. Перевірка що закриває той хто взяв
        if str(order.get("courier_id")) != str(callback.from_user.id):
            await callback.message.answer("⛔️ Це замовлення веде інший кур'єр.")
            return

        if order["status"] == "completed":
            await callback.message.answer("✅ Замовлення вже закрите.")
            return

        # 4. Закриваємо в БД з реальним типом оплати який натиснув кур'єр
        await db.update_order_status(order_id, "completed", actual_pay_type=pay_type_closed)

        short_id = str(order_id)[:6].upper()
        biz_id   = order["business_id"]
        biz      = await db.get_business_by_id(biz_id)
        currency = biz.get("currency", "zł") if biz else "zł"

        pay_type     = pay_type_closed  # Використовуємо реальний тип що натиснув кур'єр
        pay_icon     = "💵" if pay_type == "cash" else ("💳" if pay_type == "terminal" else "🌐")
        pay_type_str = {"cash": "Готівка", "terminal": "Термінал", "online": "Онлайн"}.get(pay_type, pay_type)

        safe_courier = _html.escape(courier_name)
        safe_address = _html.escape(order.get("address", "—"))
        safe_client  = _html.escape(order.get("client_name", "Клієнт"))
        safe_phone   = _html.escape(order.get("client_phone", "—") or "—")
        safe_comment = _html.escape(order.get("comment", "") or "")

        # 5. Будуємо фінальний текст — стиль старого боту
        import datetime as _dt
        time_str    = _dt.datetime.now().strftime("%H:%M")
        pay_icon_cl = "💵" if pay_type == "cash" else ("🏧" if pay_type == "terminal" else "✅")
        status_line = f"🔴 Закрито ({time_str}, {safe_courier} - {pay_icon_cl})"

        safe_details = _html.escape(order.get("details", "") or "")
        final_text = _build_order_text(short_id, safe_address, safe_details,
                                        safe_client, safe_phone,
                                        pay_type, order.get("amount", "0"),
                                        currency, safe_comment, status_line)

        if len(final_text) > 1024:
            final_text = final_text[:1020] + "..."

        # 6. Оновлюємо повідомлення — прибираємо кнопки
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
            logger.error(f"[uber_close] edit failed: {e}")

        # 7. Нотифікація менеджерів / власника
        lang = callback.from_user.language_code or "uk"
        notify_text = _(lang, "finish_notify",
                        short_id=short_id,
                        amount=order.get("amount", "0"),
                        cur=currency,
                        courier_name=courier_name)

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
        await callback.message.answer(f"❌ Системна помилка при закритті замовлення: {e}")
