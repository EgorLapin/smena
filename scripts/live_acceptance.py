# -*- coding: utf-8 -*-
"""Живой прогон приёмочных сценариев AC-1…AC-8, AC-11, AC-12 по HTTP.

    python scripts/live_acceptance.py <БАЗА>          # напр. http://127.0.0.1:8011

Запускается против РАЗВЁРНУТОГО приложения на НОВЫХ тестовых отелях (slug по
времени) — существующие данные (в т.ч. seed_demo) не затрагиваются. Cookie
переносятся вручную: httpx, как браузер, не шлёт Secure-cookie по http.
Финальный отчёт: PASS/FAIL по каждой проверке; выход 1 — есть провалы.
Тексты проверок соответствуют факту приложения (flash-строки маршрутов),
а не чек-листу: это регрессионный прогон, а не слепое цитирование ТЗ.
"""
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

if hasattr(sys.stdout, "reconfigure"):  # Windows cp1251 не выводит «≤» и кириллицу
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8011").rstrip("/")
PASS, FAIL = [], []
PASSW = "night-pass-123"


def ok(name: str, cond: bool, note: str = "") -> None:
    (PASS if cond else FAIL).append(name + (f"" if cond else (f" — {note}" if note else "")))


class Client:
    """Cookie-перенос вручную + СВОИ редиректы: jar обновляется ПЕРЕД каждым
    прыжком 303 (иначе cookie с 303 не долетает до следующего запроса)."""

    def __init__(self) -> None:
        self.c = httpx.Client(timeout=30)
        self.jar: dict[str, str] = {}

    def _once(self, method, path, kw):
        headers = dict(kw.pop("headers", {}))
        if self.jar:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.jar.items())
        r = self.c.request(method, BASE + path, headers=headers, **kw)
        for line in r.headers.get_list("set-cookie"):
            k, _, rest = line.partition("=")
            # Как браузер: кавычки снимаем, пустое значение («smena_csrf=""» при
            # ротации CSRF) — удаление cookie, а не токен из двух кавычек.
            v = rest.split(";")[0].strip().strip('"')
            if v:
                self.jar[k.strip()] = v
            elif "smena" in k:
                self.jar.pop(k.strip(), None)
        return r

    def req(self, method, path, *, data=None, follow_redirects=True, **kw):
        if data is not None:
            kw["data"] = data
        for _ in range(6):
            r = self._once(method, path, kw)
            if not follow_redirects or r.status_code not in (301, 302, 303, 307):
                return r
            path = r.headers["location"]
            method, kw = "GET", {}
        return r

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, data=None, **kw):
        return self.req("POST", path, data=data, **kw)

    def csrf(self, path="/login"):
        m = re.search(r'name="csrf_token" value="([^"]+)"', self.get(path).text)
        return m.group(1) if m else None

    def login(self, name, password=PASSW):
        r = self.post("/login", data={"login": name, "password": password,
                                      "csrf_token": self.csrf()}, follow_redirects=True)
        assert r.status_code == 200, (name, r.status_code)
        return r


def flash(html: str) -> str:
    m = re.search(r'class="flash[^"]*"[^>]*>(.*?)<', html, re.S)
    return m.group(1).strip() if m else ""


def register_hotel(slug: str) -> Client:
    c = Client()
    r = c.post("/register", data={
        "hotel_name": f"Ночной тест {slug}", "timezone": "Asia/Novosibirsk",
        "display_name": f"Упр {slug}", "login": f"{slug}-mgr",
        "password": PASSW, "password_confirm": PASSW, "csrf_token": c.csrf(),
    }, follow_redirects=True)
    ok(f"FR-A1 регистрация {slug}: отель+MANAGER+вход",
       r.status_code == 200 and "/overview" in str(r.url))
    return c


def add_staff(mgr: Client, login: str, name: str) -> bool:
    r = mgr.post("/staff", data={"display_name": name, "login": login,
                                 "password": PASSW, "csrf_token": mgr.csrf("/staff")},
                 follow_redirects=True)
    return "Сотрудник добавлен" in r.text


def open_shift(admin: Client) -> None:
    r = admin.post("/shifts/open", data={"csrf_token": admin.csrf("/journal")},
                   follow_redirects=True)
    assert "Смена открыта" in r.text or "Смена открыта в" in r.text, r.status_code


