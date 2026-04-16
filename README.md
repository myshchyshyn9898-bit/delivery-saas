# DeliPro — Виправлення

## Структура (замінити файли в проекті):

handlers/commands.py  ← замінити
handlers/orders.py    ← замінити
texts.py              ← замінити
keyboards.py          ← замінити
schedule.html         ← НОВИЙ файл, покласти поруч з map.html

## Нові таблиці Supabase (виконати перед запуском):

CREATE TABLE schedule (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    business_id UUID REFERENCES businesses(id),
    courier_id BIGINT,
    date DATE NOT NULL,
    planned_start TIME,
    planned_end TIME,
    UNIQUE(business_id, courier_id, date)
);

CREATE TABLE salary_settings (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    business_id UUID REFERENCES businesses(id),
    courier_id BIGINT,
    hourly_rate NUMERIC DEFAULT 0,
    km_rate NUMERIC DEFAULT 0,
    km_enabled BOOLEAN DEFAULT false,
    order_rate NUMERIC DEFAULT 0,
    order_enabled BOOLEAN DEFAULT false,
    UNIQUE(business_id, courier_id)
);

CREATE TABLE salary_bonuses (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    business_id UUID REFERENCES businesses(id),
    courier_id BIGINT,
    month VARCHAR(7),
    amount NUMERIC DEFAULT 0,
    comment TEXT
);
