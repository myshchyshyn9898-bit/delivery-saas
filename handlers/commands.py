import datetime
import logging

from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import SUPER_ADMIN_IDS, BASE_URL
import database as db
import keyboards as kb
from texts import get_text as _

logger = logging.getLogger(__name__)
router = Router()

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
        text = _(lang, 'expired_trial_text')
        builder = InlineKeyboardBuilder()
        builder.button(text=_(lang, 'btn_open_dashboard'), web_app=types.WebAppInfo(url=f"{BASE_URL}dashboard.html?biz_id={biz_id}"))
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

# ==========================================
# --- БЛОК: ГЕНЕРАЦІЯ ЗВІТУ ---
# ==========================================
report_buttons = ["📊 Зробити звіт", "📊 Сделать отчет", "📊 Zrób raport", "📊 Make Report", "/zvit"]

@router.message(F.text.in_(report_buttons))
async def cmd_generate_report(message: types.Message):
    lang = message.from_user.language_code
    context = db.get_user_context(message.from_user.id)
    if not context or context['role'] not in ['manager', 'owner']:
        await message.answer(_(lang, 'no_zvit_access'))
        return
        
    biz = context['biz']
    biz_id = biz['id']

    if db.get_actual_plan(biz_id) == "expired":
        await message.answer(_(lang, 'expired_no_report'))
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
@router.message(Command("boss"))
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
@router.message(Command("start"))
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
            logger.error(f"Помилка пошуку токена: {e}")
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

# --- РЕЄСТРАЦІЯ КУР'ЄРА ТА МЕНЕДЖЕРА ---
@router.message(RegStaff.waiting_for_name)
async def process_staff_name(message: types.Message, state: FSMContext):
    name, lang = message.text, message.from_user.language_code
    data = await state.get_data()
    try:
        db.create_staff(message.from_user.id, name, data['joining_biz_id'], role=data.get('joining_role', 'courier'))
        await state.clear()
        context = db.get_user_context(message.from_user.id)
        await message.answer(_(lang, 'staff_added', name=name, role=_(lang, 'role_c') if data.get('joining_role', 'courier') == "courier" else _(lang, 'role_m')))
        await show_main_menu(message, context)
    except Exception as e:
        logger.error(f"Помилка додавання персоналу: {e}")
        await message.answer(_(lang, 'staff_add_err'))
