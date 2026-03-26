import asyncio
import json
import urllib.parse
import os
import requests
import aiohttp
import datetime # <--- ДОДАНО ДЛЯ ЧАСУ В ЗВІТІ
import math     # <--- ДОДАНО ДЛЯ РОЗРАХУНКУ ПУНКТИРУ
from staticmap import StaticMap, Line, CircleMarker
from aiogram.types import FSInputFile
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import API_TOKEN, SUPER_ADMIN_IDS
import database as db
import keyboards as kb
from texts import get_text as _  # <--- ІМПОРТ НАШОГО СЛОВНИКА

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- СТАНИ (FSM) ---
class RegStaff(StatesGroup):
    waiting_for_name = State()

# --- ДОПОМІЖНА ФУНКЦІЯ МЕНЮ ---
async def show_main_menu(message: types.Message, context: dict):
    lang = message.from_user.language_code
    role = context['role']
    biz = context['biz']
    biz_id = biz['id']

    if not biz['is_active']:
        await message.answer(_(lang, 'sub_expired'))
        return

    # ДОДАНО message.from_user.id та lang у виклики клавіатур
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
    """Синхронна функція для малювання карти"""
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
        
        # Світлий стиль карти (як на скріншоті Web App)
        tile_url = "https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png"
        m = StaticMap(800, 450, padding_x=50, padding_y=50, url_template=tile_url)
        
        # 1. МАЛЮЄМО ПОМАРАНЧЕВИЙ ШТРИХ-ПУНКТИР (імітація пунктиру через рівновіддалені крапки)
        dot_spacing = 0.0003 # Відстань між крапками (чим менше - тим густіше)
        for i in range(len(coordinates)-1):
            p1 = coordinates[i]
            p2 = coordinates[i+1]
            dist = math.hypot(p1[0] - p2[0], p1[1] - p2[1])
            steps = max(1, int(dist / dot_spacing))
            
            for j in range(steps):
                lon = p1[0] + (p2[0] - p1[0]) * (j / steps)
                lat = p1[1] + (p2[1] - p1[1]) * (j / steps)
                # Малюємо крапку пунктиру
                m.add_marker(CircleMarker((lon, lat), '#ff6b4a', 3))
                
        # Додаємо останню крапку наприкінці
        m.add_marker(CircleMarker(coordinates[-1], '#ff6b4a', 3))

        # 2. МАРКЕРИ (Іконки)
        # Магазин (старт) - Помаранчевий маркер з білою обводкою
        m.add_marker(CircleMarker((start_lon, start_lat), '#ffffff', 14)) # Обводка
        m.add_marker(CircleMarker((start_lon, start_lat), '#ff6b4a', 10)) # Центр
        
        # Клієнт (фініш) - Синій маркер з білою обводкою
        m.add_marker(CircleMarker((end_lon, end_lat), '#ffffff', 14)) # Обводка
        m.add_marker(CircleMarker((end_lon, end_lat), '#3b82f6', 10)) # Центр
        
        image = m.render()
        image.save(filename)
        return filename
    except Exception as e:
        print(f"Помилка рендеру карти: {e}")
        return None

async def get_route_map_file(biz: dict, client_address: str, order_id: str):
    """Асинхронна обгортка для отримання координат і запуску генератора"""
    c_lat, c_lon = None, None
    
    print(f"Шукаємо координати клієнта для: {client_address}")
    encoded_client = urllib.parse.quote(client_address)
    client_url = f"https://nominatim.openstreetmap.org/search?q={encoded_client}&format=json&limit=1"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(client_url, headers={'User-Agent': 'DeliveProBot/1.0'}) as resp:
                if resp.status == 200:
                    c_data = await resp.json()
                    if c_data and len(c_data) > 0:
                        c_lat, c_lon = float(c_data[0]['lat']), float(c_data[0]['lon'])
                    else:
                        print(f"❌ GPS не знайшов адресу клієнта: {client_address}")
                else:
                    print(f"❌ Помилка Nominatim (клієнт): {resp.status}")
    except Exception as e:
        print(f"❌ Критична помилка Nominatim: {e}")

    if not c_lat: return None 

    biz_address = biz.get('address') if biz else None
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
                    else:
                        print(f"❌ Помилка Nominatim (бізнес): {resp.status}")
        except Exception as e:
            print(f"❌ Критична помилка Nominatim (бізнес): {e}")

    filename = f"map_{order_id}.png"
    result_file = await asyncio.to_thread(generate_route_image_sync, b_lat, b_lon, c_lat, c_lon, filename)
    return result_file