def add_issue(admin: Client, place: str, desc: str, action: str = "") -> None:
    r = admin.post("/issues", data={"place": place, "description": desc,
                                    "next_action": action,
                                    "csrf_token": admin.csrf("/issues/new")},
                   follow_redirects=True)
    assert "Вопрос добавлен" in r.text, (place, flash(r.text) or r.status_code)


def issue_id(admin: Client, place: str) -> int:
    m = re.search(rf'href="/issues/(\d+)"[^>]*>{re.escape(place)}<', admin.get("/journal").text)
    assert m, place
    return int(m.group(1))


def open_shift_id(admin: Client) -> int:
    m = re.search(r'href="/shifts/(\d+)/handover"', admin.get("/journal").text)
    assert m, "нет открытой смены"
    return int(m.group(1))


def pending_ids(admin: Client) -> list[int]:
    return [int(x) for x in re.findall(r'href="/handovers/(\d+)"', admin.get("/journal").text)]


def submit(admin: Client) -> int:
    """Сдать смену, вернуть id передачи (303 на /handovers/{id})."""
    r = admin.post(f"/shifts/{open_shift_id(admin)}/handover",
                   data={"csrf_token": admin.csrf()}, follow_redirects=False)
    assert r.status_code == 303, r.status_code
    return int(r.headers["location"].split("/")[-1])


def accept(c: Client, hid: int) -> str:
    r = c.post(f"/handovers/{hid}/accept", data={"csrf_token": c.csrf("/journal")},
               follow_redirects=True)
    return flash(r.text) or f"HTTP{r.status_code}"


