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

    # 1. Логіка приєднання кур'єра через посилання
    if args and args.startswith("join_"):
        biz_id = args.replace("join_", "")
        biz = db.get_business_by_id(biz_id)
        if not biz:
            await message.answer("❌ Помилка: Посилання недійсне.")
            return
        
        await state.update_data(joining_biz_id=biz_id, biz_name=biz['name'])
        await state.set_state(RegStaff.waiting_for_name)
        await message.answer(f"👋 Приєднуємось до команди **{biz['name']}**!\nВведіть ваше Прізвище та Ім'я:")
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

# --- РЕЄСТРАЦІЯ БІЗНЕСУ ЧЕРЕЗ WEB APP ---
@dp.message(F.web_app_data)
async def handle_web_app_data(message: types.Message):
    # Розпаковуємо JSON, який прийшов від нашого delivepro.html
    data = json.loads(message.web_app_data.data)
    
    if data.get("action") == "register_business":
        user_id = message.from_user.id
        
        try:
            # 1. Записуємо в базу
            db.register_new_business(user_id, data)
            
            # 2. Отримуємо оновлений контекст юзера
            context = db.get_user_context(user_id)
            biz = context['biz']
            
            # 3. Видаємо вітальне повідомлення і дашборд!
            await message.answer(
                f"🎉 **Вітаємо! Ваш бізнес '{biz['name']}' успішно створено.**\n"
                f"📦 **Тариф:** {biz['plan'].upper()} (Активовано 7 днів тріалу)\n\n"
                f"Тепер ви можете перейти до повноцінного керування 👇",
                reply_markup=kb.get_owner_kb(biz['id']),
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"Помилка реєстрації: {e}")
            await message.answer("❌ Сталася помилка при реєстрації. Перевірте логи Railway.")

# --- РЕЄСТРАЦІЯ КУР'ЄРА ---
@dp.message(RegStaff.waiting_for_name)
async def process_staff_name(message: types.Message, state: FSMContext):
    name = message.text
    data = await state.get_data()
    biz_id = data['joining_biz_id']

    try:
        db.create_staff(message.from_user.id, name, biz_id, role='courier')
        await state.clear()
        
        context = db.get_user_context(message.from_user.id)
        await message.answer(f"✅ Вітаємо, {name}! Ви успішно приєдналися до команди.")
        await show_main_menu(message, context)
    except Exception as e:
        await message.answer(f"❌ Помилка: {e}")

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
    await super_admin_panel(callback.message) # Оновити список

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
