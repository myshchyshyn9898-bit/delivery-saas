import datetime
import logging

from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import SUPER_ADMIN_IDS, BASE_URL, BUSINESS_TZ
import database as db
import keyboards as kb
from texts import get_text as _

logger = logging.getLogger(__name__)
router = Router()

# — СТАНИ (FSM) —

class RegStaff(StatesGroup):
    waiting_for_name = State()
    waiting_for_lang  = State()

class ShiftOpen(StatesGroup):
    waiting_photo = State()
    waiting_km    = State()

class ShiftClose(StatesGroup):
    waiting_photo = State()
    waiting_km    = State()

# — ДОПОМІЖНА ФУНКЦІЯ МЕНЮ —

async def show_main_menu(message: types.Message, context: dict):
    _raw_lang = message.from_user.language_code
    _tg_lang  = (_raw_lang or "en").split("-")[0].lower()
    import logging as _lg; _lg.getLogger(__name__).info(f"[lang debug] user={message.from_user.id} raw_lang={_raw_lang!r} normalized={_tg_lang!r}")

    role = context['role']
    biz  = context['biz']

    # ✅ Читаємо мову з БД (пріоритет над Telegram language_code)
    if role == 'owner':
        lang = (biz.get('lang') or _tg_lang)
    else:
        # Для персоналу - читаємо staff.lang через context
        lang = (context.get('staff', {}).get('lang') or _tg_lang)
    if lang not in ('uk', 'ru', 'pl', 'en'):
        lang = 'en'
    biz_id = biz['id']

    # Перевірка статусу підписки
    actual_plan = await db.get_actual_plan(biz_id)

    if not biz['is_active'] or actual_plan == "expired":
        if role == "owner":
            text = _(lang, 'expired_trial_text')
            import time as _t
            from keyboards import generate_token
            token = generate_token(biz_id=biz_id, user_id=message.from_user.id)
            t = int(_t.time())
            url = f"{BASE_URL}dashboard.html?biz_id={biz_id}&tg_id={message.from_user.id}&v={t}&token={token}"
            builder = InlineKeyboardBuilder()
            builder.button(text=_(lang, 'btn_open_dashboard'), web_app=types.WebAppInfo(url=url))
            await message.answer(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
        else:
            await message.answer(_(lang, 'expired_staff_text'), parse_mode="Markdown")
        return

    if role == "owner":
        text = _(lang, 'owner_panel', name=biz['name'])
        markup = kb.get_owner_kb(biz_id, message.from_user.id, lang)
    elif role == "manager":
        text = _(lang, 'manager_panel', name=biz['name'])
        markup = kb.get_manager_kb(biz_id, message.from_user.id, lang)
    else:  # courier
        text = _(lang, 'courier_panel', name=biz['name'])
        # Перевіряємо чи є активна зміна — щоб показати правильну кнопку
        active_shift = await db.get_active_shift(message.from_user.id, biz_id)
        markup = kb.get_courier_kb(biz_id, message.from_user.id, lang, shift_active=bool(active_shift))

    await message.answer(text, reply_markup=markup, parse_mode="Markdown")

# ==========================================
# — БЛОК: ЗМІНА КУР'ЄРА —
# ==========================================

start_shift_buttons = ["🟢 Розпочати зміну", "🟢 Начать смену", "🟢 Rozpocznij zmianę", "🟢 Start Shift"]
close_shift_buttons = ["🔴 Закрити зміну", "🔴 Закрыть смену", "🔴 Zakończ zmianę", "🔴 End Shift"]
shift_report_buttons = ["📋 Звіт змін", "📋 Отчёт смен", "📋 Raport zmian", "📋 Shift Report"]

# --- Кур'єр натискає "Розпочати зміну" ---
@router.message(F.text.in_(start_shift_buttons))
async def cmd_start_shift(message: types.Message, state: FSMContext):
    lang = (message.from_user.language_code or "en").split("-")[0].lower()
    ctx = await db.get_user_context_cached(message.from_user.id)
    if not ctx or ctx['role'] != 'courier':
        await message.answer(_(lang, 'no_access'))
        return
    biz_id = ctx['biz']['id']
    active = await db.get_active_shift(message.from_user.id, biz_id)
    if active:
        await message.answer(_(lang, 'shift_already_active'))
        return
    await state.set_state(ShiftOpen.waiting_photo)
    await state.update_data(biz_id=biz_id)
    await message.answer(_(lang, 'shift_send_start_photo'))

@router.message(ShiftOpen.waiting_photo, F.photo)
async def shift_open_got_photo(message: types.Message, state: FSMContext):
    lang = (message.from_user.language_code or "en").split("-")[0].lower()
    file_id = message.photo[-1].file_id
    await state.update_data(start_photo_id=file_id)
    await state.set_state(ShiftOpen.waiting_km)
    await message.answer(_(lang, 'shift_send_start_km'), parse_mode="HTML")

@router.message(ShiftOpen.waiting_km, F.text)
async def shift_open_got_km(message: types.Message, state: FSMContext):
    lang = (message.from_user.language_code or "en").split("-")[0].lower()
    if not message.text.strip().isdigit():
        await message.answer(_(lang, 'shift_km_invalid'), parse_mode="HTML")
        return
    km = int(message.text.strip())
    data = await state.get_data()
    biz_id = data['biz_id']
    start_photo_id = data['start_photo_id']
    user_id = message.from_user.id

    await db.open_shift(user_id, biz_id, km, start_photo_id)

    courier = await db.get_courier(user_id)
    name = courier['name'] if courier else str(user_id)

    await state.clear()
    await message.answer(
        _(lang, 'shift_started', name=name, km=km),
        parse_mode="HTML"
    )

    # Сповіщаємо адміна/менеджера про початок зміни
    await _notify_shift(
        biz_id=biz_id,
        sender_id=user_id,
        text=_(lang, 'shift_notify_start', name=name, km=km)
    )

    # Оновлюємо клавіатуру — тепер кнопка "Закрити зміну"
    ctx = await db.get_user_context_cached(user_id)
    markup = kb.get_courier_kb(biz_id, user_id, lang, shift_active=True)
    await message.answer("✅", reply_markup=markup)

# --- Кур'єр натискає "Закрити зміну" ---
@router.message(F.text.in_(close_shift_buttons))
async def cmd_close_shift(message: types.Message, state: FSMContext):
    lang = (message.from_user.language_code or "en").split("-")[0].lower()
    ctx = await db.get_user_context_cached(message.from_user.id)
    if not ctx or ctx['role'] != 'courier':
        await message.answer(_(lang, 'no_access'))
        return
    biz_id = ctx['biz']['id']
    active = await db.get_active_shift(message.from_user.id, biz_id)
    if not active:
        await message.answer(_(lang, 'shift_no_active'))
        return
    await state.set_state(ShiftClose.waiting_photo)
    await state.update_data(biz_id=biz_id, shift_id=active['id'], start_km=active['start_km'])
    await message.answer(_(lang, 'shift_send_end_photo'))

@router.message(ShiftOpen.waiting_photo)
async def shift_open_wrong_input(message: types.Message, state: FSMContext):
    """Кур'єр надіслав не фото під час очікування фото початку зміни"""
    lang = (message.from_user.language_code or "en").split("-")[0].lower()
    await message.answer(_(lang, 'shift_send_start_photo'))

@router.message(ShiftClose.waiting_photo)
async def shift_close_wrong_input(message: types.Message, state: FSMContext):
    """Кур'єр надіслав не фото під час очікування фото кінця зміни"""
    lang = (message.from_user.language_code or "en").split("-")[0].lower()
    await message.answer(_(lang, 'shift_send_end_photo'))

@router.message(ShiftClose.waiting_photo, F.photo)
async def shift_close_got_photo(message: types.Message, state: FSMContext):
    lang = (message.from_user.language_code or "en").split("-")[0].lower()
    file_id = message.photo[-1].file_id
    await state.update_data(end_photo_id=file_id)
    await state.set_state(ShiftClose.waiting_km)
    await message.answer(_(lang, 'shift_send_end_km'), parse_mode="HTML")

@router.message(ShiftClose.waiting_km, F.text)
async def shift_close_got_km(message: types.Message, state: FSMContext):
    lang = (message.from_user.language_code or "en").split("-")[0].lower()
    if not message.text.strip().isdigit():
        await message.answer(_(lang, 'shift_km_invalid'), parse_mode="HTML")
        return
    end_km = int(message.text.strip())
    data = await state.get_data()
    start_km = data['start_km']
    if end_km < start_km:
        await message.answer(_(lang, 'shift_km_less_than_start', start_km=start_km), parse_mode="HTML")
        return

    shift_id = data['shift_id']
    biz_id = data['biz_id']
    end_photo_id = data['end_photo_id']
    user_id = message.from_user.id

    await db.close_shift(shift_id, end_km, end_photo_id)

    courier = await db.get_courier(user_id)
    name = courier['name'] if courier else str(user_id)
    biz = await db.get_business_by_id(biz_id)
    currency = biz.get('currency', '₴') if biz else '₴'
    km_rate = await db.get_km_rate(biz_id)

    # Беремо зміну щоб знати started_at
    from database import _run, supabase
    shift_res = await _run(lambda: supabase.table("shifts").select("started_at").eq("id", shift_id).execute())
    since_iso = shift_res.data[0]['started_at'] if shift_res.data else None

    orders_count, cash, term = 0, 0.0, 0.0
    if since_iso:
        orders_count, cash, term = await db.get_shift_orders_stats(user_id, biz_id, since_iso)

    km_diff = end_km - start_km
    km_total = round(km_diff * km_rate, 2)
    to_pay = round(cash - km_total, 2)

    await state.clear()
    await message.answer(
        _(lang, 'shift_report',
          name=name, km=km_diff, orders=orders_count,
          cash=f"{cash:.2f}", term=f"{term:.2f}", cur=currency,
          rate=km_rate, km_total=f"{km_total:.2f}", to_pay=f"{to_pay:.2f}"),
        parse_mode="HTML"
    )

    # Сповіщаємо адміна/менеджера про кінець зміни
    await _notify_shift(
        biz_id=biz_id,
        sender_id=user_id,
        text=_(lang, 'shift_notify_end', name=name, km=end_km)
    )

    # Оновлюємо клавіатуру — кнопка "Розпочати зміну"
    markup = kb.get_courier_kb(biz_id, user_id, lang, shift_active=False)
    await message.answer("✅", reply_markup=markup)

# --- Допоміжна: сповіщає адміна/менеджера про початок або кінець зміни ---
async def _notify_shift(biz_id: str, sender_id: int, text: str):
    """
    Надсилає власнику та менеджерам просте текстове повідомлення
    про початок/кінець зміни кур'єра. Фото зберігається в БД і
    доступне тільки через "Звіт змін".
    """
    from bot_setup import bot
    from database import _run, supabase

    biz_res = await _run(lambda: supabase.table("businesses").select("owner_id").eq("id", biz_id).execute())
    recipients = []
    if biz_res.data:
        recipients.append(int(biz_res.data[0]['owner_id']))
    mgr_res = await _run(lambda: supabase.table("staff").select("user_id").eq("business_id", biz_id).eq("role", "manager").execute())
    if mgr_res.data:
        for m in mgr_res.data:
            uid = int(m['user_id'])
            if uid != sender_id and uid not in recipients:
                recipients.append(uid)
    for uid in recipients:
        try:
            await bot.send_message(uid, text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"[shift_notify] не вдалось надіслати uid={uid}: {e}")

# --- Адмін-звіт змін ---
@router.message(F.text.in_(shift_report_buttons))
async def cmd_shift_report(message: types.Message):
    lang = (message.from_user.language_code or "en").split("-")[0].lower()
    ctx = await db.get_user_context_cached(message.from_user.id)
    if not ctx or ctx['role'] not in ('owner', 'manager'):
        await message.answer(_(lang, 'no_zvit_access'))
        return
    biz_id = ctx['biz']['id']
    biz = ctx['biz']
    currency = biz.get('currency', '₴')
    km_rate = await db.get_km_rate(biz_id)

    shifts = await db.get_today_shifts_report(biz_id)
    closed = [s for s in shifts if s.get('ended_at')]
    active = [s for s in shifts if not s.get('ended_at')]

    if not closed and not active:
        await message.answer(_(lang, 'shift_admin_report_empty'))
        return

    # ✅ FIX: підтягуємо імена + замовлення одним запитом замість N+1
    from database import _run, supabase
    import datetime as _dt2
    from config import BUSINESS_TZ as _BTZ2
    staff_res = await _run(lambda: supabase.table("staff").select("user_id,name").eq("business_id", biz_id).execute())
    staff_map = {str(s['user_id']): s['name'] for s in (staff_res.data or [])}

    # Один запит на всі замовлення за сьогодні
    _now_local = _dt2.datetime.now(_BTZ2)
    _day_start = _now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(_dt2.timezone.utc).isoformat()
    orders_res = await _run(lambda: supabase.table("orders")
        .select("courier_id,amount,pay_type,status")
        .eq("business_id", biz_id).eq("status", "completed")
        .gte("completed_at", _day_start).execute())
    # Групуємо по кур'єру
    _orders_by = {}
    for o in (orders_res.data or []):
        cid = str(o['courier_id'])
        if cid not in _orders_by:
            _orders_by[cid] = {'count': 0, 'cash': 0.0, 'term': 0.0}
        _orders_by[cid]['count'] += 1
        if o.get('pay_type') == 'cash': _orders_by[cid]['cash'] += float(o.get('amount') or 0)
        elif o.get('pay_type') == 'terminal': _orders_by[cid]['term'] += float(o.get('amount') or 0)

    today = datetime.datetime.now(BUSINESS_TZ).strftime("%d.%m.%Y")
    text = _(lang, 'shift_admin_report_header', date=today)

    builder = InlineKeyboardBuilder()

    for s in closed:
        c_id = str(s['courier_id'])
        name = staff_map.get(c_id, "id:" + c_id)
        km_diff = (s.get('end_km') or 0) - (s.get('start_km') or 0)
        od = _orders_by.get(c_id, {'count': 0, 'cash': 0.0, 'term': 0.0})
        orders_count, cash, term = od['count'], od['cash'], od['term']
        km_total = round(km_diff * km_rate, 2)
        to_pay = round(cash - km_total, 2)

        text += _(lang, 'shift_admin_report_line',
                  name=name, km=km_diff, orders=orders_count,
                  cash=f"{cash:.2f}", term=f"{term:.2f}", cur=currency,
                  km_total=f"{km_total:.2f}", to_pay=f"{to_pay:.2f}")

        if s.get('start_photo_id'):
            builder.button(text=_(lang, "btn_photo_start", name=name), callback_data=f"shiftphoto:start:{s['id']}")
        if s.get('end_photo_id'):
            builder.button(text=_(lang, "btn_photo_end", name=name), callback_data=f"shiftphoto:end:{s['id']}")

    # ✅ FIX: показуємо активні зміни окремо
    if active:
        text += "\n" + _(lang, "shift_on_duty") + "\n"
        for s in active:
            c_id = str(s['courier_id'])
            name = staff_map.get(c_id, "id:" + c_id)
            started = s.get('started_at', '')
            start_time = ''
            if started:
                try:
                    import datetime as _dt3
                    t = _dt3.datetime.fromisoformat(started.replace('Z', '+00:00')).astimezone(BUSINESS_TZ)
                    start_time = t.strftime('%H:%M')
                except Exception:
                    pass
            od = _orders_by.get(c_id, {'count': 0, 'cash': 0.0, 'term': 0.0})
            text += "  \U0001f6f5 <b>" + name + "</b> з " + start_time + " \u00b7 " + str(od['count']) + " зам. \u00b7 \U0001f4b5 " + f"{od['cash']:.2f}" + " " + currency + "\n"

    builder.adjust(1)
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

# --- Callback: перегляд фото зміни ---
@router.callback_query(F.data.startswith("shiftphoto:"))
async def cb_shift_photo(callback: types.CallbackQuery):
    lang = (callback.from_user.language_code or "en").split("-")[0].lower()
    ctx = await db.get_user_context_cached(callback.from_user.id)
    if not ctx or ctx['role'] not in ('owner', 'manager'):
        await callback.answer(_(lang, 'no_access'), show_alert=True)
        return
    parts = callback.data.split(":")
    photo_type = parts[1]  # start / end
    shift_id = parts[2]

    from database import _run, supabase
    res = await _run(lambda: supabase.table("shifts").select("*").eq("id", shift_id).execute())
    if not res.data:
        await callback.answer(_(lang, 'shift_photo_not_found'), show_alert=True)
        return
    shift = res.data[0]

    staff_res = await _run(lambda: supabase.table("staff").select("name").eq("user_id", shift['courier_id']).execute())
    name = staff_res.data[0]['name'] if staff_res.data else str(shift['courier_id'])

    if photo_type == "start":
        photo_id = shift.get('start_photo_id')
        km = shift.get('start_km', '?')
        caption = _(lang, 'shift_photo_start_caption', name=name, km=km)
    else:
        photo_id = shift.get('end_photo_id')
        km = shift.get('end_km', '?')
        caption = _(lang, 'shift_photo_end_caption', name=name, km=km)

    if not photo_id:
        await callback.answer(_(lang, 'shift_photo_not_found'), show_alert=True)
        return

    await callback.message.answer_photo(photo_id, caption=caption)
    await callback.answer()

# ==========================================
# — БЛОК: ГЕНЕРАЦІЯ ЗВІТУ —
# ==========================================

report_buttons = ["📊 Зробити звіт", "📊 Сделать отчет", "📊 Zrób raport", "📊 Make Report", "/zvit"]

# Всі переклади кнопок "Налаштування" та "Персонал"
settings_buttons = [
    "⚙️ Налаштування бізнесу", "⚙️ Настройки бизнеса",
    "⚙️ Ustawienia firmy",     "⚙️ Business Settings",
]
staff_buttons = [
    "👥 Персонал", "👥 Personel", "👥 Staff",
]

# ==========================================
# — БЛОК: НАЛАШТУВАННЯ БІЗНЕСУ —
# ==========================================

@router.message(F.text.in_(settings_buttons))
async def cmd_open_settings(message: types.Message):
    """✅ ВИПРАВЛЕНО: кнопка 'Налаштування бізнесу' більше не мовчить."""
    import time as _t
    from keyboards import generate_token
    lang = (message.from_user.language_code or "en").split("-")[0].lower()
    context = await db.get_user_context_cached(message.from_user.id)
    if not context or context['role'] != 'owner':
        await message.answer(_(lang, 'no_access'))
        return
    biz_id = context['biz']['id']
    user_id = message.from_user.id
    token = generate_token(biz_id=biz_id, user_id=user_id)
    t = int(_t.time())
    url = f"{BASE_URL}dashboard.html?biz_id={biz_id}&tg_id={user_id}&v={t}&token={token}#settings"
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text=_(lang, 'btn_settings'), web_app=types.WebAppInfo(url=url))
    await message.answer(_(lang, 'btn_settings'), reply_markup=builder.as_markup())


