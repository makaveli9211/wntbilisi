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

# --- подписчики ---
SUBSCRIBERS_FILE = "subscribers.json"  # список хранится прямо в репозитории
STORE_NAMES = False    # писать ли имена подписчиков в файл (см. раздел «Приватность» в README)
SEND_PAUSE = 0.05      # пауза между отправками, чтобы не упереться в лимит Telegram

# --- новости ---
NEWS_MAX_AGE_HOURS = 30              # насколько старые заголовки ещё считаем свежими
HTTP_TIMEOUT = 20

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


def fetch(url, timeout=HTTP_TIMEOUT, retries=2):
    """GET с ретраями. Возвращает bytes или бросает исключение."""
    last = None
    ctx = ssl.create_default_context()
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "*/*",
                    "Accept-Language": "ka,ru,en;q=0.8",
                },
            )
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


def collect_news(section, limit, seen, lang):
    if limit <= 0:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=NEWS_MAX_AGE_HOURS)
    pools, stale_pools = [], []
    for url in FEEDS.get(lang, {}).get(section, []):
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
    for section in ("world", "georgia", "finance", "tech"):
        log(f"Собираю раздел: {section}")
        items = collect_news(section, COUNTS.get(section, 0), seen, lang)
        block = format_news(section, items, S)
        if section == "finance" and rates_line:
            block = (block + "\n" + rates_line) if block else rates_line
        if block:
            blocks.append(block)

    msg = "\n\n".join(blocks)
    if len(msg) > 4000:
        msg = msg[:3990].rsplit("\n", 1)[0] + "\n…"
    return msg


# ============================================================================
# Telegram
# ============================================================================


class TelegramError(RuntimeError):
    def __init__(self, code, description):
        super().__init__(f"Telegram API {code}: {description}")
        self.code = code
        self.description = description or ""


def tg_api(token, method, payload):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            desc = json.loads(body).get("description", body)
        except ValueError:
            desc = body
        raise TelegramError(exc.code, desc) from exc


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
    "group chat was upgraded",
    "peer_id_invalid",
)


def is_gone(exc):
    if exc.code not in (400, 403):
        return False
    low = exc.description.lower()
    return any(m in low for m in GONE_MARKERS)


# ============================================================================
# Список подписчиков
# ============================================================================


def load_state(path=None):
    path = path or SUBSCRIBERS_FILE
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"offset": 0, "subscribers": {}}
    data.setdefault("offset", 0)
    data.setdefault("subscribers", {})
    return data


def save_state(state, path=None):
    path = path or SUBSCRIBERS_FILE
    state["subscribers"] = dict(sorted(state["subscribers"].items(), key=lambda kv: int(kv[0])))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


def merge_state(local, remote):
    """Сливает наш список с тем, что лежит в репозитории.

    Нужно, когда файл успели поменять со стороны: параллельный запуск или
    правка руками. Текстовое слияние тут бессмысленно, а по смыслу всё просто —
    подписчики объединяются, наши настройки приоритетнее, offset только растёт.
    Спорные случаи решаем в пользу того, чтобы человека не потерять.
    """
    subs = dict(remote.get("subscribers") or {})
    subs.update(local.get("subscribers") or {})
    return {
        "offset": max(int(local.get("offset") or 0), int(remote.get("offset") or 0)),
        "subscribers": subs,
    }


def guess_lang(update_from):
    """Первый язык подбираем по языку клиента Telegram, дальше человек решает сам."""
    code = ((update_from or {}).get("language_code") or "").lower()
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
    if key in subs:
        subs[key]["lang"] = lang
    else:
        # Нажал кнопку, не будучи подписанным — заодно подписываем.
        subs[key] = {"lang": lang,
                     "added": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    text = STRINGS[lang]["lang_set"]
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
    return True


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
    dedup = dedup if dedup is not None else set()
    subs = state["subscribers"]
    key = str(chat_id)
    known = subs.get(key)
    lang = (known or {}).get("lang") or guess_lang(sender)
    S = STRINGS[lang]

    cmd, _, arg = text.strip().partition(" ")
    cmd = cmd.split("@")[0].lower()          # /start@my_bot в группах
    arg = arg.strip().lower()

    if cmd == "/start":
        if known:
            if once(dedup, chat_id, "already"):
                send_plain(token, chat_id, S["already"], markup=lang_keyboard(lang))
            return False
        entry = {"lang": lang, "added": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
        if STORE_NAMES:
            entry["name"] = chat_title or (sender or {}).get("first_name") or ""
        subs[key] = entry
        if once(dedup, chat_id, "hello"):
            send_plain(token, chat_id, S["hello"], markup=lang_keyboard(lang))
        log(f"  [+] подписался {chat_id} ({lang})")
        return True

    if cmd == "/stop":
        if not known:
            if once(dedup, chat_id, "not_subscribed"):
                send_plain(token, chat_id, S["not_subscribed"])
            return False
        subs.pop(key, None)
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
            data = cb.get("data") or ""
            msg = cb.get("message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
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
                state["subscribers"].pop(key, None)
                log(f"  [-] {key} заблокировал бота — вычеркнул")
                changed = True
            continue

        msg = upd.get("message") or {}
        text = msg.get("text") or ""
        if not text.startswith("/"):
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
    """Рассылает сводку всем подписчикам. Возвращает (успешно, вычеркнуто)."""
    targets = dict(state["subscribers"])
    if extra_chat_id:
        targets.setdefault(str(extra_chat_id), {"lang": forced_lang or DEFAULT_LANG})

    if not targets:
        log("[i] подписчиков нет — рассылать некому")
        return 0, 0

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

    sent = dropped = 0
    for chat_id, info in targets.items():
        lang = forced_lang or info.get("lang") or DEFAULT_LANG
        text = messages[lang if lang in messages else DEFAULT_LANG]
        try:
            send(token, chat_id, text)
            sent += 1
        except TelegramError as exc:
            if exc.code == 429:
                wait = 3
                log(f"  [~] лимит Telegram, жду {wait}с")
                time.sleep(wait)
                try:
                    send(token, chat_id, text)
                    sent += 1
                    continue
                except TelegramError as exc2:
                    exc = exc2
            if is_gone(exc):
                state["subscribers"].pop(str(chat_id), None)
                dropped += 1
                log(f"  [-] {chat_id} недоступен ({exc.description}) — вычеркнул")
            else:
                log(f"  [!] {chat_id}: {exc}")
        time.sleep(SEND_PAUSE)

    return sent, dropped


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
        return whoami(token)

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
        if poll(token, state):
            save_state(state)
            # Только после того, как список лёг на диск: если сохранение упадёт,
            # апдейты останутся у Telegram и обработаются на следующем запуске.
            confirm_offset(token, state)
        log(f"[ok] подписчиков: {before} → {len(state['subscribers'])}")
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
    sent, dropped = broadcast(token, state, extra_chat_id=chat_id or None, forced_lang=forced)
    if dropped:
        save_state(state)
    log(f"[ok] отправлено: {sent}, вычеркнуто: {dropped}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
