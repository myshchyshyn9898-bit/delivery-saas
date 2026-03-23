import asyncio
import json
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

# --- ОБРОБНИКИ КОМАНД ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject, state: FSMContext):
    user_id = message.from_user.id
    args = command.args

    # 1. Логіка приєднання персоналу (Кур'єр або Менеджер)
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

    # 2. Перевірка існуючого юзера
    context = db.get_user_context(user_id)
    if not context:
        # Якщо юзера немає - пропонуємо відкрити Web App
        await message.answer(
            "🌟 **Вітаємо в DelivePro!**\n\nЕволюція вашої доставки починається тут. Натисніть кнопку нижче, щоб налаштувати свій бізнес.",
            reply_markup=kb.reg_kb, 
            parse_mode="Markdown"
        )
    else:
        # Якщо вже зареєстрований - показуємо його дашборд/меню
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
                short_id = str(order_id)[:6].upper() # Короткий ID, як на скріні: #A4F6B2
                
                # Підготовка даних для красивого відображення
                pay_type_ua = "Готівка" if data['payment'] == "cash" else ("Термінал" if data['payment'] == "terminal" else "Онлайн")
                pay_icon = "💵" if data['payment'] == "cash" else ("💳" if data['payment'] == "terminal" else "🌐")
                
                # Формуємо посилання для карти
                address_query = data['address'].replace(' ', '+')
                map_url = f"https://www.google.com/maps/search/?api=1&query={address_query}"
                
                # КРАСИВИЙ ТЕКСТ ЯК НА СКРІНШОТІ
                # [\u200B] - це невидимий символ. Телеграм бачить посилання і генерує картинку карти зверху!
                courier_text = (
                    f"[\u200B]({map_url})📦 **НОВЕ ЗАМОВЛЕННЯ #{short_id}**\n"
                    f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                    f"Статус: 🟢 Активний\n\n"
                    f"📍 **Адреса:** {data['address']}\n"
                    f"📞 **Тел:** `{data['client_phone']}` ({data['client_name']})\n"
                    f"{pay_icon} **Оплата:** {data['amount']} zł ({pay_type_ua})\n"
                )
                
                if data['comment']:
                    courier_text += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n🗣 **Коментар:** {data['comment']}\n"

                # БУДУЄМО КНОПКИ (Маршрут, Дзвінок, Закрити)
                builder = InlineKeyboardBuilder()
                
                # Кнопка 1: Відкрити Google Maps (Маршрут)
                builder.button(text="🗺 Маршрут", url=f"https://www.google.com/maps/dir/?api=1&destination={address_query}")
                
                # Кнопка 2: Подзвонити (Витягуємо тільки цифри з номера)
                phone_clean = "".join(filter(str.isdigit, data['client_phone']))
                phone_url = f"tel:+{phone_clean}" if not phone_clean.startswith("+") else f"tel:{phone_clean}"
                builder.button(text="📞 Подзвонити", url=phone_url)
                
                # Кнопка 3: Закрити замовлення
                builder.button(text="✅ Доставлено (Закрити)", callback_data=f"finish_order_{order_id}")
                
                # Вишиковуємо кнопки кожну з нового рядка (1 в ряд)
                builder.adjust(1, 1, 1)
                
                # Відправляємо кур'єру
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
        # Змінюємо статус на завершено в БД
        db.update_order_status(order_id, "completed")
        
        # Красиво змінюємо текст (прибираємо зелений кружечок і ставимо галочку)
        new_text = callback.message.text.replace("Статус: 🟢 Активний", "Статус: ✅ ДОСТАВЛЕНО")
        
        # Прибираємо кнопки взагалі, бо замовлення закрите
        await callback.message.edit_text(new_text, reply_markup=None)
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
