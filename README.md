# DeliPro — Всі виправлення

## Куди що класти:
handlers/orders.py    → handlers/orders.py
handlers/commands.py  → handlers/commands.py
texts.py              → texts.py
keyboards.py          → keyboards.py
schedule.html         → schedule.html (НОВИЙ файл, поруч з map.html)
dashboard.html        → dashboard.html (замінити)
js/app.js             → js/app.js (замінити)
css/dashboard.css     → css/dashboard.css (замінити)

## Нові таблиці Supabase (якщо ще не створили):
CREATE TABLE schedule (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    business_id UUID REFERENCES businesses(id),
    courier_id BIGINT, date DATE NOT NULL,
    planned_start TIME, planned_end TIME,
    UNIQUE(business_id, courier_id, date)
);
CREATE TABLE salary_settings (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    business_id UUID REFERENCES businesses(id),
    courier_id BIGINT, hourly_rate NUMERIC DEFAULT 0,
    km_rate NUMERIC DEFAULT 0, km_enabled BOOLEAN DEFAULT false,
    order_rate NUMERIC DEFAULT 0, order_enabled BOOLEAN DEFAULT false,
    UNIQUE(business_id, courier_id)
);
CREATE TABLE salary_bonuses (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    business_id UUID REFERENCES businesses(id),
    courier_id BIGINT, month VARCHAR(7),
    amount NUMERIC DEFAULT 0, comment TEXT
);
CREATE TABLE salary_payments (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    business_id UUID REFERENCES businesses(id),
    courier_id BIGINT, month VARCHAR(7),
    paid BOOLEAN DEFAULT false, paid_at TIMESTAMPTZ,
    UNIQUE(business_id, courier_id, month)
);