# ==========================================
# — БЛОК: ПЕРСОНАЛ —
# ==========================================

@router.message(F.text.in_(staff_buttons))
async def cmd_open_staff(message: types.Message):
    """✅ ВИПРАВЛЕНО: кнопка 'Персонал' більше не мовчить."""
    import time as _t
    from keyboards import generate_token
    lang = (message.from_user.language_code or "en").split("-")[0].lower()
    context = await db.get_user_context_cached(message.from_user.id)
    if not context or context['role'] != 'owner':
        await message.answer(_(lang, 'no_access'))
        return
    biz_id = context['biz']['id']
    user_id = message.from_user.id
    token = generate_token(biz_id=biz_id, user_id=user_id)
    t = int(_t.time())
    url = f"{BASE_URL}dashboard.html?biz_id={biz_id}&tg_id={user_id}&v={t}&token={token}#staff"
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text=_(lang, 'btn_staff'), web_app=types.WebAppInfo(url=url))
    await message.answer(_(lang, 'btn_staff'), reply_markup=builder.as_markup())

@router.message(F.text.in_(report_buttons))
async def cmd_generate_report(message: types.Message):
    lang = (message.from_user.language_code or "en").split("-")[0].lower()
    context = await db.get_user_context_cached(message.from_user.id)
    if not context or context['role'] not in ['manager', 'owner']:
        await message.answer(_(lang, 'no_zvit_access'))
        return

    biz = context['biz']
    biz_id = biz['id']

    if await db.get_actual_plan(biz_id) == "expired":
        await message.answer(_(lang, 'expired_no_report'))
        return

    currency = biz.get('currency', 'zł')
    report_data, total_cash, total_term, total_online, total_online_sum = await db.get_daily_report(biz_id)

    if not report_data:
        await message.answer(_(lang, 'zvit_empty'))
        return

    # ✅ ВИПРАВЛЕНО: враховує DST (UTC+1 зимою, UTC+2 влітку)
    now_time = datetime.datetime.now(tz=BUSINESS_TZ).strftime("%H:%M")
    text = _(lang, 'zvit_title', time=now_time)

    for c_id, stats in report_data.items():
        line = _(lang, 'report_courier_line', name=stats['name'], count=stats['count'])
        if stats['cash'] > 0:
            line += f" | 💵 {stats['cash']:.2f}"
        if stats['term'] > 0:
            line += f" | 🏧 {stats['term']:.2f}"
        if stats.get('online', 0) > 0:
            line += _(lang, 'report_online_line', count=stats['online'], sum=f"{stats.get('online_sum', 0.0):.2f}")
        text += line + "\n"

    text += "➖ ➖ ➖ ➖ ➖\n"
    text += _(lang, 'zvit_cash', cash=f"{total_cash:.2f}", cur=currency)
    text += "\n"
    text += _(lang, 'zvit_term', term=f"{total_term:.2f}", cur=currency)
    if total_online > 0:
        text += _(lang, 'report_online_summary', count=total_online, sum=f"{total_online_sum:.2f}", cur=currency)

    await message.answer(text)

