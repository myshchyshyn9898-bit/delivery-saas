from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import SUPER_ADMIN_IDS
import database as db
from texts import get_text as _

router = Router()

# --- ПАНЕЛЬ СУПЕР-АДМІНА (/sa) ---
@router.message(Command("sa"))
async def super_admin_panel(message: types.Message):
    if message.from_user.id not in SUPER_ADMIN_IDS:
        return
    # ✅ ВИПРАВЛЕНО: додано await (раніше повертав корутину замість даних)
    businesses = await db.get_all_businesses()
    if not businesses:
        return await message.answer(_(message.from_user.language_code, 'sa_empty'))
    builder = InlineKeyboardBuilder()
    for b in businesses:
        builder.button(
            text=f"{'🟢' if b['is_active'] else '🔴'} {b['name']}",
            callback_data=f"manage_biz_{b['id']}"
        )
    builder.adjust(1)
    await message.answer(_(message.from_user.language_code, 'sa_manage'), reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("manage_biz_"))
async def manage_biz(callback: types.CallbackQuery):
    if callback.from_user.id not in SUPER_ADMIN_IDS:
        await callback.answer("❌ Доступ заборонено", show_alert=True)
        return
    biz_id = callback.data.replace("manage_biz_", "")
    biz = await db.get_business_by_id(biz_id)
    if not biz:
        await callback.answer("❌ Бізнес не знайдено", show_alert=True)
        return
    await db.update_subscription(biz_id, not biz['is_active'])
    # Скидаємо кеш власника щоб зміна підписки відобразилась одразу
    if biz.get('owner_id'):
        db.invalidate_user_cache(int(biz['owner_id']))
    await callback.answer(_(callback.from_user.language_code, 'sa_changed'))

    # ✅ ВИПРАВЛЕНО: раніше викликалось super_admin_panel(callback.message) —
    # callback.message це повідомлення з inline-клавіатурою, не звичайне.
    # Тепер будуємо оновлений список бізнесів і редагуємо поточне повідомлення.
    lang = callback.from_user.language_code
    businesses = await db.get_all_businesses()
    if not businesses:
        await callback.message.edit_text(_(lang, 'sa_empty'))
        return
    builder = InlineKeyboardBuilder()
    for b in businesses:
        builder.button(
            text=f"{'🟢' if b['is_active'] else '🔴'} {b['name']}",
            callback_data=f"manage_biz_{b['id']}"
        )
    builder.adjust(1)
    await callback.message.edit_text(_(lang, 'sa_manage'), reply_markup=builder.as_markup())
