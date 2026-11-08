# -*- coding: utf-8 -*-
"""Приёмо-сдаточный прогон по живому стенду (после seed_demo.py).

    python scripts/smoke_live.py [БАЗА]     # БАЗА по умолчанию http://127.0.0.1:8000

Cookie переносятся вручную: httpx, как браузер, не шлёт Secure-cookie по http
(в браузере localhost — исключение, D-011). Проверки: вход обеих ролей, обзор
(PENDING «Просрочено», подписка, показатель), история и поиск «хол», сотрудники,
роль-маршрутизация /, ADMIN 403 на обзоре.
"""
import re
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
jar: dict[str, str] = {}


def request(method: str, path: str, **kw):
    headers = kw.pop("headers", {})
    if jar:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
    resp = httpx.request(method, BASE + path, headers=headers, **kw)
    for line in resp.headers.get_list("set-cookie"):
        pair = line.split(";")[0]
        k, _, v = pair.partition("=")
        if v:
            jar[k.strip()] = v
    return resp


def login_as(name: str) -> None:
    jar.clear()  # «другой браузер»: /login для вошедшего — 303, а не форма
    r = request("GET", "/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)
    r = request("POST", "/login", data={"login": name, "password": "demo-pass-123",
                                        "csrf_token": csrf})
    assert r.status_code == 303, (name, r.status_code)
    assert r.headers["location"] == "/", r.headers["location"]


login_as("demo-manager")

r = request("GET", "/overview")
assert r.status_code == 200
checks = {
    "Просрочено": "Просрочено" in r.text,
    "подписка": "Подписка: пилот" in r.text,
    "показатель": "Сдано 2, принято 1" in r.text,  # PENDING тоже «сдано»
    "затянувшийся вопрос": "Комната 7" in r.text,
    "возраст передачи": "прошло" in r.text,
}

r = request("GET", "/")  # роль MANAGER -> /overview
assert r.status_code == 303 and r.headers["location"] == "/overview"

r = request("GET", "/history")
checks["история: Холл"] = "Холл" in r.text

r = request("GET", "/history", params={"tab": "issues", "place": "хол"})
checks["поиск 'хол'"] = "Протечность" in r.text

r = request("GET", "/history", params={"tab": "handovers"})
checks["передачи"] = "Смена Анна Смена" in r.text

r = request("GET", "/staff")
checks["сотрудники"] = "demo-anna" in r.text

# ADMIN-путь
login_as("demo-anna")
r = request("GET", "/journal")
checks["журнал Анны"] = r.status_code == 200 and "Смена не открыта" in r.text
checks["журнал видит чужую PENDING"] = "Ожидают приёма" in r.text
r = request("GET", "/overview")
checks["ADMIN на обзоре 403"] = r.status_code == 403
r = request("GET", "/")
checks["ADMIN / -> /journal"] = (r.status_code == 303
                                 and r.headers["location"] == "/journal")

bad = [k for k, v in checks.items() if not v]
print("SMOKE:", "OK — все проверки пройдены" if not bad else f"MISS: {bad}")
sys.exit(0 if not bad else 1)
