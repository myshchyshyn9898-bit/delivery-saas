import asyncio
import json
import urllib.parse
import os
import requests
import aiohttp
import datetime # <--- ДОДАНО ДЛЯ ЧАСУ В ЗВІТІ
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

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- СТАНИ (FSM) ---
class RegStaff(StatesGroup):
    waiting_for_name = State()

# --- ДОПОМІЖНА ФУНКЦІЯ МЕНЮ ---
async def show_main_menu(message: types.Message, context: dict):
    role = context['role']
    biz = context['biz']
    biz_id = biz['id']

    if not biz['is_active']:
        await message.answer("⚠️ **Ваша підписка закінчилася або призупинена.**\nБудь ласка, зверніться до адміністратора.")
        return

    # ДОДАНО message.from_user.id у виклики клавіатур
    if role == "owner":
        text = f"🏢 **Кабінет власника: {biz['name']}**"
        markup = kb.get_owner_kb(biz_id, message.from_user.id)
    elif role == "manager":
        text = f"👨‍💼 **Панель менеджера: {biz['name']}**"
        markup = kb.get_manager_kb(biz_id, message.from_user.id)
    else: # courier
        text = f"🛵 **Робоче місце кур'єра: {biz['name']}**"
        markup = kb.get_courier_kb(biz_id, message.from_user.id)

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
        
        tile_url = "https://cartodb-basemaps-a.global.ssl.fastly.net/light_all/{z}/{x}/{y}.png"
        m = StaticMap(600, 300, padding_x=30, padding_y=30, url_template=tile_url)
        
        m.add_line(Line(coordinates, '#4A6CF7', 5)) 
        m.add_marker(CircleMarker((start_lon, start_lat), '#34C759', 12)) 
        m.add_marker(CircleMarker((end_lon, end_lat), '#FF3B30', 12))       
        
        image = m.render()
        image.save(filename)
        return filename
    except Exception as e:
        print(f"Помилка карти: {e}")
        return None

async def get_route_map_file(biz: dict, client_address: str, order_id: str):
    """Асинхронна обгортка для отримання координат і запуску генератора"""
    c_lat, c_lon = None, None
    
    print(f"Шукаємо координати клієнта для: {client_address}")
    encoded_client = urllib.parse.quote(client_address)
    client_url = f"https://nominatim.openstreetmap.org/search?q={encoded_client}&format=json&limit=1"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(client_url, headers={'User-Agent': 'DeliveProBot/1.0'}) as resp:
            c_data = await resp.json()
            if c_data and len(c_data) > 0:
                c_lat, c_lon = float(c_data[0]['lat']), float(c_data[0]['lon'])
            else:
                print(f"❌ GPS не знайшов адресу клієнта: {client_address}")

    if not c_lat: return None 

    biz_address = biz.get('address') if biz else None
    b_lat, b_lon = 50.04132, 21.99901 
    
    if biz_address:
        encoded_biz = urllib.parse.quote(biz_address)
        biz_url = f"https://nominatim.openstreetmap.org/search?q={encoded_biz}&format=json&limit=1"
        async with aiohttp.ClientSession() as session:
            async with session.get(biz_url, headers={'User-Agent': 'DeliveProBot/1.0'}) as resp:
                b_data = await resp.json()
                if b_data and len(b_data) > 0:
                    b_lat, b_lon = float(b_data[0]['lat']), float(b_data[0]['lon'])

    filename = f"map_{order_id}.png"
    result_file = await asyncio.to_thread(generate_route_image_sync, b_lat, b_lon, c_lat, c_lon, filename)
    return result_file


