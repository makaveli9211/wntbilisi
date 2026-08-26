#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Офлайн-проверка сборки сообщения на подставных данных (сеть не нужна).

Прогоняет обе локали и печатает итог. Запуск: python test_offline.py
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import bot
import football

# ---------------------------------------------------------------- фикстуры --

WEATHER = {
    "daily": {
        "time": ["2026-08-26"],
        "weather_code": [80],
        "temperature_2m_max": [31.4],
        "temperature_2m_min": [19.2],
        "apparent_temperature_max": [34.1],
        "precipitation_sum": [2.3],
        "precipitation_probability_max": [45],
        "wind_speed_10m_max": [17.6],
        "uv_index_max": [8.35],
        "sunrise": ["2026-08-26T06:42"],
        "sunset": ["2026-08-26T20:11"],
    },
    "hourly": {
        "time": [f"2026-08-26T{h:02d}:00" for h in range(24)],
        "temperature_2m": [18 + h * 0.6 for h in range(24)],
        "weather_code": [0, 0, 0, 0, 0, 0, 1, 1, 2, 2, 2, 3,
                         3, 80, 80, 81, 2, 1, 0, 0, 0, 0, 0, 0],
        "precipitation_probability": [0] * 12 + [35, 60, 55, 40, 10, 5, 0, 0, 0, 0, 0, 0],
        "wind_speed_10m": [5 + h * 0.5 for h in range(24)],
    },
}

NBG = [{"date": "2026-08-26", "currencies": [
    {"code": "USD", "quantity": 1, "rate": 2.7015, "diff": 0.0032},
    {"code": "EUR", "quantity": 1, "rate": 2.9440, "diff": -0.0110},
    {"code": "RUB", "quantity": 100, "rate": 3.3120, "diff": 0.0},
    {"code": "GBP", "quantity": 1, "rate": 3.4100, "diff": 0.01},   # лишняя — отфильтруется
]}]

FRESH = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%a, %d %b %Y %H:%M:%S +0000")
OLD = (datetime.now(timezone.utc) - timedelta(days=9)).strftime("%a, %d %b %Y %H:%M:%S +0000")


def rss(channel, titles, host="example.com", date=None):
    items = "\n".join(
        f"<item><title>{t}</title><link>https://{host}/a{i}</link>"
        f"<pubDate>{date or FRESH}</pubDate></item>"
        for i, t in enumerate(titles)
    )
    return (f'<?xml version="1.0"?><rss version="2.0"><channel>'
            f"<title>{channel}</title>{items}</channel></rss>").encode()


