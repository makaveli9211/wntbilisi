#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Блок с результатами матчей для утренней сводки.

Отдельный файл, ни от чего не зависит кроме стандартной библиотеки.
Если его нет рядом с bot.py — сводка просто выходит без футбола.

Данные: football-data.org, бесплатный навсегда тариф (12 турниров,
10 запросов в минуту). Ключ — в переменной окружения FOOTBALL_API_TOKEN.

Проверить, что отдаёт API:
    FOOTBALL_API_TOKEN=... python football.py
"""

import html
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# ------------------------------------------------------------- настройки --

# Порядок в списке задаёт порядок блоков в сводке.
# Коды бесплатного тарифа: CL Лига чемпионов, PL АПЛ, PD Ла Лига, SA Серия A,
# BL1 Бундеслига, FL1 Лига 1, PPL Примейра, DED Эредивизи, ELC Чемпионшип,
# BSA Бразилейрао, WC Чемпионат мира, EC Чемпионат Европы.
COMPETITIONS = ["CL", "PL", "PD", "SA", "BL1", "FL1"]
MAX_MATCHES = 12      # сколько матчей показывать максимум
HOURS_BACK = 36       # окно, за которое считаем матч «вчерашним»
TIMEOUT = 20

TITLE = {
    "ru": "⚽️ Футбол — вчера",
    "ka": "⚽️ ფეხბურთი — გუშინ",
}

NAMES = {
    "ru": {
        "CL": "Лига чемпионов", "PL": "Премьер-лига", "PD": "Ла Лига",
        "SA": "Серия A", "BL1": "Бундеслига", "FL1": "Лига 1",
        "PPL": "Примейра-лига", "DED": "Эредивизи", "ELC": "Чемпионшип",
        "BSA": "Бразилейрао", "WC": "Чемпионат мира", "EC": "Чемпионат Европы",
    },
    "ka": {
        "CL": "ჩემპიონთა ლიგა", "PL": "პრემიერ ლიგა", "PD": "ლა ლიგა",
        "SA": "სერია A", "BL1": "ბუნდესლიგა", "FL1": "ლიგა 1",
        "PPL": "პრიმეირა ლიგა", "DED": "ერედივიზი", "ELC": "ჩემპიონშიპი",
        "BSA": "ბრაზილეირაო", "WC": "მსოფლიო ჩემპიონატი", "EC": "ევროპის ჩემპიონატი",
    },
}

USER_AGENT = "Mozilla/5.0 (compatible; TbilisiDailyBot/1.0)"


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def _get(url, token):
    req = urllib.request.Request(url, headers={
        "X-Auth-Token": token,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def get_matches(now):
    """Завершённые матчи выбранных турниров за последние HOURS_BACK часов."""
    token = os.environ.get("FOOTBALL_API_TOKEN", "").strip()
    if not token:
        log("[i] футбол: ключа нет (FOOTBALL_API_TOKEN), блок пропускаю")
        return []

    # Окно вчера–сегодня: матч, начавшийся поздно вечером, по UTC уезжает
    # на следующую дату.
    #
    # Фильтр по турнирам НЕ отправляем: /v4/matches знает только ids, date,
    # dateFrom, dateTo и status — лишний параметр он отвергает целиком.
    # Нужные турниры отбираем уже здесь.
    params = {
        "dateFrom": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
        "dateTo": now.strftime("%Y-%m-%d"),
        "status": "FINISHED",
    }
    url = "https://api.football-data.org/v4/matches?" + urllib.parse.urlencode(params)

    try:
        data = _get(url, token)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:200]
        hint = {
            400: "неверный запрос",
            403: "ключ не даёт доступа к этим данным",
            429: "превышен лимит запросов",
        }.get(exc.code, "")
        log(f"[!] футбол: HTTP {exc.code} {hint} — {body}")
        return []
    except Exception as exc:  # noqa: BLE001
        log(f"[!] футбол: {exc.__class__.__name__}: {exc}")
        return []

    all_matches = data.get("matches", [])
    log(f"[i] футбол: API вернул {len(all_matches)} завершённых матчей "
        f"за {params['dateFrom']}–{params['dateTo']}")
    if not all_matches:
        return []

    codes = sorted({(m.get("competition") or {}).get("code") or "?" for m in all_matches})
    log(f"[i] футбол: турниры в ответе — {', '.join(codes)}; "
        f"отбираю {', '.join(COMPETITIONS)}")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=HOURS_BACK)
    out = []
    for m in all_matches:
        comp = m.get("competition") or {}
        code = comp.get("code") or ""
        if COMPETITIONS and code not in COMPETITIONS:
            continue
        score = (m.get("score") or {}).get("fullTime") or {}
        if score.get("home") is None or score.get("away") is None:
            continue
        try:
            when = datetime.fromisoformat((m.get("utcDate") or "").replace("Z", "+00:00"))
        except ValueError:
            when = None
        if when and when < cutoff:
            continue
        out.append({
            "code": code,
            "competition": comp.get("name") or "",
            "home": (m.get("homeTeam") or {}).get("shortName")
                    or (m.get("homeTeam") or {}).get("name") or "?",
            "away": (m.get("awayTeam") or {}).get("shortName")
                    or (m.get("awayTeam") or {}).get("name") or "?",
            "hg": score["home"],
            "ag": score["away"],
            "when": when,
        })

    out.sort(key=lambda x: (COMPETITIONS.index(x["code"]) if x["code"] in COMPETITIONS else 99,
                            x["when"] or datetime.min.replace(tzinfo=timezone.utc)))
    out = out[:MAX_MATCHES]
    log(f"[i] футбол: в сводку пойдёт {len(out)} матчей")
    return out


def football_block(lang="ru", now=None):
    """Готовый HTML-блок для сводки. Пусто — значит блока не будет."""
    lang = lang if lang in TITLE else "ru"
    now = now or datetime.now(timezone(timedelta(hours=4)))
    matches = get_matches(now)
    if not matches:
        return None

    esc = lambda t: html.escape(str(t), quote=False)   # noqa: E731
    lines = [f"<b>{esc(TITLE[lang])}</b>"]
    current = None
    for m in matches:
        name = NAMES[lang].get(m["code"]) or m["competition"]
        if name != current:
            current = name
            lines.append(f"<i>{esc(name)}</i>")
        lines.append(f"{esc(m['home'])} <b>{m['hg']}:{m['ag']}</b> {esc(m['away'])}")
    return "\n".join(lines)


if __name__ == "__main__":
    lang = sys.argv[1] if len(sys.argv) > 1 else "ru"
    block = football_block(lang)
    print(block if block else "Матчей для сводки нет — причина в строках выше.")
