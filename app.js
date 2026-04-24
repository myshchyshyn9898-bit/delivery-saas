/*
  ====================================================================
  ФАЙЛ: js/app.js
  ДЛЯ ЧОГО: Це логіка твого додатку. Запити до Supabase, карти, таби.
  ЩО ТУТ РЕДАГУВАТИ: 
  - Якщо треба змінити токени (Supabase, Railway) — вони на початку.
  - Якщо треба додати новий запит до бази даних.
  - Логіка створення лінків-запрошень або тікетів підтримки.
  ====================================================================
*/

// --- ГЛОБАЛЬНІ ЗМІННІ ТА НАЛАШТУВАННЯ ---
let globalOrdersForExport = []; 
let currentPosSystem = '';
let connectedIntegrations = {}; 

// 🔴 ВСТАВ СЮДИ СВІЙ ДОМЕН З RAILWAY (без слеша в кінці)
// SERVER_URL: змінюється тут або через мета-тег <meta name="server-url" content="...">
const RAILWAY_DOMAIN = (function() {
    const meta = document.querySelector('meta[name="server-url"]');
    if (meta && meta.content) return meta.content.replace(/\/$/, '');
    return "https://web-production-df704.up.railway.app";
})();
let currentInviteToken = ''; 

// Читаємо параметри з URL (Telegram передає їх сюди)
const urlParams = new URLSearchParams(window.location.search);
const bizId = urlParams.get('biz_id');
const tgUserIdParam = urlParams.get('tg_id');
const authToken = urlParams.get('token');

let botUsername = "DeliProBot"; // Твій бот для інвайтів
let currencySymbol = "zł";
let selectedDashPlan = 'pro';

// 🔴 КЛЮЧІ SUPABASE — завантажуються з бекенду (не зберігати тут!)
let supabaseClient = null;

async function initSupabase() {
    // ✅ FIX: таймаут 8с — Railway cold start може тривати довго
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 8000);

    try {
        const res = await fetch(RAILWAY_DOMAIN + '/config', { signal: controller.signal });
        clearTimeout(timeoutId);

        if (!res.ok) {
            console.error('Config error:', res.status);
            _showInitError('Сервер недоступний (' + res.status + '). Спробуйте пізніше.');
            return;
        }
        const cfg = await res.json();
        if (cfg.mapbox_token) { window.MAPBOX_TOKEN = cfg.mapbox_token; }
        if (window.supabase && cfg.supabase_url && cfg.supabase_key) {
            // ✅ FIX: x-tg-user-id залишаємо у global.headers, але Authorization НЕ передаємо тут —
            //         Supabase JS v2 ігнорує Authorization з global.headers і перезаписує своїм anon-ключем.
            const tgId = tgUserIdParam || (window.Telegram?.WebApp?.initDataUnsafe?.user?.id);
            supabaseClient = window.supabase.createClient(cfg.supabase_url, cfg.supabase_key, {
                auth: {
                    persistSession: false,     // не зберігати сесію в localStorage
                    autoRefreshToken: false,   // не оновлювати токен автоматично
                    detectSessionInUrl: false  // не читати токен з URL
                },
                global: {
                    headers: tgId ? { 'x-tg-user-id': String(tgId) } : {}
                }
            });

            // ✅ FIX: Правильний спосіб передати JWT в Supabase JS v2 — через setSession.
            //         Тільки так SDK буде використовувати наш токен (а не anon-ключ) у всіх запитах.
            if (authToken) {
                const { error: sessionErr } = await supabaseClient.auth.setSession({
                    access_token: authToken,
                    refresh_token: authToken  // dummy — autoRefreshToken: false, тому не використовується
                });
                if (sessionErr) {
                    console.warn('JWT сесія не встановлена:', sessionErr.message);
                    // Продовжуємо як anon — якщо RLS дозволяє
                }
            }
        } else {
            _showInitError('Помилка конфігурації. Зверніться до підтримки.');
        }
    } catch(e) {
        clearTimeout(timeoutId);
        if (e.name === 'AbortError') {
            _showInitError('⏳ Сервер не відповідає (холодний старт). Зачекайте 15 секунд і поновіть.');
        } else {
            _showInitError('Помилка з\u2019єднання: ' + e.message);
        }
        console.error('initSupabase:', e);
    }
}

// ✅ FIX: показуємо зрозуміле повідомлення замість вічного спінера
function _showInitError(msg) {
    const el = document.getElementById('display-biz-name');
    if (el) el.innerHTML = '⚠️ ' + msg;
    const total = document.getElementById('val-total');
    if (total) total.innerHTML = '—';
    const recent = document.getElementById('recent-orders-list');
    if (recent) recent.innerHTML = '<div style="text-align:center;padding:30px 20px;color:var(--text-muted);font-size:13px;font-weight:600;">'
        + '<i class="fa-solid fa-triangle-exclamation" style="font-size:28px;color:var(--warning);display:block;margin-bottom:12px;"></i>'
        + msg
        + '<br><br><button onclick="location.reload()" style="margin-top:8px;padding:10px 20px;background:var(--primary);color:white;border:none;border-radius:12px;font-size:13px;font-weight:700;cursor:pointer;">🔄 Спробувати знову</button>'
        + '</div>';
}

// Стан дашборду
let currentPlanIsPro = true;
let currentCouriersCount = 0;
let currentManagersCount = 0;
let currentFilter = 'today';

// Змінні для карт та графіків
let bizLat = null; let bizLon = null; let bizRadius = 5; 
let revenueChart = null; // settingsMap тепер window.settingsMap (Mapbox GL)

// Генератор випадкових токенів
function generateUUID() {
    // Використовуємо crypto.randomUUID() якщо доступний (безпечніший)
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
        return crypto.randomUUID();
    }
    // Fallback з crypto.getRandomValues
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
        var r = crypto.getRandomValues(new Uint8Array(1))[0] % 16;
        var v = c === 'x' ? r : (r & 0x3 | 0x8);
        return v.toString(16);
    });
}

// --- ЛОГІКА ІНТЕРФЕЙСУ (ТАБИ, МОДАЛКИ) ---

function switchTab(tabId, el) {
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.getElementById('tab-' + tabId).classList.add('active');
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    el.classList.add('active');
    window.scrollTo(0, 0);
    if(tabId === 'home' && window.dashboardMap) { setTimeout(() => window.dashboardMap.invalidateSize(), 100); }
    if(tabId === 'salary') { initSalaryTab(); }
}

function setFilter(el, filterType) {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    el.classList.add('active');
    
    if (filterType) { currentFilter = filterType; } 
    else {
        const txt = el.getAttribute('data-i18n');
        if (txt === 'tab_today') currentFilter = 'today';
        else if (txt === 'tab_week') currentFilter = 'week';
        else if (txt === 'tab_month') currentFilter = 'month';
    }
    
    const actTab = document.querySelector('.tab-content.active');
    if(actTab) { actTab.style.opacity = '0.5'; setTimeout(() => actTab.style.opacity = '1', 300); }
    
    document.getElementById('val-total').innerHTML = '<i class="fa-solid fa-circle-notch fa-spin" style="font-size:16px;"></i>';
    document.getElementById('val-cash').innerHTML = '...'; document.getElementById('val-term').innerHTML = '...';
    document.getElementById('val-orders').innerHTML = '...'; document.getElementById('val-late').innerHTML = '...';
    document.getElementById('val-avg-check').innerHTML = '...'; document.getElementById('val-time').innerHTML = '...';
    document.getElementById('recent-orders-list').innerHTML = `<div style="text-align: center; color: var(--text-muted); font-size: 13px; padding: 20px 0;"><i class="fa-solid fa-circle-notch fa-spin"></i> ${t('loading')}</div>`;

    loadDashboardData(); 
}

function closeApp() { if (window.Telegram && window.Telegram.WebApp) window.Telegram.WebApp.close(); }

// Підписка
function openSubscriptionMenu() { document.getElementById('subscription-modal').classList.add('active'); document.body.style.overflow = 'hidden'; }
function closeSubscriptionMenu() { document.getElementById('subscription-modal').classList.remove('active'); document.body.style.overflow = ''; }

// Налаштування Бізнесу
function openBizSettingsMenu() { 
    document.getElementById('biz-settings-modal').classList.add('active'); document.body.style.overflow = 'hidden'; 
    setTimeout(() => { if(bizLat && bizLon) { document.getElementById('settings-map-container').style.display = 'block'; initSettingsMap(bizLat, bizLon, bizRadius); } }, 300);
}
function closeBizSettingsMenu() { document.getElementById('biz-settings-modal').classList.remove('active'); document.body.style.overflow = ''; }

function initSettingsMap(lat, lon, r) {
    if (window.settingsMap) { window.settingsMap.remove(); window.settingsMap = null; }

    mapboxgl.accessToken = window.MAPBOX_TOKEN || '';
    window.settingsMap = new mapboxgl.Map({
        container: 'settings-map-container',
        style: 'mapbox://styles/mapbox/streets-v12',
        center: [lon, lat],
        zoom: 12,
        interactive: false,
        attributionControl: false
    });

    window.settingsMap.on('load', () => {
        const steps = 64;
        const radiusM = (r || 5) * 1000;
        const coords = [];
        for (let i = 0; i <= steps; i++) {
            const angle = (i / steps) * 2 * Math.PI;
            const dx = (radiusM / 111320) * Math.cos(angle);
            const dy = (radiusM / (111320 * Math.cos(lat * Math.PI / 180))) * Math.sin(angle);
            coords.push([lon + dy, lat + dx]);
        }
        window.settingsMap.addSource('delivery-zone', {
            type: 'geojson',
            data: { type: 'Feature', geometry: { type: 'Polygon', coordinates: [coords] } }
        });
        window.settingsMap.addLayer({
            id: 'delivery-fill', type: 'fill', source: 'delivery-zone',
            paint: { 'fill-color': '#FF5A5F', 'fill-opacity': 0.12 }
        });
        window.settingsMap.addLayer({
            id: 'delivery-border', type: 'line', source: 'delivery-zone',
            paint: { 'line-color': '#FF5A5F', 'line-width': 2, 'line-dasharray': [3, 2] }
        });
        const el = document.createElement('div');
        el.innerHTML = '<i class="fa-solid fa-store" style="color:#FF5A5F;font-size:22px;filter:drop-shadow(0 2px 4px rgba(0,0,0,0.35));"></i>';
        el.style.cssText = 'display:flex;align-items:center;justify-content:center;';
        new mapboxgl.Marker({ element: el, anchor: 'bottom' }).setLngLat([lon, lat]).addTo(window.settingsMap);
        const kmToDeg = (r || 5) / 111.32;
        window.settingsMap.fitBounds(
            [[lon - kmToDeg * 1.4, lat - kmToDeg], [lon + kmToDeg * 1.4, lat + kmToDeg]],
            { padding: 24, duration: 0 }
        );
    });
}

