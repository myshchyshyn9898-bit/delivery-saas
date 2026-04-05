import asyncio
import json
import urllib.parse
import os
import requests
import aiohttp
import datetime 
import math     
from aiohttp import web # <--- ДОДАНО ДЛЯ WEBHOOK WHOP
from staticmap import StaticMap, Line, CircleMarker
from aiogram.types import FSInputFile
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler # <--- ПОВЕРНУТО З ВЕРСІЇ 1.0 ДЛЯ ТАЙМЕРА

from config import API_TOKEN, SUPER_ADMIN_IDS
import database as db
import keyboards as kb
from texts import get_text as _  

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Warsaw") # Ініціалізація планувальника

# --- СТАНИ (FSM) ---
class RegStaff(StatesGroup):
    waiting_for_name = State()

# --- ДОПОМІЖНА ФУНКЦІЯ МЕНЮ ---
async def show_main_menu(message: types.Message, context: dict):
    lang = message.from_user.language_code
    role = context['role']
    biz = context['biz']
    biz_id = biz['id']

    # 🔴 ОХОРОНЕЦЬ: Перевірка статусу підписки
    actual_plan = db.get_actual_plan(biz_id)

    if not biz['is_active'] or actual_plan == "expired":
        text = "⚠️ **Ваш тестовий період або підписка завершилася!**\n\nЩоб продовжити роботу, будь ласка, відкрийте *Дашборд* та оберіть тариф (PRO)."
        builder = InlineKeyboardBuilder()
        builder.button(text="Відкрити Дашборд", web_app=types.WebAppInfo(url=f"https://твоє-посилання-на-дашборд?biz_id={biz_id}")) # ЗАМІНИ НА СВІЙ ЛІНК ДАШБОРДУ
        await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
        return

    if role == "owner":
        text = _(lang, 'owner_panel', name=biz['name'])
        markup = kb.get_owner_kb(biz_id, message.from_user.id, lang)
    elif role == "manager":
        text = _(lang, 'manager_panel', name=biz['name'])
        markup = kb.get_manager_kb(biz_id, message.from_user.id, lang)
    else: # courier
        text = _(lang, 'courier_panel', name=biz['name'])
        markup = kb.get_courier_kb(biz_id, message.from_user.id, lang)

    await message.answer(text, reply_markup=markup, parse_mode="Markdown")

# --- ГЕНЕРАТОР КАРТИ (МАРШРУТ) ---
def generate_route_image_sync(start_lat, start_lon, end_lat, end_lon, filename="map_preview.png"):
    try:
        url = f"http://router.project-osrm.org/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}?overview=full&geometries=geojson"
        headers = {'User-Agent': 'DeliveProBot/1.0'}
        r = requests.get(url, headers=headers, timeout=15)
        
        if r.status_code != 200: 
            print(f"OSRM помилка: {r.status_code} - {r.text}")
            return None
        
        route_data = r.json()
        if not route_data.get('routes'): 
            print("OSRM не знайшов маршрут")
            return None
        
        coordinates = route_data['routes'][0]['geometry']['coordinates']
        tile_url = "https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png"
        m = StaticMap(800, 450, padding_x=50, padding_y=50, url_template=tile_url)
        
        dot_spacing = 0.0003 
        for i in range(len(coordinates)-1):
            p1 = coordinates[i]
            p2 = coordinates[i+1]
            dist = math.hypot(p1[0] - p2[0], p1[1] - p2[1])
            steps = max(1, int(dist / dot_spacing))
            
            for j in range(steps):
                lon = p1[0] + (p2[0] - p1[0]) * (j / steps)
                lat = p1[1] + (p2[1] - p1[1]) * (j / steps)
                m.add_marker(CircleMarker((lon, lat), '#ff6b4a', 3))
                
        m.add_marker(CircleMarker(coordinates[-1], '#ff6b4a', 3))
        m.add_marker(CircleMarker((start_lon, start_lat), '#ffffff', 14)) 
        m.add_marker(CircleMarker((start_lon, start_lat), '#ff6b4a', 10)) 
        m.add_marker(CircleMarker((end_lon, end_lat), '#ffffff', 14)) 
        m.add_marker(CircleMarker((end_lon, end_lat), '#3b82f6', 10)) 
        
        image = m.render()
        image.save(filename)
        return filename
    except Exception as e:
        print(f"Помилка рендеру карти: {e}")
        return None

