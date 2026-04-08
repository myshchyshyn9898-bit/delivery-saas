# 🛵 DelivePro — Delivery SaaS Bot

Telegram-бот для управління доставками. Мультитенантна SaaS-платформа з підтримкою кількох мов (🇺🇦 🇷🇺 🇵🇱 🇬🇧).

## ✨ Функціональність

- 📦 Створення та управління замовленнями через Telegram Web App
- 🗺 Інтерактивна карта з маршрутами (Mapbox + OSRM)
- 👥 Ролі: Власник, Менеджер, Кур'єр, Супер-Адмін
- 💳 Інтеграція з Whop (підписки) та Poster (POS-система)
- 📊 Автоматичні звіти за день
- ⏱ Сповіщення про запізнення замовлень
- 🔐 JWT-авторизація для Web App
- 🌍 4 мови: українська, російська, польська, англійська

## 📁 Структура проєкту

```
├── main.py                 # Точка входу
├── bot_setup.py            # Ініціалізація бота та диспетчера
├── config.py               # Конфігурація (env vars)
├── database.py             # Робота з Supabase
├── keyboards.py            # Клавіатури + JWT токени
├── texts.py                # i18n переклади (4 мови)
├── handlers/
│   ├── commands.py          # /start, /boss, меню, звіти
│   ├── orders.py            # Замовлення, Web App дані
│   ├── admin.py             # /sa панель суперадміна
│   ├── map_service.py       # Генерація карт маршрутів
│   ├── scheduler.py         # Фонова перевірка запізнень
│   └── webhooks.py          # Whop/Poster вебхуки
├── tests/                   # 94 тести
├── archive/                 # Бекап оригінального коду
├── *.html                   # Web App фронтенд (GitHub Pages)
├── requirements.txt         # Production залежності
└── requirements-dev.txt     # Dev/test залежності
```

## 🚀 Швидкий старт

### 1. Клонування
```bash
git clone https://github.com/myshchyshyn9898-bit/delivery-saas.git
cd delivery-saas
```

### 2. Встановлення залежностей
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

pip install -r requirements.txt       # Production
pip install -r requirements-dev.txt   # + тести
```

### 3. Налаштування середовища
```bash
cp .env.example .env
# Заповніть .env реальними значеннями
```

### 4. Запуск
```bash
python main.py
```

## 🧪 Тестування

```bash
pytest tests/ -v
```

## ⚙️ Змінні середовища

| Змінна | Обов'язкова | Опис |
|--------|------------|------|
| `BOT_TOKEN` | ✅ | Токен Telegram бота |
| `SUPABASE_URL` | ✅ | URL вашого Supabase проєкту |
| `SUPABASE_KEY` | ✅ | Anon ключ Supabase |
| `SUPABASE_JWT_SECRET` | ✅ | JWT секрет для підпису токенів |
| `MAPBOX_TOKEN` | ✅ | Токен Mapbox для карт |
| `SUPER_ADMIN_IDS` | ✅ | ID суперадмінів (через кому) |
| `BOT_USERNAME` | ❌ | Username бота (за замовч. @deliprobot) |
| `BASE_URL` | ❌ | URL GitHub Pages |
| `PORT` | ❌ | Порт webhook сервера (за замовч. 8000) |
| `WHOP_WEBHOOK_SECRET` | ❌ | Секрет для верифікації Whop |
| `POSTER_WEBHOOK_SECRET` | ❌ | Секрет для верифікації Poster |

## 🛠 Деплой (Railway)

1. Підключіть репозиторій до Railway
2. Задайте всі обов'язкові env vars
3. Railway автоматично запустить `python main.py`
