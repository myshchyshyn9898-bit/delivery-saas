"""
middleware.py — Rate limiting для захисту від спаму.
Підключається в main.py через dp.message.middleware(ThrottlingMiddleware())
"""
import time
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery


class ThrottlingMiddleware(BaseMiddleware):
    """
    Простий rate limiter без зовнішніх залежностей.
    Зберігає час останнього повідомлення від кожного юзера в пам'яті.
    """

    def __init__(self, rate_limit: float = 1.0):
        # rate_limit — мінімальний інтервал між повідомленнями в секундах
        self.rate_limit = rate_limit
        self._last_seen: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        user_id = event.from_user.id
        now = time.monotonic()
        last = self._last_seen.get(user_id, 0)

        if now - last < self.rate_limit:
            # Мовчки ігноруємо — не відповідаємо, щоб не провокувати ще більше спаму
            return

        self._last_seen[user_id] = now

        # Очищаємо старі записи щоб уникнути memory leak (раз на ~1000 подій)
        if len(self._last_seen) > 1000:
            cutoff = now - 300  # видаляємо тих хто мовчить більше 5 хвилин
            self._last_seen = {uid: ts for uid, ts in self._last_seen.items() if ts > cutoff}

        return await handler(event, data)