async def get_route_map_file(biz: dict, client_address: str, order_id: str):
    c_lat, c_lon = None, None
    encoded_client = urllib.parse.quote(client_address)
    client_url = f"https://nominatim.openstreetmap.org/search?q={encoded_client}&format=json&limit=1"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(client_url, headers={'User-Agent': 'DeliveProBot/1.0'}) as resp:
                if resp.status == 200:
                    c_data = await resp.json()
                    if c_data and len(c_data) > 0:
                        c_lat, c_lon = float(c_data[0]['lat']), float(c_data[0]['lon'])
    except Exception as e:
        print(f"❌ Критична помилка Nominatim: {e}")

    if not c_lat: return None 

    biz_address = biz.get('street') if biz else None
    b_lat, b_lon = 50.04132, 21.99901 
    
    if biz_address:
        encoded_biz = urllib.parse.quote(biz_address)
        biz_url = f"https://nominatim.openstreetmap.org/search?q={encoded_biz}&format=json&limit=1"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(biz_url, headers={'User-Agent': 'DeliveProBot/1.0'}) as resp:
                    if resp.status == 200:
                        b_data = await resp.json()
                        if b_data and len(b_data) > 0:
                            b_lat, b_lon = float(b_data[0]['lat']), float(b_data[0]['lon'])
        except Exception as e:
            pass

    filename = f"map_{order_id}.png"
    result_file = await asyncio.to_thread(generate_route_image_sync, b_lat, b_lon, c_lat, c_lon, filename)
    return result_file


# ==========================================
# --- БЛОК: ГЕНЕРАЦІЯ ЗВІТУ ---
# ==========================================
report_buttons = ["📊 Зробити звіт", "📊 Сделать отчет", "📊 Zrób raport", "📊 Make Report", "/zvit"]

@dp.message(F.text.in_(report_buttons))
async def cmd_generate_report(message: types.Message):
    lang = message.from_user.language_code
    context = db.get_user_context(message.from_user.id)
    if not context or context['role'] not in ['manager', 'owner']:
        await message.answer(_(lang, 'no_zvit_access'))
        return
        
    biz = context['biz']
    biz_id = biz['id']

    if db.get_actual_plan(biz_id) == "expired":
        await message.answer("⚠️ Підписка закінчилася. Відкрийте Дашборд для оплати.")
        return

    currency = biz.get('currency', 'zł')
    report_data, total_cash, total_term = db.get_daily_report(biz_id)
    
    if not report_data:
        await message.answer(_(lang, 'zvit_empty'))
        return
        
    now_time = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).strftime("%H:%M")
    text = _(lang, 'zvit_title', time=now_time)
    
    for c_id, stats in report_data.items():
        text += f"👤 {stats['name']}: {stats['count']} | 💵 {stats['cash']:.2f} | 🏧 {stats['term']:.2f}\n"
        
    text += f"➖ ➖ ➖ ➖ ➖\n"
    text += _(lang, 'zvit_cash', cash=f"{total_cash:.2f}", cur=currency)
    text += _(lang, 'zvit_term', term=f"{total_term:.2f}", cur=currency)
    
    await message.answer(text)

# ==========================================
# --- СЕКРЕТНА ПАНЕЛЬ ВЛАСНИКА БОТА ---
# ==========================================
@dp.message(Command("boss"))
async def cmd_boss_panel(message: types.Message):
    lang = message.from_user.language_code
    if message.from_user.id in SUPER_ADMIN_IDS:
        await message.answer(
            _(lang, 'boss_panel'), 
            reply_markup=kb.get_superadmin_kb(message.from_user.id, lang)
        )
    else:
        await message.answer(_(lang, 'dont_understand'))

