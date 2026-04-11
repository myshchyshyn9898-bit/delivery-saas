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

def _build_uber_group_text(short_id, source_label, address, details_text,
                            client_name, phone, pay_icon, amount, currency,
                            pay_type_str, comment, status_line):
    """
    Будує уніфікований текст для групового повідомлення uber-режиму.
    status_line — рядок статусу, наприклад:
        "🟢 <b>Активний</b> — хто перший, той і везе!"
        "🟡 <b>Везтиме: Іван</b>"
        "✅ <b>Доставлено: Іван</b>"
    """
    prefix = f"{source_label}\n" if source_label else ""
    txt = (
        f"{prefix}"
        f"🛵 <b>Нове замовлення #{short_id}</b>\n\n"
        f"📍 <b>Адреса:</b> {address}\n"
    )
    if details_text:
        txt += f"🏢 <b>Деталі:</b> {details_text}\n"
    txt += (
        f"👤 <b>Клієнт:</b> {client_name or '—'}\n"
        f"📞 <b>Телефон:</b> {phone or '—'}\n"
        f"{pay_icon} <b>Сума:</b> {amount} {currency} ({pay_type_str})\n"
    )
    if comment:
        txt += f"\n💬 <b>Коментар:</b> <i>{comment}</i>\n"
    txt += f"\n{status_line}"
    return txt


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
        call_url = f"tel:{phone}"
        builder.button(text="📞 Подзвонити", url=call_url)

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
    status_line = "🟢 <b>Активний</b> — хто перший, той і везе!"
    text = _build_uber_group_text(
        short_id, source_label, address, details_text,
        client_name, phone, pay_icon, amount, currency,
        pay_type_str, comment, status_line
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
                courier_text = _(lang, 'order_new',
                                 short_id=short_id,
                                 status=_(lang, 'status_active_full'),
                                 address=address,
                                 details_text=details_text,
                                 phone=phone_clean,
                                 client_name=client_name,
                                 pay_icon=pay_icon,
                                 amount=amount,
                                 cur=currency,
                                 pay_type=pay_type_str)

                if comment:
                    courier_text += _(lang, 'comment_prefix', comment=comment)

                builder = InlineKeyboardBuilder()
                if is_pro:
                    builder.button(text=_(lang, 'btn_route'), url=route_url)
                builder.button(text=_(lang, 'btn_finish'), callback_data=f"finish_order_{order_id}")
                builder.adjust(1)

                if is_pro:
                    map_filename = await get_route_map_file(biz, address, short_id)
                    if map_filename and os.path.exists(map_filename):
                        photo = FSInputFile(map_filename)
                        await bot.send_photo(
                            chat_id=data['courier_id'], photo=photo,
                            caption=courier_text, reply_markup=builder.as_markup(),
                            parse_mode="Markdown"
                        )
                        os.remove(map_filename)
                    else:
                        await bot.send_message(
                            chat_id=data['courier_id'], text=courier_text,
                            reply_markup=builder.as_markup(), parse_mode="Markdown"
                        )
                else:
                    await bot.send_message(
                        chat_id=data['courier_id'], text=courier_text,
                        reply_markup=builder.as_markup(), parse_mode="Markdown"
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

            courier_text = _(lang, 'order_new',
                             short_id=short_id,
                             status=_(lang, 'status_active_full'),
                             address=address,
                             details_text="",
                             phone=order_db.get('client_phone', '-'),
                             client_name=order_db.get('client_name', 'Клієнт'),
                             pay_icon=pay_icon,
                             amount=order_db['amount'],
                             cur=currency,
                             pay_type=pay_type_str)

            if order_db.get('comment'):
                courier_text += _(lang, 'comment_prefix', comment=order_db['comment'])

            builder = InlineKeyboardBuilder()
            if is_pro:
                builder.button(text=_(lang, 'btn_route'), url=route_url)
            builder.button(text=_(lang, 'btn_finish'), callback_data=f"finish_order_{order_id}")
            builder.adjust(1)

            if is_pro:
                map_filename = await get_route_map_file(biz, address, short_id)
                if map_filename and os.path.exists(map_filename):
                    photo = FSInputFile(map_filename)
                    await bot.send_photo(
                        chat_id=courier_id, photo=photo,
                        caption=courier_text, reply_markup=builder.as_markup(),
                        parse_mode="Markdown"
                    )
                    os.remove(map_filename)
                else:
                    await bot.send_message(
                        chat_id=courier_id, text=courier_text,
                        reply_markup=builder.as_markup(), parse_mode="Markdown"
                    )
            else:
                await bot.send_message(
                    chat_id=courier_id, text=courier_text,
                    reply_markup=builder.as_markup(), parse_mode="Markdown"
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

        # Оновлюємо текст особистого повідомлення кур'єра
        status_active = _(lang, 'status_active_full')
        status_done = _(lang, 'status_done_full')
        msg_text = callback.message.caption or callback.message.text or ""

        async def _edit(parse_mode, new_text):
            if callback.message.caption is not None:
                await callback.message.edit_caption(caption=new_text, reply_markup=None, parse_mode=parse_mode)
            else:
                await callback.message.edit_text(text=new_text, reply_markup=None, parse_mode=parse_mode)

        if status_active in msg_text:
            await _edit("Markdown", msg_text.replace(status_active, status_done))
        else:
            try:
                await _edit("Markdown", msg_text)
            except Exception:
                pass

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
    order_id = callback.data.replace("take_order_", "")
    taker_id = callback.from_user.id
    taker_name = callback.from_user.full_name
    lang = callback.from_user.language_code

    try:
        # 1. Читаємо замовлення
        res = await db._run(
            lambda: db.supabase.table("orders").select("*").eq("id", order_id).execute()
        )
        if not res.data:
            await callback.answer("❌ Замовлення не знайдено.", show_alert=True)
            return

        order = res.data[0]

        if order['status'] != 'pending':
            await callback.answer("⚡️ Хтось був швидшим! Замовлення вже забрали.", show_alert=True)
            return

        # 2. Атомарно захоплюємо (race condition protection)
        await db._run(
            lambda: db.supabase.table("orders")
                .update({"status": "delivering", "courier_id": taker_id})
                .eq("id", order_id)
                .eq("status", "pending")
                .execute()
        )

        verify = await db._run(
            lambda: db.supabase.table("orders")
                .select("courier_id, status")
                .eq("id", order_id)
                .execute()
        )
        if not verify.data or str(verify.data[0].get('courier_id')) != str(taker_id):
            await callback.answer("⚡️ Хтось був швидшим! Замовлення вже забрали.", show_alert=True)
            return

        short_id = str(order_id)[:6].upper()
        biz_id = order.get('business_id')
        biz = await db.get_business_by_id(biz_id) if biz_id else None
        currency = biz.get('currency', 'zł') if biz else 'zł'

        pay_type = order.get('pay_type', 'cash')
        pay_icon = "💵" if pay_type == "cash" else ("💳" if pay_type == "terminal" else "🌐")
        pay_type_str = _(lang, 'pay_' + pay_type)

        address = order.get('address', '—')
        address_query = urllib.parse.quote(address)
        route_url = f"https://www.google.com/maps/dir/?api=1&destination={address_query}"
        amount = order.get('amount', '0')
        phone = order.get('client_phone', '—')
        client_name = order.get('client_name', 'Клієнт')
        comment = order.get('comment', '')

        # Визначаємо details_text (зберігається в БД або порожньо)
        details_text = ""

        # 3. Оновлюємо повідомлення в групі — тепер з кнопками закриття
        status_line = f"🟡 <b>Везтиме: {taker_name}</b>"
        new_text = _build_uber_group_text(
            short_id, "", address, details_text,
            client_name, phone, pay_icon, amount, currency,
            pay_type_str, comment, status_line
        )
        new_kb = _build_uber_keyboard(order_id, route_url, phone, pay_type, amount, currency, state="delivering")

        try:
            if callback.message.caption is not None:
                await callback.message.edit_caption(
                    caption=new_text, reply_markup=new_kb, parse_mode="HTML"
                )
            else:
                await callback.message.edit_text(
                    text=new_text, reply_markup=new_kb, parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"[take_order] Помилка оновлення групового повідомлення: {e}")

        await callback.answer(f"✅ Ви взяли замовлення #{short_id}!", show_alert=False)

    except Exception as e:
        logger.error(f"Помилка take_order {order_id} від {taker_id}: {e}")
        await callback.answer("❌ Виникла помилка. Спробуйте ще раз.", show_alert=True)


# ===========================================================================
# UBER MODE: закриття замовлення прямо в груповому повідомленні
# ===========================================================================

@router.callback_query(F.data.startswith("uber_close_"))
async def uber_close_handler(callback: types.CallbackQuery, bot: Bot):
    """
    Формат: uber_close_{pay_type}_{order_id}
    pay_type: cash | terminal | online
    """
    parts = callback.data.replace("uber_close_", "").split("_", 1)
    if len(parts) != 2:
        await callback.answer("❌ Помилка формату.", show_alert=True)
        return

    pay_type_closed, order_id = parts[0], parts[1]
    courier_name = callback.from_user.full_name
    lang = callback.from_user.language_code

    try:
        # 1. Читаємо замовлення
        res = await db._run(
            lambda: db.supabase.table("orders").select("*").eq("id", order_id).execute()
        )
        if not res.data:
            await callback.answer("❌ Замовлення не знайдено.", show_alert=True)
            return

        order = res.data[0]

        # Перевіряємо що закриває саме той хто взяв
        if str(order.get('courier_id')) != str(callback.from_user.id):
            await callback.answer("⛔️ Це замовлення веде інший кур'єр.", show_alert=True)
            return

        if order['status'] == 'completed':
            await callback.answer("✅ Замовлення вже закрите.", show_alert=True)
            return

        # 2. Закриваємо в БД
        await db.update_order_status(order_id, "completed")

        short_id = str(order_id)[:6].upper()
        biz_id = order['business_id']
        biz = await db.get_business_by_id(biz_id)
        currency = biz.get('currency', 'zł') if biz else 'zł'

        pay_type = order.get('pay_type', 'cash')
        pay_icon = "💵" if pay_type == "cash" else ("💳" if pay_type == "terminal" else "🌐")
        pay_type_str = _(lang, 'pay_' + pay_type)
        amount = order.get('amount', '0')
        address = order.get('address', '—')
        phone = order.get('client_phone', '—')
        client_name = order.get('client_name', 'Клієнт')
        comment = order.get('comment', '')

        # 3. Оновлюємо повідомлення в групі — фінальний статус, без кнопок
        status_line = f"✅ <b>Доставлено: {courier_name}</b>"
        final_text = _build_uber_group_text(
            short_id, "", address, "",
            client_name, phone, pay_icon, amount, currency,
            pay_type_str, comment, status_line
        )

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
            logger.error(f"[uber_close] Помилка оновлення повідомлення: {e}")

        await callback.answer("✅ Замовлення закрито!", show_alert=False)

        # 4. Нотифікація менеджерів / власника
        notify_text = _(lang, 'finish_notify',
                        short_id=short_id,
                        amount=amount,
                        cur=currency,
                        courier_name=courier_name)

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
        logger.error(f"Помилка uber_close {order_id}: {e}")
        await callback.answer("❌ Виникла помилка.", show_alert=True)
