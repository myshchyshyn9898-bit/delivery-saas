from config import API_TOKEN, BUSINESS_TZ
from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler

if not API_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set. Bot cannot start without it.")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()
# ✅ ВИПРАВЛЕНО: timezone з config (враховує DST, не захардкоджено)
scheduler = AsyncIOScheduler(timezone=BUSINESS_TZ)
