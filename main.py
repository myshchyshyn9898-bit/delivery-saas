import asyncio

from bot_setup import bot, dp, scheduler
from handlers import commands, orders, admin
from handlers.webhooks import start_webhook_server
from handlers.scheduler import check_late_orders

dp.include_router(commands.router)
dp.include_router(orders.router)
dp.include_router(admin.router)

# ==========================================
# ГОЛОВНИЙ ЗАПУСК
# ==========================================
async def main():
    # ⏱ Запускаємо таймер запізнень (перевірка кожну 1 хвилину)
    scheduler.add_job(check_late_orders, "interval", minutes=1)
    scheduler.start()

    await start_webhook_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