# ==========================================
# --- НОВИЙ БЛОК: ГЕНЕРАЦІЯ ЗВІТУ ---
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
    currency = biz.get('currency', 'zł')
    report_data, total_cash, total_term = db.get_daily_report(biz['id'])
    
    if not report_data:
        await message.answer(_(lang, 'zvit_empty'))
        return
        
    # Час (додаємо +1 годину для Польщі, якщо сервер в UTC)
    now_time = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).strftime("%H:%M")
    
    text = _(lang, 'zvit_title', time=now_time)
    
    for c_id, stats in report_data.items():
        text += f"👤 {stats['name']}: {stats['count']} | 💵 {stats['cash']:.2f} | 🏧 {stats['term']:.2f}\n"
        
    text += f"➖ ➖ ➖ ➖ ➖\n"
    text += _(lang, 'zvit_cash', cash=f"{total_cash:.2f}", cur=currency)
    text += _(lang, 'zvit_term', term=f"{total_term:.2f}", cur=currency)
    
    await message.answer(text)
# ==========================================


# ==========================================
# --- СЕКРЕТНА ПАНЕЛЬ ВЛАСНИКА БОТА ---
# ==========================================
@dp.message(Command("boss"))
async def cmd_boss_panel(message: types.Message):
    lang = message.from_user.language_code
    # Перевіряємо, чи є ID користувача у списку адмінів
    if message.from_user.id in SUPER_ADMIN_IDS:
        # Викликаємо клавіатуру з файлу keyboards.py (ДОДАНО message.from_user.id ТА lang)
        await message.answer(
            _(lang, 'boss_panel'), 
            reply_markup=kb.get_superadmin_kb(message.from_user.id, lang)
        )
    else:
        # Якщо хтось чужий введе команду, бот просто прикинеться дурником
        await message.answer(_(lang, 'dont_understand'))
# ==========================================