function updateSettingsMap() {
    let r = parseFloat(document.getElementById('input-biz-radius').value) || 5;
    if(bizLat && bizLon) { document.getElementById('settings-map-container').style.display = 'block'; setTimeout(() => { initSettingsMap(bizLat, bizLon, r); }, 100); }
}

async function saveBizSettings(btn) {
    if (!bizId || !supabaseClient) return;
    const newName = document.getElementById('input-biz-name').value.trim();
    const newCurr = document.getElementById('input-biz-currency').value;
    const newRadius = document.getElementById('input-biz-radius').value.trim();
    const newAddress = document.getElementById('input-biz-address').value.trim();
    const newDeliveryMode = window.DeliveryMode ? window.DeliveryMode.get() : 'dispatcher';
    const newGroupId = (document.getElementById('courier_group_id')?.value || '').trim();
    const newKmRate = parseFloat(document.getElementById('input-km-rate')?.value) || 0;

    // ✅ FIX 2: валідація — uber без group ID не зберігаємо
    if (newDeliveryMode === 'uber' && !newGroupId) {
        alert("⚠️ Для режиму \"Вільна каса\" потрібно вказати ID Telegram-групи кур'єрів.");
        document.getElementById('courier_group_id')?.focus();
        return;
    }
    
    const oldHtml = btn.innerHTML;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i>`; btn.disabled = true;
    
    try {
        let updatePayload = { name: newName, currency: newCurr, delivery_mode: newDeliveryMode, km_rate: newKmRate };
        if (newRadius !== "") updatePayload.radius_km = parseFloat(newRadius);
        if (newAddress !== "") updatePayload.street = newAddress; 
        if (bizLat !== null && bizLon !== null) { updatePayload.lat = bizLat; updatePayload.lng = bizLon; }
        if (newDeliveryMode === 'uber' && newGroupId) updatePayload.courier_group_id = newGroupId;
        else updatePayload.courier_group_id = null;

        const { error } = await supabaseClient.from('businesses').update(updatePayload).eq('id', bizId);
        if (error) throw error;

        // ✅ FIX 3: скидаємо кеш бота після зміни режиму доставки
        if (authToken) {
            try {
                await fetch(`${RAILWAY_DOMAIN}/api/invalidate_cache`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + authToken },
                    body: JSON.stringify({ user_id: tgUserIdParam || 0, biz_id: bizId })
                });
            } catch(e) { console.warn('Cache invalidation after mode change:', e); }
        }
        
        alert(t('alert_save'));
        closeBizSettingsMenu(); loadDashboardData(); 
    } catch(e) { alert(t('alert_err') + e.message); } 
    finally { btn.innerHTML = oldHtml; btn.disabled = false; }
}

// 🔴 СПОВІЩЕННЯ TOAST 🔴
function showToast(title, desc) {
    const toast = document.getElementById('ios-toast');

    // ✅ ВИПРАВЛЕНО: якщо передано текст — підміняємо вміст тосту
    // Якщо без аргументів — показує дефолтне "підписка закінчується" з HTML
    if (title) {
        const titleEl = toast.querySelector('.ios-toast-title');
        const descEl  = toast.querySelector('.ios-toast-desc');
        if (titleEl) titleEl.innerText = title;
        if (descEl)  descEl.innerText  = desc || '';
    }

    toast.classList.add('show');
    setTimeout(() => hideToast(), 6000);
}
function hideToast() {
    document.getElementById('ios-toast').classList.remove('show');
}

// Логіка підписок та Whop
function selectDashPlan(plan) {
    selectedDashPlan = plan;
    document.getElementById('dash-plan-basic').classList.remove('selected');
    document.getElementById('dash-plan-pro').classList.remove('selected');
    document.getElementById(`dash-plan-${plan}`).classList.add('selected');
    document.getElementById('pay-price').innerText = plan === 'pro' ? '59.99 zł' : '29.99 zł';
}

function renderSubscriptionUI(biz) {
    let dbPlan = biz.plan || 'trial'; 
    let expireDate = new Date();
    if (biz.subscription_expires_at) {
        expireDate = new Date(biz.subscription_expires_at);
    } else {
        let createdAt = new Date(biz.created_at || Date.now());
        expireDate = new Date(createdAt);
        if (dbPlan === 'trial') expireDate.setDate(expireDate.getDate() + 7);
        else if (dbPlan !== 'expired') expireDate.setDate(expireDate.getDate() + 30);
    }

    const statusCard = document.getElementById('modal-status-card');
    const badge = document.getElementById('modal-plan-badge');
    const planName = document.getElementById('modal-plan-name');
    const planDesc = document.getElementById('modal-plan-desc');
    const progressCont = document.getElementById('trial-progress-container');
    const btnPay = document.getElementById('btn-upgrade-pay');
    const btnManage = document.getElementById('btn-manage-sub');
    const plansBlock = document.getElementById('plans-selection-block');
    const basicCard = document.getElementById('dash-plan-basic');
    const proCard = document.getElementById('dash-plan-pro');

    statusCard.className = 'status-card-dynamic';
    let daysLeft = Math.ceil((expireDate.getTime() - new Date().getTime()) / (1000 * 3600 * 24));
    
    // ✅ ВИПРАВЛЕНО: передаємо явний текст щоб не плутати з toast після збереження токена
    if (daysLeft <= 1 && daysLeft >= 0 && dbPlan !== 'expired') {
        setTimeout(() => showToast(
            "⚠️ Підписка скоро закінчується",
            `Залишилось ${Math.max(0, daysLeft)} дн. Відкрийте керування підпискою.`
        ), 1500);
    }

    if (dbPlan === 'trial') {
        let displayDays = Math.max(0, daysLeft);
        let percentUsed = Math.max(0, Math.min(100, ((7 - displayDays) / 7) * 100));

        statusCard.classList.add('is-trial');
        planName.innerText = 'FREE TRIAL (PRO)'; planName.style.color = 'var(--text-main)';
        badge.innerText = t('badge_active'); badge.style.background = 'var(--info)';
        planDesc.innerHTML = `${t('txt_left')} <b style="color: var(--text-main);">${displayDays} ${t('txt_days')}</b>. ${t('txt_avail')} <b>${expireDate.toLocaleDateString('uk-UA')}</b>`;
        
        progressCont.style.display = 'block';
        setTimeout(() => { document.getElementById('trial-progress-bar').style.width = percentUsed + '%'; }, 100);
        
        plansBlock.style.display = 'block'; basicCard.style.display = 'block'; proCard.style.display = 'block';
        document.getElementById('plan-select-title').innerText = t('title_sel_plan');
        
        btnPay.style.display = 'block'; btnManage.style.display = 'none';
        document.getElementById('btn-pay-text').innerText = t('btn_pay');
        selectDashPlan('pro');
    } 
    else if (dbPlan === 'basic') {
        statusCard.classList.add('is-pro');
        planName.innerText = t('state_basic'); planName.style.color = 'var(--text-main)';
        badge.innerText = t('badge_paid'); badge.style.background = 'var(--success)';
        planDesc.innerHTML = `${t('txt_next_pay')} <b>${expireDate.toLocaleDateString('uk-UA')}</b>`;
        progressCont.style.display = 'none';

        plansBlock.style.display = 'block';
        document.getElementById('plan-select-title').innerText = t('title_upsell');
        basicCard.style.display = 'none'; 
        proCard.style.display = 'block';
        selectDashPlan('pro');
        
        btnPay.style.display = 'block'; btnManage.style.display = 'block';
        document.getElementById('btn-pay-text').innerText = t('btn_upgrade_now');
        document.getElementById('pay-price').innerText = "";
    }
    else if (dbPlan === 'pro') {
        statusCard.classList.add('is-pro');
        planName.innerText = t('state_pro'); planName.style.color = 'var(--success)';
        badge.innerText = 'MAXIMUM'; badge.style.background = 'linear-gradient(135deg, var(--primary), #ffa07a)';
        planDesc.innerHTML = `${t('txt_max')} ${t('txt_next_pay')} <b>${expireDate.toLocaleDateString('uk-UA')}</b>`;
        progressCont.style.display = 'none';

        plansBlock.style.display = 'none';
        btnPay.style.display = 'none';
        btnManage.style.display = 'block';
    }
    else if (dbPlan === 'expired') {
        statusCard.classList.add('is-expired');
        planName.innerText = t('state_closed'); planName.style.color = 'var(--danger)';
        badge.innerText = t('badge_block'); badge.style.background = 'var(--danger)';
        planDesc.innerHTML = t('txt_expired');
        progressCont.style.display = 'none';
        
        plansBlock.style.display = 'block'; basicCard.style.display = 'block'; proCard.style.display = 'block';
        document.getElementById('plan-select-title').innerText = t('title_sel_unlock');
        
        btnPay.style.display = 'block'; btnManage.style.display = 'none';
        document.getElementById('btn-pay-text').innerText = t('btn_unlock');
        selectDashPlan('pro');

        document.getElementById('subscription-modal').onclick = null; // Забороняємо закривати вікно
        document.getElementById('subscription-modal').classList.add('active');
    }
}

function actionPayWhop() {
    if (!bizId) return;
    let tgUserId = tgUserIdParam || (window.Telegram?.WebApp?.initDataUnsafe?.user?.id);

    if (!tgUserId) {
        alert(t('err_no_tg_id') || "Помилка: не вдалося визначити ваш Telegram ID. Спробуйте відкрити через Telegram.");
        return;
    }

    const linkPro = "https://whop.com/checkout/ТВІЙ_КОД_PRO";
    const linkBasic = "https://whop.com/checkout/ТВІЙ_КОД_BASIC";
    
    const baseLink = selectedDashPlan === 'pro' ? linkPro : linkBasic;
    const checkoutUrl = `${baseLink}?custom_fields[biz_id]=${bizId}&custom_fields[tg_user_id]=${tgUserId}`;

    if (window.Telegram && window.Telegram.WebApp) {
        window.Telegram.WebApp.openLink(checkoutUrl);
    } else { window.open(checkoutUrl, "_blank"); }
}

function actionManageWhop() {
    if (window.Telegram && window.Telegram.WebApp) { 
        window.Telegram.WebApp.openLink("https://whop.com/orders/"); 
    } else {
        window.open("https://whop.com/orders/", "_blank");
    }
}

// Робота з персоналом (Staff)
function generateInvite(role) {
    if (!bizId) return alert(t('err_biz'));
    if (!currentInviteToken) return alert(t('err_tok'));
    if (!currentPlanIsPro) {
        if (role === 'courier' && currentCouriersCount >= 2) { alert(t('limit_c')); openSubscriptionMenu(); return; }
        if (role === 'manager' && currentManagersCount >= 1) { alert(t('limit_m')); openSubscriptionMenu(); return; }
    }
    let prefix = role === 'courier' ? 'c_' : 'm_';
    navigator.clipboard.writeText(`https://t.me/${botUsername}?start=${prefix}${currentInviteToken}`).then(() => { alert(t('success_copy')); });
}

