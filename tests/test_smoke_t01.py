"""T01 — smoke-тесты каркаса (обновлены под T03: появилась аутентификация).

Реальные тесты приёмки (AC-2…AC-8, AC-10, AC-11) добавляются вместе
с задачами T02–T14. Здесь: приложение живое, /health отвечает, статика и
базовый шаблон рендерятся, SQLite поднимается с нужными PRAGMA.
"""
from __future__ import annotations


def test_login_page_renders(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "Вход" in resp.text
    assert "<!DOCTYPE html>" in resp.text
    #csrf-поле обязательо на всех формах (NFR-2)
    assert 'name="csrf_token"' in resp.text


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_static_css_ok(client):
    resp = client.get("/static/css/app.css")
    assert resp.status_code == 200


def test_sqlite_pragmas(client):
    """WAL и busy_timeout=5000 обязаны стоять на каждом подключении (ТЗ)."""
    from app.db import get_engine

    with get_engine().connect() as conn:
        row = conn.exec_driver_sql("PRAGMA journal_mode").fetchone()
        assert str(row[0]).lower() == "wal"
        timeout = conn.exec_driver_sql("PRAGMA busy_timeout").fetchone()
        assert int(timeout[0]) == 5000
        fkeys = conn.exec_driver_sql("PRAGMA foreign_keys").fetchone()
        assert int(fkeys[0]) == 1