# ==========================================
# — СЕКРЕТНА ПАНЕЛЬ ВЛАСНИКА БОТА —
# ==========================================

@router.message(Command("boss"))
async def cmd_boss_panel(message: types.Message):
    lang = (message.from_user.language_code or "en").split("-")[0].lower()
    if message.from_user.id in SUPER_ADMIN_IDS:
        await message.answer(
            _(lang, 'boss_panel'),
            reply_markup=kb.get_superadmin_kb(message.from_user.id, lang)
        )
    else:
        await message.answer(_(lang, 'dont_understand'))

# — ОБРОБНИКИ КОМАНД —

@router.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject, state: FSMContext):
    user_id = message.from_user.id
    lang = (message.from_user.language_code or "en").split("-")[0].lower()
    args = command.args

    # Скидаємо FSM стан якщо юзер надіслав /start під час незавершеного флоу
    current_state = await state.get_state()
    if current_state and not (args and (args.startswith("c_") or args.startswith("m_"))):
        await state.clear()

    if args and (args.startswith("c_") or args.startswith("m_")):
        prefix = args[:2]
        token = args[2:]

        role = "courier" if prefix == "c_" else "manager"

        # Власник не може стати кур'єром/менеджером
        existing = await db.get_user_context_cached(user_id)
        if existing and existing['role'] == 'owner':
            await message.answer(_(lang, 'dont_understand'))
            await show_main_menu(message, existing)
            return

        try:
            res = await db._run(lambda: db.supabase.table("businesses").select("*").eq("invite_token", token).execute())
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

    context = await db.get_user_context_cached(user_id)
    if not context:
        await message.answer(
            _(lang, 'start_welcome'),
            reply_markup=kb.get_reg_kb(lang),
            parse_mode="Markdown"
        )
    else:
        await show_main_menu(message, context)

