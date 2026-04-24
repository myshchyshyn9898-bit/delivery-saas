/*
  ====================================================================
  ФАЙЛ: js/i18n.js
  ДЛЯ ЧОГО: Тут зберігаються всі переклади твого додатку.
  ЩО ТУТ РЕДАГУВАТИ: 
  - Знайшов одруківку в тексті? Шукай її тут.
  - Хочеш додати нову мову? Скопіюй блок 'en', назви його 'de' (німецька)
    і переклади всі слова всередині.
  ====================================================================
*/

// Безпечне визначення мови користувача з Телеграму
var tgLang = 'en';
try { 
    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initDataUnsafe && window.Telegram.WebApp.initDataUnsafe.user) {
        tgLang = window.Telegram.WebApp.initDataUnsafe.user.language_code || 'en'; 
    }
} catch(e) {}

var savedLang = localStorage.getItem('appLang');
var currentLang = savedLang || tgLang;
if (!['uk', 'ru', 'pl', 'en'].includes(currentLang)) currentLang = 'en';

// СЛОВНИК ПЕРЕКЛАДІВ
var i18n = {
    'uk': {
        
        mode_d_title: "Адмін розподіляє замовлення",
        mode_u_title: "Кур'єри самі беруть замовлення",
        mode_d_desc: "Кожне замовлення ви особисто призначаєте потрібному кур'єру через карту",
        mode_u_desc: "Замовлення падає в загальну групу — хто перший натиснув, той і везе",
        mode_classic: "Класика", mode_glovo: "Glovo-стиль",
        mode_dispatcher_short: "Диспетчер", mode_uber_short: "Вільна каса",
        flow_order: "Замовлення", flow_admin: "Адмін", flow_assign: "Призначає",
        flow_courier: "Кур'єр", flow_group: "Група", flow_first: "Перший бере",
        d_bullet_1: "Повний контроль — ви вирішуєте хто і що везе",
        d_bullet_2: "Ідеально для невеликих команд і POS-інтеграцій",
        d_bullet_3: "Кур'єр отримує замовлення тільки від адміна",
        u_bullet_1: "Автоматично — адмін не витрачає час на розподіл",
        u_bullet_2: "Потрібна окрема Telegram-група для кур'єрів",
        u_bullet_3: "Ідеально для великого потоку замовлень",
        delivery_mode_label: "Режим доставки",
        tab_today: "Сьогодні", tab_week: "Тиждень", tab_month: "Місяць",
        metric_total: "Загальна каса", metric_heat: "Теплова карта", metric_avg: "Середній чек",
        metric_chart: "Динаміка доходу", metric_cash: "Готівка", metric_term: "Термінал",
        metric_orders: "Замовлень", metric_time: "Сер. час", metric_late: "Запізнення (> 45 хв)",
        title_recent: "Останні замовлення", title_rating: "Рейтинг кур'єрів (PRO)", 
        title_invite: "Запрошення персоналу", btn_add_c: "Додати Кур'єра", btn_add_m: "Додати Менеджера", 
        btn_reset: "Оновити посилання (Анулювати старі)", title_active: "Активний персонал",
        title_profile: "Профіль бізнесу", lbl_biz_id: "ID Закладу", lbl_edit: "Редагувати профіль",
        lbl_sub: "Підписка", lbl_cur: "Валюта системи", btn_logout: "Вийти",
        nav_home: "Головна", nav_team: "Команда", nav_settings: "Налаштування",
        modal_plan: "Управління підпискою", plan_active: "АКТИВНА", plan_paid: "Оплачено до:",
        set_title: "Налаштування бізнесу", set_name: "Назва бізнесу", set_addr: "Адреса закладу (база)",
        set_rad: "Радіус доставки (км)", set_cur: "Валюта", btn_save: "💾 Зберегти зміни",
        set_name_ph: "Введіть назву...", set_addr_ph: "Почніть вводити вулицю...", set_rad_ph: "Наприклад: 5",
        tariff: "Тариф",
        loading: "Завантаження...", empty_team: "В команді поки нікого немає.", empty_orders: "За цей період замовлень не знайдено",
        empty_rating: "Немає даних для рейтингу.", err_export: "Немає замовлень для експорту.",
        status_done: "Доставлено", status_proc: "В процесі", min: "хв", order: "Замовлення", avg_time_lbl: "Сер. час:",
        orders_lbl: "замовлень", role_c: "Кур'єр", role_m: "Менеджер",
        search_load: "Шукаємо адресу...", search_empty: "Нічого не знайдено 😕", search_err: "❌ Помилка пошуку",
        alert_save: "✅ Налаштування успішно збережено!", alert_err: "❌ Помилка: ", confirm_reset: "⚠️ ВИ ВПЕВНЕНІ?\n\nВсі старі посилання миттєво перестануть працювати.",
        alert_reset: "✅ Успіх! Старі посилання анульовано.", confirm_del: "Ви дійсно хочете звільнити працівника",
        limit_c: "⚠️ ЛІМІТ ВИЧЕРПАНО!\n\nУ тарифі BASIC можна додати максимум 2 кур'єри.",
        limit_m: "⚠️ ЛІМІТ ВИЧЕРПАНО!\n\nУ тарифі BASIC можна додати максимум 1 адміністратора.",
        err_biz: "Помилка: Business ID не знайдено.", err_tok: "Помилка безпеки: Токен запрошення відсутній.",
        success_copy: "✅ Безпечне посилання скопійовано!",
        f1: "Базовый дашборд", f2: "Безлімітні замовлення", f3: "Експорт CSV, Графік та Рейтинг",
        f4: "Інтерактивна тепловая карта", f5: "Статистика часу та Запізнень", f6: "Безлімітний персонал",
        pr_month: "/ міс", pr_b1: "До 2 кур'єрів у штаті", pr_b2: "Базова статистика", pr_p1_b: "Необмежена", pr_p1: "кількість кур'єрів", pr_p2: "Теплова карта та глибока аналітика", pr_p3: "Управління менеджерами",
        btn_pay: "Оплатити", btn_manage: "Керувати підпискою (Whop)", btn_upgrade_now: "Оновити до PRO", btn_unlock: "Розблокувати",
        txt_left: "Залишилося", txt_days: "днів", txt_avail: "Доступно до", txt_next_pay: "Наступне автоматичне списання:", txt_max: "Усі преміум функції розблоковано. Дякуємо, що ви з нами!", txt_expired: "Ваш тріал або підписка завершилася. Оберіть тариф для розблокування.",
        title_sel_plan: "Оберіть тариф для продовження:", title_upsell: "⚡️ Хочете більше можливостей?", title_sel_unlock: "Оберіть тариф для розблокування:",
        badge_active: "АКТИВНО", badge_paid: "ОПЛАЧЕНО", badge_block: "БЛОКУВАННЯ",
        state_basic: "BASIC ТАРИФ", state_pro: "PRO ТАРИФ", state_closed: "ДОСТУП ЗАКРИТО",
        toast_title: "Увага!", toast_desc: "Ваш тариф завершується дуже скоро. Оновіть підписку, щоб не втратити доступ.",
        support_title: "Служба підтримки", support_desc: "Маєте питання чи знайшли баг?",
        ticket_create_title: "Створити тікет", ticket_create_desc: "Ми отримаємо ваше повідомлення в Telegram і швидко відповімо.",
        ticket_label_reason: "Причина звернення", ticket_btn_bug: "Технічний баг", ticket_btn_setup: "Налаштування", ticket_btn_pay: "Оплата/Тариф", ticket_btn_other: "Інше",
        ticket_label_topic: "Тема тікета", ticket_ph_topic: "Коротко про проблему...",
        ticket_label_msg: "Опис проблеми", ticket_ph_msg: "Опишіть ситуацію максимально детально...",
        ticket_btn_send: "Відправити тікет", ticket_btn_sending: "Відправляємо...",
        ticket_alert_empty: "📍 Будь ласка, заповніть тему та опис проблеми.",
        ticket_alert_test: "Тікет відправлено (Тестовий режим)!"
    },
        api_token_lbl: "API Токен (Ключ доступу)",
        tg_group_id_lbl: "ID Telegram-групи кур'єрів",
        integrations_lbl: "Інтеграції (POS)",
        metric_activity: "Активність замовлень",
        feature_deep: "Глибока аналітика та експорт",
        feature_schedule: "Графік та зарплати",
        salary_fund: "Загальний фонд оплати праці",
        mode_uber_desc: "Замовлення падають у загальну групу, хто з кур'єрів перший натиснув — той і везе.",
        mode_disp_desc: "Замовлення приходять адміну, який призначає кур'єрів через карту.",
        nav_salary: "Зарплати",
        salary_team_title: "Зарплати команди",
        integrations_cat: "Каталог Інтеграцій",
        btn_manage_sub: "Керувати підпискою",
        not_connected: "Не підключено",
        priority_low: "Низька",
        priority_high: "Висока",
        select_plan_lbl: "Оберіть тариф:",
        delivery_mode_lbl: "Режим роботи доставки",
        btn_connect: "Підключити",
        pos_connect_hint: "Підключіть вашу касу для автоматизації доставки.",
        mode_dispatcher: "👑 Диспетчер (Класика)",
        mode_uber: "🛵 Вільна каса (Glovo-стиль)",
    'ru': {
        
        mode_d_title: "Администратор распределяет заказы",
        mode_u_title: "Курьеры берут заказы сами",
        mode_d_desc: "Каждый заказ вы лично назначаете нужному курьеру через карту",
        mode_u_desc: "Заказ падает в группу — кто первым нажал, тот и везёт",
        mode_classic: "Классика", mode_glovo: "Glovo-стиль",
        mode_dispatcher_short: "Диспетчер", mode_uber_short: "Свободный рынок",
        flow_order: "Заказ", flow_admin: "Админ", flow_assign: "Назначает",
        flow_courier: "Курьер", flow_group: "Группа", flow_first: "Первый берёт",
        d_bullet_1: "Полный контроль — вы решаете кто и что везёт",
        d_bullet_2: "Идеально для небольших команд и POS-интеграций",
        d_bullet_3: "Курьер получает заказы только от администратора",
        u_bullet_1: "Автоматически — администратор экономит время",
        u_bullet_2: "Нужна отдельная Telegram-группа для курьеров",
        u_bullet_3: "Идеально для большого потока заказов",
        delivery_mode_label: "Режим доставки",
        tab_today: "Сегодня", tab_week: "Неделя", tab_month: "Месяц",
        metric_total: "Общая касса", metric_heat: "Тепловая карта", metric_avg: "Средний чек",
        metric_chart: "Динамика дохода", metric_cash: "Наличные", metric_term: "Терминал",
        metric_orders: "Заказов", metric_time: "Ср. время", metric_late: "Опоздания (> 45 мин)",
        title_recent: "Последние заказы", title_rating: "Рейтинг курьеров (PRO)", 
        title_invite: "Приглашение персонала", btn_add_c: "Добавить Курьера", btn_add_m: "Добавить Менеджера", 
        btn_reset: "Обновить ссылки (Аннулировать старые)", title_active: "Активный персонал",
        title_profile: "Профиль бизнеса", lbl_biz_id: "ID Заведения", lbl_edit: "Редактировать профиль",
        lbl_sub: "Подписка", lbl_cur: "Валюта системы", btn_logout: "Выйти",
        nav_home: "Главная", nav_team: "Команда", nav_settings: "Настройки",
        modal_plan: "Управление подпиской", plan_active: "АКТИВНА", plan_paid: "Оплачено до:",
        set_title: "Настройки бизнеса", set_name: "Название заведения", set_addr: "Адрес заведения (база)",
        set_rad: "Радиус доставки (км)", set_cur: "Валюта", btn_save: "💾 Сохранить изменения",
        set_name_ph: "Введите название...", set_addr_ph: "Начните вводить улицу...", set_rad_ph: "Например: 5",
        tariff: "Тариф",
        loading: "Загрузка...", empty_team: "В команде пока никого нет.", empty_orders: "За этот период заказов не найдено",
        empty_rating: "Нет данных для рейтинга.", err_export: "Нет заказов для экспорта.",
        status_done: "Доставлено", status_proc: "В процессе", min: "мин", order: "Заказ", avg_time_lbl: "Ср. время:",
        orders_lbl: "заказов", role_c: "Курьер", role_m: "Менеджер",
        search_load: "Ищем адрес...", search_empty: "Ничего не найдено 😕", search_err: "❌ Ошибка поиска",
        alert_save: "✅ Настройки успешно сохранены!", alert_err: "❌ Ошибка: ", confirm_reset: "⚠️ ВЫ УВЕРЕНЫ?\n\nВсе старые ссылки мгновенно перестанут работать.",
        alert_reset: "✅ Успех! Старые ссылки аннулированы.", confirm_del: "Вы действительно хотите уволить сотрудника",
        limit_c: "⚠️ ЛИМИТ ИСЧЕРПАН!\n\nВ тарифе BASIC можно добавить максимум 2 курьера.",
        limit_m: "⚠️ ЛИМИТ ИСЧЕРПАН!\n\nВ тарифе BASIC можно добавить максимум 1 администратора.",
        err_biz: "Ошибка: Business ID не найден.", err_tok: "Ошибка безопасности: Токен приглашения отсутствует.",
        success_copy: "✅ Безопасная ссылка скопирована!",
        f1: "Базовый дашборд", f2: "Безлимитные заказы", f3: "Экспорт CSV, График и Рейтинг",
        f4: "Интерактивная тепловая карта", f5: "Статистика времени и Опозданий", f6: "Безлимитный персонал",
        pr_month: "/ мес", pr_b1: "До 2 курьеров в штате", pr_b2: "Базовая статистика", pr_p1_b: "Неограниченное", pr_p1: "количество курьеров", pr_p2: "Тепловая карта и глубокая аналитика", pr_p3: "Управление менеджерами",
        btn_pay: "Оплатить", btn_manage: "Управление подпиской (Whop)", btn_upgrade_now: "Обновить до PRO", btn_unlock: "Разблокировать",
        txt_left: "Осталось", txt_days: "дней", txt_avail: "Доступно до", txt_next_pay: "Следующее списание:", txt_max: "Все функции разблокированы. Спасибо, что вы с нами!", txt_expired: "Ваш триал или подписка завершилась. Выберите тариф.",
        title_sel_plan: "Выберите тариф для продления:", title_upsell: "⚡️ Хотите больше возможностей?", title_sel_unlock: "Выберите тариф для разблокировки:",
        badge_active: "АКТИВНО", badge_paid: "ОПЛАЧЕНО", badge_block: "БЛОКИРОВКА",
        state_basic: "BASIC ТАРИФ", state_pro: "PRO ТАРИФ", state_closed: "ДОСТУП ЗАКРЫТ",
        toast_title: "Внимание!", toast_desc: "Ваш тариф скоро истекает. Обновите подписку, чтобы не потерять доступ.",
        support_title: "Служба поддержки", support_desc: "Есть вопросы или нашли баг?",
        ticket_create_title: "Создать тикет", ticket_create_desc: "Мы получим ваше сообщение в Telegram и быстро ответим.",
        ticket_label_reason: "Причина обращения", ticket_btn_bug: "Технический баг", ticket_btn_setup: "Настройка", ticket_btn_pay: "Оплата/Тариф", ticket_btn_other: "Другое",
        ticket_label_topic: "Тема тикета", ticket_ph_topic: "Коротко о проблеме...",
        ticket_label_msg: "Описание проблемы", ticket_ph_msg: "Опишите ситуацию максимально подробно...",
        ticket_btn_send: "Отправить тикет", ticket_btn_sending: "Отправляем...",
        ticket_alert_empty: "📍 Пожалуйста, заполните тему и описание проблемы.",
        ticket_alert_test: "Тикет отправлен (Тестовый режим)!"
    },
        api_token_lbl: "API Токен (Ключ доступа)",
        tg_group_id_lbl: "ID Telegram-группы курьеров",
        integrations_lbl: "Интеграции (POS)",
        metric_activity: "Активность заказов",
        feature_deep: "Глубокая аналитика и экспорт",
        feature_schedule: "График и зарплаты",
        salary_fund: "Общий фонд оплаты труда",
        mode_uber_desc: "Заказы падают в общую группу, кто первый нажал — тот и везёт.",
        mode_disp_desc: "Заказы приходят администратору, который назначает курьеров через карту.",
        nav_salary: "Зарплаты",
        salary_team_title: "Зарплаты команды",
        integrations_cat: "Каталог интеграций",
        btn_manage_sub: "Управление подпиской",
        not_connected: "Не подключено",
        priority_low: "Низкая",
        priority_high: "Высокая",
        select_plan_lbl: "Выберите тариф:",
        delivery_mode_lbl: "Режим работы доставки",
        btn_connect: "Подключить",
        pos_connect_hint: "Подключите вашу кассу для автоматизации доставки.",
        mode_dispatcher: "👑 Диспетчер (Классика)",
        mode_uber: "🛵 Свободная касса (Glovo-стиль)",
    'pl': {
        
        mode_d_title: "Admin przydziela zamówienia",
        mode_u_title: "Kurierzy sami przyjmują zamówienia",
        mode_d_desc: "Każde zamówienie przypisujesz osobiście kurierowi przez mapę",
        mode_u_desc: "Zamówienie trafia do grupy — kto pierwszy kliknie, ten je wiezie",
        mode_classic: "Klasyczny", mode_glovo: "Styl Glovo",
        mode_dispatcher_short: "Dyspozytor", mode_uber_short: "Wolny rynek",
        flow_order: "Zamówienie", flow_admin: "Admin", flow_assign: "Przydziela",
        flow_courier: "Kurier", flow_group: "Grupa", flow_first: "Pierwszy bierze",
        d_bullet_1: "Pełna kontrola — ty decydujesz kto co wiezie",
        d_bullet_2: "Idealne dla małych zespołów i integracji POS",
        d_bullet_3: "Kurier otrzymuje zamówienia tylko od admina",
        u_bullet_1: "Automatycznie — admin nie traci czasu na przydział",
        u_bullet_2: "Wymaga osobnej grupy Telegram dla kurierów",
        u_bullet_3: "Idealne dla dużego przepływu zamówień",
        delivery_mode_label: "Tryb dostawy",
        tab_today: "Dzisiaj", tab_week: "Tydzień", tab_month: "Miesiąc",
        metric_total: "Całkowity utarg", metric_heat: "Mapa cieplna", metric_avg: "Średni paragon",
        metric_chart: "Dynamika dochodu", metric_cash: "Gotówka", metric_term: "Terminal",
        metric_orders: "Zamówień", metric_time: "Śr. czas", metric_late: "Spóźnienia (> 45 min)",
        title_recent: "Ostatnie zamówienia", title_rating: "Ranking kurierów (PRO)", 
        title_invite: "Zaproszenie personelu", btn_add_c: "Dodaj Kuriera", btn_add_m: "Dodaj Menedżera", 
        btn_reset: "Odśwież linki (Anuluj stare)", title_active: "Aktywny personel",
        title_profile: "Profil firmy", lbl_biz_id: "ID Lokalu", lbl_edit: "Edytuj profil",
        lbl_sub: "Subskrypcja", lbl_cur: "Waluta systemu", btn_logout: "Wyloguj",
        nav_home: "Główna", nav_team: "Zespół", nav_settings: "Ustawienia",
        modal_plan: "Zarządzanie subskrypcją", plan_active: "AKTYWNA", plan_paid: "Opłacono do:",
        set_title: "Ustawienia firmy", set_name: "Nazwa lokalu", set_addr: "Adres lokalu (baza)",
        set_rad: "Promień dostawy (km)", set_cur: "Waluta", btn_save: "💾 Zapisz zmiany",
        set_name_ph: "Wprowadź nazwę...", set_addr_ph: "Zacznij wpisywać ulicę...", set_rad_ph: "Na przykład: 5",
        tariff: "Plan",
        loading: "Ładowanie...", empty_team: "W zespole nikogo nie ma.", empty_orders: "Brak zamówień w tym okresie",
        empty_rating: "Brak danych do rankingu.", err_export: "Brak zamówień do eksportu.",
        status_done: "Dostarczono", status_proc: "W trakcie", min: "min", order: "Zamówienie", avg_time_lbl: "Śr. czas:",
        orders_lbl: "zamówień", role_c: "Kurier", role_m: "Menedżer",
        search_load: "Szukamy adresu...", search_empty: "Nic nie znaleziono 😕", search_err: "❌ Błąd wyszukiwania",
        alert_save: "✅ Ustawienia zapisane pomyślnie!", alert_err: "❌ Błąd: ", confirm_reset: "⚠️ JESTEŚ PEWIEN?\n\nWszystkie stare linki natychmiast przestaną działać.",
        alert_reset: "✅ Sukces! Stare linki anulowane.", confirm_del: "Czy na pewno chcesz zwolnić pracownika",
        limit_c: "⚠️ LIMIT WYCZERPANY!\n\nW planie BASIC można dodać max 2 kurierów.",
        limit_m: "⚠️ LIMIT WYCZERPANY!\n\nW planie BASIC można dodać max 1 administratora.",
        err_biz: "Błąd: Nie znaleziono Business ID.", err_tok: "Błąd bezp.: Brak tokenu zaproszenia.",
        success_copy: "✅ Bezpieczny link skopiowany!",
        f1: "Podstawowy panel", f2: "Nielimitowane zamówienia", f3: "Eksport CSV, Wykres i Ranking",
        f4: "Interaktywna mapa cieplna", f5: "Statystyki czasu i Spóźnień", f6: "Nielimitowany personel",
        pr_month: "/ m-c", pr_b1: "Do 2 kurierów w zespole", pr_b2: "Podstawowe statystyki", pr_p1_b: "Nielimitowana", pr_p1: "liczba kurierów", pr_p2: "Mapa cieplna i głęboka analityka", pr_p3: "Zarządzanie menedżerami",
        btn_pay: "Zapłać", btn_manage: "Zarządzaj subskrypcją (Whop)", btn_upgrade_now: "Zaktualizuj do PRO", btn_unlock: "Odblokuj",
        txt_left: "Pozostało", txt_days: "dni", txt_avail: "Dostępne do", txt_next_pay: "Następna płatność:", txt_max: "Wszystkie funkcje odblokowane. Dziękujemy!", txt_expired: "Twój okres próbny lub subskrypcja wygasła. Wybierz plan.",
        title_sel_plan: "Wybierz plan do przedłużenia:", title_upsell: "⚡️ Chcesz więcej funkcji?", title_sel_unlock: "Wybierz plan, aby odblokować:",
        badge_active: "AKTYWNIE", badge_paid: "OPŁACONE", badge_block: "ZABLOKOWANE",
        state_basic: "PLAN BASIC", state_pro: "PLAN PRO", state_closed: "DOSTĘP ZAMKNIĘTY",
        toast_title: "Uwaga!", toast_desc: "Twój plan wkrótce wygaśnie. Odnów subskrypcję, aby nie stracić dostępu.",
        support_title: "Wsparcie techniczne", support_desc: "Masz pytania lub znalazłeś błąd?",
        ticket_create_title: "Utwórz zgłoszenie", ticket_create_desc: "Otrzymamy Twoją wiadomość na Telegramie i szybko odpowiemy.",
        ticket_label_reason: "Powód zgłoszenia", ticket_btn_bug: "Błąd techniczny", ticket_btn_setup: "Konfiguracja", ticket_btn_pay: "Płatność/Plan", ticket_btn_other: "Inne",
        ticket_label_topic: "Temat zgłoszenia", ticket_ph_topic: "Krótko o problemie...",
        ticket_label_msg: "Opis problemu", ticket_ph_msg: "Opisz sytuację jak najdokładniej...",
        ticket_btn_send: "Wyślij zgłoszenie", ticket_btn_sending: "Wysyłanie...",
        ticket_alert_empty: "📍 Proszę wypełnić temat i opis problemu.",
        ticket_alert_test: "Zgłoszenie wysłane (Tryb testowy)!"
    },
        api_token_lbl: "Token API (Klucz dostępu)",
        tg_group_id_lbl: "ID grupy Telegram kurierów",
        integrations_lbl: "Integracje (POS)",
        metric_activity: "Aktywność zamówień",
        feature_deep: "Głęboka analityka i eksport",
        feature_schedule: "Harmonogram i wypłaty",
        salary_fund: "Łączny fundusz płac",
        mode_uber_desc: "Zamówienia trafiają do grupy, kto pierwszy kliknie — ten dostarcza.",
        mode_disp_desc: "Zamówienia trafiają do admina, który przypisuje kurierów przez mapę.",
        nav_salary: "Wypłaty",
        salary_team_title: "Wypłaty zespołu",
        integrations_cat: "Katalog integracji",
        btn_manage_sub: "Zarządzaj subskrypcją",
        not_connected: "Nie połączono",
        priority_low: "Niska",
        priority_high: "Wysoka",
        select_plan_lbl: "Wybierz plan:",
        delivery_mode_lbl: "Tryb pracy dostawy",
        btn_connect: "Połącz",
        pos_connect_hint: "Połącz swoją kasę, aby zautomatyzować dostawę.",
        mode_dispatcher: "👑 Dyspozytor (Klasyczny)",
        mode_uber: "🛵 Wolna kasa (styl Glovo)",
    'en': {
        
        mode_d_title: "Admin assigns orders",
        mode_u_title: "Couriers take orders themselves",
        mode_d_desc: "Each order is personally assigned to a courier via the map",
        mode_u_desc: "Order drops in the group — whoever taps first delivers it",
        mode_classic: "Classic", mode_glovo: "Glovo-style",
        mode_dispatcher_short: "Dispatcher", mode_uber_short: "Free market",
        flow_order: "Order", flow_admin: "Admin", flow_assign: "Assigns",
        flow_courier: "Courier", flow_group: "Group", flow_first: "First takes",
        d_bullet_1: "Full control — you decide who delivers what",
        d_bullet_2: "Perfect for small teams and POS integrations",
        d_bullet_3: "Courier only gets orders from admin",
        u_bullet_1: "Automatic — admin saves time on routing",
        u_bullet_2: "Requires a separate Telegram group for couriers",
        u_bullet_3: "Perfect for high order volume",
        delivery_mode_label: "Delivery mode",
        tab_today: "Today", tab_week: "Week", tab_month: "Month",
        metric_total: "Total Revenue", metric_heat: "Heatmap", metric_avg: "Avg Check",
        metric_chart: "Revenue Dynamics", metric_cash: "Cash", metric_term: "Terminal",
        metric_orders: "Orders", metric_time: "Avg Time", metric_late: "Late (> 45 min)",
        title_recent: "Recent Orders", title_rating: "Courier Ranking (PRO)", 
        title_invite: "Invite Staff", btn_add_c: "Add Courier", btn_add_m: "Add Manager", 
        btn_reset: "Reset Links (Revoke old)", title_active: "Active Staff",
        title_profile: "Business Profile", lbl_biz_id: "Business ID", lbl_edit: "Edit Profile",
        lbl_sub: "Subscription", lbl_cur: "System Currency", btn_logout: "Logout",
        nav_home: "Home", nav_team: "Team", nav_settings: "Settings",
        modal_plan: "Manage Subscription", plan_active: "ACTIVE", plan_paid: "Paid until:",
        set_title: "Business Settings", set_name: "Business Name", set_addr: "Business Address (Base)",
        set_rad: "Delivery Radius (km)", set_cur: "Currency", btn_save: "💾 Save Changes",
        set_name_ph: "Enter name...", set_addr_ph: "Start typing street...", set_rad_ph: "Example: 5",
        tariff: "Plan",
        loading: "Loading...", empty_team: "No team members yet.", empty_orders: "No orders found for this period",
        empty_rating: "No data for ranking.", err_export: "No orders to export.",
        status_done: "Delivered", status_proc: "In Progress", min: "min", order: "Order", avg_time_lbl: "Avg time:",
        orders_lbl: "orders", role_c: "Courier", role_m: "Manager",
        search_load: "Searching address...", search_empty: "Nothing found 😕", search_err: "❌ Search error",
        alert_save: "✅ Settings saved successfully!", alert_err: "❌ Error: ", confirm_reset: "⚠️ ARE YOU SURE?\n\nAll old links will stop working immediately.",
        alert_reset: "✅ Success! Old links revoked.", confirm_del: "Are you sure you want to dismiss",
        limit_c: "⚠️ LIMIT REACHED!\n\nIn BASIC plan you can add max 2 couriers.",
        limit_m: "⚠️ LIMIT REACHED!\n\nIn BASIC plan you can add max 1 administrator.",
        err_biz: "Error: Business ID not found.", err_tok: "Security Error: Invite token missing.",
        success_copy: "✅ Secure link copied!",
        f1: "Basic dashboard", f2: "Unlimited orders", f3: "CSV Export, Chart & Ranking",
        f4: "Interactive Heatmap", f5: "Time & Late Statistics", f6: "Unlimited staff",
        pr_month: "/ mo", pr_b1: "Up to 2 couriers", pr_b2: "Basic statistics", pr_p1_b: "Unlimited", pr_p1: "couriers", pr_p2: "Heatmap & deep analytics", pr_p3: "Manager roles",
        btn_pay: "Pay", btn_manage: "Manage Subscription (Whop)", btn_upgrade_now: "Upgrade to PRO", btn_unlock: "Unlock",
        txt_left: "Left", txt_days: "days", txt_avail: "Available until", txt_next_pay: "Next billing date:", txt_max: "All premium features unlocked. Thank you!", txt_expired: "Your trial or subscription has ended. Choose a plan.",
        title_sel_plan: "Select plan to continue:", title_upsell: "⚡️ Want more features?", title_sel_unlock: "Select plan to unlock:",
        badge_active: "ACTIVE", badge_paid: "PAID", badge_block: "BLOCKED",
        state_basic: "BASIC PLAN", state_pro: "PRO PLAN", state_closed: "ACCESS DENIED",
        toast_title: "Warning!", toast_desc: "Your plan is expiring very soon. Please renew to avoid losing access.",
        support_title: "Support Service", support_desc: "Have questions or found a bug?",
        ticket_create_title: "Create Ticket", ticket_create_desc: "We will receive your message on Telegram and reply quickly.",
        ticket_label_reason: "Reason", ticket_btn_bug: "Technical Bug", ticket_btn_setup: "Setup Help", ticket_btn_pay: "Payment/Plan", ticket_btn_other: "Other",
        ticket_label_topic: "Ticket Topic", ticket_ph_topic: "Briefly about the problem...",
        ticket_label_msg: "Problem Description", ticket_ph_msg: "Describe the situation in detail...",
        ticket_btn_send: "Send Ticket", ticket_btn_sending: "Sending...",
        ticket_alert_empty: "📍 Please fill in the topic and description.",
        api_token_lbl: "API Token (Access Key)",
        tg_group_id_lbl: "Telegram Courier Group ID",
        integrations_lbl: "Integrations (POS)",
        metric_activity: "Order Activity",
        feature_deep: "Deep analytics & export",
        feature_schedule: "Schedule & Salaries",
        salary_fund: "Total Payroll Fund",
        mode_uber_desc: "Orders go to a group, whoever clicks first delivers.",
        mode_disp_desc: "Orders go to admin who assigns couriers via the map.",
        nav_salary: "Salaries",
        salary_team_title: "Team Salaries",
        integrations_cat: "Integration Catalog",
        btn_manage_sub: "Manage Subscription",
        not_connected: "Not connected",
        priority_low: "Low",
        priority_high: "High",
        select_plan_lbl: "Select plan:",
        delivery_mode_lbl: "Delivery Mode",
        btn_connect: "Connect",
        pos_connect_hint: "Connect your POS to automate delivery.",
        mode_dispatcher: "👑 Dispatcher (Classic)",
        mode_uber: "🛵 Free queue (Glovo-style)",
        ticket_alert_test: "Ticket sent (Test mode)!"
    }
};

