#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сквозная проверка: прогоняем сутки работы бота целиком, без сети.

Моделируется то, что реально происходит на GitHub Actions:
каждый запуск — свежий процесс, читающий subscribers.json из репозитория,
а между запусками файл проходит через слияние с удалённой версией
(то самое, что делает шаг «Сохранить список подписчиков»).

Запуск: python test_e2e.py
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import bot
import football

os.environ["FOOTBALL_API_TOKEN"] = "test-key"

TZ = timezone(timedelta(hours=4))
problems = []


def check(name, ok, detail=""):
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        problems.append(f"{name} {detail}".strip())


# --------------------------------------------------------------- окружение --

class Telegram:
    """Модель Telegram: очередь апдейтов, доставленные сводки, сбои."""

    def __init__(self):
        self.queue = []            # ожидающие апдейты
        self.next_id = 1000
        self.delivered = {}        # chat_id -> [сводки]
        self.replies = {}          # chat_id -> [служебные ответы]
        self.blocked = set()       # кто заблокировал бота
        self.fail_once = {}        # chat_id -> исключение на одну попытку
        self.confirmed_upto = 0

    # --- со стороны людей ---
    def user_says(self, chat_id, text, lang_code="ru"):
        self.next_id += 1
        self.queue.append({
            "update_id": self.next_id,
            "message": {"chat": {"id": chat_id, "type": "private"},
                        "from": {"id": chat_id, "language_code": lang_code},
                        "text": text},
        })

    def user_taps(self, chat_id, data, message_id=900):
        self.next_id += 1
        self.queue.append({
            "update_id": self.next_id,
            "callback_query": {"id": f"cb{self.next_id}", "data": data,
                               "from": {"id": chat_id, "language_code": "ru"},
                               "message": {"message_id": message_id,
                                           "chat": {"id": chat_id, "type": "private"}}},
        })

    # --- со стороны бота ---
    def __call__(self, token, method, payload, retries=2):
        if method == "getUpdates":
            offset = int(payload.get("offset") or 0)
            if payload.get("limit") == 1 and "allowed_updates" not in payload:
                self.confirmed_upto = offset
                self.queue = [u for u in self.queue if u["update_id"] >= offset]
                return {"ok": True, "result": []}
            batch = [u for u in self.queue if u["update_id"] >= offset]
            return {"ok": True, "result": batch}

        if method == "sendMessage":
            chat_id = str(payload["chat_id"])
            exc = self.fail_once.pop(chat_id, None)
            if exc:
                raise exc
            if chat_id in self.blocked:
                raise bot.TelegramError(403, "Forbidden: bot was blocked by the user")
            if payload.get("parse_mode") == "HTML":
                self.delivered.setdefault(chat_id, []).append(payload["text"])
            else:
                self.replies.setdefault(chat_id, []).append(payload["text"])
            return {"ok": True}

        if method in ("editMessageText", "answerCallbackQuery"):
            return {"ok": True}
        raise AssertionError(f"неожиданный метод {method}")


class Repo:
    """Модель репозитория: файл состояния + слияние при каждом «коммите»."""

    def __init__(self, path):
        self.path = path
        self._write({"offset": 0, "subscribers": {}})

    def _write(self, state):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)

    def read(self):
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh)


def fake_feeds(url, **kw):
    return b'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title></channel></rss>'