async function resetInviteToken() {
    if (!confirm(t('confirm_reset'))) return;
    if (!supabaseClient) return;
    try {
        const newToken = generateUUID();
        const { error } = await supabaseClient.from('businesses').update({ invite_token: newToken }).eq('id', bizId);
        if (error) throw error;
        currentInviteToken = newToken;
        alert(t('alert_reset'));
    } catch(e) { alert(t('alert_err') + e.message); }
}

async function removeStaff(staffRowId, staffName, staffUserId) {
    if (!confirm(`${t('confirm_del')} "${staffName}"?`)) return;
    if (!supabaseClient) return;
    try {
        // Закриваємо активну зміну перед видаленням зі staff
        if (staffUserId) {
            try {
                await supabaseClient.from('shifts')
                    .update({ ended_at: new Date().toISOString() })
                    .eq('courier_id', staffUserId)
                    .eq('business_id', bizId)
                    .is('ended_at', null);
            } catch(shiftErr) {
                console.warn('Не вдалось закрити зміну:', shiftErr);
            }
        }
        const { error } = await supabaseClient.from('staff').delete().eq('id', staffRowId).eq('business_id', bizId);
        if (error) throw error;

        // ✅ ВИПРАВЛЕНО bug #2: скидаємо кеш бота одразу після видалення персоналу,
        // щоб видалений кур'єр не бачив активне меню ще 60 секунд
        if (staffUserId && authToken) {
            try {
                await fetch(`${RAILWAY_DOMAIN}/api/invalidate_cache`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + authToken },
                    body: JSON.stringify({ user_id: staffUserId })
                });
            } catch (e) { console.warn('Cache invalidation failed (non-critical):', e); }
        }

        loadDashboardData();
    } catch (err) { alert(t('alert_err') + (err.message || '')); }
}

