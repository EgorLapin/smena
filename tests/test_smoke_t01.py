"""T01 — smoke-тесты каркаса.

Реальные тесты приёмки (AC-2…AC-8, AC-10, AC-11) добавляются вместе
с задачами T02–T14. Здесь проверяется только то, что каркас живой:
приложение собирается, Jinja2 рендерит базовый шаблон, /health отвечает,
SQLite поднимается с нужными PRAGMA (WAL, busy_timeout — ТЗ).
"""
from __future__ import annotations


def test_index_renders_jinja(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "СменаПринята" in resp.text
    assert "<!DOCTYPE html>" in resp.text


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_stub_returns_501(client):
    resp = client.get("/stub/journal")
    assert resp.status_code == 501
    assert "Раздел" in resp.text


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