// Функція-помічник, яка дістає переклад по ключу
function t(key) { return i18n[currentLang][key] || key; }

// Функція зміни мови користувачем
function setLanguage(lang) {
    currentLang = lang;
    localStorage.setItem('appLang', lang); 
    document.getElementById('current-lang-display').innerText = lang.toUpperCase();
    
    // Замінюємо текст, зберігаючи іконки (FontAwesome) всередині елементів
    document.querySelectorAll('[data-i18n]').forEach(el => {
        var key = el.getAttribute('data-i18n');
        var translation = i18n[lang][key];
        if (translation) {
            // Шукаємо, чи є всередині елемента іконка <i>
            const icon = el.querySelector('i');
            if (icon) {
                // Якщо є іконка, зберігаємо її і додаємо перекладений текст поруч
                el.innerHTML = ''; // Очищаємо вміст
                el.appendChild(icon); // Повертаємо іконку на місце
                el.appendChild(document.createTextNode(' ' + translation)); 
            } else {
                // Якщо іконки немає, просто міняємо текст
                el.textContent = translation;
            }
        }
    });

    // Замінюємо placeholder в інпутах
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        var key = el.getAttribute('data-i18n-placeholder');
        if (i18n[lang][key]) el.placeholder = i18n[lang][key];
    });

    // Оновлюємо бейдж плану та перезавантажуємо дані (якщо функція loadDashboard вже завантажена)
    if (typeof bizId !== 'undefined' && bizId) {
        document.getElementById('set-plan-status').innerText = `${currentPlanIsPro ? 'PRO' : 'BASIC'} (${t('badge_active')})`;
        // ✅ FIX: викликати loadDashboard тільки після першого завантаження (не під час init)
        if(typeof loadDashboard === 'function' && window._dashboardReady) { loadDashboard(); } 
    }
}

// Показуємо/ховаємо меню вибору мови
function toggleLangMenu() { document.getElementById('lang-menu').classList.toggle('show'); }
document.addEventListener('click', (e) => { 
    if (!e.target.closest('.lang-switcher') && !e.target.closest('.lang-menu')) {
        var menu = document.getElementById('lang-menu');
        if(menu) menu.classList.remove('show');
    }
});