function exportOrdersCSV() {
    if (!currentPlanIsPro) { openSubscriptionMenu(); return; }
    if (globalOrdersForExport.length === 0) { alert(t('err_export')); return; }

    // Захист від CSV formula injection
    function csvCell(val) {
        var s = String(val || '').replace(/"/g, '""');
        if (s && /^[=+\-@]/.test(s)) s = "'" + s;
        return '"' + s + '"';
    }

    var csvContent = "data:text/csv;charset=utf-8,\uFEFF";
    csvContent += "ID,Amount,Type,Status,Created,Completed,CourierID\n";

    globalOrdersForExport.forEach(function(o) {
        var status = o.status === 'completed' ? 'Completed' : 'Active';
        var payType = o.pay_type || '';
        var created = o.created_at ? new Date((o.created_at||'').replace(' ','T')).toLocaleString('uk-UA') : '';
        var completed = o.completed_at ? new Date((o.completed_at||'').replace(' ','T')).toLocaleString('uk-UA') : '';
        csvContent += csvCell(o.id) + ',' + csvCell(o.amount) + ',' + csvCell(payType) + ',' +
            csvCell(status) + ',' + csvCell(created) + ',' + csvCell(completed) + ',' +
            csvCell(o.courier_id || '') + '\n';
    });

    var encodedUri = encodeURI(csvContent);
    var link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", "Export_" + currentFilter + ".csv");
    document.body.appendChild(link); link.click(); document.body.removeChild(link);
}

// Підтримка (Тікети)
var selectedTicketReason = 'bug';
function openSupportModal() { document.getElementById('support-modal').classList.add('active'); document.body.style.overflow = 'hidden'; }
function closeSupportModal() { document.getElementById('support-modal').classList.remove('active'); document.body.style.overflow = ''; }
function selectTicketType(element, reason) {
    document.querySelectorAll('.ticket-type-btn').forEach(btn => btn.classList.remove('active'));
    element.classList.add('active');
    selectedTicketReason = reason;
}

function sendSupportTicket() {
    const topic = document.getElementById('ticket-topic').value.trim();
    const msg = document.getElementById('ticket-msg').value.trim();

    if (!topic || !msg) { alert(t('ticket_alert_empty')); return; }

    const ticketData = { action: "support_ticket", biz_id: bizId, reason: selectedTicketReason, topic: topic, message: msg };

    const btn = document.getElementById('btn-send-ticket');
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> ${t('ticket_btn_sending')}`;
    btn.disabled = true;

    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.sendData) {
        window.Telegram.WebApp.sendData(JSON.stringify(ticketData));
    } else {
        console.log("ТЕСТ ТІКЕТА:", ticketData);
        setTimeout(() => {
            alert(t('ticket_alert_test'));
            closeSupportModal();
            btn.innerHTML = `<i class="fa-solid fa-paper-plane"></i> <span id="text-btn-send-ticket">${t('ticket_btn_send')}</span>`;
            btn.disabled = false;
        }, 1000);
    }
}

// ==========================================
// Інтеграції POS
// ==========================================

// Маппінг: id картки → реальний шлях webhook і назва колонки в БД
const POS_CONFIG = {
    poster:   { webhookPath: 'poster',   dbColumn: 'poster_token',   name: 'Poster POS',
                tokenLabel: 'API Токен',
                tokenHint: 'Знайдіть у кабінеті Poster: Налаштування → Інтеграції → API → Скопіюйте "API токен".',
                webhookEvent: 'Нове замовлення доставки (incoming_order → added)',
                webhookStep: 'Налаштування → Інтеграції → Webhooks → Додати Webhook' },
    choice:   { webhookPath: 'choiceqr', dbColumn: 'choice_token',   name: 'ChoiceQR',
                tokenLabel: 'Bearer Токен',
                tokenHint: 'Перейдіть у панель ChoiceQR: Settings → Integrations → Webhooks → згенеруйте Bearer Token.',
                webhookEvent: 'order.created',
                webhookStep: 'Settings → Integrations → Webhooks → New Webhook' },
    gopos:    { webhookPath: 'gopos',    dbColumn: 'gopos_token',    name: 'GoPOS',
                tokenLabel: 'Authorization Token',
                tokenHint: 'Скопіюйте Authorization Token з адмін-панелі GoPOS: Ustawienia → Integracje → Tokeny.',
                webhookEvent: 'order.created',
                webhookStep: 'Ustawienia → Integracje → Webhooki → Dodaj' },
    syrve:    { webhookPath: 'syrve',    dbColumn: 'syrve_token',    name: 'Syrve (iiko)',
                tokenLabel: 'API Login',
                tokenHint: 'Введіть API Login від системи Syrve (iiko) — знаходиться в налаштуваннях інтеграцій.',
                webhookEvent: 'DeliveryOrderStatusChanged',
                webhookStep: 'Syrve Office → Administration → API' },
};

function openIntegrationsList() { document.getElementById('integrations-list-modal').classList.add('active'); document.body.style.overflow = 'hidden'; }
function closeIntegrationsList() { document.getElementById('integrations-list-modal').classList.remove('active'); document.body.style.overflow = ''; }

function openConnectModal(name, id, color, letter, desc) {
    closeIntegrationsList();
    setTimeout(() => {
        currentPosSystem = id;
        const cfg = POS_CONFIG[id] || {};
        const logo = document.getElementById('connect-logo');
        logo.innerText = letter; logo.style.background = color;
        const btn = document.getElementById('btn-save-pos'); btn.style.background = color;

        if (connectedIntegrations[id]) {
            const webhookUrl = `${RAILWAY_DOMAIN}/webhook/${cfg.webhookPath || id}?biz_id=${bizId}`;
            document.getElementById('connect-title').innerText = `${cfg.name || name} ✅ Підключено`;
            document.getElementById('connect-desc').innerHTML = `
                <div style="text-align:left; background:rgba(16,185,129,0.05); padding:12px; border-radius:12px; margin-bottom:12px; border:1px dashed #10b981;">
                    <b style="color:#10b981; font-size:12px; display:block; margin-bottom:6px;">✅ Токен збережено. Залишилось налаштувати Webhook:</b>
                    <ol style="font-size:12px; color:var(--text-muted); margin-left:15px; line-height:1.8;">
                        <li>Скопіюйте посилання нижче</li>
                        <li>У кабінеті каси відкрийте:<br><b style="color:var(--text-main);">${cfg.webhookStep || 'Налаштування → Webhooks'}</b></li>
                        <li>Вставте посилання та оберіть подію:<br><b style="color:var(--text-main);">"${cfg.webhookEvent || 'Нове замовлення'}"</b></li>
                        <li>Збережіть — готово! 🎉</li>
                    </ol>
                </div>
                <code style="background:#f1f5f9; padding:10px; border-radius:8px; display:block; font-size:11px; word-break:break-all; color:var(--text-main); border:1px solid #e2e8f0; font-weight:700; cursor:pointer;" onclick="navigator.clipboard.writeText('${webhookUrl}'); showToast('Скопійовано!', 'Вставте посилання в налаштування каси.');">${webhookUrl}<br><span style="color:#94a3b8; font-weight:400; font-size:10px;">натисніть щоб скопіювати</span></code>
            `;
            document.getElementById('input-pos-token').style.display = 'none';
            btn.innerHTML = `<i class="fa-solid fa-copy"></i> Скопіювати посилання`;
            btn.onclick = function() {
                navigator.clipboard.writeText(webhookUrl);
                showToast("Скопійовано!", "Вставте посилання в налаштування каси.");
                closeConnectModal();
            };
        } else {
            const hint = cfg.tokenHint || desc;
            document.getElementById('connect-title').innerText = `Підключити ${cfg.name || name}`;
            document.getElementById('connect-desc').innerHTML = `
                <div style="background:#f8fafc; border:1px solid var(--border); border-radius:10px; padding:10px 12px; font-size:12px; color:var(--text-muted); line-height:1.6; text-align:left;">
                    <b style="color:var(--text-main);">Де знайти токен?</b><br>${hint}
                </div>`;
            const tokenInput = document.getElementById('input-pos-token');
            tokenInput.style.display = 'block';
            tokenInput.placeholder = `${cfg.tokenLabel || 'API Токен'}...`;
            tokenInput.value = '';
            btn.innerHTML = `<i class="fa-solid fa-link"></i> Підключити`;
            btn.onclick = savePosIntegration;
        }

        document.getElementById('pos-connect-modal').classList.add('active');
        document.body.style.overflow = 'hidden';
    }, 300);
}

function closeConnectModal() { document.getElementById('pos-connect-modal').classList.remove('active'); document.body.style.overflow = ''; }

async function savePosIntegration() {
    const token = document.getElementById('input-pos-token').value.trim();
    if (!token) { alert("📍 Будь ласка, введіть токен."); return; }

    const cfg = POS_CONFIG[currentPosSystem] || {};
    const btn = document.getElementById('btn-save-pos');
    const originalHtml = btn.innerHTML;
    btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Збереження...';
    btn.disabled = true;

    try {
        if (!supabaseClient || !bizId) throw new Error("Помилка з'єднання з базою.");
        const columnName = cfg.dbColumn || `${currentPosSystem}_token`;

        const { error } = await supabaseClient.from('businesses').update({ [columnName]: token }).eq('id', bizId);
        if (error) throw error;

        connectedIntegrations[currentPosSystem] = true;
        const statusEl = document.getElementById(`status-${currentPosSystem}`);
        if (statusEl) {
            statusEl.innerHTML = `<i class="fa-solid fa-circle-check"></i> Підключено`;
            statusEl.classList.add('active');
            statusEl.parentElement.parentElement.classList.add('connected');
        }

        const savedColor = btn.style.background;
        openConnectModal(cfg.name || currentPosSystem, currentPosSystem, savedColor, currentPosSystem[0].toUpperCase(), '');
        showToast("✅ Токен збережено!", "Тепер скопіюйте Webhook URL і вставте в касу.");
    } catch (err) {
        alert("❌ Помилка: " + err.message);
        btn.innerHTML = originalHtml;
    } finally {
        btn.disabled = false;
    }
}

// 🚀 ОСНОВНА ФУНКЦІЯ: ЗАВАНТАЖЕННЯ ДАНИХ З БАЗИ 🚀
async function loadDashboardData() {
    try {
        if (window.Telegram && window.Telegram.WebApp) { window.Telegram.WebApp.expand(); window.Telegram.WebApp.ready(); }
        if (!bizId) { document.getElementById('display-biz-name').innerText = "DEMO MODE"; return; }

        if (!supabaseClient) return;

        let startDate = new Date();
        if (currentFilter === 'today') startDate.setHours(0, 0, 0, 0);
        else if (currentFilter === 'week') startDate.setDate(startDate.getDate() - 7);
        else if (currentFilter === 'month') startDate.setDate(startDate.getDate() - 30);

        const { data: biz, error: bizError } = await supabaseClient.from('businesses').select('*').eq('id', bizId).single();
        if (bizError) console.warn('Biz query error:', bizError.message, bizError.code);
        
        // ✅ FIX: якщо бізнес не знайдено — показуємо помилку замість вічного спінера
        if (!biz) {
            _showInitError('Заклад не знайдено. Перевірте посилання або зверніться до підтримки.');
            return;
        }
        
        window._bizData = biz; // зберігаємо глобально для salary tab
        
        if (biz) {
            currencySymbol = biz.currency || "zł";
            currentInviteToken = biz.invite_token; 
            document.getElementById('display-biz-name').innerText = biz.name;
            document.getElementById('set-id').innerText = bizId;
            document.getElementById('set-currency').innerText = currencySymbol;

            document.getElementById('input-biz-name').value = biz.name || '';
            document.getElementById('input-biz-currency').value = biz.currency || 'zł';
            document.getElementById('input-biz-radius').value = biz.radius_km || '';
            bizRadius = biz.radius_km || 5;
            
            document.getElementById('input-biz-address').value = biz.street || '';
            bizLat = biz.lat || null; bizLon = biz.lng || null; 

            if (document.getElementById('input-km-rate')) {
                document.getElementById('input-km-rate').value = biz.km_rate || '';
            }

            // 👇 Встановлюємо збережений режим доставки 👇
            if (window.DeliveryMode) {
                window.DeliveryMode.set(biz.delivery_mode || 'dispatcher');
            }
            if (biz.courier_group_id && document.getElementById('courier_group_id')) {
                document.getElementById('courier_group_id').value = biz.courier_group_id;
            }
            // 👆 ================================== 👆

            if (biz.subscription_expires_at) {
                let expDate = new Date(biz.subscription_expires_at);
                if (new Date() > expDate) { biz.plan = 'expired'; }
            }

            currentPlanIsPro = (biz.plan === 'pro' || biz.plan === 'trial');
            
            document.getElementById('display-plan').innerText = currentPlanIsPro ? 'PRO' : 'BASIC';
            document.getElementById('display-plan').style.color = currentPlanIsPro ? 'var(--success)' : '#8a8d91';
            document.getElementById('display-plan').style.background = currentPlanIsPro ? 'rgba(52, 199, 89, 0.1)' : '#edf1f7';
            
            renderSubscriptionUI(biz);

            Object.entries(POS_CONFIG).forEach(([id, cfg]) => {
                const tokenVal = biz[cfg.dbColumn];
                const statusEl = document.getElementById(`status-${id}`);
                if (tokenVal && statusEl) {
                    connectedIntegrations[id] = true;
                    statusEl.innerHTML = `<i class="fa-solid fa-circle-check"></i> Підключено`;
                    statusEl.classList.add('active');
                    statusEl.parentElement.parentElement.classList.add('connected');
                }
            });

            if (!currentPlanIsPro && biz.plan !== 'expired') {
                ['btn-csv-export', 'card-chart', 'card-late', 'couriers-leaderboard', 'card-heatmap'].forEach(id => {
                    const el = document.getElementById(id);
                    if (el && !el.querySelector('.pro-overlay')) {
                        el.classList.add('pro-locked');
                        const overlay = document.createElement('div'); overlay.className = 'pro-overlay'; 
                        overlay.innerHTML = '<div class="pro-badge"><i class="fa-solid fa-lock"></i> PRO</div>'; 
                        overlay.onclick = openSubscriptionMenu; el.appendChild(overlay);
                    }
                });
            } else {
                ['btn-csv-export', 'card-chart', 'card-late', 'couriers-leaderboard', 'card-heatmap'].forEach(id => {
                    const el = document.getElementById(id);
                    if (el) { el.classList.remove('pro-locked'); const overlay = el.querySelector('.pro-overlay'); if (overlay) overlay.remove(); }
                });
            }
        }

        const staffBox = document.getElementById('staff-list');
        staffBox.innerHTML = `<div style="text-align: center; color: var(--text-muted); font-size: 13px; padding: 10px;"><i class="fa-solid fa-spinner fa-spin"></i> Оновлення...</div>`;
        
        const { data: staff } = await supabaseClient.from('staff').select('*').eq('business_id', bizId);
        let staffMap = {}; currentCouriersCount = 0; currentManagersCount = 0;

        if (staff && staff.length > 0) {
            staffBox.innerHTML = '';
            staff.forEach(person => {
                let safeName = String(person.name || person.full_name || 'Staff').replace(/</g, "&lt;").replace(/>/g, "&gt;");
                let staffTgId = person.tg_id || person.user_id; 
                staffMap[staffTgId] = safeName;
                
                if (person.role === 'courier') currentCouriersCount++; if (person.role === 'manager') currentManagersCount++;
                
                let roleClass = person.role === 'courier' ? 'role-courier' : 'role-manager'; 
                let roleName = person.role === 'courier' ? t('role_c') : t('role_m'); 
                
                let avatar = person.role === 'courier' 
                    ? '<i class="fa-solid fa-motorcycle" style="font-size: 20px; color: var(--primary);"></i>' 
                    : '<i class="fa-solid fa-user-tie" style="font-size: 20px; color: var(--info);"></i>';
                
                staffBox.innerHTML += `
                    <div class="team-card">
                        <div class="team-avatar" style="background: transparent; border: 1px solid rgba(0,0,0,0.05);">${avatar}</div>
                        <div class="team-info"><div class="team-name">${safeName}</div><div class="team-role ${roleClass}">${roleName}</div></div>
                        <button class="btn-delete-staff" onclick="removeStaff('${person.id}', '${safeName.replace(/'/g, "\\'")}', '${person.user_id}')"><i class="fa-solid fa-trash-can"></i></button>
                    </div>
                `;
            });
        } else {
            staffBox.innerHTML = `<div style="text-align: center; color: var(--text-muted); font-size: 13px; padding: 10px;">${t('empty_team')}</div>`;
        }

        const { data: orders } = await supabaseClient.from('orders').select('*').eq('business_id', bizId).gte('created_at', startDate.toISOString()).order('created_at', { ascending: false }).limit(5000);
        
        globalOrdersForExport = orders || [];
        let heatData = []; let chartDataRaw = {}; let courierStats = {};

        if (orders && orders.length > 0) {
            let totalCash = 0, totalTerminal = 0, completedCount = 0, totalDeliveryMinutes = 0, ordersWithTimeCount = 0, lateCount = 0;
            
            orders.forEach(o => {
                if (o.lat && o.lon) heatData.push([parseFloat(o.lat), parseFloat(o.lon), 1]);
                if (o.status === 'completed') {
                    completedCount++; let amt = parseFloat(o.amount) || 0;
                    if (o.pay_type === 'cash') totalCash += amt; if (o.pay_type === 'terminal' || o.pay_type === 'online') totalTerminal += amt;
                    
                    let diffMs = 0;
                    if (o.completed_at && o.created_at) { 
                        diffMs = new Date((o.completed_at||'').replace(' ','T')) - new Date((o.created_at||'').replace(' ','T')); 
                        if (diffMs > 0) { totalDeliveryMinutes += (diffMs / 1000 / 60); ordersWithTimeCount++; if(diffMs > 45 * 60 * 1000) lateCount++; } 
                    }
                    
                    let d = new Date((o.created_at||'').replace(' ','T'));
                    let sortKey, displayLabel;

                    if (currentFilter === 'today') {
                        sortKey = String(d.getHours()).padStart(2, '0') + ':00';
                        displayLabel = sortKey;
                    } else {
                        sortKey = d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
                        displayLabel = d.toLocaleDateString('uk-UA', {day:'2-digit', month:'2-digit'});
                    }

                    if (!chartDataRaw[sortKey]) chartDataRaw[sortKey] = { amount: 0, label: displayLabel };
                    chartDataRaw[sortKey].amount += amt;

                    let cid = o.courier_id;
                    if (cid) {
                        if (!courierStats[cid]) courierStats[cid] = {count: 0, timeTotal: 0, name: staffMap[cid] || '?'};
                        courierStats[cid].count++;
                        if (diffMs > 0) courierStats[cid].timeTotal += (diffMs / 1000 / 60);
                    }
                }
            });

            let totalRevenue = totalCash + totalTerminal;
            document.getElementById('val-total').innerText = totalRevenue.toFixed(2) + ' ' + currencySymbol;
            document.getElementById('val-cash').innerText = totalCash.toFixed(2);
            document.getElementById('val-term').innerText = totalTerminal.toFixed(2);
            document.getElementById('val-orders').innerText = completedCount;
            document.getElementById('val-late').innerText = lateCount;
            document.getElementById('val-avg-check').innerText = completedCount > 0 ? (totalRevenue / completedCount).toFixed(2) + ' ' + currencySymbol : '0.00 ' + currencySymbol;
            document.getElementById('val-time').innerText = ordersWithTimeCount > 0 ? Math.round(totalDeliveryMinutes / ordersWithTimeCount) + ` ${t('min')}` : `-- ${t('min')}`;

            if (currentPlanIsPro) {
                let sortedKeys = Object.keys(chartDataRaw).sort();
                let labels = sortedKeys.map(k => chartDataRaw[k].label);
                let dataPoints = sortedKeys.map(k => chartDataRaw[k].amount);
                const ctx = document.getElementById('revenueChart').getContext('2d');
                if (revenueChart) revenueChart.destroy();
                
                let gradient = ctx.createLinearGradient(0, 0, 0, 160); gradient.addColorStop(0, 'rgba(255, 90, 95, 0.4)'); gradient.addColorStop(1, 'rgba(255, 90, 95, 0.0)');
                revenueChart = new Chart(ctx, {
                    type: 'line', data: { labels: labels, datasets: [{ label: 'Rev', data: dataPoints, borderColor: '#FF5A5F', backgroundColor: gradient, borderWidth: 3, pointBackgroundColor: '#ffffff', pointBorderColor: '#FF5A5F', pointRadius: 4, fill: true, tension: 0.4 }] },
                    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { display: false }, ticks: { color: '#8a8d91', font: {size: 10} } }, y: { grid: { color: 'rgba(0,0,0,0.05)', borderDash: [5, 5] }, ticks: { color: '#8a8d91', font: {size: 10}, beginAtZero: true } } } }
                });

                const lbBox = document.getElementById('couriers-leaderboard');
                let cArr = Object.values(courierStats).sort((a,b) => b.count - a.count);
                if(cArr.length > 0) {
                    lbBox.innerHTML = '';
                    cArr.forEach((c, index) => {
                        let avgTime = c.count > 0 ? Math.round(c.timeTotal / c.count) : 0;
                        let medal = index === 0 ? '🥇' : (index === 1 ? '🥈' : (index === 2 ? '🥉' : `${index+1}.`));
                        lbBox.innerHTML += `
                            <div class="courier-rank">
                                <div class="cr-medal">${medal}</div>
                                <div class="cr-info"><div class="cr-name">${c.name}</div><div class="cr-stats">${t('avg_time_lbl')} ${avgTime} ${t('min')}</div></div>
                                <div class="cr-score"><div class="cr-score-val">${c.count}</div><div class="cr-score-lbl">${t('orders_lbl')}</div></div>
                            </div>
                        `;
                    });
                } else { lbBox.innerHTML = `<div style="text-align: center; color: var(--text-muted); font-size: 13px; padding: 10px;">${t('empty_rating')}</div>`; }
            }

            const feedBox = document.getElementById('recent-orders-list'); feedBox.innerHTML = '';
            orders.slice(0, 10).forEach(o => {
                let isCompleted = o.status === 'completed';
                let statusIcon = isCompleted ? '<i class="fa-solid fa-check"></i>' : '<i class="fa-solid fa-motorcycle"></i>';
                let iconBg = isCompleted ? 'style="color: #34C759; background: rgba(52, 199, 89, 0.1);"' : 'style="color: #FFB020; background: rgba(255, 176, 32, 0.1);"';
                let statusText = isCompleted ? `<span style="color: #34C759;">${t('status_done')}</span>` : `<span style="color: #FFB020;">${t('status_proc')}</span>`;
                let shortId = o.id.toString().substring(0, 5).toUpperCase();
                let timeStr = new Date((o.created_at||'').replace(' ','T')).toLocaleTimeString('uk-UA', {hour: '2-digit', minute:'2-digit'});

                feedBox.innerHTML += `
                    <div class="activity-item">
                        <div class="act-icon" style="${iconBg}">${statusIcon}</div>
                        <div class="act-details">
                            <div class="act-title">${t('order')} #${shortId}</div>
                            <div class="act-time">${timeStr} • ${statusText}</div>
                        </div>
                        <div class="act-price">${parseFloat(o.amount || 0).toFixed(2)} ${currencySymbol}</div>
                    </div>
                `;
            });
        } else {
            document.getElementById('val-total').innerText = '0.00 ' + currencySymbol; document.getElementById('val-cash').innerText = '0.00'; document.getElementById('val-term').innerText = '0.00';
            document.getElementById('val-orders').innerText = '0'; document.getElementById('val-late').innerText = '0'; document.getElementById('val-avg-check').innerText = '0.00 ' + currencySymbol; document.getElementById('val-time').innerText = `-- ${t('min')}`;

            document.getElementById('recent-orders-list').innerHTML = `<div style="text-align: center; color: var(--text-muted); font-size: 13px; padding: 15px 0;">${t('empty_orders')}</div>`;
            if(currentPlanIsPro) { document.getElementById('couriers-leaderboard').innerHTML = `<div style="text-align: center; color: var(--text-muted); font-size: 13px; padding: 10px;">${t('empty_rating')}</div>`; if (revenueChart) { revenueChart.destroy(); revenueChart = null; } }
        }

        if (currentPlanIsPro) {
            setTimeout(() => {
                // ── Mapbox heatmap ──────────────────────────────────────────
                const MAPBOX_TOKEN = window.MAPBOX_TOKEN || '';

                let centerLat = bizLat || 50.04132, centerLon = bizLon || 21.99901;
                if (!bizLat && heatData.length > 0) {
                    centerLat = heatData.reduce((s, p) => s + p[0], 0) / heatData.length;
                    centerLon = heatData.reduce((s, p) => s + p[1], 0) / heatData.length;
                }

                // Знищуємо попередню карту якщо є
                if (window.dashboardMap) {
                    window.dashboardMap.remove();
                    window.dashboardMap = null;
                }

                mapboxgl.accessToken = MAPBOX_TOKEN;
                window.dashboardMap = new mapboxgl.Map({
                    container: 'heatmap',
                    style: 'mapbox://styles/mapbox/light-v11',
                    center: [centerLon, centerLat],
                    zoom: 11.5,
                    interactive: false,   // як у Leaflet — без драгу/зуму
                    attributionControl: false
                });

                window.dashboardMap.on('load', () => {
                    // GeoJSON з точками замовлень
                    const geojson = {
                        type: 'FeatureCollection',
                        features: heatData.map(p => ({
                            type: 'Feature',
                            geometry: { type: 'Point', coordinates: [p[1], p[0]] },
                            properties: { weight: p[2] || 1 }
                        }))
                    };

                    window.dashboardMap.addSource('orders-heat', {
                        type: 'geojson',
                        data: geojson
                    });

                    // Heatmap layer — градієнт від coral до deep red
                    window.dashboardMap.addLayer({
                        id: 'orders-heat-layer',
                        type: 'heatmap',
                        source: 'orders-heat',
                        maxzoom: 17,
                        paint: {
                            'heatmap-weight': ['interpolate', ['linear'], ['get', 'weight'], 0, 0, 1, 1],
                            'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 0, 1, 15, 3],
                            'heatmap-color': [
                                'interpolate', ['linear'], ['heatmap-density'],
                                0,    'rgba(255,255,255,0)',
                                0.15, 'rgba(255,220,180,0.4)',
                                0.35, 'rgba(255,170,100,0.65)',
                                0.6,  'rgba(255,110,60,0.82)',
                                0.85, 'rgba(230,50,30,0.93)',
                                1,    'rgba(180,0,0,1)'
                            ],
                            'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 8, 18, 15, 45],
                            'heatmap-opacity': 0.75
                        }
                    });

                    // Коло радіусу доставки
                    if (bizLat && bizLon) {
                        window.dashboardMap.addSource('delivery-zone', {
                            type: 'geojson',
                            data: { type: 'Feature', geometry: { type: 'Point', coordinates: [bizLon, bizLat] }, properties: {} }
                        });
                        window.dashboardMap.addLayer({
                            id: 'delivery-zone-layer',
                            type: 'circle',
                            source: 'delivery-zone',
                            paint: {
                                'circle-radius': {
                                    stops: [
                                        [0, 0],
                                        [20, (bizRadius || 3) * 1000 / 0.075]
                                    ],
                                    base: 2
                                },
                                'circle-color': 'rgba(255,90,95,0.06)',
                                'circle-stroke-color': 'rgba(255,90,95,0.5)',
                                'circle-stroke-width': 1.5
                            }
                        });
                    }
                });
                // ────────────────────────────────────────────────────────────
            }, 500);
        }
    } catch (error) {
        console.error("DB Error:", error);
        // ✅ FIX: показуємо помилку замість вічного спінера
        _showInitError('Помилка завантаження даних: ' + (error?.message || error));
        const staffBox = document.getElementById('staff-list');
        if (staffBox) staffBox.innerHTML = '';
    }
}

// ════════════════════════════════════════
// ЛОГІКА ПЕРЕМИКАЧА РЕЖИМУ ДОСТАВКИ
// Підключи після DOM-ready або в кінці body
// ════════════════════════════════════════

(function initDeliveryModeSelector() {
  var uberField  = document.getElementById('uber-group-field');
  var groupInput = document.getElementById('courier_group_id');
  if (!uberField) return;

  var _savedGroupId = '';
  var _currentMode  = 'dispatcher';

  window.dmSelect = function(mode) {
    _currentMode = mode;
    var isUber = mode === 'uber';

    // Перемикач-таблетка
    var btnD = document.getElementById('dm-btn-dispatcher');
    var btnU = document.getElementById('dm-btn-uber');
    if (btnD) btnD.className = 'dm-toggle-btn' + (isUber ? '' : ' dm-toggle-btn--active');
    if (btnU) btnU.className = 'dm-toggle-btn' + (isUber ? ' dm-toggle-btn--active' : '');

    // Картки
    var cardD = document.getElementById('dm-card-dispatcher');
    var cardU = document.getElementById('dm-card-uber');
    if (cardD) { cardD.style.display = isUber ? 'none' : 'block'; }
    if (cardU) { cardU.style.display = isUber ? 'block' : 'none'; }

    // Поле group_id
    if (isUber) {
      uberField.classList.add('is-visible');
      if (groupInput) {
        groupInput.required = true;
        if (!groupInput.value && _savedGroupId) groupInput.value = _savedGroupId;
      }
    } else {
      if (groupInput && groupInput.value) _savedGroupId = groupInput.value;
      uberField.classList.remove('is-visible');
      if (groupInput) { groupInput.required = false; groupInput.value = ''; }
    }

    // Синхронізуємо hidden radio (для saveBizSettings)
    var radioD = document.getElementById('mode-dispatcher');
    var radioU = document.getElementById('mode-uber');
    if (radioD) radioD.checked = !isUber;
    if (radioU) radioU.checked = isUber;
  };

  window.DeliveryMode = {
    set: function(mode) { window.dmSelect(mode); },
    get: function() { return _currentMode; }
  };

  // Ініціалізація
  window.dmSelect('dispatcher');
})();

// Автозаповнення адреси
const bizAddrInput = document.getElementById('input-biz-address');
const bizAddrList = document.getElementById('biz-autocomplete-list');
let bizTimeout = null;

bizAddrInput.addEventListener('input', function() {
    clearTimeout(bizTimeout); bizLat = null; bizLon = null;
    document.getElementById('settings-map-container').style.display = 'none';
    const query = this.value.trim();
    if (query.length < 3) { bizAddrList.style.display = 'none'; return; }

    bizAddrList.innerHTML = `<div class="autocomplete-item" style="color: var(--primary); text-align: center; font-weight: 600;"><i class="fa-solid fa-spinner fa-spin"></i> ${t('search_load')}</div>`;
    bizAddrList.style.display = 'block';

    bizTimeout = setTimeout(async () => {
        const url = `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)}&format=json&addressdetails=1&limit=5`;
        try {
            const res = await fetch(url, { headers: { 'Accept-Language': currentLang } });
            const data = await res.json();
            if (data && data.length > 0) {
                bizAddrList.innerHTML = ''; 
                data.forEach(item => {
                    const addr = item.address;
                    const mainText = item.name || (addr && addr.road ? `${addr.road} ${addr.house_number || ''}`.trim() : item.display_name.split(',')[0]);
                    const city = addr ? (addr.city || addr.town || addr.village || '') : '';
                    const subText = city ? `${city}` : '';
                    const div = document.createElement('div'); div.className = 'autocomplete-item';
                    div.innerHTML = `<div class="addr-main">${mainText}</div><div class="addr-sub">${subText}</div>`;
                    
                    let currentLat = parseFloat(item.lat);
                    let currentLon = parseFloat(item.lon);

                    div.onclick = function() {
                        bizAddrInput.value = `${mainText}, ${city}`.replace(/, $/, '');
                        bizLat = currentLat; 
                        bizLon = currentLon;
                        bizAddrList.style.display = 'none'; 
                        updateSettingsMap(); 
                    };
                    bizAddrList.appendChild(div);
                });
            } else { bizAddrList.innerHTML = `<div class="autocomplete-item" style="color: #8a8d91; text-align: center;">${t('search_empty')}</div>`; }
        } catch(e) { bizAddrList.innerHTML = `<div class="autocomplete-item" style="color: #ff3b30; text-align: center;">${t('search_err')}</div>`; }
    }, 600); 
});

document.addEventListener('click', function(e) { if (e.target !== bizAddrInput && !bizAddrList.contains(e.target)) bizAddrList.style.display = 'none'; });


// Alias для сумісності (i18n.js викликає loadDashboard)
function loadDashboard() { return loadDashboardData(); }

// 🏁 ЗАПУСК ДОДАТКУ
(async () => {
    await initSupabase();
    setLanguage(currentLang);
    if (bizId && supabaseClient) {
        await loadDashboardData();
        window._dashboardReady = true; // ✅ FIX: прапор для setLanguage — не перезавантажувати під час init
    } else if (!bizId) {
        // ✅ FIX: bizId відсутній — відкрито не через Telegram бота
        _showInitError('Відкрийте дашборд через бота DeliPro.');
    } else if (!supabaseClient) {
        // initSupabase вже показав помилку, нічого не робимо
    }
})();

// ============================================================
// SALARY TAB
// ============================================================

var MONTH_NAMES_SAL = ['Січень','Лютий','Березень','Квітень','Травень','Червень',
    'Липень','Серпень','Вересень','Жовтень','Листопад','Грудень'];

var salaryMonthKey = '';
var salaryStaff = [];
var salarySettings = {};
var salaryBonuses = {};
var salaryPayments = {};
var salaryInitialized = false;

async function initSalaryTab() {
    if (!bizId || !supabaseClient) return;

    // Кнопка графіку — рендеримо тільки раз
    var schedWrap = document.getElementById('salary-schedule-btn-wrap');
    if (schedWrap && !schedWrap.dataset.rendered) {
        schedWrap.dataset.rendered = '1';
        var t = Math.floor(Date.now()/1000);
        var token = authToken || '';
        var tgId = tgUserIdParam || window.Telegram?.WebApp?.initDataUnsafe?.user?.id || '';
        // Беремо базовий URL з поточної сторінки (GitHub Pages)
        var basePageUrl = window.location.href.replace(/[^/]*$/, '');
        var url = basePageUrl + 'schedule.html?biz_id=' + bizId + '&tg_id=' + tgId + '&v=' + t + '&token=' + token;
        schedWrap.innerHTML = `
        <button class="btn-schedule-open" onclick="window.location.href='${url}'">
            <div class="btn-schedule-icon"><i class="fa-solid fa-calendar-days"></i></div>
            <div class="btn-schedule-text">
                <div class="btn-schedule-title">Графік роботи команди</div>
                <div class="btn-schedule-sub">Перегляд та редагування розкладу</div>
            </div>
            <i class="fa-solid fa-chevron-right" style="color:var(--primary);font-size:13px;"></i>
        </button>`;
    }

    // Init month — тільки при першому відкритті, зберігаємо вибраний місяць
    if (!salaryInitialized) {
        var now = new Date();
        salaryMonthKey = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}`;
        salaryInitialized = true;
    }

    await loadSalaryData();
    renderSalaryMonthTabs();
    await renderSalaryList();
}