# — РЕЄСТРАЦІЯ КУР'ЄРА ТА МЕНЕДЖЕРА —

@router.message(RegStaff.waiting_for_name)
async def process_staff_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    lang = (message.from_user.language_code or "en").split("-")[0].lower()
    # ✅ FIX: валідація імені — мін 2, макс 50 символів, не команда
    if len(name) < 2 or len(name) > 50 or name.startswith("/"):
        await message.answer(_(lang, 'name_invalid') if 'name_invalid' in str(_(lang, 'name_invalid')) else "⚠️ Введіть ім'я: від 2 до 50 символів.")
        return
    data = await state.get_data()
    biz_id = data['joining_biz_id']
    user_id = message.from_user.id

    # Перевіряємо чи вже зареєстрований в цьому бізнесі
    try:
        existing = await db._run(
            lambda: db.supabase.table("staff")
                .select("user_id, role")
                .eq("user_id", user_id)
                .eq("business_id", biz_id)
                .execute()
        )
        if existing.data:
            await state.clear()
            db.invalidate_user_cache(user_id)
            context = await db.get_user_context_cached(user_id)
            role_label = _(lang, 'role_c') if existing.data[0].get('role') == 'courier' else _(lang, 'role_m')
            await message.answer(_(lang, 'already_registered', role=role_label))
            if context:
                await show_main_menu(message, context)
            return
    except Exception as e:
        logger.warning(f"Помилка перевірки існуючого staff: {e}")

    # ✅ Зберігаємо ім'я і просимо вибрати мову
    await state.update_data(staff_name=name, staff_lang=lang)
    await state.set_state(RegStaff.waiting_for_lang)

    lang_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🇺🇦 Українська", callback_data="setlang:uk"),
            InlineKeyboardButton(text="🇬🇧 English", callback_data="setlang:en"),
        ],
        [
            InlineKeyboardButton(text="🇵🇱 Polski", callback_data="setlang:pl"),
            InlineKeyboardButton(text="🇷🇺 Русский", callback_data="setlang:ru"),
        ],
    ])
    await message.answer(
        _(lang, "choose_lang"),
        reply_markup=lang_kb
    )
    return

    # (unreachable - kept for reference)
    db.invalidate_user_cache(message.from_user.id)
    await state.clear()
    context = await db.get_user_context_cached(message.from_user.id)
    role_label = _(lang, 'role_c') if data.get('joining_role', 'courier') == "courier" else _(lang, 'role_m')
    await message.answer(_(lang, 'staff_added', name=name, role=role_label))
    if context:
        try:
            await show_main_menu(message, context)
        except Exception as e:
            logger.error(f"Помилка показу меню після реєстрації: {e}")
    else:
        # ✅ FIX: retry через 0.7с замість загадкового "натисніть /start"
        import asyncio as _aio
        await _aio.sleep(0.7)
        db.invalidate_user_cache(message.from_user.id)
        context = await db.get_user_context_cached(message.from_user.id)
        if context:
            try:
                await show_main_menu(message, context)
            except Exception as e:
                logger.error(f"Помилка показу меню (retry): {e}")
        else:
            await message.answer(_(lang, "start_menu_hint"))