# ==========================================
# --- НОВИЙ БЛОК: ГЕНЕРАЦІЯ ЗВІТУ ---
# ==========================================
@dp.message(F.text.in_(["📊 Зробити звіт", "/zvit"]))
async def cmd_generate_report(message: types.Message):
    context = db.get_user_context(message.from_user.id)
    if not context or context['role'] not in ['manager', 'owner']:
        await message.answer("❌ У вас немає доступу до звітів.")
        return
        
    biz = context['biz']
    report_data, total_cash, total_term = db.get_daily_report(biz['id'])
    
    if not report_data:
        await message.answer("📭 Сьогодні ще немає доставлених замовлень для звіту.")
        return
        
    # Час (додаємо +1 годину для Польщі, якщо сервер в UTC)
    now_time = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).strftime("%H:%M")
    
    text = f"📊 ЗВІТ ({now_time})\n➖ ➖ ➖ ➖ ➖\n"
    
    for c_id, stats in report_data.items():
        text += f"👤 {stats['name']}: {stats['count']} зам. | 💵 {stats['cash']:.2f} | 🏧 {stats['term']:.2f}\n"
        
    text += f"➖ ➖ ➖ ➖ ➖\n"
    text += f"💰 Готівка: {total_cash:.2f} {biz.get('currency', 'zł')}\n"
    text += f"💳 Термінал: {total_term:.2f} {biz.get('currency', 'zł')}"
    
    await message.answer(text)
# ==========================================


# ==========================================
# --- СЕКРЕТНА ПАНЕЛЬ ВЛАСНИКА БОТА ---
# ==========================================
@dp.message(Command("boss"))
async def cmd_boss_panel(message: types.Message):
    # Перевіряємо, чи є ID користувача у списку адмінів
    if message.from_user.id in SUPER_ADMIN_IDS:
        # Викликаємо клавіатуру з файлу keyboards.py (ДОДАНО message.from_user.id)
        await message.answer(
            "Вітаю, Бос! 🫡\nОсь доступ до керування всіма бізнесами:", 
            reply_markup=kb.get_superadmin_kb(message.from_user.id)
        )
    else:
        # Якщо хтось чужий введе команду, бот просто прикинеться дурником
        await message.answer("Я вас не розумію 🤷‍♂️")
# ==========================================


# --- ОБРОБНИКИ КОМАНД ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject, state: FSMContext):
    user_id = message.from_user.id
    args = command.args

    if args and (args.startswith("join_") or args.startswith("admin_")):
        role = "courier" if args.startswith("join_") else "manager"
        prefix = "join_" if role == "courier" else "admin_"
        biz_id = args.replace(prefix, "")
        
        biz = db.get_business_by_id(biz_id)
        if not biz:
            await message.answer("❌ Помилка: Посилання недійсне або такий заклад не існує.")
            return
        
        await state.update_data(joining_biz_id=biz_id, biz_name=biz['name'], joining_role=role)
        await state.set_state(RegStaff.waiting_for_name)
        
        role_ua = "Кур'єра 🛵" if role == "courier" else "Менеджера 👨‍💼"
        await message.answer(
            f"👋 Вітаємо!\nВи отримали запрошення на посаду **{role_ua}** у заклад **{biz['name']}**.\n\n"
            f"✏️ Введіть ваше Прізвище та Ім'я, щоб приєднатися до команди:"
        )
        return

    context = db.get_user_context(user_id)
    if not context:
        await message.answer(
            "🌟 **Вітаємо в DelivePro!**\n\nЕволюція вашої доставки починається тут. Натисніть кнопку нижче, щоб налаштувати свій бізнес.",
            reply_markup=kb.reg_kb, 
            parse_mode="Markdown"
        )
    else:
        await show_main_menu(message, context)

