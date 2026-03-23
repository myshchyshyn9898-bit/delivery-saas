<!DOCTYPE html>
<html lang="uk">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Замовлення</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root { --bg-color: #f4f7f9; --text-main: #111418; --text-muted: #8a8d91; --primary: #ff7652; --surface: #ffffff; --success: #34C759; }
        * { box-sizing: border-box; font-family: sans-serif; margin: 0; padding: 0; }
        body { background: var(--bg-color); color: var(--text-main); padding: 15px; }
        .stat-card { background: var(--surface); border-radius: 15px; padding: 20px; box-shadow: 0 4px 10px rgba(0,0,0,0.05); margin-bottom: 15px; border-left: 5px solid var(--primary); }
        .tabs { display: flex; background: #e2e8f0; border-radius: 12px; padding: 4px; margin-bottom: 15px; }
        .tab-btn { flex: 1; text-align: center; padding: 10px; font-size: 14px; font-weight: bold; color: #64748b; border-radius: 10px; cursor: pointer; }
        .tab-btn.active { background: white; color: var(--text-main); box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .order-card { background: white; border-radius: 12px; padding: 15px; margin-bottom: 10px; border: 1px solid #e2e8f0; }
        .order-id { font-weight: 800; margin-bottom: 8px; display: flex; justify-content: space-between; }
        .badge { font-size: 10px; padding: 3px 8px; border-radius: 5px; }
        .order-row { display: flex; gap: 10px; font-size: 14px; margin-bottom: 5px; }
        .order-footer { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; padding-top: 10px; border-top: 1px dashed #e2e8f0; }
        .price { font-weight: 900; color: var(--primary); font-size: 18px; }
    </style>
</head>
<body>
    <div class="stat-card">
        <div style="font-size: 12px; color: var(--text-muted);">ЗАРОБЛЕНО СЬОГОДНІ</div>
        <div id="total-earned" style="font-size: 24px; font-weight: 900;">0.00 zł</div>
    </div>

    <div class="tabs">
        <div class="tab-btn active" onclick="switchTab('active', this)">📋 Активні</div>
        <div class="tab-btn" onclick="switchTab('closed', this)">✅ Закриті</div>
    </div>

    <div id="tab-active" class="tab-content active"><div id="list-active" style="text-align:center; padding:20px; color:gray;">Пошук замовлень...</div></div>
    <div id="tab-closed" class="tab-content"><div id="list-closed" style="text-align:center; padding:20px; color:gray;">Пошук замовлень...</div></div>

    <script>
        const SUPABASE_URL = 'https://kvanzkcwpwmfexsmldvx.supabase.co';
        const SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imt2YW56a2N3cHdtZmV4c21sZHZ4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQwMzgyMzksImV4cCI6MjA4OTYxNDIzOX0.ZHXB9-PwJhH07LzPGpxK0HD-BkLGlf5w2L4WbgrX4JA';
        const supabase = window.supabase.createClient(SUPABASE_URL, SUPABASE_KEY);
        const bizId = new URLSearchParams(window.location.search).get('biz_id');

        function switchTab(id, el) {
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById('tab-' + id).classList.add('active');
            el.classList.add('active');
        }

        async function loadData() {
            if (!bizId) return;
            try {
                // Отримуємо персонал (кур'єрів)
                const { data: staff } = await supabase.from('staff').select('user_id, name').eq('business_id', bizId);
                const couriers = {};
                if (staff) staff.forEach(s => couriers[s.user_id] = s.name);

                // Отримуємо замовлення за сьогодні
                const startDay = new Date(); startDay.setHours(0,0,0,0);
                const { data: orders, error } = await supabase.from('orders')
                    .select('*').eq('business_id', bizId)
                    .gte('created_at', startDay.toISOString())
                    .order('created_at', { ascending: false });

                if (error) throw error;

                let htmlActive = '', htmlClosed = '', total = 0;

                if (orders && orders.length > 0) {
                    orders.forEach(o => {
                        const card = `
                            <div class="order-card">
                                <div class="order-id">#${o.id.toString().slice(0,6).toUpperCase()} 
                                    <span class="badge" style="background:${o.status==='completed'?'#dcfce7':'#fef9c3'}">
                                        ${o.status==='completed'?'ГОТОВО':'В ДОРОЗІ'}
                                    </span>
                                </div>
                                <div class="order-row"><i class="fa fa-location-dot"></i> ${o.address}</div>
                                <div class="order-row"><i class="fa fa-motorcycle"></i> ${couriers[o.courier_id] || 'Кур\'єр'}</div>
                                <div class="order-footer">
                                    <span style="font-size:12px; color:gray;">${o.pay_type==='cash'?'💵 Готівка':'💳 Термінал'}</span>
                                    <span class="price">${o.amount} zł</span>
                                </div>
                            </div>`;
                        if (o.status === 'completed') { htmlClosed += card; total += parseFloat(o.amount); }
                        else { htmlActive += card; }
                    });
                }

                document.getElementById('total-earned').innerText = total.toFixed(2) + ' zł';
                document.getElementById('list-active').innerHTML = htmlActive || '<div style="text-align:center; padding:30px; color:gray;">Активних немає</div>';
                document.getElementById('list-closed').innerHTML = htmlClosed || '<div style="text-align:center; padding:30px; color:gray;">Закритих немає</div>';

            } catch (e) {
                document.getElementById('list-active').innerHTML = "Помилка: " + e.message;
            }
        }
        loadData();
    </script>
</body>
</html>