def run(tg, repo, mode, now):
    """Один запуск workflow целиком, как на раннере.

    Важно: бот работает не с файлом репозитория напрямую, а с рабочей копией —
    ровно как actions/checkout. Только так слияние получается настоящим,
    а не файла с самим собой (иначе тест ничего не проверяет).
    """
    work = tempfile.mktemp(suffix=".json")
    with open(work, "w", encoding="utf-8") as fh:
        json.dump(repo.read(), fh, ensure_ascii=False)

    bot.tg_api = tg
    bot.fetch = fake_feeds
    bot.SEND_PAUSE = 0
    bot.SUBSCRIBERS_FILE = work
    bot.tbilisi_now = lambda: now
    football._cache.clear()
    football._get = lambda url, token: {"matches": []}
    bot.build_message = lambda lang=bot.DEFAULT_LANG, now=None: f"СВОДКА[{lang}]"

    code = bot.main(mode)

    # шаг «Сохранить список подписчиков»: слияние рабочей копии с репозиторием
    merged = bot.merge_state(bot.load_state(work), repo.read())
    bot.save_state(merged, repo.path)
    # и только теперь — «Подтвердить обработку апдейтов»
    if mode == ["--poll"]:
        bot.SUBSCRIBERS_FILE = repo.path
        bot.main(["--confirm"])
    os.remove(work)
    return code


# ------------------------------------------------------------------ сценарий --

