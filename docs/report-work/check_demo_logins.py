# Проверка для отчёта: живы ли демо-логины seed_demo на проде (только чтение).
# Не пишет в БД: логин + GET страниц.
import http.cookiejar
import re
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://smena.chickenkiller.com"


def make_opener():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def csrf_of(opener):
    with opener.open(BASE + "/login", timeout=25) as r:
        html = r.read().decode("utf-8")
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return m.group(1) if m else None


def login(opener, login_name, password):
    tok = csrf_of(opener)
    data = urllib.parse.urlencode(
        {"login": login_name, "password": password, "csrf_token": tok}
    ).encode()
    req = urllib.request.Request(BASE + "/login", data=data)
    try:
        with opener.open(req, timeout=25) as r:
            return r.geturl(), r.status
    except urllib.error.HTTPError as e:
        return "/login", e.code


def probe(opener, path):
    try:
        with opener.open(BASE + path, timeout=25) as r:
            body = r.read().decode("utf-8")
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S)
        return r.status, (h1.group(1).strip() if h1 else "?")
    except urllib.error.HTTPError as e:
        return e.code, "-"


for who in ("demo-anna", "demo-ivan", "demo-manager"):
    op = make_opener()
    url, status = login(op, who, "demo-pass-123")
    print(f"{who:13s} login -> HTTP {status} at {url}")
    for p in ("/journal", "/overview", "/history", "/staff"):
        code, h1 = probe(op, p)
        print(f"    GET {p:10s} -> {code} {h1}")