# --- ОБРОБНИКИ КОМАНД ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject, state: FSMContext):
    user_id = message.from_user.id
    lang = message.from_user.language_code
    args = command.args

    # --- ОНОВЛЕНИЙ БЛОК ОБРОБКИ КОРОТКИХ ЛІНКІВ З ТОКЕНАМИ ---
    if args and (args.startswith("c_") or args.startswith("m_")):
        prefix = args[:2] 
        token = args[2:]  
        
        role = "courier" if prefix == "c_" else "manager"
        
        # Шукаємо бізнес прямо по унікальному токену
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
    # ----------------------------------------------

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
                reply_markup=kb.get_owner_kb(biz['id'], user_id, lang), # ДОДАНО lang
                parse_mode="Markdown"
            )
        except Exception as e:
            await message.answer(_(lang, 'reg_error'))
            
    # 2. СТВОРЕННЯ НОВОГО ЗАМОВЛЕННЯ
    elif data.get("action") == "new_order":
        try:
            new_order = db.create_new_order(data)
            
            if new_order:
                order_id = new_order['id']
                short_id = str(order_id)[:6].upper()
                biz = db.get_business_by_id(data['biz_id'])
                currency = biz.get('currency', 'zł')
                
                # ПЕРЕВІРКА ТАРИФУ БІЗНЕСУ (PRO чи BASIC)
                is_pro = biz.get('plan', 'basic').lower() == 'pro'

                # Визначаємо мову для повідомлення кур'єру (якщо не знаємо - беремо мову менеджера)
                courier_lang = lang 
                try:
                    # Спроба отримати мову безпосередньо кур'єра
                    c_info = await bot.get_chat(data['courier_id'])
                    # Залежно від API, іноді get_chat повертає мову
                except:
                    pass
                
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
                
                # Якщо PRO - додаємо кнопку маршруту
                if is_pro:
                    builder.button(text=_(courier_lang, 'btn_route'), url=route_url)
                
                # Кнопку "Доставлено" додаємо завжди
                builder.button(text=_(courier_lang, 'btn_finish'), callback_data=f"finish_order_{order_id}")
                builder.adjust(1) # Кнопки будуть стовпчиком

                # Якщо PRO - генеруємо та відправляємо карту
                if is_pro:
                    map_filename = await get_route_map_file(biz, data['address'], short_id)
                    
                    if map_filename and os.path.exists(map_filename):
                        photo = FSInputFile(map_filename)
                        await bot.send_photo(
                            chat_id=data['courier_id'], 
                            photo=photo,
                            caption=courier_text, 
                            reply_markup=builder.as_markup(),
                            parse_mode="Markdown"
                        )
                        os.remove(map_filename)
                    else:
                        await bot.send_message(
                            chat_id=data['courier_id'], 
                            text=courier_text, 
                            reply_markup=builder.as_markup(),
                            parse_mode="Markdown"
                        )
                # Якщо BASIC - відправляємо тільки текст без карти
                else:
                    await bot.send_message(
                        chat_id=data['courier_id'], 
                        text=courier_text, 
                        reply_markup=builder.as_markup(),
                        parse_mode="Markdown"
                    )
                
                await message.answer(_(lang, 'order_sent', short_id=short_id))
            else:
                await message.answer(_(lang, 'order_save_err'))
                
        except Exception as e:
            print(f"Помилка створення замовлення: {e}")
            await message.answer(_(lang, 'order_send_err'))

    # ==========================================
    # --- 3. МАСОВА РОЗСИЛКА ВІД СУПЕРАДМІНА ---
    # ==========================================
    elif data.get("action") == "broadcast":
        if user_id not in SUPER_ADMIN_IDS:
            await message.answer(_(lang, 'broadcast_no_access'))
            return
            
        msg_text = data.get("text")
        businesses = db.get_all_businesses()
        
        owner_ids = set()
        if businesses:
            for b in businesses:
                if b.get('owner_id'):
                    owner_ids.add(int(b['owner_id']))
                
        if not owner_ids:
            await message.answer(_(lang, 'broadcast_empty'))
            return
            
        await message.answer(_(lang, 'broadcast_start', count=len(owner_ids)))
        
        sent_count = 0
        for oid in owner_ids:
            try:
                # В ідеалі ми маємо знати мову власника, але відправляємо текст який написав адмін (поки базуємося на укр)
                msg_final = _('uk', 'broadcast_msg', text=msg_text) 
                await bot.send_message(
                    chat_id=oid, 
                    text=msg_final, 
                    parse_mode="Markdown"
                )
                sent_count += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                print(f"Не вдалося відправити повідомлення власнику {oid}: {e}")
                
        await message.answer(_(lang, 'broadcast_done', sent=sent_count, total=len(owner_ids)))
    # ==========================================

# --- РЕЄСТРАЦІЯ КУР'ЄРА ТА МЕНЕДЖЕРА ---
@dp.message(RegStaff.waiting_for_name)
async def process_staff_name(message: types.Message, state: FSMContext):
    name = message.text
    lang = message.from_user.language_code
    data = await state.get_data()
    biz_id = data['joining_biz_id']
    role = data.get('joining_role', 'courier')

    try:
        db.create_staff(message.from_user.id, name, biz_id, role=role)
        await state.clear()
        
        context = db.get_user_context(message.from_user.id)
        role_ua = _(lang, 'role_c') if role == "courier" else _(lang, 'role_m')
        
        await message.answer(_(lang, 'staff_added', name=name, role=role_ua))
        await show_main_menu(message, context)
    except Exception as e:
        await message.answer(_(lang, 'staff_add_err'))