ATOM = ("""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Source</title>
<entry><title>Atom-заголовок &amp; символы &lt;b&gt;псевдотег&lt;/b&gt;</title>
<link rel="alternate" href="https://atom.example/1"/>
<published>%s</published></entry></feed>"""
        % datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")).encode()

def match(code, comp, home, away, hg, ag, hours_ago=14):
    when = (datetime.now(timezone.utc) - timedelta(hours=hours_ago))
    return {
        "utcDate": when.isoformat().replace("+00:00", "Z"),
        "status": "FINISHED",
        "competition": {"code": code, "name": comp},
        "homeTeam": {"shortName": home, "name": home + " FC"},
        "awayTeam": {"shortName": away, "name": away + " FC"},
        "score": {"fullTime": {"home": hg, "away": ag}},
    }


FOOTBALL = {"matches": [
    match("PL", "Premier League", "Arsenal", "Chelsea", 2, 1),
    match("PL", "Premier League", "Liverpool", "Everton", 0, 0, hours_ago=16),
    match("CL", "UEFA Champions League", "Real Madrid", "Bayern", 3, 2, hours_ago=13),
    match("SA", "Serie A", "Inter", "Milan", 1, 2, hours_ago=15),
    # позавчерашний — не должен попасть
    match("PD", "La Liga", "СТАРЫЙ", "МАТЧ", 9, 9, hours_ago=50),
    # турнир вне нашего списка — фильтруем у себя, раз API этого не умеет
    match("ELC", "Championship", "ЛИШНИЙ", "ТУРНИР", 4, 4),
    # ещё не доигран — счёта нет
    {"utcDate": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
     "status": "FINISHED", "competition": {"code": "PL", "name": "Premier League"},
     "homeTeam": {"shortName": "БЕЗ"}, "awayTeam": {"shortName": "СЧЁТА"},
     "score": {"fullTime": {"home": None, "away": None}}},
]}

COMMON = {
    "open-meteo": json.dumps(WEATHER).encode(),
    "nbg.gov.ge": json.dumps(NBG).encode(),
}

FIXTURES = {
    "ru": dict(COMMON, **{
        "bbci.co.uk": rss("BBC Russian", ["Мировая новость один", "Мировая новость два"], "bbc.com"),
        "dw.com": rss("DW", ["Мировая новость три", "Мировая новость один"], "dw.com"),  # дубль
        "meduza.io": rss("Meduza", ["Мировая новость четыре", "Мировая новость пять"], "meduza.io"),
        "euronews.com": ATOM,
        "civil.ge": rss("Civil.ge", ["Новость про Грузию раз"], "civil.ge"),
        "jam-news.net": rss("JAMnews", ["Новость про Грузию два"], "jam-news.net"),
        "investing.com": rss("Investing", ["Финансы раз", "Финансы два"], "investing.com"),
        "habr.com": rss("Habr", ["Техно раз &lt;b&gt;с тегом&lt;/b&gt;", "Техно два"], "habr.com"),
        # подвисшая лента: девятидневные заголовки не должны вытеснить свежие
        "3dnews.ru": rss("3DNews", ["ПРОТУХШАЯ новость раз", "ПРОТУХШАЯ новость два"],
                         "3dnews.ru", date=OLD),
    }),
    "ka": dict(COMMON, **{
        "radiotavisupleba.ge": rss("რადიო თავისუფლება",
                                   ["მსოფლიო ამბავი ერთი", "მსოფლიო ამბავი ორი"],
                                   "radiotavisupleba.ge"),
        "publika.ge": rss("პუბლიკა", ["მსოფლიო ამბავი სამი"], "publika.ge"),
        "on.ge": rss("On.ge", ["მსოფლიო ამბავი ოთხი", "მსოფლიო ამბავი ხუთი"], "on.ge"),
        "netgazeti.ge/feed": rss("Netgazeti", ["ქართული ამბავი ერთი"], "netgazeti.ge"),
        "netgazeti.ge/category/south_caucasus": rss(
            "სამხრეთ კავკასია", ["ქართული ამბავი ორი"], "netgazeti.ge"),
        "netgazeti.ge/category/business": rss(
            "ეკონომიკა", ["ფინანსური ამბავი ერთი", "ფინანსური ამბავი ორი"], "netgazeti.ge"),
        "civil.ge": rss("Civil.ge", ["ქართული ამბავი სამი"], "civil.ge"),
        "techcrunch.com": rss("TechCrunch", ["Tech story one", "Tech story two"], "techcrunch.com"),
    }),
}

# ------------------------------------------------------------------ проверки --

NOW = datetime(2026, 8, 26, 8, 0, tzinfo=timezone(timedelta(hours=4)))

# Блок футбола появляется только при наличии ключа — в тестах подставляем свой.
os.environ["FOOTBALL_API_TOKEN"] = "test-key"


def fake_football_get(url, token):
    if not token:
        raise AssertionError("запрос к football-data.org без ключа")
    return FOOTBALL


football._get = fake_football_get


def make_fetch(lang):
    table = FIXTURES[lang]

    def fake_fetch(url, **kw):
        for key, payload in table.items():
            if key in url:
                return payload
        raise OSError(f"нет фикстуры для {url}")   # имитируем мёртвую ленту

    return fake_fetch


def structural_checks(msg, problems):
    if len(msg) > 4096:
        problems.append(f"сообщение длиннее лимита Telegram: {len(msg)}")

    tags = re.findall(r"</?([a-z]+)", msg)
    allowed = {"b", "i", "a", "pre", "code", "u", "s"}
    bad = set(tags) - allowed
    if bad:
        problems.append(f"недопустимые для Telegram теги: {bad}")
    for tag in set(tags):
        opened = len(re.findall(rf"<{tag}(?:\s|>)", msg))
        closed = len(re.findall(rf"</{tag}>", msg))
        if opened != closed:
            problems.append(f"непарный тег <{tag}>: открыт {opened}, закрыт {closed}")

    for m in re.finditer(r"&(?!amp;|lt;|gt;|quot;|#\d+;)", msg):
        problems.append(f"неэкранированный & на позиции {m.start()}")


def hours_in(msg):
    table = msg.split("<pre>")[1].split("</pre>")[0] if "<pre>" in msg else ""
    return [int(m) for m in re.findall(r"^(\d{2}):00", table, re.M)]


def run(lang, checks, show=True):
    bot.fetch = make_fetch(lang)
    msg = bot.build_message(lang=lang, now=NOW)
    if show:
        print("=" * 64)
        print(msg)
        print("=" * 64)

    problems = []
    structural_checks(msg, problems)
    print(f"\n[{lang}] длина: {len(msg)} символов (лимит 4096)")
    for name, ok in checks(msg).items():
        print(f"  {'✓' if ok else '✗'} {name}")
        if not ok:
            problems.append(f"[{lang}] провалена проверка: {name}")
    return msg, problems


def ru_checks(msg):
    hours = hours_in(msg)
    return {
        "погода: заголовок": "🌦 Тбилиси · Среда, 26 августа" in msg,
        "погода: макс/мин": "Днём +31°, ночью +19°" in msg,
        "погода: ощущается": "(ощущается как +34°)" in msg,
        "погода: описание с большой буквы": "Кратковременный дождь" in msg,
        "погода: осадки": "Вероятность осадков 45%, до 2.3 мм" in msg,
        "погода: ветер и УФ": "Ветер до 18 км/ч · УФ 8 (очень высокий)" in msg,
        "погода: восход/закат": "06:42" in msg and "20:11" in msg,
        "почасовой: заголовок": "По часам" in msg,
        "почасовой: каждый час 08–23": hours == list(range(8, 24)),
        "почасовой: прошедшие часы скрыты": 7 not in hours,
        "почасовой: вероятность осадков в строке": re.search(r"13:00.+60%", msg) is not None,
        "почасовой: сухие часы без процентов": re.search(r"09:00\s+\+\d+°\s+\S+\s*$",
                                                         msg, re.M) is not None,
        "мир: 5 пунктов": msg.count("\n5. ") == 1,
        "мир: дубль отброшен": msg.count("Мировая новость один") == 1,
        "мир: заголовки по кругу из всех лент": all(
            s in msg for s in ("BBC Russian", "DW", "Meduza", "Atom Source")),
        "грузия: мёртвые ленты не уронили раздел": "Новость про Грузию два" in msg,
        "финансы: блок": "Рынки и финансы" in msg,
        "курсы НБГ": "2.7015" in msg and "▲" in msg and "▼" in msg,
        "курс RUB за 100": "за 100" in msg,
        "курсы: лишняя валюта отфильтрована": "3.4100" not in msg,
        "техно: блок": "Технологии и AI" in msg,
        "устаревшая лента не вытеснила свежую": "ПРОТУХШАЯ" not in msg,
        "разметка внутри заголовка вырезана": "с тегом" in msg and "псевдотег" in msg
                                              and "<b>с тегом</b>" not in msg,
        "экранирование &": "&amp;" in msg,
        "ссылки проставлены": '<a href="https://meduza.io/a0">' in msg,
        # футбол
        "футбол: заголовок": "⚽️ Футбол — вчера" in msg,
        "футбол: счёт": "Arsenal <b>2:1</b> Chelsea" in msg,
        "футбол: нулевая ничья не потерялась": "Liverpool <b>0:0</b> Everton" in msg,
        "футбол: турниры названы по-русски": "Лига чемпионов" in msg and "Премьер-лига" in msg,
        "футбол: Лига чемпионов идёт первой":
            msg.index("Лига чемпионов") < msg.index("Премьер-лига"),
        "футбол: позавчерашний матч отброшен": "СТАРЫЙ" not in msg,
        "футбол: недоигранный отброшен": "БЕЗ" not in msg,
        "футбол: лишний турнир отфильтрован у нас": "ЛИШНИЙ" not in msg,
    }


def ka_checks(msg):
    hours = hours_in(msg)
    return {
        "ამინდი: სათაური": "🌦 თბილისი · ოთხშაბათი, 26 აგვისტო" in msg,
        "ამინდი: დღე/ღამე": "დღისით +31°, ღამით +19°" in msg,
        "ამინდი: იგრძნობა": "(იგრძნობა როგორც +34°)" in msg,
        "ამინდი: აღწერა": "ხანმოკლე წვიმა" in msg,
        "მხედრული მთავრულად არ გადადის": not re.search(r"[Ა-Ჺ]", msg),
        "ამინდი: ნალექი": "ნალექის ალბათობა 45%, 2.3 მმ-მდე" in msg,
        "ამინდი: ქარი და UV": "ქარი 18 კმ/სთ-მდე · UV 8 (ძალიან მაღალი)" in msg,
        "საათობრივად: სათაური": "საათობრივად" in msg,
        "საათობრივად: 08–23": hours == list(range(8, 24)),
        "ახალი ამბები: 5 პუნქტი": msg.count("\n5. ") == 1,
        "საქართველო: ბლოკი": "🇬🇪 საქართველო" in msg,
        "ფინანსები: ბლოკი": "ბაზრები და ფინანსები" in msg,
        "ლარის კურსი": "ლარი (ეროვნული ბანკი)" in msg and "2.7015" in msg,
        "RUB 100-ზე": "100-ზე" in msg,
        "ტექნოლოგიები: ბლოკი": "ტექნოლოგიები და AI" in msg,
        "რუსული სტრიქონები არ ურევია": not re.search(r"[Дд]нём|Главное|По часам", msg),
        "ბმულები": '<a href="https://on.ge/a0">' in msg,
        "ფეხბურთი: სათაური": "⚽️ ფეხბურთი — გუშინ" in msg,
        "ფეხბურთი: ტურნირები ქართულად": "ჩემპიონთა ლიგა" in msg and "პრემიერ ლიგა" in msg,
        "ფეხბურთი: ანგარიში": "Arsenal <b>2:1</b> Chelsea" in msg,
    }


# ------------------------------------------------------- подписка и рассылка --


class FakeTelegram:
    """Подменяет tg_api: копит исходящие вызовы и отдаёт заготовленные апдейты."""

    def __init__(self, updates=(), fail=None):
        self.updates = list(updates)
        self.fail = fail or {}          # chat_id -> TelegramError
        self.sent = []                  # (chat_id, text)
        self.replies = {}               # chat_id -> [текст, ...]
        self.markups = {}               # chat_id -> [reply_markup, ...]
        self.edits = []                 # (chat_id, message_id, text, markup)
        self.answered = []              # callback_query_id
        self.confirmed = []             # offset, подтверждённый в Telegram
        self.edit_fails = False

    def __call__(self, token, method, payload):
        if method == "getUpdates":
            # limit=1 без allowed_updates — это подтверждение обработки, не выборка
            if payload.get("limit") == 1 and "allowed_updates" not in payload:
                self.confirmed.append(payload["offset"])
                return {"ok": True, "result": []}
            batch, self.updates = self.updates, []
            return {"ok": True, "result": batch}
        if method == "sendMessage":
            chat_id = str(payload["chat_id"])
            if payload.get("parse_mode") == "HTML":
                err = self.fail.get(chat_id)
                if err:
                    raise err
                self.sent.append((chat_id, payload["text"]))
            else:
                self.replies.setdefault(chat_id, []).append(payload["text"])
                self.markups.setdefault(chat_id, []).append(payload.get("reply_markup"))
            return {"ok": True}
        if method == "editMessageText":
            if self.edit_fails:
                raise bot.TelegramError(400, "Bad Request: message can't be edited")
            self.edits.append((str(payload["chat_id"]), payload["message_id"],
                               payload["text"], payload.get("reply_markup")))
            return {"ok": True}
        if method == "answerCallbackQuery":
            self.answered.append(payload["callback_query_id"])
            return {"ok": True}
        raise AssertionError(f"неожиданный метод {method}")


def msg_update(uid, chat_id, text, lang_code="ru", chat_type="private"):
    return {
        "update_id": uid,
        "message": {
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": chat_id, "first_name": "Тест", "language_code": lang_code},
            "text": text,
        },
    }


def marked_lang(markup):
    """Какой язык помечен галочкой в клавиатуре (markup приходит JSON-строкой)."""
    if not markup:
        return None
    for row in json.loads(markup).get("inline_keyboard", []):
        for btn in row:
            if btn["text"].startswith("✅"):
                return btn["callback_data"].split(":", 1)[1]
    return None


def button_update(uid, chat_id, data, message_id=500):
    return {
        "update_id": uid,
        "callback_query": {
            "id": f"cb{uid}",
            "data": data,
            "from": {"id": chat_id, "language_code": "ru"},
            "message": {"message_id": message_id, "chat": {"id": chat_id, "type": "private"}},
        },
    }


def kicked_update(uid, chat_id):
    return {
        "update_id": uid,
        "my_chat_member": {
            "chat": {"id": chat_id, "type": "private"},
            "new_chat_member": {"status": "kicked"},
        },
    }


def subscription_checks(problems):
    print("\n[подписка] команды бота")

    state = {"offset": 0, "subscribers": {}}
    tg = FakeTelegram([
        msg_update(10, 111, "/start"),                      # обычная подписка
        msg_update(11, 222, "/start", lang_code="ka-GE"),   # язык угадан по клиенту
        msg_update(12, 111, "/start"),                      # повторно — уже подписан
        msg_update(13, 333, "/start@wntbilisi_bot"),        # вариант из группы
        msg_update(14, 333, "/lang ka"),                    # смена языка
        msg_update(15, 222, "/lang klingon"),               # неизвестный язык
        msg_update(16, 444, "/start"),
        msg_update(17, 444, "/stop"),                       # подписался и ушёл
        msg_update(18, 555, "/stop"),                       # не был подписан
        msg_update(19, 111, "привет"),                      # не команда — игнор
        button_update(20, 111, "lang:ka"),                  # нажал кнопку «ქართული»
        msg_update(21, 666, "/lang"),                       # без аргумента — показать кнопки
        button_update(22, 777, "lang:ru"),                  # кнопка от неподписанного
        button_update(23, 111, "lang:klingon"),             # мусор в callback_data
        kicked_update(24, 222),                             # заблокировал бота
    ])
    bot.tg_api = tg
    bot.poll("token", state)

    subs = state["subscribers"]
    checks = {
        "/start подписывает": "111" in subs,
        "/stop отписывает": "444" not in subs,
        "повторный /start не дублирует": len([r for r in tg.replies.get("111", [])
                                              if "уже подписаны" in r]) == 1,
        "/start@имя_бота работает в группе": "333" in subs,
        "язык угадан по клиенту Telegram": subs.get("222", {}).get("lang") == "ka"
                                           or "222" not in subs,
        "/lang меняет язык": subs.get("333", {}).get("lang") == "ka",
        # вместо ошибки показываем кнопки, на языке собеседника (у 222 грузинский)
        "неизвестный язык показывает кнопки": (
            bot.STRINGS["ka"]["choose_lang"] in tg.replies.get("222", [])
            and not any(r == bot.STRINGS[x]["lang_set"] for x in ("ru", "ka")
                        for r in tg.replies.get("222", []))),
        "заблокировавший вычеркнут": "222" not in subs,
        "не-команда игнорируется": "привет" not in str(tg.replies),
        "offset сдвинулся": state["offset"] == 25,
        # кнопки
        "кнопка меняет язык": subs.get("111", {}).get("lang") == "ka",
        "кнопка правит то же сообщение": any(e[0] == "111" and e[1] == 500
                                             for e in tg.edits),
        "галочка переезжает на выбранный": any(
            marked_lang(e[3]) == "ka" for e in tg.edits if e[0] == "111"),
        "«часики» на кнопке гасятся": "cb20" in tg.answered,
        "/lang без аргумента показывает кнопки": any(
            m and "inline_keyboard" in m for m in tg.markups.get("666", [])),
        "кнопка подписывает неподписанного": subs.get("777", {}).get("lang") == "ru",
        "мусор в callback_data не ломает": "cb23" in tg.answered
                                           and subs.get("111", {}).get("lang") == "ka",
        "приветствие приходит с кнопками": any(
            m and "inline_keyboard" in m for m in tg.markups.get("111", [])),
        "приветствие отправлено": any("подписаны" in r for r in tg.replies.get("111", [])),
        "приветствие на грузинском": any("გამოწერილი" in r for r in tg.replies.get("222", [])),
        "прощание отправлено": any("Отписал" in r for r in tg.replies.get("444", [])),
    }
    for name, ok in checks.items():
        print(f"  {'✓' if ok else '✗'} {name}")
        if not ok:
            problems.append(f"[подписка] провалена проверка: {name}")

    print("\n[дубли] человек нажал кнопку пять раз подряд")

    state = {"offset": 0, "subscribers": {}}
    tg = FakeTelegram([
        msg_update(30, 888, "/start"),
        msg_update(31, 888, "/start"),       # ещё раз, не дождавшись ответа
        msg_update(32, 888, "/lang"),        # тапнул «Меню → язык»
        msg_update(33, 888, "/lang"),
        msg_update(34, 888, "/lang"),
        button_update(35, 888, "lang:ru", message_id=600),
        button_update(36, 888, "lang:ru", message_id=600),
        button_update(37, 888, "lang:ru", message_id=600),
    ])
    bot.tg_api = tg
    bot.poll("token", state)
    replies = tg.replies.get("888", [])

    checks = {
        "приветствие одно": sum("подписаны 👋" in r for r in replies) == 1,
        "«уже подписаны» один раз": sum(r == bot.STRINGS["ka"]["already"]
                                        or r == bot.STRINGS["ru"]["already"]
                                        for r in replies) <= 1,
        "выбор языка предложен один раз": sum(r in (bot.STRINGS["ru"]["choose_lang"],
                                                    bot.STRINGS["ka"]["choose_lang"])
                                              for r in replies) == 1,
        "новых сообщений всего не больше трёх": len(replies) <= 3,
        "каждое нажатие кнопки подтверждено": {"cb35", "cb36", "cb37"} <= set(tg.answered),
        "повторные нажатия только правят сообщение": len(tg.edits) == 3,
        "язык в итоге верный": state["subscribers"]["888"]["lang"] == "ru",
    }
    for name, ok in checks.items():
        print(f"  {'✓' if ok else '✗'} {name}")
        if not ok:
            problems.append(f"[дубли] провалена проверка: {name}")

    print("\n[подтверждение] offset уходит в Telegram")
    tg = FakeTelegram()
    bot.tg_api = tg
    bot.confirm_offset("token", {"offset": 42, "subscribers": {}})
    ok = tg.confirmed == [42]
    print(f"  {'✓' if ok else '✗'} Telegram получил offset и удалит апдейты у себя")
    if not ok:
        problems.append("[подтверждение] offset не подтверждён")
    tg2 = FakeTelegram()
    bot.tg_api = tg2
    bot.confirm_offset("token", {"offset": 0, "subscribers": {}})
    if not tg2.confirmed:
        print("  ✓ на пустом offset лишний запрос не шлётся")
    else:
        problems.append("[подтверждение] лишний запрос на пустом offset")

    print("\n[рассылка] отправка по списку")

    state = {"offset": 0, "subscribers": {
        "111": {"lang": "ru"},
        "222": {"lang": "ka"},
        "333": {"lang": "ru"},
        "444": {"lang": "ru"},          # заблокировал бота
        "555": {"lang": "ru"},          # временная ошибка — остаётся в списке
    }}
    tg = FakeTelegram(fail={
        "444": bot.TelegramError(403, "Forbidden: bot was blocked by the user"),
        "555": bot.TelegramError(500, "Internal Server Error"),
    })
    bot.tg_api = tg
    bot.SEND_PAUSE = 0

    built = []
    bot.build_message = lambda lang=bot.DEFAULT_LANG, now=None: (
        built.append(lang) or f"СВОДКА[{lang}]")

    sent, dropped = bot.broadcast("token", state, extra_chat_id="999")

    by_chat = dict(tg.sent)
    checks = {
        "сообщение собрано по разу на язык": sorted(built) == ["ka", "ru"],
        "русским ушла русская версия": by_chat.get("111") == "СВОДКА[ru]",
        "грузину ушла грузинская": by_chat.get("222") == "СВОДКА[ka]",
        "TELEGRAM_CHAT_ID получает язык по умолчанию":
            by_chat.get("999") == f"СВОДКА[{bot.DEFAULT_LANG}]",
        "успешных отправок": sent == 4,
        "заблокировавший вычеркнут": dropped == 1 and "444" not in state["subscribers"],
        "временная ошибка не вычёркивает": "555" in state["subscribers"],
    }
    for name, ok in checks.items():
        print(f"  {'✓' if ok else '✗'} {name}")
        if not ok:
            problems.append(f"[рассылка] провалена проверка: {name}")

    print("\n[файл] сохранение списка")
    import tempfile
    path = tempfile.mktemp(suffix=".json")
    bot.save_state({"offset": 7, "subscribers": {"222": {"lang": "ka"}, "111": {"lang": "ru"}}},
                   path=path)
    back = bot.load_state(path=path)
    ok_roundtrip = back["offset"] == 7 and list(back["subscribers"]) == ["111", "222"]
    print(f"  {'✓' if ok_roundtrip else '✗'} файл читается обратно, id отсортированы")
    if not ok_roundtrip:
        problems.append("[файл] список не пережил сохранение")
    missing = bot.load_state(path=path + ".нет")
    if missing == {"offset": 0, "subscribers": {}}:
        print("  ✓ отсутствующий файл даёт пустой список, а не падение")
    else:
        problems.append("[файл] отсутствующий файл не обработан")

    print("\n[слияние] версия из репозитория против нашей")
    local = {"offset": 50, "subscribers": {"111": {"lang": "ka"}, "222": {"lang": "ru"}}}
    remote = {"offset": 40, "subscribers": {"222": {"lang": "ka"}, "333": {"lang": "ru"}}}
    m = bot.merge_state(local, remote)
    checks = {
        "наши подписчики сохранены": "111" in m["subscribers"],
        "чужие подписчики не потеряны": "333" in m["subscribers"],
        "при конфликте побеждает наша настройка": m["subscribers"]["222"]["lang"] == "ru",
        "offset берётся больший": m["offset"] == 50,
        "затёртый вручную файл не сносит список":
            bot.merge_state(local, {"offset": 0, "subscribers": {}})["subscribers"].keys()
            == local["subscribers"].keys(),
        "пустая своя версия подхватывает репозиторий":
            "333" in bot.merge_state({"offset": 0, "subscribers": {}}, remote)["subscribers"],
    }
    for name, ok in checks.items():
        print(f"  {'✓' if ok else '✗'} {name}")
        if not ok:
            problems.append(f"[слияние] провалена проверка: {name}")


def main():
    problems = []

    ru_msg, p = run("ru", ru_checks)
    problems += p
    ka_msg, p = run("ka", ka_checks)
    problems += p

    print("\n[футбол] когда ключа нет или API молчит")
    bot.fetch = make_fetch("ru")
    saved = os.environ.pop("FOOTBALL_API_TOKEN")
    without = bot.build_message(lang="ru", now=NOW)
    if "Футбол" not in without and "Тбилиси" in without:
        print("  ✓ без ключа блок просто не появляется, сводка целая")
    else:
        problems.append("[футбол] без ключа блок повёл себя не так")
    os.environ["FOOTBALL_API_TOKEN"] = saved

    bot.fetch = make_fetch("ru")

    def football_broken(url, token):
        raise OSError("403 Forbidden")

    football._get = football_broken
    broken = bot.build_message(lang="ru", now=NOW)
    football._get = fake_football_get
    if "Футбол" not in broken and "Главное в мире" in broken:
        print("  ✓ отказ футбольного API не рушит остальную сводку")
    else:
        problems.append("[футбол] отказ API уронил сводку")

    print("\n[доп.] крайние случаи")

    # шаг в 3 часа
    bot.fetch = make_fetch("ru")
    bot.HOURLY_STEP = 3
    stepped = hours_in(bot.build_message(lang="ru", now=NOW))
    bot.HOURLY_STEP = 1
    if stepped == [10, 13, 16, 19, 22]:
        print("  ✓ HOURLY_STEP=3 прореживает таблицу")
    else:
        problems.append(f"HOURLY_STEP=3 дал часы {stepped}")

    # поздний вечер: таблица почти пуста, но сообщение живо
    late = bot.build_message(lang="ru", now=NOW.replace(hour=22))
    if hours_in(late) == [22, 23]:
        print("  ✓ поздним вечером остаются только оставшиеся часы")
    else:
        problems.append(f"поздний запуск дал часы {hours_in(late)}")

    # неизвестный язык откатывается на русский
    if bot.pick_lang(["--lang", "de"]) == bot.DEFAULT_LANG:
        print(f"  ✓ неизвестный язык откатывается на {bot.DEFAULT_LANG}")
    else:
        problems.append("неизвестный язык не откатился на язык по умолчанию")
    if bot.pick_lang(["--lang", "ka"]) == "ka":
        print("  ✓ флаг --lang ka распознан")
    else:
        problems.append("флаг --lang ka не распознан")

    # полный отказ сети не должен ронять сводку
    bot.fetch = lambda url, **kw: (_ for _ in ()).throw(OSError("сеть недоступна"))
    for lang, marker in (("ru", "недоступен"), ("ka", "მიუწვდომელია")):
        if marker in bot.build_message(lang=lang, now=NOW):
            print(f"  ✓ [{lang}] при полном отказе сети сводка не падает")
        else:
            problems.append(f"[{lang}] нет запасного текста при отказе сети")

    # Идёт последним: подменяет build_message и tg_api.
    subscription_checks(problems)

    print()
    if problems:
        print("ПРОБЛЕМЫ:")
        for pr in problems:
            print("  -", pr)
        return 1
    print("Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