# --- ОБРОБКА ДАНИХ З WEB APP ---
@dp.message(F.web_app_data)
async def handle_web_app_data(message: types.Message, bot: Bot):
    data = json.loads(message.web_app_data.data)
    user_id = message.from_user.id
    
    if data.get("action") == "register_business":
        try:
            db.register_new_business(user_id, data)
            context = db.get_user_context(user_id)
            biz = context['biz']
            await message.answer(
                f"🎉 **Вітаємо! Ваш бізнес '{biz['name']}' успішно створено.**\n"
                f"📦 **Тариф:** {biz['plan'].upper()} (Активовано 7 днів тріалу)\n\n"
                f"Тепер ви можете перейти до повноцінного керування 👇",
                reply_markup=kb.get_owner_kb(biz['id'], user_id), # ДОДАНО user_id
                parse_mode="Markdown"
            )
        except Exception as e:
            await message.answer("❌ Сталася помилка при реєстрації. Перевірте логи Railway.")
            
    # 2. СТВОРЕННЯ НОВОГО ЗАМОВЛЕННЯ
    elif data.get("action") == "new_order":
        try:
            new_order = db.create_new_order(data)
            
            if new_order:
                order_id = new_order['id']
                short_id = str(order_id)[:6].upper()
                biz = db.get_business_by_id(data['biz_id'])
                
                # ПЕРЕВІРКА ТАРИФУ БІЗНЕСУ (PRO чи BASIC)
                is_pro = biz.get('plan', 'basic').lower() == 'pro'
                
                pay_type_ua = "Готівка" if data['payment'] == "cash" else ("Термінал" if data['payment'] == "terminal" else "Онлайн")
                pay_icon = "💵" if data['payment'] == "cash" else ("💳" if data['payment'] == "terminal" else "🌐")
                
                details_parts = []
                if data.get('apt'): details_parts.append(f"Кв/Оф: {data['apt']}")
                if data.get('code'): details_parts.append(f"Домофон: {data['code']}")
                details_text = f"🏢 **Деталі:** {', '.join(details_parts)}\n" if details_parts else ""

                address_query = urllib.parse.quote(data['address'])
                route_url = f"https://www.google.com/maps/dir/?api=1&destination={address_query}"
                
                phone_clean = "".join(filter(lambda x: x.isdigit() or x == '+', data['client_phone']))
                if not phone_clean.startswith('+'): phone_clean = '+' + phone_clean
                
                courier_text = (
                    f"📦 **НОВЕ ЗАМОВЛЕННЯ #{short_id}**\n"
                    f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                    f"Статус: 🟢 Активний\n\n"
                    f"📍 **Адреса:** {data['address']}\n"
                    f"{details_text}"
                    f"📞 **Тел:** {phone_clean} ({data['client_name']})\n"
                    f"{pay_icon} **Оплата:** {data['amount']} zł ({pay_type_ua})\n"
                )
                
                if data.get('comment'):
                    courier_text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n🗣 **Коментар:** {data['comment']}\n"

                builder = InlineKeyboardBuilder()
                
                # Якщо PRO - додаємо кнопку маршруту
                if is_pro:
                    builder.button(text="🗺 Відкрити маршрут", url=route_url)
                
                # Кнопку "Доставлено" додаємо завжди
                builder.button(text="✅ Доставлено (Закрити)", callback_data=f"finish_order_{order_id}")
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
                
                await message.answer(f"✅ Замовлення #{short_id} успішно створено та відправлено кур'єру!")
            else:
                await message.answer("❌ Помилка при збереженні замовлення в базу.")
                
        except Exception as e:
            print(f"Помилка створення замовлення: {e}")
            await message.answer("❌ Сталася помилка при відправці замовлення. Перевірте базу даних.")

# --- РЕЄСТРАЦІЯ КУР'ЄРА ТА МЕНЕДЖЕРА ---
@dp.message(RegStaff.waiting_for_name)
async def process_staff_name(message: types.Message, state: FSMContext):
    name = message.text
    data = await state.get_data()
    biz_id = data['joining_biz_id']
    role = data.get('joining_role', 'courier')

    try:
        db.create_staff(message.from_user.id, name, biz_id, role=role)
        await state.clear()
        
        context = db.get_user_context(message.from_user.id)
        role_ua = "Кур'єр" if role == "courier" else "Менеджер"
        
        await message.answer(f"✅ Вітаємо, {name}! Ви успішно приєдналися до команди як {role_ua}.")
        await show_main_menu(message, context)
    except Exception as e:
        await message.answer(f"❌ Помилка при додаванні: {e}. Можливо, ви вже працюєте тут.")

