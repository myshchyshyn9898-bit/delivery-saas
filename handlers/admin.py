import logging

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import SUPER_ADMIN_IDS
import database as db
from texts import get_text as _

logger = logging.getLogger(__name__)
router = Router()

# --- ПАНЕЛЬ СУПЕР-АДМІНА (/sa) ---
@router.message(Command("sa"))
async def super_admin_panel(message: types.Message):
    if message.from_user.id not in SUPER_ADMIN_IDS:
        return
    # ✅ ВИПРАВЛЕНО: додано await (раніше повертав корутину замість даних)
    businesses = await db.get_all_businesses()
    lang = (message.from_user.language_code or 'en').split('-')[0].lower()
    if lang not in ('uk','ru','pl','en'): lang = 'en'
    if not businesses:
        return await message.answer(_(lang, 'sa_empty'))
    builder = InlineKeyboardBuilder()
    for b in businesses:
        builder.button(
            text=f"{'🟢' if b['is_active'] else '🔴'} {b['name']}",
            callback_data=f"manage_biz_{b['id']}"
        )
    builder.adjust(1)
    await message.answer(_(lang, 'sa_manage'), reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("manage_biz_"))
async def manage_biz(callback: types.CallbackQuery):
    if callback.from_user.id not in SUPER_ADMIN_IDS:
        await callback.answer("❌ Access denied", show_alert=True)
        return
    biz_id = callback.data.replace("manage_biz_", "")
    biz = await db.get_business_by_id(biz_id)
    if not biz:
        await callback.answer("❌ Business not found", show_alert=True)
        return
    new_active = not biz['is_active']
    await db.update_subscription(biz_id, new_active)

    # При деактивації — скасовуємо всі активні замовлення
    if not new_active:
        try:
            await db._run(
                lambda: db.supabase.table("orders")
                    .update({"status": "cancelled"})
                    .eq("business_id", biz_id)
                    .in_("status", ["pending", "delivering"])
                    .execute()
            )
            logger.info(f"[admin] Скасовано активні замовлення для biz={biz_id}")
        except Exception as e:
            logger.warning(f"[admin] Помилка скасування замовлень: {e}")
    # Якщо активуємо бізнес з expired планом — повертаємо trial на 7 днів
    if new_active and biz.get('plan') == 'expired':
        import datetime
        from datetime import timezone, timedelta
        trial_end = (datetime.datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        await db._run(
            lambda: db.supabase.table("businesses")
                .update({"plan": "trial", "subscription_expires_at": trial_end})
                .eq("id", biz_id)
                .execute()
        )
    # Скидаємо кеш власника щоб зміна підписки відобразилась одразу
    if biz.get('owner_id'):
        db.invalidate_user_cache(int(biz['owner_id']))
    lang = (callback.from_user.language_code or 'en').split('-')[0].lower()
    if lang not in ('uk','ru','pl','en'): lang = 'en'
    await callback.answer(_(lang, 'sa_changed'))
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

@router.callback_query(lambda c: c.data and c.data.startswith("fix_sub_"))
async def fix_subscription(callback: types.CallbackQuery):
    """Суперадмін: примусово скидає підписку на trial і очищає кеш."""
    if callback.from_user.id not in SUPER_ADMIN_IDS:
        await callback.answer("❌ Access denied", show_alert=True)
        return
    biz_id = callback.data.replace("fix_sub_", "")
    import datetime
    from datetime import timezone, timedelta
    trial_end = (datetime.datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    try:
        await db._run(
            lambda: db.supabase.table("businesses")
                .update({
                    "plan": "trial",
                    "is_active": True,
                    "subscription_expires_at": trial_end
                })
                .eq("id", biz_id)
                .execute()
        )
        # Знаходимо власника і скидаємо кеш
        biz = await db.get_business_by_id(biz_id)
        if biz and biz.get("owner_id"):
            db.invalidate_user_cache(int(biz["owner_id"]))
        await callback.answer("✅ Trial restored for 7 days, cache cleared!", show_alert=True)
    except Exception as e:
        await callback.answer(f"❌ Помилка: {e}", show_alert=True)