async function loadSalaryData() {
    // Load staff
    var _r = await supabaseClient.from('staff')
        .select('*').eq('business_id', bizId); var staffData = _r.data;
    salaryStaff = staffData || [];

    // Load salary settings for all staff
    try {
        var _r = await supabaseClient.from('salary_settings')
            .select('*').eq('business_id', bizId); var sets = _r.data;
        salarySettings = {};
        (sets || []).forEach(s => { salarySettings[String(s.courier_id)] = s; });
    } catch(e) { salarySettings = {}; }

    // Load bonuses for current month
    try {
        var _r = await supabaseClient.from('salary_bonuses')
            .select('*').eq('business_id', bizId).eq('month', salaryMonthKey); var bons = _r.data;
        salaryBonuses = {};
        (bons || []).forEach(b => {
            if (!salaryBonuses[String(b.courier_id)]) salaryBonuses[String(b.courier_id)] = [];
            salaryBonuses[String(b.courier_id)].push(b);
        });
    } catch(e) { salaryBonuses = {}; }

    // Load payments status
    try {
        var _r = await supabaseClient.from('salary_payments')
            .select('*').eq('business_id', bizId).eq('month', salaryMonthKey); var pays = _r.data;
        salaryPayments = {};
        (pays || []).forEach(p => { salaryPayments[String(p.courier_id)] = p; });
    } catch(e) { salaryPayments = {}; }
}