# --- ОБРОБНИКИ КОМАНД ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject, state: FSMContext):
    user_id = message.from_user.id
    lang = message.from_user.language_code
    args = command.args

    if args and (args.startswith("c_") or args.startswith("m_")):
        prefix = args[:2] 
        token = args[2:]  
        
        role = "courier" if prefix == "c_" else "manager"
        try:
            res = db.supabase.table("businesses").select("*").eq("invite_token", token).execute()
            if not res.data:
                await message.answer(_(lang, 'link_invalid'))
                return
            
            biz = res.data[0]
            biz_id = biz['id']
        except Exception as e:
            print("Помилка пошуку токена:", e)
            await message.answer(_(lang, 'link_error'))
            return
        
        await state.update_data(joining_biz_id=biz_id, biz_name=biz['name'], joining_role=role)
        await state.set_state(RegStaff.waiting_for_name)
        
        role_ua = _(lang, 'role_c_full') if role == "courier" else _(lang, 'role_m_full')
        await message.answer(_(lang, 'invite_welcome', role=role_ua, biz_name=biz['name']))
        return

    context = db.get_user_context(user_id)
    if not context:
        await message.answer(
            _(lang, 'start_welcome'),
            reply_markup=kb.get_reg_kb(lang), 
            parse_mode="Markdown"
        )
    else:
        await show_main_menu(message, context)