# --- ОБРОБКА КНОПКИ "ЗАКРИТИ ЗАМОВЛЕННЯ" ---
@dp.callback_query(F.data.startswith("finish_order_"))
async def finish_order_handler(callback: types.CallbackQuery):
    order_id = callback.data.replace("finish_order_", "")
    
    try:
        # 1. Дістаємо інфо про замовлення
        res = db.supabase.table("orders").select("*").eq("id", order_id).execute()
        
        # 2. Оновлюємо статус в базі
        db.update_order_status(order_id, "completed")
        
        # 3. Змінюємо текст повідомлення кур'єра
        if callback.message.caption: # Якщо повідомлення з картинкою (PRO)
            new_text = callback.message.caption.replace("Статус: 🟢 Активний", "Статус: ✅ ДОСТАВЛЕНО")
            await callback.message.edit_caption(caption=new_text, reply_markup=None, parse_mode="Markdown")
        elif callback.message.text: # Якщо повідомлення тільки з текстом (BASIC або помилка карти)
            new_text = callback.message.text.replace("Статус: 🟢 Активний", "Статус: ✅ ДОСТАВЛЕНО")
            await callback.message.edit_text(text=new_text, reply_markup=None, parse_mode="Markdown")
            
        await callback.answer("🎉 Замовлення успішно завершено! Гроші в касі.")
        
        # 4. Відправляємо сповіщення МЕНЕДЖЕРАМ (не власнику!)
        if res.data:
            order_info = res.data[0]
            biz_id = order_info['business_id']
            short_id = str(order_info['id'])[:6].upper()
            
            admin_text = (
                f"🔔 **Замовлення доставлено!**\n"
                f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                f"📦 Номер: `#{short_id}`\n"
                f"💰 Сума: {order_info['amount']} zł\n"
                f"🛵 Кур'єр: {callback.from_user.full_name}\n"
                f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                f"✅ Кур'єр знову **вільний** для роботи."
            )
            
            # Шукаємо всіх працівників з роллю manager для цього бізнесу
            managers_res = db.supabase.table("staff").select("user_id").eq("business_id", biz_id).eq("role", "manager").execute()
            
            if managers_res.data:
                for manager in managers_res.data:
                    try:
                        await bot.send_message(chat_id=manager['user_id'], text=admin_text, parse_mode="Markdown")
                    except Exception as e:
                        print(f"Не вдалося відправити сповіщення менеджеру {manager['user_id']}: {e}")
                
    except Exception as e:
        print(f"Помилка завершення: {e}")
        await callback.answer("❌ Помилка завершення", show_alert=True)

# --- ПАНЕЛЬ СУПЕР-АДМІНА (/sa) ---
@dp.message(Command("sa"))
async def super_admin_panel(message: types.Message):
    if message.from_user.id not in SUPER_ADMIN_IDS: return
    
    businesses = db.get_all_businesses()
    if not businesses:
        await message.answer("📭 Бізнесів поки немає.")
        return

    builder = InlineKeyboardBuilder()
    for b in businesses:
        status = "🟢" if b['is_active'] else "🔴"
        builder.button(text=f"{status} {b['name']}", callback_data=f"manage_biz_{b['id']}")
    builder.adjust(1)
    await message.answer("🚀 Керування SaaS:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("manage_biz_"))
async def manage_biz(callback: types.CallbackQuery):
    biz_id = callback.data.replace("manage_biz_", "")
    biz = db.get_business_by_id(biz_id)
    new_status = not biz['is_active']
    
    db.update_subscription(biz_id, new_status)
    await callback.answer(f"Статус змінено!")
    await super_admin_panel(callback.message)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