function renderSalaryMonthTabs() {
    var wrap = document.getElementById('salary-month-tabs');
    if (!wrap) return;
    var now = new Date();
    var months = [];
    for (var d = -2; d <= 1; d++) {
        var dt = new Date(now.getFullYear(), now.getMonth() + d, 1);
        months.push(`${dt.getFullYear()}-${String(dt.getMonth()+1).padStart(2,'0')}`);
    }
    wrap.innerHTML = months.map(mk => {
        var [y, m] = mk.split('-');
        var label = MONTH_NAMES_SAL[parseInt(m)-1] + ' ' + y;
        return `<div class="salary-month-tab${mk===salaryMonthKey?' active':''}"
            onclick="switchSalaryMonth('${mk}')">${label}</div>`;
    }).join('');
}

async function switchSalaryMonth(mk) {
    salaryMonthKey = mk;
    await loadSalaryData();
    renderSalaryMonthTabs();
    await renderSalaryList();
}

async function renderSalaryList() {
    var list = document.getElementById('salary-list');
    var totalCard = document.getElementById('salary-total-card');
    if (!list) return;

    if (!salaryStaff.length) {
        list.innerHTML = '<div style="text-align:center;color:var(--text-muted);font-size:13px;font-weight:600;padding:30px 0;">Персонал не знайдено</div>';
        return;
    }

    list.innerHTML = '<div style="text-align:center;padding:30px 0;"><i class="fa-solid fa-circle-notch fa-spin" style="color:var(--primary);font-size:22px;"></i></div>';

    var mParts = salaryMonthKey.split('-');
    var mY = parseInt(mParts[0]);
    var mM = parseInt(mParts[1]);
    var startDate = new Date(Date.UTC(mY, mM-1, 1)).toISOString();
    var endDate   = new Date(Date.UTC(mY, mM, 1)).toISOString();
    var daysInMonth = new Date(mY, mM, 0).getDate();
    var cur = currencySymbol || 'zł';

    // 1. Завантажуємо shifts
    var allShifts = [], allOrders = [];
    try {
        var shRes = await supabaseClient.from('shifts')
            .select('*').eq('business_id', bizId)
            .gte('started_at', startDate).lt('started_at', endDate);
        allShifts = shRes.data || [];
    } catch(e) {}
    try {
        var ordRes = await supabaseClient.from('orders')
            .select('courier_id')
            .eq('business_id', bizId).eq('status', 'completed')
            .gte('created_at', startDate).lt('created_at', endDate);
        allOrders = ordRes.data || [];
    } catch(e) {}

    // 2. Завантажуємо дані графіку (actual_hours + планові)
    var salaryScheduleData = {};
    try {
        var schedRes = await supabaseClient.from('schedule')
            .select('*').eq('business_id', bizId)
            .gte('date', startDate.slice(0,10))
            .lt('date', endDate.slice(0,10));
        (schedRes.data || []).forEach(function(r) {
            var cid = String(r.courier_id);
            var day = parseInt(r.date.slice(8,10));
            if (!salaryScheduleData[cid]) salaryScheduleData[cid] = {};
            salaryScheduleData[cid][day] = {
                planned: true,
                start: r.planned_start,
                end: r.planned_end,
                actual_hours: r.actual_hours != null ? parseFloat(r.actual_hours) : null
            };
        });
    } catch(e) {}

    // 3. Групуємо shifts по кур'єру і по дню
    var shiftsByCourier = {};
    var realDailyByCourier = {};
    allShifts.forEach(function(sh) {
        var cid = String(sh.courier_id);
        // По кур'єру (для km)
        if (!shiftsByCourier[cid]) shiftsByCourier[cid] = [];
        shiftsByCourier[cid].push(sh);
        // По дню
        var dd = new Date((sh.started_at||'').replace(' ','T')).getDate();
        if (!realDailyByCourier[cid]) realDailyByCourier[cid] = {};
        if (!realDailyByCourier[cid][dd]) realDailyByCourier[cid][dd] = { hours: 0 };
        if (sh.ended_at) {
            realDailyByCourier[cid][dd].hours += (new Date((sh.ended_at||'').replace(' ','T')) - new Date((sh.started_at||'').replace(' ','T'))) / 3600000;
        } else {
            realDailyByCourier[cid][dd].hours += (new Date() - new Date((sh.started_at||'').replace(' ','T'))) / 3600000;
        }
    });

    var ordersByCourier = {};
    allOrders.forEach(function(o) {
        var cid = String(o.courier_id);
        ordersByCourier[cid] = (ordersByCourier[cid] || 0) + 1;
    });

    var totalFund = 0;
    var html = '';

    for (var si = 0; si < salaryStaff.length; si++) {
        var s = salaryStaff[si];
        var cid = String(s.user_id);
        var sets = salarySettings[cid] || {};
        var hourlyRate  = parseFloat(sets.hourly_rate) || 0;
        var kmEnabled   = !!sets.km_enabled;
        var kmRate      = kmEnabled ? (parseFloat(sets.km_rate) || parseFloat(window._bizData && window._bizData.km_rate) || 0) : 0;
        var orderEnabled = !!sets.order_enabled;
        var orderRate   = orderEnabled ? (parseFloat(sets.order_rate) || 0) : 0;
        var bonusList   = salaryBonuses[cid] || [];
        var payment     = salaryPayments[cid];

        // Km з реальних змін
        var totalKm = 0;
        (shiftsByCourier[cid] || []).forEach(function(sh) {
            if (sh.ended_at && sh.end_km && sh.start_km) totalKm += (sh.end_km - sh.start_km);
        });

        // Години по днях (пріоритет: actual > real > planned)
        var totalHours = 0;
        var shiftCount = 0;
        var monthSched = salaryScheduleData[cid] || {};

        for (var dd = 1; dd <= daysInMonth; dd++) {
            var slot = monthSched[dd];
            var realDay = realDailyByCourier[cid] ? realDailyByCourier[cid][dd] : null;
            var dayHours = 0;
            var workedToday = false;

            if (slot && slot.actual_hours != null && !isNaN(slot.actual_hours)) {
                dayHours = slot.actual_hours;
                workedToday = true;
            } else if (realDay && realDay.hours > 0) {
                dayHours = realDay.hours;
                workedToday = true;
            } else if (slot && slot.planned && slot.start && slot.end) {
                var ps = slot.start.split(':');
                var pe = slot.end.split(':');
                var diff = (parseInt(pe[0])*60+parseInt(pe[1]))-(parseInt(ps[0])*60+parseInt(ps[1]));
                if (diff < 0) diff += 1440;
                if (diff > 0) { dayHours = diff/60; workedToday = true; }
            }
            totalHours += dayHours;
            if (workedToday || (realDay && realDay.hours > 0)) shiftCount++;
        }
        totalHours = Math.round(totalHours * 10) / 10;
        totalKm = Math.round(totalKm);
        var ordersCount = ordersByCourier[cid] || 0;

        // Бонуси
        var bonusTotal = 0;
        bonusList.forEach(function(b) {
            bonusTotal += parseFloat(b.amount) || 0;
        });

        // Розрахунок
        var baseSalary  = Math.round(totalHours * hourlyRate * 100) / 100;
        var orderBonus  = Math.round(ordersCount * orderRate * 100) / 100;
        var kmDeduction = Math.round(totalKm * kmRate * 100) / 100;
        var earnedSafe  = baseSalary + orderBonus - kmDeduction + bonusTotal;
        if (isNaN(earnedSafe)) earnedSafe = 0;
        totalFund += earnedSafe;

        var isPaid = payment && payment.paid;
        var roleLabel = s.role === 'manager' ? 'Адміністратор' :
                        s.role === 'kitchen'  ? 'Кухня' : "Кур'єр";
        var roleClass = s.role === 'manager' ? 'av-admin' :
                        s.role === 'kitchen'  ? 'av-sushi' : 'av-courier';
        var initials = (s.name || '?').split(' ').map(function(w){return w[0]||'';}).join('').slice(0,2).toUpperCase();
        var bodyId = 'sal-body-' + cid;
        var settId = 'sal-sett-' + cid;

        // Ledger
        var ledger = '';
        if (hourlyRate > 0 || totalHours > 0) {
            ledger += '<div class="ledger-row"><span class="ledger-desc">Базова ЗП (' + totalHours.toFixed(1) + 'г × ' + hourlyRate + ' ' + cur + ')</span><span class="ledger-amount neutral">' + fmtAmt(baseSalary, cur) + '</span></div>';
        }
        if (orderEnabled && (orderRate > 0 || ordersCount > 0)) {
            ledger += '<div class="ledger-row"><span class="ledger-desc">За замовлення (' + ordersCount + ' × ' + orderRate + ' ' + cur + ')</span><span class="ledger-amount ' + (orderBonus >= 0 ? 'plus' : 'minus') + '">' + fmtAmt(orderBonus, cur, true) + '</span></div>';
        }
        if (kmEnabled && (kmRate > 0 || totalKm > 0)) {
            ledger += '<div class="ledger-row"><span class="ledger-desc">Пробіг (' + totalKm + ' км × ' + kmRate + ' ' + cur + ')</span><span class="ledger-amount minus">-' + kmDeduction.toFixed(2) + ' ' + cur + '</span></div>';
        }
        bonusList.forEach(function(b) {
            var amt = parseFloat(b.amount) || 0;
            var cls = amt >= 0 ? 'plus' : 'minus';
            var desc = b.comment || (amt >= 0 ? 'Премія' : 'Штраф');
            ledger += '<div class="ledger-row"><span class="ledger-desc">' + esc(desc) + '</span><span class="ledger-amount ' + cls + '">' + fmtAmt(amt, cur, true) + '</span></div>';
        });
        if (!ledger) ledger = '<div style="font-size:12px;color:var(--text-muted);font-weight:600;">Немає даних за цей місяць</div>';

        // Settings rows
        var settHourly = '<div class="setting-row"><span class="setting-label">Погодинна (' + cur + '/год)</span><div class="salary-settings-right"><input type="number" class="setting-input" id="hr-' + cid + '" value="' + hourlyRate + '" min="0" step="0.5"></div></div>';
        var settOrders = '<div class="setting-row"><span class="setting-label">За замовлення (' + cur + '/шт)</span><div class="salary-settings-right"><label class="toggle-switch"><input type="checkbox" id="oe-' + cid + '"' + (orderEnabled ? ' checked' : '') + '><span class="slider"></span></label><input type="number" class="setting-input" id="or-' + cid + '" value="' + orderRate + '" min="0" step="0.1"></div></div>';
        var settKm = '<div class="setting-row"><span class="setting-label">Вирахувати за км (' + cur + '/км)</span><div class="salary-settings-right"><label class="toggle-switch"><input type="checkbox" id="ke-' + cid + '"' + (kmEnabled ? ' checked' : '') + '><span class="slider"></span></label><input type="number" class="setting-input" id="kr-' + cid + '" value="' + kmRate + '" min="0" step="0.1"></div></div>';

        html += '<div class="salary-card' + (isPaid ? ' salary-card-paid' : '') + '" id="sal-card-' + cid + '">' +
            '<div class="card-summary" data-toggle="' + bodyId + '">' +
              '<div class="avatar ' + roleClass + '">' + initials + '</div>' +
              '<div class="user-meta"><span class="name">' + esc(s.name) + '</span><span class="role">' + roleLabel + '</span></div>' +
              '<div class="amount-block">' +
                '<div class="total-sum">' + fmtAmt(earnedSafe, cur) + '</div>' +
                '<div class="status-badge ' + (isPaid ? 'paid' : 'unpaid') + '" data-pay="' + cid + '" data-paid="' + (isPaid ? '1' : '0') + '">' +
                  (isPaid ? '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> Виплачено' :
                            '<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg> Не виплачено') +
                '</div>' +
              '</div>' +
              '<div class="chevron" id="chev-' + cid + '"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg></div>' +
            '</div>' +
            '<div class="card-body" id="' + bodyId + '" style="display:none;">' +
              '<div class="settings-block">' +
                '<button class="settings-summary" data-settid="' + settId + '">' +
                  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="21" x2="4" y2="14"></line><line x1="4" y1="10" x2="4" y2="3"></line><line x1="12" y1="21" x2="12" y2="12"></line><line x1="12" y1="8" x2="12" y2="3"></line><line x1="20" y1="21" x2="20" y2="16"></line><line x1="20" y1="12" x2="20" y2="3"></line><line x1="1" y1="14" x2="7" y2="14"></line><line x1="9" y1="8" x2="15" y2="8"></line><line x1="17" y1="16" x2="23" y2="16"></line></svg>' +
                  'Ставки та налаштування' +
                  '<svg class="sett-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>' +
                '</button>' +
                '<div class="settings-content" id="' + settId + '" style="display:none;">' +
                  settHourly + settOrders + settKm +
                  '<button class="btn btn-save-rates" data-savecid="' + cid + '">Зберегти ставки</button>' +
                '</div>' +
              '</div>' +
              '<div class="stats-grid cols-3">' +
                '<div class="stat-item"><div class="stat-label">Зміни</div><div class="stat-value">' + shiftCount + ' шт</div></div>' +
                '<div class="stat-item"><div class="stat-label">Години</div><div class="stat-value">' + totalHours.toFixed(1) + 'г</div></div>' +
                '<div class="stat-item"><div class="stat-label">Замовлення</div><div class="stat-value">' + ordersCount + ' шт</div></div>' +
              '</div>' +
              '<div class="section-heading">ПРЕМІЯ / ШТРАФ / АВАНС</div>' +
              '<div class="ledger">' + ledger + '</div>' +
              '<div class="add-action">' +
                '<input type="number" class="input-styled in-amount" id="bon-amt-' + cid + '" placeholder="Сума (-штраф)">' +
                '<input type="text" class="input-styled in-desc" id="bon-com-' + cid + '" placeholder="Коментар">' +
              '</div>' +
              '<div class="card-actions">' +
                '<button class="btn btn-add-bonus" data-addbonus="' + cid + '"><i class="fa-solid fa-plus"></i> Додати</button>' +
                '<button class="btn btn-primary" data-pay="' + cid + '" data-paid="' + (isPaid ? '1' : '0') + '">' +
                  (isPaid ? '<i class="fa-solid fa-rotate-left"></i> Скасувати виплату' : '<i class="fa-solid fa-check"></i> Виплатити') +
                '</button>' +
              '</div>' +
            '</div>' +
        '</div>';
    }

    list.innerHTML = html || '<div style="text-align:center;color:var(--text-muted);padding:30px 0;">Даних немає</div>';

    list.onclick = function(e) {
        var header = e.target.closest('[data-toggle]');
        if (header && !e.target.closest('[data-pay]')) { toggleSalaryCard(header.getAttribute('data-toggle'), header); return; }
        var payEl = e.target.closest('[data-pay]');
        if (payEl) { e.stopPropagation(); togglePayment(payEl.getAttribute('data-pay'), payEl.getAttribute('data-paid') === '1'); return; }
        var settBtn = e.target.closest('[data-settid]');
        if (settBtn) { toggleSalarySettings(settBtn.getAttribute('data-settid'), settBtn); return; }
        var saveBtn = e.target.closest('[data-savecid]');
        if (saveBtn) { saveSalarySettings(saveBtn.getAttribute('data-savecid')); return; }
        var bonusBtn = e.target.closest('[data-addbonus]');
        if (bonusBtn) { addBonus(bonusBtn.getAttribute('data-addbonus')); return; }
    };

    if (totalCard) {
        totalCard.style.display = 'block';
        document.getElementById('salary-total-amount').textContent = fmtAmt(totalFund, cur);
    }
}


