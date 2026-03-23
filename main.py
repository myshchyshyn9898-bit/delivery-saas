import asyncio
import json
import urllib.parse
import os
import requests
import aiohttp
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

    if role == "owner":
        text = f"🏢 **Кабінет власника: {biz['name']}**"
        markup = kb.get_owner_kb(biz_id)
    elif role == "manager":
        text = f"👨‍💼 **Панель менеджера: {biz['name']}**"
        markup = kb.get_manager_kb(biz_id)
    else: # courier
        text = f"🛵 **Робоче місце кур'єра: {biz['name']}**"
        markup = kb.get_courier_kb(biz_id)

    await message.answer(text, reply_markup=markup, parse_mode="Markdown")

# --- ГЕНЕРАТОР КАРТИ (МАРШРУТ) ---
def generate_route_image_sync(start_lat, start_lon, end_lat, end_lon, filename="map_preview.png"):
    """Синхронна функція для малювання карти (твій старий код, адаптований)"""
    try:
        url = f"http://router.project-osrm.org/route/v1/driving/{start_lon},{start_lat};{end_lon},{end_lat}?overview=full&geometries=geojson"
        r = requests.get(url, timeout=15)
        if r.status_code != 200: return None
        
        route_data = r.json()
        if not route_data.get('routes'): return None
        
        coordinates = route_data['routes'][0]['geometry']['coordinates']
        
        m = StaticMap(600, 300, 10)
        m.add_line(Line(coordinates, 'blue', 4))
        m.add_marker(CircleMarker((start_lon, start_lat), 'green', 12)) # Точка бізнесу (Зелена)
        m.add_marker(CircleMarker((end_lon, end_lat), 'red', 12))       # Точка клієнта (Червона)
        
        image = m.render()
        image.save(filename)
        return filename
    except Exception as e:
        print(f"Помилка карти: {e}")
        return None

async def get_route_map_file(biz: dict, client_address: str, order_id: str):
    """Асинхронна обгортка для отримання координат і запуску генератора"""
    c_lat, c_lon = None, None
    
    # 1. Шукаємо координати клієнта
    encoded_client = urllib.parse.quote(client_address)
    client_url = f"https://nominatim.openstreetmap.org/search?q={encoded_client}&format=json&limit=1"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(client_url, headers={'User-Agent': 'DeliveProBot/1.0'}) as resp:
            c_data = await resp.json()
            if c_data:
                c_lat, c_lon = float(c_data[0]['lat']), float(c_data[0]['lon'])

    if not c_lat: return None # Якщо адресу не знайдено

    # 2. Шукаємо координати бізнесу (або ставимо дефолтні, якщо адреса не вказана)
    biz_address = biz.get('address') if biz else None
    b_lat, b_lon = 50.04132, 21.99901 # Дефолт: центр Жешува (щоб не падало, якщо адреси немає)
    
    if biz_address:
        encoded_biz = urllib.parse.quote(biz_address)
        biz_url = f"https://nominatim.openstreetmap.org/search?q={encoded_biz}&format=json&limit=1"
        async with session.get(biz_url, headers={'User-Agent': 'DeliveProBot/1.0'}) as resp:
            b_data = await resp.json()
            if b_data:
                b_lat, b_lon = float(b_data[0]['lat']), float(b_data[0]['lon'])

    # 3. Малюємо карту у фоновому потоці (щоб бот не зависав)
    filename = f"map_{order_id}.png"
    result_file = await asyncio.to_thread(generate_route_image_sync, b_lat, b_lon, c_lat, c_lon, filename)
    return result_file

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
                reply_markup=kb.get_owner_kb(biz['id']),
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
                
                pay_type_ua = "Готівка" if data['payment'] == "cash" else ("Термінал" if data['payment'] == "terminal" else "Онлайн")
                pay_icon = "💵" if data['payment'] == "cash" else ("💳" if data['payment'] == "terminal" else "🌐")
                
                address_query = urllib.parse.quote(data['address'])
                route_url = f"https://www.google.com/maps/dir/?api=1&destination={address_query}"
                
                phone_clean = "".join(filter(lambda x: x.isdigit() or x == '+', data['client_phone']))
                if not phone_clean.startswith('+'): phone_clean = '+' + phone_clean
                
                courier_text = (
                    f"📦 **НОВЕ ЗАМОВЛЕННЯ #{short_id}**\n"
                    f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                    f"Статус: 🟢 Активний\n\n"
                    f"📍 **Адреса:** {data['address']}\n"
                    f"📞 **Тел:** {phone_clean} ({data['client_name']})\n"
                    f"{pay_icon} **Оплата:** {data['amount']} zł ({pay_type_ua})\n"
                )
                
                if data['comment']:
                    courier_text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n🗣 **Коментар:** {data['comment']}\n"

                builder = InlineKeyboardBuilder()
                builder.button(text="🗺 Відкрити маршрут", url=route_url)
                builder.button(text="✅ Доставлено (Закрити)", callback_data=f"finish_order_{order_id}")
                builder.adjust(1, 1)

                # ГЕНЕРУЄМО КАРТУ З СИНЬОЮ ЛІНІЄЮ (ФАЙЛ)
                map_filename = await get_route_map_file(biz, data['address'], short_id)
                
                if map_filename and os.path.exists(map_filename):
                    # Відправляємо фото з картою
                    photo = FSInputFile(map_filename)
                    await bot.send_photo(
                        chat_id=data['courier_id'], 
                        photo=photo,
                        caption=courier_text, 
                        reply_markup=builder.as_markup(),
                        parse_mode="Markdown"
                    )
                    os.remove(map_filename) # Очищаємо пам'ять сервера
                else:
                    # Якщо карта чомусь не згенерувалася, відправляємо звичайним текстом
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
        db.update_order_status(order_id, "completed")
        
        # Перевіряємо чи це повідомлення з картинкою чи звичайне
        if callback.message.caption:
            new_text = callback.message.caption.replace("Статус: 🟢 Активний", "Статус: ✅ ДОСТАВЛЕНО")
            await callback.message.edit_caption(caption=new_text, reply_markup=None, parse_mode="Markdown")
        else:
            new_text = callback.message.text.replace("Статус: 🟢 Активний", "Статус: ✅ ДОСТАВЛЕНО")
            await callback.message.edit_text(text=new_text, reply_markup=None, parse_mode="Markdown")
            
        await callback.answer("🎉 Замовлення успішно завершено! Гроші в касі.")
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
