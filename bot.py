#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ежедневная сводка в Telegram: почасовая погода в Тбилиси + главные новости.
ყოველდღიური შეჯამება Telegram-ში: თბილისის საათობრივი ამინდი + მთავარი ამბები.

Зависимостей нет — только стандартная библиотека Python 3.9+.

Использование:
    python bot.py                # собрать и отправить
    python bot.py --lang ka      # то же, но на грузинском
    python bot.py --dry-run      # только напечатать сообщение, не отправлять
    python bot.py --whoami       # показать chat_id тех, кто писал боту

Переменные окружения:
    TELEGRAM_BOT_TOKEN   токен от @BotFather            (обязательно)
    TELEGRAM_CHAT_ID     ваш chat_id                    (обязательно, кроме --dry-run/--whoami)
    DIGEST_LANG          ru или ka, по умолчанию ru     (можно заменить флагом --lang)
"""

import html
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

try:
    from football import football_block
except ImportError:          # файла нет — просто не будет блока с футболом
    football_block = None

# ============================================================================
# НАСТРОЙКИ — правьте здесь
# ============================================================================

LAT, LON = 41.6938, 44.8015          # Тбилиси
TIMEZONE = "Asia/Tbilisi"

# --- почасовой прогноз ---
HOURLY_FROM = 7        # с какого часа показывать
HOURLY_TO = 23         # по какой час включительно
HOURLY_STEP = 1        # шаг: 1 = каждый час, 2 = через час, 3 = раз в три часа
HOURLY_SKIP_PAST = True  # не показывать часы, которые уже прошли
HOURLY_SHOW_WIND = False  # добавить колонку с ветром

# --- когда рассылать ---
# Час по Тбилиси, начиная с которого сводка за сегодня считается «пора».
# Проверка идёт при каждом опросе подписчиков, поэтому даже если GitHub
# проглотит запуск по расписанию, сводка уйдёт на ближайшем следующем.
SEND_HOUR = 8

# --- подписчики ---
SUBSCRIBERS_FILE = "subscribers.json"  # список хранится прямо в репозитории
TOMBSTONE_DAYS = 60    # сколько помнить отписавшихся, чтобы их не воскресило слияние
STORE_NAMES = False    # писать ли имена подписчиков в файл (см. раздел «Приватность» в README)
SEND_PAUSE = 0.05      # пауза между отправками, чтобы не упереться в лимит Telegram
BROADCAST_BUDGET_SEC = 420   # общий бюджет на рассылку
MAX_RETRY_WAIT = 60          # сколько максимум ждать, если Telegram просит паузу

# --- новости ---
NEWS_MAX_AGE_HOURS = 30   # насколько старые заголовки ещё считаем свежими
HTTP_TIMEOUT = 12         # на одну ленту
FETCH_RETRIES = 1         # повторов при сбое; лент много, ждать каждую долго нельзя
NEWS_BUDGET_SEC = 100     # общий бюджет на сбор новостей за один язык

# Зависшая лента раньше стоила 3 попытки по 20 секунд, а лент до четырнадцати.
# На медленном дне это выходило за таймаут всего запуска, и рассылка обрывалась
# на середине списка. Теперь сбор новостей укладывается в бюджет: что не успело
# — пропускается, сводка всё равно уходит.

# Сколько пунктов в каждом блоке. Поставьте 0, чтобы выключить блок целиком.
COUNTS = {
    "world": 5,
    "georgia": 2,
    "finance": 2,
    "tech": 2,
}

# RSS-ленты, отдельно для каждого языка.
# Нерабочая лента просто пропускается — можно смело добавлять свои.
FEEDS = {
    "ru": {
        "world": [
            "https://feeds.bbci.co.uk/russian/rss.xml",
            "https://rss.dw.com/xml/rss-ru-all",
            "https://meduza.io/rss/news",
            "https://ru.euronews.com/rss?level=theme&name=news",
        ],
        "georgia": [
            "https://jam-news.net/ru/feed/",
            "https://civil.ge/ru/feed/",
            "https://civil.ge/feed/",          # английская, если русской нет
            "https://oc-media.org/feed/",      # английская
        ],
        "finance": [
            "https://ru.investing.com/rss/market_overview.rss",
            "https://ru.investing.com/rss/news_25.rss",
            "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
        ],
        "tech": [
            "https://habr.com/ru/rss/news/?fl=ru",
            "https://3dnews.ru/news/rss/",
            "https://www.ixbt.com/export/news.rss",
        ],
    },
    "ka": {
        # Грузинские издания не делят ленты на «мир» и «Грузию», поэтому здесь
        # первый блок — просто главные новости дня, второй — грузинская повестка.
        "world": [
            "https://www.radiotavisupleba.ge/api/zivpol-vomx-tpemqyi",
            "https://publika.ge/feed/",
            "https://on.ge/rss",
        ],
        "georgia": [
            "https://netgazeti.ge/feed/",
            "https://civil.ge/ka/feed/",
            "https://netgazeti.ge/category/south_caucasus/feed/",
        ],
        "finance": [
            "https://netgazeti.ge/category/business/feed/",
            "https://www.radiotavisupleba.ge/api/zyvp_l-vomx-tpetqyy",
        ],
        # Ленты новостей о технологиях на грузинском языке практически не существует,
        # поэтому этот блок — англоязычный. См. README.
        "tech": [
            "https://techcrunch.com/feed/",
            "https://www.theverge.com/rss/index.xml",
            "https://feeds.arstechnica.com/arstechnica/index",
        ],
    },
}

RATE_CODES = ("USD", "EUR", "RUB")

# ============================================================================
# Локализация
# ============================================================================

STRINGS = {
    "ru": {
        # В грузинском письме нет заглавных букв: str.upper() превращает мхедрули
        # в мтаврули (Ხანმოკლე), что выглядит как ошибка. Отсюда этот флаг.
        "capitalize": True,
        "city": "Тбилиси",
        "weekdays": ["понедельник", "вторник", "среда", "четверг",
                     "пятница", "суббота", "воскресенье"],
        "months": ["января", "февраля", "марта", "апреля", "мая", "июня",
                   "июля", "августа", "сентября", "октября", "ноября", "декабря"],
        "day_night": "Днём {tmax}, ночью {tmin}",
        "feels": " (ощущается как {feels})",
        "precip": "☔️ Вероятность осадков {p}%",
        "precip_mm": ", до {mm} мм",
        "wind": "ветер до {w} км/ч",
        "uv": "УФ {uv} ({label})",
        "uv_labels": ["низкий", "умеренный", "высокий", "очень высокий", "экстремальный"],
        "hourly_header": "🕒 По часам",
        "col_wind": "ветер",
        "rates": "💱 Лари (НБГ):",
        "per_qty": "за {qty}",
        "weather_unavailable": "Прогноз сейчас недоступен.",
        # ответы бота на команды
        "hello": ("Готово, вы подписаны 👋\n\nСводка приходит каждый день "
                  "в 08:00 по Тбилиси: погода в Тбилиси по часам и главные новости.\n\n"
                  "Язык — кнопками ниже. /stop — отписаться."),
        "already": "Вы уже подписаны. Сводка придёт завтра в 08:00.",
        "choose_lang": "Выберите язык сводки:",
        "bye": "Отписал. Чтобы вернуться — /start",
        "not_subscribed": "Вы и не были подписаны. /start — подписаться.",
        "lang_set": "Язык переключён на русский.",
        "resubscribed": "Заодно вернул вас в рассылку — вы были отписаны. /stop, если это лишнее.",
        "lang_usage": "Укажите язык: /lang ru или /lang ka",
        "help": ("Я присылаю утреннюю сводку по Тбилиси: погода по часам и главные новости.\n\n"
                 "/start — подписаться\n"
                 "/stop — отписаться\n"
                 "/lang ru | ka — язык сводки"),
        "sections": {
            "world": "📰 Главное в мире",
            "georgia": "🇬🇪 Грузия",
            "finance": "💹 Рынки и финансы",
            "tech": "💻 Технологии и AI",
        },
        "wmo": {
            0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность",
            3: "пасмурно", 45: "туман", 48: "изморозь",
            51: "слабая морось", 53: "морось", 55: "сильная морось",
            56: "ледяная морось", 57: "сильная ледяная морось",
            61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
            66: "ледяной дождь", 67: "сильный ледяной дождь",
            71: "небольшой снег", 73: "снег", 75: "сильный снег",
            77: "снежная крупа", 80: "кратковременный дождь", 81: "ливень",
            82: "сильный ливень", 85: "снегопад", 86: "сильный снегопад",
            95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом",
        },
        "wmo_default": "погода",
    },
    "ka": {
        "capitalize": False,
        "city": "თბილისი",
        "weekdays": ["ორშაბათი", "სამშაბათი", "ოთხშაბათი", "ხუთშაბათი",
                     "პარასკევი", "შაბათი", "კვირა"],
        "months": ["იანვარი", "თებერვალი", "მარტი", "აპრილი", "მაისი", "ივნისი",
                   "ივლისი", "აგვისტო", "სექტემბერი", "ოქტომბერი", "ნოემბერი", "დეკემბერი"],
        "day_night": "დღისით {tmax}, ღამით {tmin}",
        "feels": " (იგრძნობა როგორც {feels})",
        "precip": "☔️ ნალექის ალბათობა {p}%",
        "precip_mm": ", {mm} მმ-მდე",
        "wind": "ქარი {w} კმ/სთ-მდე",
        "uv": "UV {uv} ({label})",
        "uv_labels": ["დაბალი", "ზომიერი", "მაღალი", "ძალიან მაღალი", "ექსტრემალური"],
        "hourly_header": "🕒 საათობრივად",
        "col_wind": "ქარი",
        "rates": "💱 ლარი (ეროვნული ბანკი):",
        "per_qty": "{qty}-ზე",
        "weather_unavailable": "პროგნოზი ამჟამად მიუწვდომელია.",
        # ბოტის პასუხები ბრძანებებზე
        "hello": ("მზადაა, თქვენ გამოწერილი ხართ 👋\n\nშეჯამება მოდის ყოველდღე "
                  "08:00-ზე თბილისის დროით: თბილისის საათობრივი ამინდი და მთავარი ამბები.\n\n"
                  "ენა — ქვემოთ ღილაკებით. /stop — გამოწერის გაუქმება."),
        "already": "თქვენ უკვე გამოწერილი ხართ. შეჯამება ხვალ 08:00-ზე მოვა.",
        "choose_lang": "აირჩიეთ შეჯამების ენა:",
        "bye": "გამოწერა გაუქმებულია. დასაბრუნებლად — /start",
        "not_subscribed": "თქვენ გამოწერილი არ იყავით. /start — გამოსაწერად.",
        "lang_set": "ენა შეიცვალა ქართულზე.",
        "resubscribed": "ასევე დაგაბრუნეთ გამოწერაში — გამოწერილი აღარ იყავით. /stop, თუ ეს ზედმეტია.",
        "lang_usage": "მიუთითეთ ენა: /lang ru ან /lang ka",
        "help": ("გიგზავნით დილის შეჯამებას თბილისზე: საათობრივი ამინდი და მთავარი ამბები.\n\n"
                 "/start — გამოწერა\n"
                 "/stop — გამოწერის გაუქმება\n"
                 "/lang ru | ka — შეჯამების ენა"),
        "sections": {
            "world": "📰 დღის მთავარი ამბები",
            "georgia": "🇬🇪 საქართველო",
            "finance": "💹 ბაზრები და ფინანსები",
            "tech": "💻 ტექნოლოგიები და AI",
        },
        "wmo": {
            0: "მოწმენდილი", 1: "ძირითადად მოწმენდილი", 2: "ცვალებადი ღრუბლიანობა",
            3: "მოღრუბლული", 45: "ნისლი", 48: "მოყინული ნისლი",
            51: "სუსტი წვრილი წვიმა", 53: "წვრილი წვიმა", 55: "ძლიერი წვრილი წვიმა",
            56: "მოყინული წვრილი წვიმა", 57: "ძლიერი მოყინული წვრილი წვიმა",
            61: "სუსტი წვიმა", 63: "წვიმა", 65: "ძლიერი წვიმა",
            66: "მოყინული წვიმა", 67: "ძლიერი მოყინული წვიმა",
            71: "სუსტი თოვლი", 73: "თოვლი", 75: "ძლიერი თოვლი",
            77: "თოვლის მარცვლები", 80: "ხანმოკლე წვიმა", 81: "კოკისპირული წვიმა",
            82: "ძლიერი კოკისპირული წვიმა", 85: "თოვა", 86: "ძლიერი თოვა",
            95: "ჭექა-ქუხილი", 96: "ჭექა-ქუხილი სეტყვით", 99: "ძლიერი ჭექა-ქუხილი სეტყვით",
        },
        "wmo_default": "ამინდი",
    },
}

# Эмодзи по кодам WMO — общие для обоих языков.
WMO_EMOJI = {
    0: "☀️", 1: "🌤", 2: "⛅️", 3: "☁️", 45: "🌫", 48: "🌫",
    51: "🌦", 53: "🌦", 55: "🌧", 56: "🌧", 57: "🌧",
    61: "🌦", 63: "🌧", 65: "🌧", 66: "🌧", 67: "🌧",
    71: "🌨", 73: "🌨", 75: "❄️", 77: "🌨",
    80: "🌦", 81: "🌧", 82: "⛈", 85: "🌨", 86: "❄️",
    95: "⛈", 96: "⛈", 99: "⛈",
}

# Язык для тех, кто не выбирал сам и чей Telegram не говорит на русском.
DEFAULT_LANG = "ka"
USER_AGENT = "Mozilla/5.0 (compatible; TbilisiDailyBot/1.0; +https://github.com/)"

# Подписи кнопок — на своих же языках, переводить нечего.
LANG_BUTTONS = [("ka", "🇬🇪 ქართული"), ("ru", "🇷🇺 Русский")]


def pick_lang(argv=()):
    lang = os.environ.get("DIGEST_LANG", DEFAULT_LANG).strip().lower()
    if "--lang" in argv:
        i = list(argv).index("--lang")
        if i + 1 < len(argv):
            lang = argv[i + 1].strip().lower()
    if lang not in STRINGS:
        log(f"[!] неизвестный язык «{lang}», беру {DEFAULT_LANG}")
        lang = DEFAULT_LANG
    return lang


# ============================================================================
# Вспомогательное
# ============================================================================


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def fetch(url, timeout=HTTP_TIMEOUT, retries=FETCH_RETRIES, headers=None):
    """GET с ретраями. Возвращает bytes или бросает исключение."""
    last = None
    ctx = ssl.create_default_context()
    base = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "ka,ru,en;q=0.8",
    }
    base.update(headers or {})
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=base)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries:
                continue
    raise last


def fetch_json(url, **kw):
    return json.loads(fetch(url, **kw).decode("utf-8", "replace"))


def temp(value):
    """+31° / -3° / 0°"""
    v = int(round(value))
    sign = "+" if v > 0 else ""
    return f"{sign}{v}°"


def esc(text):
    return html.escape(text, quote=False)


# ============================================================================
# Погода
# ============================================================================


def get_weather():
    params = {
        "latitude": LAT,
        "longitude": LON,
        "timezone": TIMEZONE,
        "forecast_days": 1,
        "daily": ",".join([
            "weather_code", "temperature_2m_max", "temperature_2m_min",
            "apparent_temperature_max", "precipitation_sum",
            "precipitation_probability_max", "wind_speed_10m_max",
            "uv_index_max", "sunrise", "sunset",
        ]),
        "hourly": ",".join([
            "temperature_2m", "weather_code",
            "precipitation_probability", "wind_speed_10m",
        ]),
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    return fetch_json(url)


def uv_label(uv, S):
    if uv is None:
        return None
    labels = S["uv_labels"]
    if uv < 3:
        return labels[0]
    if uv < 6:
        return labels[1]
    if uv < 8:
        return labels[2]
    if uv < 11:
        return labels[3]
    return labels[4]


def cap(text, S):
    """Заглавная первая буква — только там, где алфавит это поддерживает."""
    if not text or not S.get("capitalize"):
        return text
    return text[0].upper() + text[1:]


def describe(code, S):
    return WMO_EMOJI.get(code, "🌡"), S["wmo"].get(code, S["wmo_default"])


def format_hourly(data, now, S):
    """Таблица по часам. Возвращает готовый <pre>-блок или None."""
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return None

    temps = hourly.get("temperature_2m") or []
    codes = hourly.get("weather_code") or []
    probs = hourly.get("precipitation_probability") or []
    winds = hourly.get("wind_speed_10m") or []

    rows = []
    for i, t in enumerate(times):
        hour = int(t[11:13])
        if hour < HOURLY_FROM or hour > HOURLY_TO:
            continue
        if (hour - HOURLY_FROM) % max(HOURLY_STEP, 1):
            continue
        if HOURLY_SKIP_PAST and hour < now.hour:
            continue
        if i >= len(temps) or temps[i] is None:
            continue

        cells = [t[11:16], f"{temp(temps[i]):>4}"]
        cells.append(WMO_EMOJI.get(codes[i] if i < len(codes) else None, " "))
        p = probs[i] if i < len(probs) else None
        cells.append(f"{int(p):>3}%" if p is not None and p >= 20 else "    ")
        if HOURLY_SHOW_WIND and i < len(winds) and winds[i] is not None:
            cells.append(f"{int(round(winds[i])):>3}")
        rows.append("  ".join(cells).rstrip())

    if not rows:
        return None

    header = f"<b>{esc(S['hourly_header'])}</b>"
    return header + "\n<pre>" + "\n".join(esc(r) for r in rows) + "</pre>"


def format_weather(data, now, S):
    d = data["daily"]
    emoji, desc = describe(d["weather_code"][0], S)

    date_line = (
        f"{cap(S['weekdays'][now.weekday()], S)}, "
        f"{now.day} {S['months'][now.month - 1]}"
    )

    lines = [f"<b>{emoji} {esc(S['city'])} · {esc(date_line)}</b>", ""]

    tmax, tmin = d["temperature_2m_max"][0], d["temperature_2m_min"][0]
    feels = d["apparent_temperature_max"][0]
    head = S["day_night"].format(tmax=temp(tmax), tmin=temp(tmin))
    if feels is not None and abs(feels - tmax) >= 2:
        head += S["feels"].format(feels=temp(feels))
    lines.append(esc(head))
    lines.append(esc(cap(desc, S)))

    prob = d["precipitation_probability_max"][0]
    total = d["precipitation_sum"][0]
    if prob is not None and prob >= 20:
        rain = S["precip"].format(p=int(prob))
        if total:
            rain += S["precip_mm"].format(mm=f"{total:.1f}")
        lines.append(esc(rain))

    extras = []
    wind = d["wind_speed_10m_max"][0]
    if wind is not None:
        extras.append(S["wind"].format(w=int(round(wind))))
    uv = d["uv_index_max"][0]
    lbl = uv_label(uv, S)
    if lbl:
        extras.append(S["uv"].format(uv=int(round(uv)), label=lbl))
    if extras:
        lines.append("💨 " + esc(cap(" · ".join(extras), S)))

    try:
        lines.append(f"🌅 {d['sunrise'][0][11:16]}  ·  🌇 {d['sunset'][0][11:16]}")
    except (IndexError, TypeError):
        pass

    table = format_hourly(data, now, S)
    if table:
        lines.append("")
        lines.append(table)

    return "\n".join(lines)


# ============================================================================
# Курсы валют (Национальный банк Грузии)
# ============================================================================


def get_rates():
    # Эндпоинт отдаёт сразу все валюты; фильтр в параметрах он не принимает (422).
    url = "https://nbg.gov.ge/gw/api/ct/monetarypolicy/currencies/en/json/"
    data = fetch_json(url)
    out = {}
    for block in data:
        for c in block.get("currencies", []):
            if c.get("code") in RATE_CODES:
                out[c["code"]] = (c["rate"], c.get("diff", 0), c.get("quantity", 1))
    return out


def format_rates(rates, S):
    if not rates:
        return None
    parts = []
    for code, sym in (("USD", "$"), ("EUR", "€"), ("RUB", "₽")):
        if code not in rates:
            continue
        rate, diff, qty = rates[code]
        per = " " + S["per_qty"].format(qty=qty) if qty and qty != 1 else ""
        arrow = "▲" if (diff or 0) > 0 else ("▼" if (diff or 0) < 0 else "=")
        parts.append(f"{sym}{per} {rate:.4f} {arrow}")
    if not parts:
        return None
    return f"<b>{esc(S['rates'])}</b> " + "  ·  ".join(esc(p) for p in parts)


# ============================================================================
# Новости
# ============================================================================

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean_title(raw):
    if not raw:
        return ""
    # Сначала убираем реальную разметку, только потом раскрываем сущности —
    # иначе экранированный текст вроде &lt;test&gt; будет принят за тег и вырезан.
    t = TAG_RE.sub("", raw)
    t = html.unescape(t)
    t = WS_RE.sub(" ", t).strip()
    return t


def parse_feed(raw_bytes, source_hint=""):
    """Разбирает RSS 2.0 или Atom. Возвращает список dict(title, link, dt, source)."""
    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError:
        text = raw_bytes.decode("utf-8", "replace")
        start = text.find("<")
        root = ET.fromstring(text[start:])

    ns_atom = "{http://www.w3.org/2005/Atom}"
    ch = root.find("channel")
    if ch is not None:
        source = clean_title(ch.findtext("title") or "")
    else:
        source = clean_title(root.findtext(ns_atom + "title") or "")
    source = source or source_hint

    items = []
    nodes = root.findall(".//item") or root.findall(f".//{ns_atom}entry")
    for node in nodes:
        title = clean_title(node.findtext("title") or node.findtext(ns_atom + "title") or "")
        link = (node.findtext("link") or "").strip()
        if not link:
            for ln in node.findall(ns_atom + "link"):
                if ln.get("rel", "alternate") == "alternate" and ln.get("href"):
                    link = ln.get("href").strip()
                    break
        date_raw = (
            node.findtext("pubDate")
            or node.findtext("{http://purl.org/dc/elements/1.1/}date")
            or node.findtext(ns_atom + "published")
            or node.findtext(ns_atom + "updated")
            or ""
        ).strip()
        dt = None
        if date_raw:
            try:
                dt = parsedate_to_datetime(date_raw)
            except (TypeError, ValueError):
                try:
                    dt = datetime.fromisoformat(date_raw.replace("Z", "+00:00"))
                except ValueError:
                    dt = None
            if dt is not None and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        if title:
            items.append({"title": title, "link": link, "dt": dt, "source": source})
    return items


def norm_key(title):
    return WS_RE.sub(" ", re.sub(r"[^\w\s]", "", title.lower())).strip()[:70]


def collect_news(section, limit, seen, lang, deadline=None):
    if limit <= 0:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=NEWS_MAX_AGE_HOURS)
    pools, stale_pools = [], []
    for url in FEEDS.get(lang, {}).get(section, []):
        if deadline and time.monotonic() > deadline:
            log(f"  [~] бюджет на новости исчерпан, пропускаю остаток раздела «{section}»")
            break
        try:
            items = parse_feed(fetch(url), source_hint=urllib.parse.urlparse(url).netloc)
        except Exception as exc:  # noqa: BLE001
            log(f"  [!] лента недоступна: {url} ({exc.__class__.__name__})")
            continue
        fresh = [i for i in items if i["dt"] is None or i["dt"] >= cutoff]
        log(f"  [+] {url}: {len(fresh)} свежих из {len(items)}")
        if fresh:
            pools.append(fresh)
        elif items:
            stale_pools.append(items[:limit])

    # К залежавшимся заголовкам обращаемся, только если свежих нет вообще нигде —
    # иначе одна подвисшая лента протолкнёт вчерашние новости вперёд сегодняшних.
    if not pools:
        if stale_pools:
            log(f"  [!] в разделе «{section}» нет свежих новостей, беру последние доступные")
        pools = stale_pools

    # Берём по очереди из каждой ленты, чтобы не было перекоса в один источник.
    result = []
    idx = 0
    while len(result) < limit and pools:
        progressed = False
        for pool in pools:
            if idx >= len(pool):
                continue
            progressed = True
            item = pool[idx]
            key = norm_key(item["title"])
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(item)
            if len(result) >= limit:
                break
        if not progressed:
            break
        idx += 1
    return result


def format_news(section, items, S):
    if not items:
        return None
    lines = [f"<b>{esc(S['sections'][section])}</b>"]
    for n, it in enumerate(items, 1):
        title = esc(it["title"])
        body = (f'<a href="{html.escape(it["link"], quote=True)}">{title}</a>'
                if it["link"] else title)
        tail = f" <i>— {esc(it['source'])}</i>" if it["source"] else ""
        lines.append(f"{n}. {body}{tail}")
    return "\n".join(lines)


# ============================================================================
# Сборка сообщения
# ============================================================================


TAG_OPEN_RE = re.compile(r"<(/?)(b|i|u|s|a|pre|code)\b[^>]*>")


def close_tags(text):
    """Дописывает закрывающие теги, если обрезка оставила их открытыми.

    Telegram отвергает сообщение с непарным тегом целиком — то есть сводку
    не получил бы никто. Дешевле закрыть, чем потерять.
    """
    stack = []
    for closing, tag in TAG_OPEN_RE.findall(text):
        if closing:
            if tag in stack:
                while stack and stack.pop() != tag:
                    pass
        else:
            stack.append(tag)
    # Незакрытый <a href="..."> без ">" тоже возможен — обрубим хвост.
    cut = text.rfind("<")
    if cut > text.rfind(">"):
        text = text[:cut]
    return text + "".join(f"</{t}>" for t in reversed(stack))


def build_message(lang=DEFAULT_LANG, now=None):
    S = STRINGS[lang]
    now = now or datetime.now(timezone(timedelta(hours=4)))
    blocks = []

    try:
        blocks.append(format_weather(get_weather(), now, S))
    except Exception as exc:  # noqa: BLE001
        log(f"[!] погода не получена: {exc}")
        blocks.append(f"<b>🌡 {esc(S['city'])}</b>\n<i>{esc(S['weather_unavailable'])}</i>")

    rates_line = None
    if COUNTS.get("finance", 0) > 0:
        try:
            rates_line = format_rates(get_rates(), S)
        except Exception as exc:  # noqa: BLE001
            log(f"[!] курсы не получены: {exc}")

    seen = set()
    deadline = time.monotonic() + NEWS_BUDGET_SEC
    for section in ("world", "georgia", "finance", "tech"):
        log(f"Собираю раздел: {section}")
        items = collect_news(section, COUNTS.get(section, 0), seen, lang, deadline)
        block = format_news(section, items, S)
        if section == "finance" and rates_line:
            block = (block + "\n" + rates_line) if block else rates_line
        if block:
            blocks.append(block)

    # Футбол живёт в отдельном файле football.py. Нет файла — нет блока,
    # всё остальное работает как раньше.
    if football_block:
        try:
            block = football_block(lang, now)
            if block:
                blocks.append(block)
        except Exception as exc:  # noqa: BLE001
            log(f"[!] футбол не получен: {exc}")

    # Режем по границам блоков, а не по символам: обрыв внутри <pre> оставил бы
    # незакрытый тег, и Telegram отверг бы сообщение целиком.
    while len(blocks) > 1 and len("\n\n".join(blocks)) > 4000:
        blocks.pop()
    msg = "\n\n".join(blocks)
    if len(msg) > 4000:
        msg = close_tags(msg[:3990].rsplit("\n", 1)[0]) + "\n…"
    return msg


# ============================================================================
# Telegram
# ============================================================================


class TelegramError(RuntimeError):
    def __init__(self, code, description, retry_after=None, migrate_to=None):
        super().__init__(f"Telegram API {code}: {description}")
        self.code = code
        self.description = description or ""
        self.retry_after = retry_after      # сколько Telegram просит подождать
        self.migrate_to = migrate_to        # новый id, если группа стала супергруппой


def tg_api(token, method, payload, retries=2):
    """Запрос к Telegram.

    Любая беда — HTTP-ответ, обрыв связи, таймаут, битый JSON — приходит наружу
    как TelegramError. Это принципиально: рассылка ловит именно его на каждом
    получателе, и раньше обычный обрыв соединения на середине списка выбрасывал
    исключение мимо этой обработки и валил весь прогон.
    """
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(payload).encode()
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                payload_err = json.loads(body)
            except ValueError:
                payload_err = {}
            params = payload_err.get("parameters") or {}
            err = TelegramError(
                exc.code,
                payload_err.get("description", body),
                retry_after=params.get("retry_after"),
                migrate_to=params.get("migrate_to_chat_id"),
            )
            # 429 и пятисотки лечатся повтором. Раньше они летели наружу сразу,
            # и одиночный сбой Telegram на getUpdates ронял весь шаг.
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if retryable and attempt < retries and not err.migrate_to:
                time.sleep(min(_int(err.retry_after, 2 + attempt), MAX_RETRY_WAIT))
                continue
            raise err from exc
        except Exception as exc:  # noqa: BLE001 — обрыв, таймаут, битый ответ
            if attempt < retries:
                time.sleep(1 + attempt)
                continue
            raise TelegramError(0, f"{exc.__class__.__name__}: {exc}") from exc


def send(token, chat_id, text):
    return tg_api(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    })


def send_plain(token, chat_id, text, markup=None):
    """Служебный ответ на команду — без разметки, чтобы ничего не сломалось."""
    payload = {"chat_id": chat_id, "text": text}
    if markup:
        payload["reply_markup"] = markup
    try:
        tg_api(token, "sendMessage", payload)
    except TelegramError as exc:
        log(f"  [!] не ответить {chat_id}: {exc}")


def edit_plain(token, chat_id, message_id, text, markup=None):
    """Переписать уже отправленное сообщение — так галочка переезжает на кнопке."""
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if markup:
        payload["reply_markup"] = markup
    try:
        tg_api(token, "editMessageText", payload)
        return True
    except TelegramError as exc:
        log(f"  [~] не переписать сообщение {message_id}: {exc.description}")
        return False


def answer_callback(token, callback_id, text=""):
    """Убирает «часики» на кнопке. Через час после нажатия Telegram уже не примет —
    это нормально, нажатие всё равно обработано."""
    try:
        tg_api(token, "answerCallbackQuery",
               {"callback_query_id": callback_id, "text": text})
    except TelegramError as exc:
        log(f"  [~] callback просрочен: {exc.description}")


# Ошибки, после которых подписчика нужно вычеркнуть: он заблокировал бота,
# удалил аккаунт или чат перестал существовать.
GONE_MARKERS = (
    "bot was blocked",
    "user is deactivated",
    "chat not found",
    "bot was kicked",
    "peer_id_invalid",
)
# «group chat was upgraded» сюда намеренно не входит: Telegram присылает
# migrate_to_chat_id, и группу надо перевести на новый id, а не вычёркивать.


def is_gone(exc):
    if exc.code not in (400, 403):
        return False
    low = exc.description.lower()
    return any(m in low for m in GONE_MARKERS)


# ============================================================================
# Список подписчиков
# ============================================================================


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def tombstone(value):
    """Приводит надгробие к общему виду: старый формат — просто строка с датой."""
    if isinstance(value, dict):
        return {"at": str(value.get("at", "")), "rev": _int(value.get("rev"))}
    return {"at": str(value), "rev": 0}


def load_state(path=None):
    """Читает состояние и сразу приводит его в порядок.

    Файл лежит в репозитории, его правят руками и может оборвать запись.
    Дешевле один раз нормализовать всё на входе, чем защищаться от мусора
    в каждом месте использования — раньше кривой offset или строка вместо
    записи подписчика роняли весь прогон.
    """
    path = path or SUBSCRIBERS_FILE
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}

    state = {"offset": _int(data.get("offset")), "rev": _int(data.get("rev"))}

    subs = data.get("subscribers")
    state["subscribers"] = {
        str(k): v for k, v in subs.items() if isinstance(v, dict)
    } if isinstance(subs, dict) else {}

    removed = data.get("removed")
    if isinstance(removed, dict):
        state["removed"] = {str(k): tombstone(v) for k, v in removed.items()}

    if data.get("last_digest"):
        state["last_digest"] = str(data["last_digest"])
    return state


def tbilisi_now():
    return datetime.now(timezone(timedelta(hours=4)))


def claim_day(state, now):
    """Помечает сегодняшнюю рассылку как сделанную.

    Возвращает (можно_рассылать, пояснение). Отметка ставится ДО отправки —
    так повторный запуск не начнёт вторую рассылку, даже если первый упал
    на середине.
    """
    today = now.strftime("%Y-%m-%d")
    if str(state.get("last_digest") or "") == today:
        return False, f"сводка за {today} уже отправлена"
    if now.hour < SEND_HOUR:
        return False, f"ещё рано: {now:%H:%M} по Тбилиси, рассылка с {SEND_HOUR}:00"
    state["last_digest"] = today
    return True, today


def _key_order(key):
    """id обычно числовой, но руками в файл может попасть что угодно —
    на этом сортировка раньше падала и бот вставал целиком."""
    try:
        return (0, int(key), "")
    except (TypeError, ValueError):
        return (1, 0, str(key))


def save_state(state, path=None):
    path = path or SUBSCRIBERS_FILE
    if not isinstance(state.get("subscribers"), dict):
        state["subscribers"] = {}
    if not isinstance(state.get("removed"), dict):
        state.pop("removed", None)
    state["subscribers"] = dict(sorted(state["subscribers"].items(),
                                       key=lambda kv: _key_order(kv[0])))
    if state.get("removed"):
        state["removed"] = dict(sorted(state["removed"].items(),
                                       key=lambda kv: _key_order(kv[0])))
    else:
        state.pop("removed", None)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


def merge_state(local, remote):
    """Сливает наш список с тем, что лежит в репозитории.

    Нужно, когда файл успели поменять со стороны: параллельный запуск или
    правка руками. Текстовое слияние тут бессмысленно, а по смыслу всё просто —
    подписчики объединяются, наши настройки приоритетнее, offset только растёт.

    Отдельная история — удаление. Простое объединение его отменяет: ключа,
    которого мы только что лишились, в удалённой версии он ещё есть, и человек
    возвращается в список. Поэтому отписки помечаются надгробиями в removed,
    и они вычитаются из объединения.
    """
    subs = dict(remote.get("subscribers") or {})
    subs.update(local.get("subscribers") or {})
    if not isinstance(remote.get("removed"), dict):
        remote = dict(remote, removed={})
    if not isinstance(local.get("removed"), dict):
        local = dict(local, removed={})

    removed = {str(k): tombstone(v) for k, v in (remote.get("removed") or {}).items()}
    for key, value in (local.get("removed") or {}).items():
        key, value = str(key), tombstone(value)
        if value["rev"] >= removed.get(key, {"rev": -1})["rev"]:
            removed[key] = value

    # Надгробие живёт TOMBSTONE_DAYS: за это время удалённая версия точно
    # обновится, и хранить его дальше незачем.
    cutoff = (tbilisi_now() - timedelta(days=TOMBSTONE_DAYS)).isoformat(timespec="seconds")
    removed = {k: v for k, v in removed.items() if v["at"] >= cutoff}

    # Кто новее — подписка или надгробие? Без этого сравнения человек, нажавший
    # /start после отписки, воскрешал бы надгробие из удалённой версии и не мог
    # подписаться заново все 60 дней, получая при этом бодрое «вы подписаны».
    for key in list(removed):
        entry = subs.get(key)
        entry_rev = _int(entry.get("rev")) if isinstance(entry, dict) else -1
        if entry_rev > removed[key]["rev"]:
            removed.pop(key)          # подписался позже — надгробие устарело
        else:
            subs.pop(key, None)

    merged = {
        "offset": max(_int(local.get("offset")), _int(remote.get("offset"))),
        "rev": max(_int(local.get("rev")), _int(remote.get("rev"))),
        "subscribers": subs,
    }
    if removed:
        merged["removed"] = removed
    # Отметку о последней рассылке берём позднюю: иначе сводка может уйти дважды.
    stamps = [str(d["last_digest"]) for d in (local, remote) if d.get("last_digest")]
    if stamps:
        merged["last_digest"] = max(stamps)
    return merged


def stamp():
    """Человекочитаемая метка времени — нужна только чтобы состарить надгробие."""
    return tbilisi_now().isoformat(timespec="seconds")


def bump(state):
    """Номер очередного изменения состояния.

    По времени подписку и отписку не упорядочить: и то и другое может прийти
    одной пачкой в ту же секунду. Счётчик растёт при каждом изменении и живёт
    в файле, поэтому даёт строгий порядок и внутри прогона, и между прогонами.
    """
    state["rev"] = _int(state.get("rev")) + 1
    return state["rev"]


def forget(state, chat_id):
    """Убирает подписчика и ставит надгробие, чтобы слияние его не вернуло.

    Надгробие ставится только тому, кто действительно был в списке: иначе
    случайный недоступный чат (например, TELEGRAM_CHAT_ID) копил бы мусор
    и потом не мог бы подписаться.
    """
    key = str(chat_id)
    if state["subscribers"].pop(key, None) is None:
        return False
    state.setdefault("removed", {})[key] = {"at": stamp(), "rev": bump(state)}
    return True


def revive(state, chat_id):
    """Снимает надгробие — человек подписался заново."""
    (state.get("removed") or {}).pop(str(chat_id), None)


def guess_lang(update_from):
    """Первый язык подбираем по языку клиента Telegram, дальше человек решает сам."""
    if not isinstance(update_from, dict):
        update_from = {}
    code = str(update_from.get("language_code") or "").lower()
    if code.startswith("ka"):
        return "ka"
    if code.startswith("ru"):
        return "ru"
    return DEFAULT_LANG


def lang_keyboard(current):
    """Кнопки под сообщением. Текущий язык помечен галочкой."""
    row = [{"text": ("✅ " if code == current else "") + title,
            "callback_data": f"lang:{code}"}
           for code, title in LANG_BUTTONS]
    return json.dumps({"inline_keyboard": [row]})


def set_lang(token, state, chat_id, lang, message_id=None, callback_id=None, dedup=None):
    """Ставит язык подписчику. Возвращает True — список изменился."""
    subs = state["subscribers"]
    key = str(chat_id)
    resubscribed = False
    if not isinstance(subs.get(key), dict):
        subs.pop(key, None)
    if key in subs:
        if subs[key].get("lang") == lang:
            changed = False          # тот же язык — нечего сохранять и коммитить
        else:
            subs[key]["lang"] = lang
            changed = True
    else:
        # Нажал кнопку старого сообщения, уже будучи отписанным. Подписываем,
        # но обязательно говорим об этом — иначе человек снова начнёт получать
        # рассылку, которую когда-то отменил, и не поймёт почему.
        subs[key] = {"lang": lang, "added": tbilisi_now().strftime("%Y-%m-%d"),
                     "rev": bump(state)}
        revive(state, chat_id)
        changed = resubscribed = True

    text = STRINGS[lang]["lang_set"]
    if resubscribed:
        text += "\n\n" + STRINGS[lang]["resubscribed"]
    if callback_id:
        answer_callback(token, callback_id, text)
    # Переписываем то же сообщение, чтобы галочка переехала на выбранную кнопку.
    # Правка существующего сообщения чат не засоряет, поэтому её не ограничиваем;
    # а вот новое сообщение шлём не чаще одного за прогон.
    if not (message_id and edit_plain(token, chat_id, message_id, text,
                                      markup=lang_keyboard(lang))):
        if dedup is None or once(dedup, chat_id, "lang_set"):
            send_plain(token, chat_id, text, markup=lang_keyboard(lang))
    log(f"  [~] {chat_id} → язык {lang}")
    return changed


def once(dedup, chat_id, tag):
    """Один ответ каждого вида на чат за прогон.

    Если человек в ожидании реакции нажал кнопку пять раз, все пять нажатий
    приедут одной пачкой — отвечать на каждое значит завалить ему чат.
    """
    key = (str(chat_id), tag)
    if key in dedup:
        return False
    dedup.add(key)
    return True


def handle_command(token, state, chat_id, text, sender, chat_title=None, dedup=None):
    """Обрабатывает одну команду. Возвращает True, если список изменился."""
    if chat_id is None:
        return False
    dedup = dedup if dedup is not None else set()
    subs = state["subscribers"]
    key = str(chat_id)
    known = subs.get(key)
    if known is not None and not isinstance(known, dict):
        # Запись могли испортить руками. Считаем её отсутствующей, а не падаем:
        # иначе одна кривая строка в файле роняла бы опрос в каждом прогоне.
        log(f"  [~] запись {key} испорчена, перезаписываю")
        subs.pop(key, None)
        known = None
    lang = (known or {}).get("lang") or guess_lang(sender)
    S = STRINGS[lang]

    cmd, _, arg = text.strip().partition(" ")
    cmd = cmd.split("@")[0].lower()          # /start@my_bot в группах
    arg = arg.strip().lower()

    if cmd == "/start":
        if known:
            # Тег общий с приветствием: пять /start подряд дают один ответ,
            # а не «подписаны» плюс «вы уже подписаны».
            if once(dedup, chat_id, "start"):
                send_plain(token, chat_id, S["already"], markup=lang_keyboard(lang))
            return False
        entry = {"lang": lang, "added": tbilisi_now().strftime("%Y-%m-%d"),
                 "rev": bump(state)}
        if STORE_NAMES:
            entry["name"] = chat_title or (sender or {}).get("first_name") or ""
        subs[key] = entry
        revive(state, chat_id)
        if once(dedup, chat_id, "hello") and once(dedup, chat_id, "start"):
            send_plain(token, chat_id, S["hello"], markup=lang_keyboard(lang))
        log(f"  [+] подписался {chat_id} ({lang})")
        return True

    if cmd == "/stop":
        if not known:
            if once(dedup, chat_id, "not_subscribed"):
                send_plain(token, chat_id, S["not_subscribed"])
            return False
        forget(state, chat_id)
        if once(dedup, chat_id, "bye"):
            send_plain(token, chat_id, S["bye"])
        log(f"  [-] отписался {chat_id}")
        return True

    if cmd == "/lang":
        # Без аргумента — просто показываем кнопки.
        if arg not in STRINGS:
            if once(dedup, chat_id, "choose_lang"):
                send_plain(token, chat_id, S["choose_lang"], markup=lang_keyboard(lang))
            return False
        return set_lang(token, state, chat_id, arg, dedup=dedup)

    if cmd in ("/help", "/помощь"):
        if once(dedup, chat_id, "help"):
            send_plain(token, chat_id, S["help"])
        return False

    return False


def poll(token, state):
    """Забирает новые апдейты и обновляет список подписчиков."""
    res = tg_api(token, "getUpdates", {
        "offset": state.get("offset", 0),
        "limit": 100,
        "timeout": 0,
        "allowed_updates": json.dumps(["message", "my_chat_member", "callback_query"]),
    })
    updates = res.get("result", [])
    log(f"[i] новых апдейтов: {len(updates)}")

    changed = False
    last_id = None
    dedup = set()
    for upd in updates:
        last_id = upd.get("update_id", last_id)

        # нажатие кнопки выбора языка
        cb = upd.get("callback_query")
        if cb:
            data = cb.get("data")
            if not isinstance(data, str):
                data = ""
            msg = cb.get("message") or {}
            # У старых сообщений Telegram может не отдать chat — берём отправителя.
            chat_id = ((msg.get("chat") or {}).get("id")
                       or (cb.get("from") or {}).get("id"))
            if data.startswith("lang:") and chat_id is not None:
                code = data.split(":", 1)[1]
                if code in STRINGS:
                    if set_lang(token, state, chat_id, code,
                                message_id=msg.get("message_id"),
                                callback_id=cb.get("id"), dedup=dedup):
                        changed = True
                else:
                    answer_callback(token, cb.get("id"))
            else:
                answer_callback(token, cb.get("id"))
            continue

        # пользователь заблокировал бота или выгнал его из группы
        member = upd.get("my_chat_member")
        if member:
            status = (member.get("new_chat_member") or {}).get("status")
            chat = member.get("chat") or {}
            key = str(chat.get("id"))
            if status in ("kicked", "left") and key in state["subscribers"]:
                forget(state, key)
                log(f"  [-] {key} заблокировал бота — вычеркнул")
                changed = True
            continue

        msg = upd.get("message") or {}
        text = msg.get("text")
        if not isinstance(text, str) or not text.startswith("/"):
            continue
        chat = msg.get("chat") or {}
        if handle_command(token, state, chat.get("id"), text,
                          msg.get("from"), chat.get("title"), dedup=dedup):
            changed = True

    if last_id is not None:
        state["offset"] = last_id + 1
        changed = True

    return changed


def confirm_offset(token, state):
    """Сообщает Telegram, что апдейты обработаны, и он их удаляет у себя.

    Ключевой момент: без этого единственная защита от повтора — offset в файле.
    Стоит коммиту не пройти, и следующий запуск заберёт те же апдейты снова
    и ответит на них по второму разу. Именно так чат и завалило дублями.
    Вызывать нужно после успешного сохранения файла.
    """
    offset = state.get("offset", 0)
    if not offset:
        return
    try:
        tg_api(token, "getUpdates", {"offset": offset, "limit": 1, "timeout": 0})
        log(f"[i] Telegram подтвердил обработку до offset {offset}")
    except TelegramError as exc:
        log(f"[!] не подтвердить offset: {exc}")


def broadcast(token, state, extra_chat_id=None, forced_lang=None):
    """Рассылает сводку всем подписчикам.

    Возвращает (доставлено, вычеркнуто, всего получателей). Третье число важно:
    по нему вызывающий код отличает «рассылать было некому» от «никому не дошло».
    """
    # Запись подписчика может оказаться чем угодно, если файл правили руками
    # или обрезали при записи. Приводим к словарю здесь, чтобы одна битая
    # строка не лишила сводки всех остальных.
    targets = {}
    for key, info in state["subscribers"].items():
        targets[str(key)] = info if isinstance(info, dict) else {}
    if extra_chat_id:
        targets.setdefault(str(extra_chat_id), {"lang": forced_lang or DEFAULT_LANG})

    if not targets:
        log("[i] подписчиков нет — рассылать некому")
        return 0, 0, 0

    # Сообщение собирается по разу на язык, а не на каждого получателя.
    langs = {forced_lang} if forced_lang else {
        (info.get("lang") or DEFAULT_LANG) for info in targets.values()
    }
    messages = {}
    for lang in langs:
        lang = lang if lang in STRINGS else DEFAULT_LANG
        if lang not in messages:
            log(f"Собираю сводку на языке: {lang}")
            messages[lang] = build_message(lang=lang)

    sent = dropped = skipped = 0
    deadline = time.monotonic() + BROADCAST_BUDGET_SEC
    for chat_id, info in targets.items():
        if time.monotonic() > deadline:
            # Иначе недоступный Telegram растягивает одного получателя на минуты,
            # и джоб убивают по таймауту где-то посередине списка.
            skipped += 1
            continue
        lang = forced_lang or info.get("lang") or DEFAULT_LANG
        text = messages[lang if lang in messages else DEFAULT_LANG]

        # До трёх попыток на получателя: Telegram сам говорит, сколько ждать
        # при лимите, а сетевые сбои лечатся простым повтором. Перенос группы
        # на новый id попыткой не считается — иначе сообщение могло потеряться.
        attempt = migrations = 0
        while attempt < 3:
            try:
                send(token, chat_id, text)
                sent += 1
                break
            except TelegramError as exc:
                if exc.migrate_to and migrations < 2:
                    # Группа стала супергруппой — переносим подписку на новый id.
                    old = state["subscribers"].pop(str(chat_id), None)
                    if old is not None:
                        state["subscribers"][str(exc.migrate_to)] = old
                    log(f"  [~] {chat_id} → {exc.migrate_to} (группа стала супергруппой)")
                    chat_id = exc.migrate_to
                    migrations += 1
                    continue                      # без attempt += 1
                attempt += 1
                if exc.code == 429 and attempt < 3:
                    wait = min(_int(exc.retry_after, 3) + 1, MAX_RETRY_WAIT)
                    log(f"  [~] лимит Telegram, жду {wait}с")
                    time.sleep(wait)
                    continue
                if exc.code == 0 and attempt < 3:
                    log(f"  [~] {chat_id}: {exc.description}, повторю")
                    time.sleep(2)
                    continue
                if is_gone(exc):
                    if forget(state, chat_id):
                        dropped += 1
                        log(f"  [-] {chat_id} недоступен ({exc.description}) — вычеркнул")
                    else:
                        log(f"  [!] {chat_id} недоступен ({exc.description})")
                else:
                    log(f"  [!] {chat_id}: {exc}")
                break
            except Exception as exc:  # noqa: BLE001 — что угодно неожиданное
                log(f"  [!] {chat_id}: непредвиденная ошибка {exc.__class__.__name__}: {exc}")
                break
        time.sleep(SEND_PAUSE)

    if skipped:
        log(f"  [!] бюджет рассылки исчерпан, {skipped} получателей пропущено")
    return sent, dropped, len(targets)


def whoami(token):
    res = tg_api(token, "getUpdates", {"limit": 20})
    chats = {}
    for upd in res.get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat.get("title") or chat.get("username") or chat.get("first_name")
    if not chats:
        print("Ничего не найдено. Напишите боту любое сообщение в Telegram и запустите снова.")
        return 1
    print("Найденные chat_id:")
    for cid, name in chats.items():
        print(f"  {cid}   {name}")
    return 0


# ============================================================================


def main(argv):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if "--whoami" in argv:
        if not token:
            log("Нужен TELEGRAM_BOT_TOKEN")
            return 2
        try:
            return whoami(token)
        except TelegramError as exc:
            log(f"[!] {exc}")
            return 1

    # Аварийный слив: выбросить всё, что накопилось у Telegram, ничего не отвечая.
    # Нужен, если backlog успел раздуться и на него не хочется реагировать вовсе.
    if "--drain" in argv:
        if not token:
            log("Нужен TELEGRAM_BOT_TOKEN")
            return 2
        try:
            res = tg_api(token, "getUpdates", {"offset": -1, "limit": 1, "timeout": 0})
            items = res.get("result", [])
            if not items:
                log("[ok] очередь и так пуста")
                return 0
            last = items[-1]["update_id"]
            tg_api(token, "getUpdates", {"offset": last + 1, "limit": 1, "timeout": 0})
        except TelegramError as exc:
            log(f"[!] очистить очередь не удалось: {exc}")
            return 1
        state = load_state()
        state["offset"] = last + 1
        save_state(state)
        log(f"[ok] очередь очищена, offset выставлен на {last + 1}")
        return 0

    # Бронирование дня. Отдельный режим нужен, чтобы отметку успел закоммитить
    # шаг сохранения ДО того, как уйдут сообщения. Иначе непрошедший push
    # означал бы, что сводка рассылается заново каждые пять минут до вечера.
    if "--claim-day" in argv:
        now = tbilisi_now()
        state = load_state()
        ok, why = claim_day(state, now)
        if not ok:
            log(f"[i] рассылка не нужна: {why}")
            return 20
        save_state(state)
        log(f"[ok] день {why} забронирован — сводка уйдёт следующим шагом")
        return 0

    # Отправка без проверок: день уже забронирован предыдущим шагом.
    if "--send-now" in argv:
        if not token:
            log("Нужен TELEGRAM_BOT_TOKEN")
            return 2
        state = load_state()
        sent, dropped, total = broadcast(token, state, extra_chat_id=chat_id or None)
        save_state(state)
        if not total:
            log("[ok] получателей нет")
            return 0
        log(f"[ok] доставлено {sent} из {total}, вычеркнуто: {dropped}")
        return 0 if sent == total else 1

    # Рассылка «если пора»: вызывается часто, но срабатывает один раз в сутки.
    # Так сводка переживает пропущенный GitHub-ом запуск по расписанию.
    if "--if-due" in argv:
        if not token:
            log("Нужен TELEGRAM_BOT_TOKEN")
            return 2
        now = tbilisi_now()
        state = load_state()
        ok, why = claim_day(state, now)
        if not ok:
            log(f"[i] рассылка не нужна: {why}")
            return 0

        log(f"[i] сводка за {why} ещё не уходила — рассылаю ({now:%H:%M} по Тбилиси)")
        sent, dropped, total = broadcast(token, state, extra_chat_id=chat_id or None)
        if not total:
            log("[ok] получателей нет")
            state.pop("last_digest", None)     # рассылать было некому, день не тратим
            return 0
        if not sent:
            log(f"[!] доставлено 0 из {total} — отметку снимаю, попробую снова")
            state.pop("last_digest", None)
            save_state(state)
            return 1
        save_state(state)
        log(f"[ok] доставлено {sent} из {total}, вычеркнуто: {dropped}")
        return 0 if sent == total else 1

    # Слияние с версией файла из репозитория (вызывается из workflow перед коммитом).
    if "--merge" in argv:
        i = list(argv).index("--merge")
        if i + 1 >= len(argv):
            log("Укажите файл: --merge путь/к/remote.json")
            return 2
        local, remote = load_state(), load_state(argv[i + 1])
        merged = merge_state(local, remote)
        save_state(merged)
        log(f"[ok] слито: было {len(local['subscribers'])}, "
            f"в репозитории {len(remote['subscribers'])}, "
            f"стало {len(merged['subscribers'])}")
        return 0

    # Режим опроса: собрать команды /start, /stop, /lang и обновить список.
    if "--poll" in argv:
        if not token:
            log("Нужен TELEGRAM_BOT_TOKEN")
            return 2
        state = load_state()
        before = len(state["subscribers"])
        try:
            changed = poll(token, state)
        except TelegramError as exc:
            # Не роняем шаг: иначе в том же прогоне пропустится проверка
            # «не пора ли разослать сводку» — ровно тогда, когда Telegram
            # барахлит. Апдейты никуда не денутся, разберём на следующем запуске.
            log(f"[!] опрос не удался: {exc}")
            return 0
        if changed:
            save_state(state)
        log(f"[ok] подписчиков: {before} → {len(state['subscribers'])}")
        return 0

    # Подтверждение обработки — отдельным шагом, ПОСЛЕ успешного коммита.
    # Подтвердив, мы разрешаем Telegram выбросить апдейты у себя. Пока offset
    # не лёг в репозиторий, этого делать нельзя: раннер уничтожается, и если
    # push не прошёл, подписка исчезнет безвозвратно — а человеку бот уже
    # написал «вы подписаны». Незакоммиченный offset означает лишь то, что
    # следующий прогон разберёт те же апдейты заново.
    if "--confirm" in argv:
        if not token:
            log("Нужен TELEGRAM_BOT_TOKEN")
            return 2
        confirm_offset(token, load_state())
        return 0

    lang = pick_lang(argv)

    if "--dry-run" in argv:
        log(f"[i] язык сводки: {lang}")
        message = build_message(lang=lang)
        print(message)
        log(f"\n[i] длина сообщения: {len(message)} символов")
        return 0

    if not token:
        log("Нужен TELEGRAM_BOT_TOKEN")
        return 2

    # Явно указанный --lang перекрывает язык подписчиков (удобно для проверки).
    forced = lang if "--lang" in argv else None

    state = load_state()
    sent, dropped, total = broadcast(token, state, extra_chat_id=chat_id or None,
                                     forced_lang=forced)
    if sent and not forced:
        # Ручной запуск отмечает день, чтобы проверка в 07:00 не приводила
        # ко второй такой же сводке в 08:00. Но запуск с принудительным языком
        # — это именно проверка: все получили чужой язык, поэтому настоящую
        # сводку дня он не заменяет и день не закрывает.
        state["last_digest"] = tbilisi_now().strftime("%Y-%m-%d")
    if sent or dropped:
        save_state(state)

    if not total:
        log("[ok] получателей нет: никто не нажал /start и TELEGRAM_CHAT_ID не задан")
        return 0

    log(f"[ok] доставлено {sent} из {total}, вычеркнуто: {dropped}")
    if not sent:
        # Раньше такой запуск завершался зелёным, и молчание бота выглядело
        # загадкой. Теперь провал виден сразу в списке запусков.
        log("[!] ни одно сообщение не доставлено — считаю запуск неудачным")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
