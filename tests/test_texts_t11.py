"""T11 — тексты и экраны (NFR-6, AC-13 без визуальной части).

Проверяется автотестами: у каждого кода ошибки есть русский текст; страницы
отдаются с viewport; кнопки/поля ≥44 px и медиа-запрос 360 px в CSS; JS
блокирует кнопку; flash показывает и текст, и латинский код. Визуальная
проверка 360 px без горизонтальной прокрутки — руками (AC-13, чек-лист T14).
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from web import (
    create_admin_via_db,
    create_issue,
    db_scalar,
    get_csrf,
    login,
    open_shift,
    register_hotel,
)

BASE = Path(__file__).resolve().parent.parent / "app"

# --- Тексты (messages_ru) ---------------------------------------------------------


def test_every_error_code_has_russian_text():
    from app.messages_ru import ERROR_TEXTS, error_text

    assert "UPSTREAM_DOWN" in ERROR_TEXTS  # текст для страницы Caddy (502)
    for code, (text, status) in ERROR_TEXTS.items():
        if code == "AUTH_REQUIRED":
            continue  # по контракту — редирект без сообщения
        assert text.strip(), code
        assert re.search(r"[а-яА-Я]", text), f"{code}: текст не по-русски"
        assert status in (401, 403, 404, 409, 422, 500, 502)
    # параметризованный текст подставляется
    assert error_text("ALREADY_ACCEPTED", name="Иван", time="01.11.2026 09:03") == \
        "Смена уже принята: Иван, 01.11.2026 09:03"


def test_page_error_displays_text_and_code(client):
    register_hotel(client)
    create_admin_via_db(client, "anna", "Анна")
    client.cookies.clear()
    login(client, "anna")
    resp = client.get("/overview")
    assert resp.status_code == 403
    assert "Это действие вам недоступно" in resp.text  # текст
    assert "FORBIDDEN" in resp.text                    # код (латиницей, мелким шрифтом)


def test_flash_shows_text_and_code(client):
    register_hotel(client)
    client.cookies.clear()
    login(client, "manager")
    # 11-я учётка не нужна — ловим более дешёвый ACCOUNT-независимый flash:
    # вход выключенного сотрудника не подойдёт; берём FORBIDDEN-POST
    resp = client.post("/shifts/open",
                       data={"csrf_token": get_csrf(client, "/overview")},
                       follow_redirects=False)
    assert resp.status_code == 303
    page = client.get("/overview").text
    assert "Это действие вам недоступно" in page
    assert "FORBIDDEN" in page


# --- 502.html (Caddy, UPSTREAM_DOWN) -----------------------------------------------


def test_502_page_matches_tz_text():
    html = (BASE.parent / "deploy" / "502.html").read_text(encoding="utf-8")
    assert "Запись не сохранена" in html
    assert "UPSTREAM_DOWN" in html
    assert '<html lang="ru">' in html and "viewport" in html


# --- Мобильная вёрстка и NFR-6 --------------------------------------------------------


def test_all_pages_have_viewport_meta():
    base = (BASE / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in base


def test_css_has_44px_targets_and_mobile_breakpoint():
    css = (BASE / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert "min-height: 44px" in css
    assert "@media (max-width: 560px)" in css       # 360 px попадает
    assert "overflow-wrap: anywhere" in css          # нет горизонтали от длинных слов
    # фиксированных ширинок, превышающих 360px, в layout нет
    for match in re.finditer(r"min-width:\s*(\d+)px", css):
        assert int(match.group(1)) <= 360, match.group(0)


def test_js_blocks_submit_button():
    js = (BASE / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "disabled = true" in js


def test_status_text_not_only_color():
    """NFR-6: статусы обозначены текстом, не только цветом."""
    journal_tpl = (BASE / "templates" / "journal.html").read_text(encoding="utf-8")
    snapshot = (BASE / "templates" / "handover_snapshot.html").read_text(encoding="utf-8")
    assert "Открыт" in journal_tpl or "Решено в этой смене" in journal_tpl
    assert "Ожидает приёма" in snapshot and "Принята" in snapshot


# --- Все экраны живые и отдают 200 для своей роли --------------------------------------


def test_all_screens_render(client):
    register_hotel(client)
    create_admin_via_db(client, "anna", "Анна")
    client.cookies.clear()
    login(client, "anna")
    open_shift(client)
    create_issue(client, place="Комната 3", description="Проверка экрана")
    issue_id = int(db_scalar("SELECT id FROM issue"))
    client.post(f"/issues/{issue_id}/edit",
                data={"place": "Комната 3", "description": "Проверка экрана",
                      "next_action": "",
                      "csrf_token": get_csrf(client, f"/issues/{issue_id}/edit")},
                follow_redirects=False)
    client.post(f"/shifts/1/handover",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)

    for url in ("/journal", f"/issues/{issue_id}", f"/issues/{issue_id}/edit",
                "/issues/new", "/handovers/1"):
        resp = client.get(url)
        assert resp.status_code == 200, url

    client.cookies.clear()
    login(client, "manager")
    client.post("/demo/handover",
                data={"csrf_token": get_csrf(client, "/overview")},
                follow_redirects=False)
    for url in ("/overview", "/history", "/history?tab=handovers", "/staff"):
        resp = client.get(url)
        assert resp.status_code == 200, url

    # неавторизованному — редирект на /login (без паники)
    client.cookies.clear()
    resp = client.get("/journal", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/login"
    assert client.get("/login").status_code == 200
    assert client.get("/register").status_code == 200