# --- ОБРОБКА ДАНИХ З WEB APP ---
@dp.message(F.web_app_data)
async def handle_web_app_data(message: types.Message, bot: Bot):
    data = json.loads(message.web_app_data.data)
    user_id = message.from_user.id
    lang = message.from_user.language_code
    
    if data.get("action") == "register_business":
        try:
            db.register_new_business(user_id, data)
            context = db.get_user_context(user_id)
            biz = context['biz']
            await message.answer(
                _(lang, 'biz_created', biz_name=biz['name'], plan=biz['plan'].upper()),
                reply_markup=kb.get_owner_kb(biz['id'], user_id, lang),
                parse_mode="Markdown"
            )
        except Exception as e:
            await message.answer(_(lang, 'reg_error'))
            
    # 2. СТВОРЕННЯ НОВОГО ЗАМОВЛЕННЯ
    elif data.get("action") == "new_order":
        try:
            biz_id = data['biz_id']
            actual_plan = db.get_actual_plan(biz_id)
            if actual_plan == "expired":
                await message.answer("⚠️ Підписка закінчилася. Ви не можете створювати нові замовлення. Відкрийте Дашборд.")
                return

            # 🔴 ЗМІНА: Перевіряємо чи замовлення "Вільне"
            original_courier_id = data.get('courier_id')
            if original_courier_id == "unassigned":
                data['courier_id'] = None

            new_order = db.create_new_order(data)
            
            if new_order:
                order_id = new_order['id']
                short_id = str(order_id)[:6].upper()
                biz = db.get_business_by_id(biz_id)
                currency = biz.get('currency', 'zł')
                is_pro = actual_plan in ['pro', 'trial']
                courier_lang = lang 
                
                # ЯКЩО КУР'ЄРА БУЛО ОБРАНО ОДРАЗУ:
                if original_courier_id != "unassigned":
                    pay_type_str = _(courier_lang, 'pay_' + data['payment']) 
                    pay_icon = "💵" if data['payment'] == "cash" else ("💳" if data['payment'] == "terminal" else "🌐")
                    
                    details_parts = []
                    if data.get('apt'): details_parts.append(_(courier_lang, 'apt_prefix', apt=data['apt']))
                    if data.get('code'): details_parts.append(_(courier_lang, 'code_prefix', code=data['code']))
                    details_text = _(courier_lang, 'details_prefix', details=', '.join(details_parts)) if details_parts else ""

                    address_query = urllib.parse.quote(data['address'])
                    route_url = f"https://www.google.com/maps/dir/?api=1&destination={address_query}"
                    
                    phone_clean = "".join(filter(lambda x: x.isdigit() or x == '+', data['client_phone']))
                    if not phone_clean.startswith('+'): phone_clean = '+' + phone_clean
                    
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
                
                # ПОВІДОМЛЕННЯ ДЛЯ АДМІНА (ЗАЛЕЖНО ВІД РОЗПОДІЛУ)
                tracking_link = f"https://myshchyshyn9898-bit.github.io/delivery-saas/track.html?id={order_id}"
                if original_courier_id == "unassigned":
                    admin_final_text = (f"🟢 **ВІЛЬНЕ ЗАМОВЛЕННЯ #{short_id}**\n\n"
                                        f"Замовлення додано на карту! Відкрийте карту, щоб призначити кур'єра.\n"
                                        f"🔗 *Лінк для відстеження клієнтом:*\n`{tracking_link}`")
                else:
                    admin_base_text = _(lang, 'order_sent', short_id=short_id)
                    admin_final_text = f"{admin_base_text}\n\n🔗 *Лінк для відстеження клієнтом:*\n`{tracking_link}`"
                
                await message.answer(admin_final_text, parse_mode="Markdown")
            else:
                await message.answer(_(lang, 'order_save_err'))
        except Exception as e:
            print(f"Помилка створення замовлення: {e}")
            await message.answer(_(lang, 'order_send_err'))

    # ==========================================
    # 🔴 НОВИЙ БЛОК: ПРИЗНАЧЕННЯ З КАРТИ (АДМІНОМ)
    # ==========================================
    elif data.get("action") == "assign_order":
        try:
            order_id = data['order_id']
            courier_id = data['courier_id']
            
            res = db.supabase.table("orders").select("*").eq("id", order_id).execute()
            if not res.data: return
            order_db = res.data[0]
            
            biz_id = order_db['business_id']
            short_id = str(order_id)[:6].upper()
            biz = db.get_business_by_id(biz_id)
            currency = biz.get('currency', 'zł')
            is_pro = db.get_actual_plan(biz_id) in ['pro', 'trial']
            
            pay_type_str = _(lang, 'pay_' + order_db['pay_type']) 
            pay_icon = "💵" if order_db['pay_type'] == "cash" else ("💳" if order_db['pay_type'] == "terminal" else "🌐")
            
            status_active = _(lang, 'status_active_full')
            
            courier_text = _(lang, 'order_new', 
                             short_id=short_id, status=status_active, address=order_db['address'], 
                             details_text="", phone=order_db.get('client_phone','-'), client_name=order_db.get('client_name','Клієнт'), 
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
                
            await message.answer(f"✅ Замовлення #{short_id} успішно призначено!")
        except Exception as e:
            print(f"Помилка призначення з карти: {e}")
            await message.answer("❌ Помилка при призначенні замовлення.")

    # ==========================================
    # --- МАСОВА РОЗСИЛКА ВІД СУПЕРАДМІНА ---
    # ==========================================
    elif data.get("action") == "broadcast":
        if user_id not in SUPER_ADMIN_IDS: return
        msg_text = data.get("text")
        businesses = db.get_all_businesses()
        owner_ids = set([int(b['owner_id']) for b in businesses if b.get('owner_id')])
        
        if not owner_ids: return
        await message.answer(_(lang, 'broadcast_start', count=len(owner_ids)))
        sent_count = 0
        for oid in owner_ids:
            try:
                await bot.send_message(chat_id=oid, text=_('uk', 'broadcast_msg', text=msg_text), parse_mode="Markdown")
                sent_count += 1
                await asyncio.sleep(0.05)
            except: pass
        await message.answer(_(lang, 'broadcast_done', sent=sent_count, total=len(owner_ids)))

    # ==========================================
    # --- СЛУЖБА ПІДТРИМКИ (ТІКЕТИ) ---
    # ==========================================
    elif data.get("action") == "support_ticket":
        try:
            biz_id, reason, topic, message_text = data.get("biz_id", "?"), data.get("reason", "?"), data.get("topic", "?"), data.get("message", "?")
            admin_msg = (f"🆘 <b>НОВИЙ ТІКЕТ ПІДТРИМКИ</b>\n\n🏢 <b>Бізнес ID:</b> <code>{biz_id}</code>\n👤 <b>Від:</b> <a href='tg://user?id={user_id}'>Клієнт (ID: {user_id})</a>\n🏷 <b>Категорія:</b> {reason}\n📌 <b>Тема:</b> {topic}\n〰️〰️〰️〰️〰️〰️〰️〰️\n💬 <b>Повідомлення:</b>\n<i>{message_text}</i>")
            for admin_id in SUPER_ADMIN_IDS:
                try: await bot.send_message(chat_id=admin_id, text=admin_msg, parse_mode="HTML")
                except: pass
            await message.answer("✅ <b>Тікет успішно відправлено!</b> Наша служба підтримки зв'яжеться з вами найближчим часом.", parse_mode="HTML")
        except: pass

# --- РЕЄСТРАЦІЯ КУР'ЄРА ТА МЕНЕДЖЕРА ---
@dp.message(RegStaff.waiting_for_name)
async def process_staff_name(message: types.Message, state: FSMContext):
    name, lang = message.text, message.from_user.language_code
    data = await state.get_data()
    try:
        db.create_staff(message.from_user.id, name, data['joining_biz_id'], role=data.get('joining_role', 'courier'))
        await state.clear()
        context = db.get_user_context(message.from_user.id)
        await message.answer(_(lang, 'staff_added', name=name, role=_(lang, 'role_c') if data.get('joining_role', 'courier') == "courier" else _(lang, 'role_m')))
        await show_main_menu(message, context)
    except: await message.answer(_(lang, 'staff_add_err'))

# --- ОБРОБКА КНОПКИ "ЗАКРИТИ ЗАМОВЛЕННЯ" ---
@dp.callback_query(F.data.startswith("finish_order_"))
async def finish_order_handler(callback: types.CallbackQuery):
    order_id = callback.data.replace("finish_order_", "")
    lang = callback.from_user.language_code
    try:
        res = db.supabase.table("orders").select("*").eq("id", order_id).execute()
        db.update_order_status(order_id, "completed")
        
        status_active, status_done = _(lang, 'status_active_full'), _(lang, 'status_done_full')
        if callback.message.caption: 
            await callback.message.edit_caption(caption=callback.message.caption.replace(status_active, status_done), reply_markup=None, parse_mode="Markdown")
        elif callback.message.text: 
            await callback.message.edit_text(text=callback.message.text.replace(status_active, status_done), reply_markup=None, parse_mode="Markdown")
            
        await callback.answer(_(lang, 'finish_success'))
        if res.data:
            order_info = res.data[0]
            biz_id, short_id, currency = order_info['business_id'], str(order_info['id'])[:6].upper(), db.get_business_by_id(order_info['business_id']).get('currency', 'zł')
            managers_res = db.supabase.table("staff").select("user_id").eq("business_id", biz_id).eq("role", "manager").execute()
            if managers_res.data:
                for manager in managers_res.data:
                    try: await bot.send_message(chat_id=manager['user_id'], text=_(lang, 'finish_notify', short_id=short_id, amount=order_info['amount'], cur=currency, courier_name=callback.from_user.full_name), parse_mode="Markdown")
                    except: pass
    except: await callback.answer(_(lang, 'finish_err'), show_alert=True)

# --- ПАНЕЛЬ СУПЕР-АДМІНА (/sa) ---
@dp.message(Command("sa"))
async def super_admin_panel(message: types.Message):
    if message.from_user.id not in SUPER_ADMIN_IDS: return
    businesses = db.get_all_businesses()
    if not businesses: return await message.answer(_(message.from_user.language_code, 'sa_empty'))
    builder = InlineKeyboardBuilder()
    for b in businesses: builder.button(text=f"{'🟢' if b['is_active'] else '🔴'} {b['name']}", callback_data=f"manage_biz_{b['id']}")
    builder.adjust(1)
    await message.answer(_(message.from_user.language_code, 'sa_manage'), reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("manage_biz_"))
async def manage_biz(callback: types.CallbackQuery):
    biz_id = callback.data.replace("manage_biz_", "")
    db.update_subscription(biz_id, not db.get_business_by_id(biz_id)['is_active'])
    await callback.answer(_(callback.from_user.language_code, 'sa_changed'))
    await super_admin_panel(callback.message)

# ==========================================
# ⏱ ФОНОВИЙ ПРОЦЕС: ТАЙМЕР ЗАПІЗНЕНЬ (5 ХВ)
# ==========================================
async def check_late_orders():
    """Фонова задача, яка перевіряє всі активні замовлення і повідомляє про запізнення"""
    try:
        # Шукаємо незавершені замовлення
        res = db.supabase.table("orders").select("*").eq("status", "pending").execute()
        if not res.data: return
        
        now = datetime.datetime.now(datetime.timezone.utc)
        
        for order in res.data:
            # Якщо вже сповіщали про це замовлення - пропускаємо
            if order.get("is_late_notified"): continue
            
            created_at = datetime.datetime.fromisoformat(order["created_at"].replace("Z", "+00:00"))
            est_time = order.get("est_time", 30)
            
            # Дедлайн = час створення + заявлений час + 5 хв "буфера"
            deadline = created_at + datetime.timedelta(minutes=est_time + 5)
            
            if now > deadline:
                # 1. Помічаємо в базі, щоб не спамити
                db.supabase.table("orders").update({"is_late_notified": True}).eq("id", order["id"]).execute()
                
                biz_id = order["business_id"]
                short_id = str(order["id"])[:6].upper()
                
                # 2. Визначаємо, хто везе
                c_name = "Не призначено (На карті)"
                if order.get("courier_id"):
                    c_res = db.supabase.table("staff").select("name").eq("user_id", order["courier_id"]).execute()
                    if c_res.data:
                        c_name = c_res.data[0]["name"]
                        
                late_mins = int((now - created_at).total_seconds() / 60) - est_time
                
                # 3. Формуємо тривожне повідомлення
                msg = (
                    f"🚨 **ЗАПІЗНЕННЯ ЗАМОВЛЕННЯ!**\n\n"
                    f"📦 Замовлення `#{short_id}`\n"
                    f"📍 Адреса: {order.get('address')}\n"
                    f"📞 Тел: {order.get('client_phone')}\n"
                    f"🛵 Кур'єр: {c_name}\n\n"
                    f"⚠️ Запізнення вже на **{late_mins} хв**!"
                )
                
                # 4. Відправляємо всім менеджерам закладу
                managers_res = db.supabase.table("staff").select("user_id").eq("business_id", biz_id).eq("role", "manager").execute()
                if managers_res.data:
                    for m in managers_res.data:
                        try:
                            await bot.send_message(chat_id=m["user_id"], text=msg, parse_mode="Markdown")
                        except Exception:
                            pass
    except Exception as e:
        print(f"Помилка перевірки запізнень: {e}")

# ==========================================
# WEBHOOKS (WHOP + POSTER)
# ==========================================
async def whop_webhook_handler(request):
    try:
        data = await request.json()
        if data.get("event_type") == "membership.went_active":
            membership_data = data.get("data", {})
            biz_id, tg_user_id = membership_data.get("custom_fields", {}).get("biz_id"), membership_data.get("custom_fields", {}).get("tg_user_id")
            if biz_id:
                db.activate_whop_subscription(biz_id, "pro", membership_data.get("id"))
                if tg_user_id:
                    try: await bot.send_message(chat_id=int(tg_user_id), text="🎉 **Вітаємо! Оплата успішна!**\nТариф **PRO** активовано.", parse_mode="Markdown")
                    except: pass
        return web.Response(text="OK")
    except: return web.Response(status=500, text="Error")

async def poster_webhook_handler(request):
    try:
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
                    except: pass
        return web.Response(text="OK")
    except: return web.Response(status=500, text="Error")

async def start_webhook_server():
    app = web.Application()
    app.router.add_post('/webhook/whop', whop_webhook_handler)
    app.router.add_post('/webhook/poster', poster_webhook_handler) 
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌐 Webhook сервер запущено на порту {port}")

# ==========================================
# ГОЛОВНИЙ ЗАПУСК
# ==========================================
async def main():
    # ⏱ Запускаємо таймер запізнень (перевірка кожну 1 хвилину)
    scheduler.add_job(check_late_orders, "interval", minutes=1)
    scheduler.start()
    
    await start_webhook_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
