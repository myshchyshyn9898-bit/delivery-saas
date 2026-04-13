import asyncio
import logging

from bot_setup import bot, dp, scheduler
from handlers import commands, orders, admin
from handlers.webhooks import start_webhook_server
from handlers.scheduler import check_late_orders
from middleware import ThrottlingMiddleware

# ==========================================
# НАЛАШТУВАННЯ ЛОГУВАННЯ
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

dp.include_router(commands.router)
dp.include_router(orders.router)
dp.include_router(admin.router)

# ✅ ВИПРАВЛЕНО: Rate limiting — 1 повідомлення/сек, 0.5 сек для кнопок
dp.message.middleware(ThrottlingMiddleware(rate_limit=1.0))
dp.callback_query.middleware(ThrottlingMiddleware(rate_limit=0.5))

# ==========================================
# ГОЛОВНИЙ ЗАПУСК
# ==========================================
async def main():
    logger.info("🚀 Запуск DeliPro бота...")

    # Таймер запізнень — перевірка кожну хвилину
    scheduler.add_job(check_late_orders, "interval", minutes=1)
    scheduler.start()
    logger.info("⏱ Scheduler запущено")

    # Aiohttp сервер для POS вебхуків
    await start_webhook_server()
    logger.info("🌐 Webhook сервер запущено")

    try:
        logger.info("🤖 Бот починає polling...")
        await dp.start_polling(bot)
    finally:
        # Graceful shutdown
        scheduler.shutdown(wait=False)
        await bot.session.close()
        logger.info("🛑 Бот зупинено коректно")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Бот зупинено вручну")