@router.callback_query(RegStaff.waiting_for_lang, F.data.startswith("setlang:"))
async def process_staff_lang(callback: types.CallbackQuery, state: FSMContext):
    """Обробляє вибір мови при реєстрації персоналу"""
    chosen_lang = callback.data.split(":")[1]  # setlang:uk -> uk
    data = await state.get_data()
    
    name    = data['staff_name']
    biz_id  = data['joining_biz_id']
    role    = data.get('joining_role', 'courier')
    user_id = callback.from_user.id

    try:
        await db.create_staff(user_id, name, biz_id, role=role, lang=chosen_lang)
    except Exception as e:
        logger.error(f"Помилка додавання персоналу: {e}")
        await callback.message.answer(_(chosen_lang, 'staff_add_err'))
        await callback.answer()
        return

    db.invalidate_user_cache(user_id)
    await state.clear()
    await callback.message.delete()
    await callback.message.answer(_(chosen_lang, "lang_saved"))

    context = await db.get_user_context_cached(user_id)
    role_label = _(chosen_lang, 'role_c') if role == 'courier' else _(chosen_lang, 'role_m')
    await callback.message.answer(_(chosen_lang, 'staff_added', name=name, role=role_label))
    if context:
        try:
            await show_main_menu(callback.message, context)
        except Exception as e:
            logger.warning(f"Не вдалось показати меню: {e}")
    await callback.answer()