def main() -> None:
    slug = f"n{int(time.time()) % 1000000}"

    # === AC-12: регистрация → 2 сотрудника → демо → приём (секундомер) -----------
    t0 = time.time()
    mgr = register_hotel(slug)
    ok("AC-12: создание двух сотрудников", add_staff(mgr, f"{slug}-anna", "Анна Тест")
       and add_staff(mgr, f"{slug}-ivan", "Иван Тест"))
    r = mgr.post("/demo/handover", data={"csrf_token": mgr.csrf("/overview")},
                 follow_redirects=True)
    ok("AC-12/F-A8: демо-передача создана", "Демо-передача создана" in r.text, flash(r.text))
    ivan = Client(); ivan.login(f"{slug}-ivan")
    ids = pending_ids(ivan)
    snap = ivan.get(f"/handovers/{ids[0]}").text if ids else ""
    ok("AC-12: демо видна ADMIN, есть «Принять смену»",
       len(ids) == 1 and "Принять смену" in snap)
    f_ = accept(ivan, ids[0])
    ok("AC-12: приём демо", "Смена принята" in f_, f_)
    ok("AC-12: 2 открытых вопроса перенесены", "перенесено 2" in f_, f_)
    dur = time.time() - t0

    # === AC-1: полный цикл Анна → Иван --------------------------------------------
    anna = Client(); anna.login(f"{slug}-anna")
    open_shift(anna)
    add_issue(anna, "Кухня", "Протечность под мойкой", "Вызвать мастера")
    add_issue(anna, "Комната 12", "Гость просит дополнительный матрац")
    kitchen = issue_id(anna, "Кухня")
    room0 = issue_id(anna, "Комната 12")
    r = anna.post(f"/issues/{kitchen}/close", data={"csrf_token": anna.csrf()},
                  follow_redirects=True)
    ok("AC-1: «Решён» закрывает вопрос", "Вопрос закрыт" in r.text, flash(r.text))
    hid1 = submit(anna)
    snap1 = anna.get(f"/handovers/{hid1}").text
    ok("AC-1/2.1: снимок со всеми вопросами (откр. + решён)",
       "Комната 12" in snap1 and "Кухня" in snap1 and "Решено (1)" in snap1)

    ids = pending_ids(ivan)
    ok("AC-3: PENDING видна другому ADMIN", hid1 in ids, str(ids))
    f_ = accept(ivan, hid1)
    ok("AC-1: приём + flash с числом", "Смена принята" in f_ and "перенесено 1" in f_, f_)
    room1 = issue_id(ivan, "Комната 12")
    ok("AC-1: тот же id у принявшего", room1 == room0, f"{room0} -> {room1}")
    card = ivan.get(f"/issues/{room1}").text
    ok("AC-1: в карточке событие «Перенесён»", "Перенесён" in card)

    # AC-3: вопрос сданной смены не правится и не закрывается (OPEN из смены Анны,
    # перешедший к Ивану). У Анны к этому моменту смены нет — открываем свою,
    # иначе маршрут отдаст NO_OPEN_SHIFT раньше проверки принадлежности смены.
    open_shift(anna)
    r = anna.post(f"/issues/{room1}/edit",
                  data={"place": "Комната 12!", "description": "х", "next_action": "",
                        "csrf_token": anna.csrf()}, follow_redirects=True)
    ok("AC-3: вопрос сданной смены не правится",
       "смена сдана или вопрос в чужой смене" in r.text.lower(), flash(r.text) or "нет flash")
    r = anna.post(f"/issues/{room1}/close", data={"csrf_token": anna.csrf()},
                  follow_redirects=True)
    ok("AC-3: вопрос сданной смены не закрывается",
       "смена сдана или вопрос в чужой смене" in r.text.lower(),
       flash(r.text) or "нет flash")

    # === AC-4: правка принявшего; снимок неизменен ---------------------------------
    r = ivan.post(f"/issues/{room1}/edit",
                  data={"place": "Комната 12", "description": "Матрац привезли",
                        "next_action": "", "csrf_token": ivan.csrf()},
                  follow_redirects=True)
    ok("AC-4: EDITED в смене принявшего", "Сохранено" in r.text, flash(r.text))
    ok("AC-4: снимок передачи — прежний текст",
       "Гость просит дополнительный матрац" in ivan.get(f"/handovers/{hid1}").text)

    # === AC-2: повторная сдача идемпотентна; гонка приёма --------------------------
    open_shift(anna)
    add_issue(anna, "Холл", "Лампочка мигает")
    sid2 = open_shift_id(anna)
    tok = anna.csrf(f"/shifts/{sid2}/handover")
    r1 = anna.post(f"/shifts/{sid2}/handover", data={"csrf_token": tok},
                   follow_redirects=False)
    tok = anna.csrf("/journal")
    r2 = anna.post(f"/shifts/{sid2}/handover", data={"csrf_token": tok},
                   follow_redirects=False)
    ok("AC-2/F-2.3: двойная сдача — одна передача",
       r1.status_code == r2.status_code == 303
       and r1.headers["location"] == r2.headers["location"],
       f"{r1.headers.get('location')} vs {r2.headers.get('location')}")
    hid2 = int(r1.headers["location"].split("/")[-1])

    add_staff(mgr, f"{slug}-petr", "Пётр Тест")
    petr = Client(); petr.login(f"{slug}-petr")
    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(accept, ivan, hid2)
        fp = pool.submit(accept, petr, hid2)
        fa, fp = fa.result(), fp.result()
    winners = [f for f in (fa, fp) if "Смена принята" in f]
    losers = [f for f in (fa, fp) if "уже принята" in f]
    ok("AC-2/F-2.7: гонка — один принял, второй ALREADY_ACCEPTED",
       len(winners) == 1 and len(losers) == 1, f"{fa} | {fp}")
    who = ivan if "Смена принята" in fa else petr
    again = accept(who, hid2)
    ok("AC-2: повтор тем же — результат первого", "Вы уже приняли" in again, again)

    # === AC-5: закрытие и история (история — MANAGER) ------------------------------
    # «Комната 12» перенеслась на смену Ивана при ПРИЁМЕ hid1 (AC-1), независимо
    # от того, кто выиграл гонку за hid2 (там «Холл») — ищем и закрываем у Ивана.
    room2 = issue_id(ivan, "Комната 12")
    r = ivan.post(f"/issues/{room2}/close", data={"csrf_token": ivan.csrf()},
                  follow_redirects=True)
    ok("AC-5: закрытие перенесённого вопроса", "Вопрос закрыт" in r.text, flash(r.text))
    hist = mgr.get("/history?tab=issues&status=CLOSED").text
    ok("AC-5/S: фильтр «закрытые» (приоритет S, T15 готов)", "Комната 12" in hist)
    card = ivan.get(f"/issues/{room2}").text
    ok("AC-5: карточка — автор, хронология, «Закрыт»",
       all(s in card for s in ("Автор", "Хронология", "Закрыт")))

    # === AC-6: изоляция отелей ------------------------------------------------------
    slubb = slug + "b"
    mgr_b = register_hotel(slubb)
    add_staff(mgr_b, f"{slubb}-admin", "Админ Б")
    admin_b = Client(); admin_b.login(f"{slubb}-admin")
    ok("AC-6: GET чужого issue -> 404", mgr_b.get(f"/issues/{room2}").status_code == 404)
    ok("AC-6: GET чужого снимка -> 404", admin_b.get(f"/handovers/{hid1}").status_code == 404)
    r = admin_b.post(f"/handovers/{hid1}/accept",
                     data={"csrf_token": admin_b.csrf("/journal")}, follow_redirects=True)
    ok("AC-6: POST чужой передачи -> «Страница не найдена»",
       "не найдена" in r.text.lower(), flash(r.text))

    # === AC-7: роли -----------------------------------------------------------------
    ok("AC-7: ADMIN /overview -> 403", anna.get("/overview").status_code == 403)
    r = mgr.get("/journal")
    ok("AC-7: MANAGER /journal -> 403", r.status_code == 403 and "недоступно" in r.text)
    r = mgr.post("/shifts/open", data={"csrf_token": mgr.csrf("/overview")},
                 follow_redirects=True)
    ok("AC-7: POST смены MANAGER -> недоступно", "недоступно" in r.text.lower())
    open_shift(anna)
    add_issue(anna, "Ресепшн", "тест самоприёма")
    hid_self = submit(anna)
    f_ = accept(anna, hid_self)
    ok("AC-7: автор не принимает свою передачу", "сдали сами" in f_.lower(), f_)
    ok("AC-7: передача цела после отказа", "Ожидает приёма" in
       anna.get(f"/handovers/{hid_self}").text)

    # === AC-8: поиск (у Ивана открытая смена после приёма hid1) ----------------------
    if "Сдать смену" not in ivan.get("/journal").text:
        open_shift(ivan)
    add_issue(ivan, "Кухня-Ёлка", "регистр и ё")
    add_issue(ivan, "Весь инвентарь", "пересчёт")
    ok("AC-8: «кух» находит «Кухня»", "Кухня" in
       mgr.get("/history?tab=issues&place=кух").text)
    ok("AC-8: «вёс» находит «Весь» (ё=е)", "Весь инвентарь" in
       mgr.get("/history?tab=issues&place=вёс").text)
    ok("AC-8: «КУХ» без учёта регистра", "Кухня" in
       mgr.get("/history?tab=issues&place=КУХ").text)
    # «Сегодня» — в поясе отеля (Asia/Novosibirsk = UTC+7, без перехода на лето):
    # на VPS машинное время UTC, и time.strftime дал бы вчерашнюю дату ночью.
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone(timedelta(hours=7))).strftime("%Y-%m-%d")
    ok("AC-8: сегодняшние события в границах дня", "Весь инвентарь" in
       mgr.get(f"/history?tab=issues&date_from={today}&date_to={today}").text)
    ok("AC-8: пустое окно дат — пусто", "Весь инвентарь" not in
       mgr.get("/history?tab=issues&date_from=2020-01-01&date_to=2020-01-02").text)

    # === AC-11: лимит 10 записей (в отеле B уже 2: mgr+admin) ------------------------
    made = 0
    for i in range(8):
        if add_staff(mgr_b, f"{slubb}-s{i}", f"Сотр {i}"):
            made += 1
    ok("AC-11: 10-я запись прошла", made == 8, str(made))
    r = mgr_b.post("/staff", data={"display_name": "Одиннадцатый",
                                   "login": f"{slubb}-s9", "password": PASSW,
                                   "csrf_token": mgr_b.csrf("/staff")},
                   follow_redirects=True)
    ok("AC-11/F-A3: 11-я запись -> лимит", "лимит" in r.text.lower(), flash(r.text))

    print(f"\nAC-12 путь скриптом: {dur:.1f} с (норматив человека ≤ 15 мин)")
    print(f"SLUG={slug} (тестовые отели: «Ночной тест {slug}», «Ночной тест {slug}b»)")
    print(f"PASS: {len(PASS)}   FAIL: {len(FAIL)}")
    for f in FAIL:
        print("  FAIL:", f)
    sys.exit(1 if FAIL else 0)


main()