function fmtAmt(val, cur, withSign) {
    var n = Math.abs(val);
    var str = n.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' ' + cur;
    if (withSign) return (val >= 0 ? '+' : '-') + str;
    return (val < 0 ? '-' : '') + str;
}

function esc(str) {
    return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function toggleSalaryCard(bodyId, headerEl) {
    var body = document.getElementById(bodyId);
    if (!body) return;
    var open = body.style.display === 'none';
    body.style.display = open ? '' : 'none';
    var chevId = 'chev-' + bodyId.replace('sal-body-','');
    var chev = document.getElementById(chevId);
    if (chev) chev.style.transform = open ? 'rotate(180deg)' : '';
    if (headerEl) headerEl.style.borderBottom = open ? '1px solid var(--border-color)' : '';
}

function toggleSalarySettings(settId, btn) {
    var sett = document.getElementById(settId);
    if (!sett) return;
    var open = sett.style.display === 'none';
    sett.style.display = open ? '' : 'none';
    var arrow = btn.querySelector('.sett-arrow');
    if (arrow) arrow.style.transform = open ? 'rotate(180deg)' : '';
}




async function saveSalarySettings(cid) {
    if (!supabaseClient) return;
    var hourlyRate = parseFloat(document.getElementById(`hr-${cid}`)?.value) || 0;
    var orderRate  = parseFloat(document.getElementById(`or-${cid}`)?.value) || 0;
    var kmRate     = parseFloat(document.getElementById(`kr-${cid}`)?.value) || 0;
    var orderEnabled = document.getElementById(`oe-${cid}`)?.checked || false;
    var kmEnabled    = document.getElementById(`ke-${cid}`)?.checked || false;

    try {
        await supabaseClient.from('salary_settings').upsert({
            business_id: bizId,
            courier_id: cid,
            hourly_rate: hourlyRate,
            order_rate: orderRate,
            km_rate: kmRate,
            order_enabled: orderEnabled,
            km_enabled: kmEnabled
        }, { onConflict: 'business_id,courier_id' });
        showToast('✅ Ставки збережено', '');
        await switchSalaryMonth(salaryMonthKey);
    } catch(e) { showToast('❌ Помилка', e.message); }
}

async function addBonus(cid) {
    var amtEl = document.getElementById('bon-amt-' + cid);
    var comEl = document.getElementById('bon-com-' + cid);
    var amt = parseFloat(amtEl ? amtEl.value : '');
    var com = (comEl && comEl.value) ? comEl.value.trim() : '';
    if (isNaN(amt)) { showToast('❌ Введіть суму', ''); return; }
    try {
        await supabaseClient.from('salary_bonuses').insert({
            business_id: bizId,
            courier_id: cid,
            month: salaryMonthKey,
            amount: amt,
            comment: com
        });
        showToast('✅ Премію додано', '');
        await switchSalaryMonth(salaryMonthKey);
    } catch(e) { showToast('❌ Помилка', e.message); }
}

async function togglePayment(cid, currentlyPaid) {
    var newPaid = !currentlyPaid;
    try {
        await supabaseClient.from('salary_payments').upsert({
            business_id: bizId,
            courier_id: cid,
            month: salaryMonthKey,
            paid: newPaid,
            paid_at: newPaid ? new Date().toISOString() : null
        }, { onConflict: 'business_id,courier_id,month' });
        showToast(newPaid ? '✅ Виплату підтверджено' : '↩️ Статус скасовано', '');
        await switchSalaryMonth(salaryMonthKey);
    } catch(e) { showToast('❌ Помилка', e.message); }
}
