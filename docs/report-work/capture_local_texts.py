# Локальный стенд на ВРЕМЕННОЙ базе (прод не затрагивается): точные тексты двух
# экранов, которых нет в docs/accept — «Журнал с открытой сменой» и «Сдача смены»
# (страница проверки списка). Только GET; на VPS не заходим.
import os
import re
import subprocess
import sys
import tempfile
import time

import httpx

VENV_PY = r"C:\Users\Egor\codesss\proj\Smena\.venv\Scripts\python.exe"
REPO = r"C:\Users\Egor\codesss\proj\Smena"
BASE = "http://127.0.0.1:8011"

tmpdir = tempfile.mkdtemp(prefix="smena-report-")
env = dict(os.environ)
env.update({
    "SECRET_KEY": "report-local-stand-not-a-real-secret",
    "DATABASE_PATH": os.path.join(tmpdir, "accept.db"),
    "PYTHONIOENCODING": "utf-8",
})

print("== seed_demo на временную БД", env["DATABASE_PATH"])
r = subprocess.run([VENV_PY, "scripts/seed_demo.py"], cwd=REPO, env=env,
                   capture_output=True, text=True, encoding="utf-8")
print(r.stdout[-500:] or r.stderr[-500:])

srv = subprocess.Popen(
    [VENV_PY, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
     "--port", "8011"],
    cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

jar: dict[str, str] = {}


def request(method, path, **kw):
    headers = kw.pop("headers", {})
    if jar:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
    resp = httpx.request(method, BASE + path, headers=headers, **kw)
    for line in resp.headers.get_list("set-cookie"):
        k, _, v = line.split(";")[0].partition("=")
        if v:
            jar[k.strip()] = v
    return resp


def dump(label, html):
    print(f"\n=== {label} ===")
    pat = re.compile(
        r"<(h1|h2|h3|p|button|li|strong|span)[^>]*>(.*?)</\1>", re.S)
    seen = set()
    for m in pat.finditer(html):
        t = " ".join(re.sub(r"<[^>]+>", " ", m.group(2)).split())
        if t and t not in seen and len(t) > 1:
            seen.add(t)
            print("  ", t[:200])


try:
    for _ in range(80):
        try:
            httpx.get(BASE + "/health", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise SystemExit("стенд не поднялся")

    request("GET", "/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"',
                     request("GET", "/login").text).group(1)
    r = request("POST", "/login", data={"login": "demo-ivan",
                                        "password": "demo-pass-123",
                                        "csrf_token": csrf})
    print("login demo-ivan ->", r.status_code, r.headers.get("location"))

    j = request("GET", "/journal")
    dump("ЖУРНАЛ СМЕНЫ (demo-ivan, смена OPEN)", j.text)
    m = re.search(r'href="(/shifts/(\d+)/handover)"', j.text)
    print("\nссылка на сдачу:", m.group(1) if m else "НЕ НАЙДЕНА")
    if m:
        dump("СТРАНИЦА ПРОВЕРКИ СПИСКА (СДАЧА СМЕНЫ)",
             request("GET", m.group(1)).text)
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()
    for f in os.listdir(tmpdir):
        os.remove(os.path.join(tmpdir, f))
    os.rmdir(tmpdir)
    print("\nстенд остановлен, временная база удалена")