def main():
    os.environ["TELEGRAM_BOT_TOKEN"] = "token"
    os.environ.pop("TELEGRAM_CHAT_ID", None)
    path = tempfile.mktemp(suffix=".json")
    tg, repo = Telegram(), Repo(path)

    day1 = datetime(2026, 8, 27, 7, 0, tzinfo=TZ)

    print("\n[1] Подписка и выбор языка")
    tg.user_says(111, "/start", lang_code="ka")
    tg.user_says(222, "/start", lang_code="ru")
    run(tg, repo, ["--poll"], day1)
    state = repo.read()
    check("оба подписались", set(state["subscribers"]) == {"111", "222"},
          str(state["subscribers"]))
    check("язык угадан по клиенту",
          state["subscribers"]["111"]["lang"] == "ka"
          and state["subscribers"]["222"]["lang"] == "ru")

    print("\n[2] До восьми утра сводка не уходит")
    run(tg, repo, ["--if-due"], day1)
    check("в 07:00 не рассылает", not tg.delivered)
    check("отметка дня не поставлена", "last_digest" not in repo.read())

    print("\n[3] Восемь утра — сводка уходит каждому на своём языке")
    day1 = day1.replace(hour=8)
    run(tg, repo, ["--if-due"], day1)
    check("оба получили", set(tg.delivered) == {"111", "222"}, str(list(tg.delivered)))
    check("грузину грузинская", tg.delivered["111"] == ["СВОДКА[ka]"])
    check("русскому русская", tg.delivered["222"] == ["СВОДКА[ru]"])
    check("день отмечен", repo.read().get("last_digest") == "2026-08-27")

    print("\n[4] Повторные запуски в тот же день ничего не шлют")
    for hour in (8, 9, 12, 20, 23):
        run(tg, repo, ["--if-due"], day1.replace(hour=hour))
    check("сводка ровно одна у каждого",
          all(len(v) == 1 for v in tg.delivered.values()),
          str({k: len(v) for k, v in tg.delivered.items()}))

    print("\n[5] Отписка переживает слияние с репозиторием")
    tg.user_says(222, "/stop")
    run(tg, repo, ["--poll"], day1)
    state = repo.read()
    check("222 исчез из списка", "222" not in state["subscribers"], str(state["subscribers"]))
    check("надгробие поставлено", "222" in (state.get("removed") or {}))
    # ещё несколько прогонов — отписавшийся не должен вернуться
    for _ in range(3):
        run(tg, repo, ["--poll"], day1)
    check("и не воскресает", "222" not in repo.read()["subscribers"])

    print("\n[6] Новый день: сводка уходит снова, но только оставшимся")
    day2 = day1.replace(day=28, hour=8)
    run(tg, repo, ["--if-due"], day2)
    check("111 получил вторую", len(tg.delivered["111"]) == 2)
    check("222 больше не получает", len(tg.delivered["222"]) == 1)
    check("день обновился", repo.read().get("last_digest") == "2026-08-28")

    print("\n[7] Пропущенные запуски: сводка догоняет")
    day3 = day2.replace(day=29, hour=13)     # утренние прогоны GitHub проглотил
    run(tg, repo, ["--if-due"], day3)
    check("догнала в 13:00", len(tg.delivered["111"]) == 3)
    check("день отмечен", repo.read().get("last_digest") == "2026-08-29")

    print("\n[8] Обрыв сети на отправке не рвёт прогон и не теряет день")
    tg.user_says(333, "/start")
    run(tg, repo, ["--poll"], day3)
    day4 = day3.replace(day=30, hour=8)
    tg.fail_once["111"] = bot.TelegramError(0, "URLError: timed out")
    run(tg, repo, ["--if-due"], day4)
    check("111 всё же получил (сработал повтор)", len(tg.delivered["111"]) == 4)
    check("333 тоже получил", len(tg.delivered.get("333", [])) == 1)
    check("день отмечен один раз", repo.read().get("last_digest") == "2026-08-30")

    print("\n[9] Лимит Telegram: ждём столько, сколько просят")
    day5 = day4.replace(day=31, hour=8)
    tg.fail_once["111"] = bot.TelegramError(429, "Too Many Requests", retry_after=1)
    run(tg, repo, ["--if-due"], day5)
    check("после паузы доставлено", len(tg.delivered["111"]) == 5)

    print("\n[10] Заблокировавший бота вычёркивается и не возвращается")
    tg.blocked.add("333")
    day6 = day5.replace(month=9, day=1, hour=8)
    run(tg, repo, ["--if-due"], day6)
    state = repo.read()
    check("333 вычеркнут", "333" not in state["subscribers"], str(state["subscribers"]))
    check("надгробие на месте", "333" in (state.get("removed") or {}))
    check("остальные получили", len(tg.delivered["111"]) == 6)
    for _ in range(3):
        run(tg, repo, ["--poll"], day6)
    check("и после слияний не вернулся", "333" not in repo.read()["subscribers"])

    print("\n[11] Возврат подписки через /start")
    tg.user_says(222, "/start")
    run(tg, repo, ["--poll"], day6)
    state = repo.read()
    check("222 снова в списке", "222" in state["subscribers"])
    check("надгробие снято", "222" not in (state.get("removed") or {}))

    print("\n[12] Ручная отправка отмечает день — двойной сводки не будет")
    day7 = day6.replace(day=2, hour=7)
    before = len(tg.delivered["111"])
    run(tg, repo, [], day7)                       # «Run workflow» в 07:00
    check("ручной запуск разослал", len(tg.delivered["111"]) == before + 1)
    check("день отмечен", repo.read().get("last_digest") == "2026-09-02")
    run(tg, repo, ["--if-due"], day7.replace(hour=8))
    check("в 08:00 повтора нет", len(tg.delivered["111"]) == before + 1)

    print("\n[13] Отписка и возврат много раз подряд")
    for i in range(3):
        tg.user_says(222, "/stop")
        run(tg, repo, ["--poll"], day6)
        check(f"цикл {i + 1}: отписался", "222" not in repo.read()["subscribers"])
        tg.user_says(222, "/start")
        run(tg, repo, ["--poll"], day6)
        check(f"цикл {i + 1}: вернулся", "222" in repo.read()["subscribers"],
              str(repo.read()))

    print("\n[13a] Испорченная запись подписчика не лишает сводки остальных")
    state = repo.read()
    state["subscribers"]["777"] = None          # как будто файл правили руками
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    day8 = day6.replace(day=3, hour=8)
    before = len(tg.delivered["111"])
    code = run(tg, repo, ["--if-due"], day8)
    check("прогон не упал", code == 0, f"код {code}")
    check("остальные получили сводку", len(tg.delivered["111"]) == before + 1)

    print("\n[13b] Сбой Telegram при опросе не отменяет рассылку дня")
    day9 = day8.replace(day=4, hour=8)

    class Flaky(Telegram):
        def __call__(self, token, method, payload, retries=2):
            if method == "getUpdates" and "allowed_updates" in payload:
                raise bot.TelegramError(502, "Bad Gateway")
            return Telegram.__call__(self, token, method, payload, retries)

    flaky = Flaky()
    flaky.delivered = tg.delivered
    before = len(tg.delivered["111"])
    code = run(flaky, repo, ["--poll"], day9)
    check("шаг опроса не упал", code == 0, f"код {code}")
    run(tg, repo, ["--if-due"], day9)
    check("сводка всё равно ушла", len(tg.delivered["111"]) == before + 1)

    print("\n[14] Файл, испорченный руками, не валит бота")
    # отдельный файл: портить рабочий репозиторий нельзя, дальше идут ещё проверки
    junk = tempfile.mktemp(suffix=".json")
    with open(junk, "w", encoding="utf-8") as fh:
        json.dump({"offset": "мусор", "rev": None,
                   "subscribers": {"не-число": {"lang": "ru"}, "555": "строка вместо записи"},
                   "removed": "вообще не словарь",
                   "last_digest": 20260902}, fh, ensure_ascii=False)
    try:
        bot.save_state(bot.load_state(junk), junk)
        check("save_state пережил мусор", True)
    except Exception as exc:  # noqa: BLE001
        check("save_state пережил мусор", False, repr(exc))
    try:
        merged = bot.merge_state(bot.load_state(junk), repo.read())
        check("merge_state пережил мусор", isinstance(merged.get("offset"), int),
              str(merged)[:120])
    except Exception as exc:  # noqa: BLE001
        check("merge_state пережил мусор", False, repr(exc))
    try:
        bot.SUBSCRIBERS_FILE = junk
        bot.tbilisi_now = lambda: day6
        bot.tg_api = tg
        code = bot.main(["--poll"])
        check("--poll пережил мусор", code == 0, f"код {code}")
    except Exception as exc:  # noqa: BLE001
        check("--poll пережил мусор", False, repr(exc))
    os.remove(junk)

    print("\n[14a] Порядок «бронь → коммит → отправка», как в workflow")

    def workflow_day(tg, repo, now, commit_ok=True):
        """Повторяет шаги workflow целиком, включая возможный отказ push."""
        work = tempfile.mktemp(suffix=".json")
        with open(work, "w", encoding="utf-8") as fh:
            json.dump(repo.read(), fh, ensure_ascii=False)
        bot.tg_api = tg
        bot.fetch = fake_feeds
        bot.SEND_PAUSE = 0
        bot.SUBSCRIBERS_FILE = work
        bot.tbilisi_now = lambda: now
        bot.build_message = lambda lang=bot.DEFAULT_LANG, now=None: f"СВОДКА[{lang}]"

        claim = bot.main(["--claim-day"])
        saved = False
        if commit_ok:                      # шаг «Записать бронь в репозиторий»
            merged = bot.merge_state(bot.load_state(work), repo.read())
            bot.save_state(merged, repo.path)
            with open(work, "w", encoding="utf-8") as fh:
                json.dump(repo.read(), fh, ensure_ascii=False)
            saved = True
        if claim == 0 and saved:           # шаг «Разослать сводку»
            bot.main(["--send-now"])
            merged = bot.merge_state(bot.load_state(work), repo.read())
            bot.save_state(merged, repo.path)
        os.remove(work)
        return claim

    dayA = day6.replace(day=5, hour=8)
    before = len(tg.delivered["111"])
    check("бронь взята", workflow_day(tg, repo, dayA) == 0)
    check("сводка ушла один раз", len(tg.delivered["111"]) == before + 1)
    for _ in range(5):
        workflow_day(tg, repo, dayA)
    check("повторные прогоны молчат", len(tg.delivered["111"]) == before + 1,
          str(len(tg.delivered["111"]) - before))

    print("\n[14b] Если бронь не записалась — сводка не уходит вовсе")
    dayB = dayA.replace(day=6)
    before = len(tg.delivered["111"])
    for _ in range(20):                    # push не проходит весь день
        workflow_day(tg, repo, dayB, commit_ok=False)
    check("ни одной рассылки вместо лавины", len(tg.delivered["111"]) == before,
          f"ушло {len(tg.delivered['111']) - before}")
    check("день так и не отмечен", repo.read().get("last_digest") != "2026-09-06")
    check("как только push проходит — сводка уходит",
          workflow_day(tg, repo, dayB) == 0 and len(tg.delivered["111"]) == before + 1)

    print("\n[14c] Непрошедший коммит не теряет подписку")

    def poll_no_commit(tg, repo, now):
        """Прогон --poll, у которого push не прошёл: коммита и подтверждения нет."""
        work = tempfile.mktemp(suffix=".json")
        with open(work, "w", encoding="utf-8") as fh:
            json.dump(repo.read(), fh, ensure_ascii=False)
        bot.tg_api = tg
        bot.SUBSCRIBERS_FILE = work
        bot.tbilisi_now = lambda: now
        bot.main(["--poll"])
        os.remove(work)          # раннер уничтожен, изменения потеряны

    dayC = dayB.replace(day=7)
    tg.user_says(888, "/start")
    poll_no_commit(tg, repo, dayC)
    check("подписчик пока не сохранён", "888" not in repo.read()["subscribers"])
    # confirmed_upto — это «следующий ожидаемый id», поэтому сравнение нестрогое
    check("но апдейт остался у Telegram — не подтверждали",
          any(u["update_id"] >= tg.confirmed_upto for u in tg.queue),
          f"очередь {len(tg.queue)}, подтверждено до {tg.confirmed_upto}")
    run(tg, repo, ["--poll"], dayC)      # исправный прогон
    check("следующий прогон подобрал подписчика", "888" in repo.read()["subscribers"],
          str(list(repo.read()["subscribers"])))

    print("\n[15] Очередь Telegram подтверждается, апдейты не обрабатываются дважды")
    check("подтверждение доходит до Telegram", tg.confirmed_upto > 0)
    check("очередь пуста", not tg.queue, f"осталось {len(tg.queue)}")
    # 111 писал боту с грузинским клиентом, поэтому приветствие у него грузинское —
    # общий признак у обеих версий только эмодзи
    hellos = [r for r in tg.replies.get("111", []) if "👋" in r]
    check("приветствие пришло ровно один раз", len(hellos) == 1, str(len(hellos)))
    check("приветствие было на грузинском",
          any("გამოწერილი" in r for r in tg.replies.get("111", [])))

    print("\n[16] Все режимы переживают недоступный Telegram")
    dead_path = tempfile.mktemp(suffix=".json")
    with open(dead_path, "w", encoding="utf-8") as fh:
        json.dump(repo.read(), fh, ensure_ascii=False)

    def dead(token, method, payload, retries=2):
        raise bot.TelegramError(0, "URLError: сеть недоступна")

    bot.tg_api = dead
    bot.SUBSCRIBERS_FILE = dead_path
    bot.tbilisi_now = lambda: day6.replace(day=9, hour=8)
    bot.fetch = lambda url, **kw: (_ for _ in ()).throw(OSError("сеть недоступна"))
    football._cache.clear()
    football._get = lambda url, token: (_ for _ in ()).throw(OSError("нет сети"))
    if hasattr(bot, "build_message_orig"):
        bot.build_message = bot.build_message_orig

    for mode in (["--poll"], ["--confirm"], ["--claim-day"], ["--send-now"],
                 ["--if-due"], ["--whoami"], ["--drain"], ["--dry-run"],
                 ["--merge", dead_path], []):
        label = " ".join(mode) or "(без флагов)"
        try:
            code = bot.main(mode)
            check(f"{label}: код {code}, без трейсбека", isinstance(code, int))
        except Exception as exc:  # noqa: BLE001
            check(f"{label}: без трейсбека", False, repr(exc))
    os.remove(dead_path)

    print()
    if problems:
        print("ПРОБЛЕМЫ:")
        for pr in problems:
            print("  -", pr)
        return 1
    print("Сквозной прогон пройден полностью.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
