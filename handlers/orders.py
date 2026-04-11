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

# — ОБРОБКА ДАНИХ З WEB APP —

@router.message(F.web_app_data)
async def handle_web_app_data(message: types.Message, bot: Bot):
    data = json.loads(message.web_app_data.data)
    user_id = message.from_user.id
    lang = message.from_user.language_code

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
            await message.answer(_(lang, 'reg_error'))

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

            if new_order:
                order_id = new_order['id']
                short_id = str(order_id)[:6].upper()
                biz = await db.get_business_by_id(biz_id)
                currency = biz.get('currency', 'zł')
                is_pro = actual_plan in ['pro', 'trial']
                courier_lang = lang

                if original_courier_id != "unassigned":
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

                    phone_clean = "".join(filter(lambda x: x.isdigit() or x == '+', data['client_phone']))
                    if not phone_clean.startswith('+'):
                        phone_clean = '+' + phone_clean

                    status_active = _(courier_lang, 'status_active_full')

                    courier_text = _(courier_lang, 'order_new',
                                     short_id=short_id, status=status_active, address=data['address'],
                                     details_text=details_text, phone=phone_clean, client_name=data['client_name'],
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

                tracking_link = f"{BASE_URL}track.html?id={order_id}"
                if original_courier_id == "unassigned":
                    admin_final_text = _(lang, 'free_order_created', short_id=short_id, tracking_link=tracking_link)
                else:
                    admin_base_text = _(lang, 'order_sent', short_id=short_id)
                    admin_final_text = f"{admin_base_text}\n\n" + _(lang, 'tracking_link_label', tracking_link=tracking_link)

                await message.answer(admin_final_text, parse_mode="Markdown")
            else:
                await message.answer(_(lang, 'order_save_err'))
        except Exception as e:
            logger.error(f"Помилка створення замовлення: {e}")
            await message.answer(_(lang, 'order_send_err'))

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

            pay_type_str = _(lang, 'pay_' + order_db['pay_type'])
            pay_icon = "💵" if order_db['pay_type'] == "cash" else ("💳" if order_db['pay_type'] == "terminal" else "🌐")

            status_active = _(lang, 'status_active_full')

            courier_text = _(lang, 'order_new',
                             short_id=short_id, status=status_active, address=order_db['address'],
                             details_text="", phone=order_db.get('client_phone', '-'), client_name=order_db.get('client_name', 'Клієнт'),
                             pay_icon=pay_icon, amount=order_db['amount'], cur=currency, pay_type=pay_type_str)

            if order_db.get('comment'):
                courier_text += _(lang, 'comment_prefix', comment=order_db['comment'])

            builder = InlineKeyboardBuilder()
            address_query = urllib.parse.quote(order_db['address'])
            route_url = f"https://www.google.com/maps/dir/?api=1&destination={address_query}"

            if is_pro:
                builder.button(text=_(lang, 'btn_route'), url=route_url)
            builder.button(text=_(lang, 'btn_finish'), callback_data=f"finish_order_{order_id}")
            builder.adjust(1)

            if is_pro:
                map_filename = await get_route_map_file(biz, order_db['address'], short_id)
                if map_filename and os.path.exists(map_filename):
                    photo = FSInputFile(map_filename)
                    await bot.send_photo(chat_id=courier_id, photo=photo, caption=courier_text, reply_markup=builder.as_markup(), parse_mode="Markdown")
                    os.remove(map_filename)
                else:
                    await bot.send_message(chat_id=courier_id, text=courier_text, reply_markup=builder.as_markup(), parse_mode="Markdown")
            else:
                await bot.send_message(chat_id=courier_id, text=courier_text, reply_markup=builder.as_markup(), parse_mode="Markdown")

            await message.answer(_(lang, 'order_assigned_success', short_id=short_id))
        except Exception as e:
            logger.error(f"Помилка призначення з карти: {e}")
            await message.answer(_(lang, 'order_assign_error'))

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

    elif data.get("action") == "support_ticket":
        try:
            biz_id = data.get("biz_id", "?")
            reason = data.get("reason", "?")
            topic = data.get("topic", "?")
            message_text = data.get("message", "?")
            admin_msg = (
                f"🆘 <b>НОВИЙ ТІКЕТ ПІДТРИМКИ</b>\n\n"
                f"🏢 <b>Бізнес ID:</b> <code>{biz_id}</code>\n"
                f"👤 <b>Від:</b> <a href='tg://user?id={user_id}'>Клієнт (ID: {user_id})</a>\n"
                f"🏷 <b>Категорія:</b> {reason}\n"
                f"📌 <b>Тема:</b> {topic}\n"
                f"〰️〰️〰️〰️〰️〰️〰️〰️\n"
                f"💬 <b>Повідомлення:</b>\n<i>{message_text}</i>"
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


# — ОБРОБКА КНОПКИ "ЗАКРИТИ ЗАМОВЛЕННЯ" —

@router.callback_query(F.data.startswith("finish_order_"))
async def finish_order_handler(callback: types.CallbackQuery, bot: Bot):
    order_id = callback.data.replace("finish_order_", "")
    lang = callback.from_user.language_code
    try:
        res = await db._run(lambda: db.supabase.table("orders").select("*").eq("id", order_id).execute())
        await db.update_order_status(order_id, "completed")

        status_active = _(lang, 'status_active_full')
        status_done = _(lang, 'status_done_full')
        if callback.message.caption:
            await callback.message.edit_caption(
                caption=callback.message.caption.replace(status_active, status_done),
                reply_markup=None, parse_mode="Markdown"
            )
        elif callback.message.text:
            await callback.message.edit_text(
                text=callback.message.text.replace(status_active, status_done),
                reply_markup=None, parse_mode="Markdown"
            )

        await callback.answer(_(lang, 'finish_success'))
        if res.data:
            order_info = res.data[0]
            biz_id = order_info['business_id']
            short_id = str(order_info['id'])[:6].upper()
            biz = await db.get_business_by_id(biz_id)
            currency = biz.get('currency', 'zł') if biz else 'zł'
            managers_res = await db._run(
                lambda: db.supabase.table("staff").select("user_id")
                    .eq("business_id", biz_id).eq("role", "manager").execute()
            )
            if managers_res.data:
                for manager in managers_res.data:
                    try:
                        await bot.send_message(
                            chat_id=manager['user_id'],
                            text=_(lang, 'finish_notify', short_id=short_id, amount=order_info['amount'],
                                   cur=currency, courier_name=callback.from_user.full_name),
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Помилка нотифікації менеджера: {e}")
    except Exception as e:
        logger.error(f"Помилка закриття замовлення {order_id}: {e}")
        await callback.answer(_(lang, 'finish_err'), show_alert=True)


# — ОБРОБКА КНОПКИ "ВЗЯТИ ЗАМОВЛЕННЯ" (Uber Mode) —

@router.callback_query(F.data.startswith("take_order_"))
async def take_order_handler(callback: types.CallbackQuery, bot: Bot):
    order_id = callback.data.replace("take_order_", "")
    taker_id = callback.from_user.id
    taker_name = callback.from_user.full_name
    
    try:
        # 1. Перевіряємо актуальний статус замовлення
        res = await db._run(
            lambda: db.supabase.table("orders")
                .select("id, status, business_id, address")
                .eq("id", order_id)
                .execute()
        )
        if not res.data:
            await callback.answer("❌ Замовлення не знайдено.", show_alert=True)
            return
            
        order = res.data[0]
        
        # 2. Якщо замовлення вже забрали — повідомляємо і виходимо
        if order['status'] != 'pending':
            await callback.answer("⚡️ Хтось був швидшим! Замовлення вже забрали.", show_alert=True)
            return
            
        # 3. Міняємо статус і записуємо кур'єра
        await db._run(
            lambda: db.supabase.table("orders")
                .update({
                    "status": "delivering",
                    "courier_id": taker_id,
                })
                .eq("id", order_id)
                .eq("status", "pending")   # Умова гонки: оновлюємо тільки якщо ще pending
                .execute()
        )
        
        # 4. Ще раз перечитуємо — переконуємось, що саме ми захопили замовлення
        verify = await db._run(
            lambda: db.supabase.table("orders")
                .select("courier_id, status")
                .eq("id", order_id)
                .execute()
        )
        
        if not verify.data or str(verify.data[0].get('courier_id')) != str(taker_id):
            await callback.answer("⚡️ Хтось був швидшим! Замовлення вже забрали.", show_alert=True)
            return

        # 5. Оновлюємо повідомлення в групі: забираємо кнопку, показуємо кур'єра
        short_id = str(order_id)[:6].upper()
        
        # Визначаємо текст повідомлення (залежно від того, чи воно з HTML/Markdown)
        original_text = callback.message.text or callback.message.caption or ""
        
        # Якщо в тексті був напис про "Вільну касу", міняємо його
        if "⏳ <i>Хто перший — той і везе!</i>" in original_text or "⏳ _Хто перший — той і везе!_" in original_text:
            updated_text = original_text.replace(
                "⏳ <i>Хто перший — той і везе!</i>", f"🟡 <b>Везтиме: {taker_name}</b>"
            ).replace(
                "⏳ _Хто перший — той і везе!_", f"🟡 *Везтиме: {taker_name}*"
            )
        else:
            updated_text = original_text + f"\n\n🟡 <b>Везтиме: {taker_name}</b>"

        # Будуємо нову клавіатуру: залишаємо кнопку "Завершити" та маршрут
        builder = InlineKeyboardBuilder()
        
        # Додаємо кнопку Маршруту
        address_query = urllib.parse.quote(order.get('address', ''))
        route_url = f"https://www.google.com/maps/dir/?api=1&destination={address_query}"
        builder.button(text="🗺 Маршрут", url=route_url)
        
        # Додаємо кнопку Завершення
        builder.button(text="✅ Завершити доставку", callback_data=f"finish_order_{order_id}")
        builder.adjust(1)
        
        # Оновлюємо повідомлення залежно від того, чи там є картинка
        if callback.message.text:
            await callback.message.edit_text(
                text=updated_text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
        elif callback.message.caption:
            await callback.message.edit_caption(
                caption=updated_text,
                reply_markup=builder.as_markup(),
                parse_mode="HTML"
            )
            
        await callback.answer(f"✅ Ви успішно взяли замовлення #{short_id}!", show_alert=False)
        
    except Exception as e:
        logger.error(f"Помилка take_order {order_id} від {taker_id}: {e}")
        await callback.answer("❌ Виникла помилка. Спробуйте ще раз.", show_alert=True)