# --- ОБРОБКА КНОПКИ "ЗАКРИТИ ЗАМОВЛЕННЯ" ---
@dp.callback_query(F.data.startswith("finish_order_"))
async def finish_order_handler(callback: types.CallbackQuery):
    order_id = callback.data.replace("finish_order_", "")
    lang = callback.from_user.language_code
    
    try:
        # 1. Дістаємо інфо про замовлення
        res = db.supabase.table("orders").select("*").eq("id", order_id).execute()
        
        # 2. Оновлюємо статус в базі
        db.update_order_status(order_id, "completed")
        
        status_active = _(lang, 'status_active_full')
        status_done = _(lang, 'status_done_full')

        # 3. Змінюємо текст повідомлення кур'єра
        if callback.message.caption: # Якщо повідомлення з картинкою (PRO)
            new_text = callback.message.caption.replace(status_active, status_done)
            await callback.message.edit_caption(caption=new_text, reply_markup=None, parse_mode="Markdown")
        elif callback.message.text: # Якщо повідомлення тільки з текстом (BASIC або помилка карти)
            new_text = callback.message.text.replace(status_active, status_done)
            await callback.message.edit_text(text=new_text, reply_markup=None, parse_mode="Markdown")
            
        await callback.answer(_(lang, 'finish_success'))
        
        # 4. Відправляємо сповіщення МЕНЕДЖЕРАМ (не власнику!)
        if res.data:
            order_info = res.data[0]
            biz_id = order_info['business_id']
            short_id = str(order_info['id'])[:6].upper()
            biz = db.get_business_by_id(biz_id)
            currency = biz.get('currency', 'zł')
            
            # Шукаємо всіх працівників з роллю manager для цього бізнесу
            managers_res = db.supabase.table("staff").select("user_id").eq("business_id", biz_id).eq("role", "manager").execute()
            
            if managers_res.data:
                for manager in managers_res.data:
                    try:
                        admin_text = _(lang, 'finish_notify', short_id=short_id, amount=order_info['amount'], cur=currency, courier_name=callback.from_user.full_name)
                        await bot.send_message(chat_id=manager['user_id'], text=admin_text, parse_mode="Markdown")
                    except Exception as e:
                        print(f"Не вдалося відправити сповіщення менеджеру {manager['user_id']}: {e}")
                
    except Exception as e:
        print(f"Помилка завершення: {e}")
        await callback.answer(_(lang, 'finish_err'), show_alert=True)

# --- ПАНЕЛЬ СУПЕР-АДМІНА (/sa) ---
@dp.message(Command("sa"))
async def super_admin_panel(message: types.Message):
    lang = message.from_user.language_code
    if message.from_user.id not in SUPER_ADMIN_IDS: return
    
    businesses = db.get_all_businesses()
    if not businesses:
        await message.answer(_(lang, 'sa_empty'))
        return

    builder = InlineKeyboardBuilder()
    for b in businesses:
        status = "🟢" if b['is_active'] else "🔴"
        builder.button(text=f"{status} {b['name']}", callback_data=f"manage_biz_{b['id']}")
    builder.adjust(1)
    await message.answer(_(lang, 'sa_manage'), reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("manage_biz_"))
async def manage_biz(callback: types.CallbackQuery):
    lang = callback.from_user.language_code
    biz_id = callback.data.replace("manage_biz_", "")
    biz = db.get_business_by_id(biz_id)
    new_status = not biz['is_active']
    
    db.update_subscription(biz_id, new_status)
    await callback.answer(_(lang, 'sa_changed'))
    await super_admin_panel(callback.message)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
