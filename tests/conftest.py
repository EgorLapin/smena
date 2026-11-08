"""Общие фикстуры pytest.

Конвенция: каждый тест получает изолированную временную БД (файл в tmp_path),
поэтому тесты не зависят друг от друга и не трогают рабочие данные.

Имена переменных окружения — по таблице «Конфигурация» ТЗ (DATABASE_PATH,
SECRET_KEY, ...); SECRET_KEY обязателен, поэтому фикстура задаёт тестовый ключ.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _apply_test_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", str((tmp_path / "test.db")))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("BACKUP_REMOTE", "")
    # Пороги ТЗ по умолчанию (12/24/2) оставляем как в ТЗ — тесты AC опираются на них.


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    """Настройки с приложением к временной БД (без FastAPI)."""
    _apply_test_env(tmp_path, monkeypatch)
    from app import db
    from app.config import get_settings

    db.reset_engine()
    s = get_settings()
    db.init_engine(s)
    db.init_db()
    yield s
    db.reset_engine()


@pytest.fixture()
def session(settings):
    """SQLAlchemy-сессия к временной БД (для тестов модели/сервисов)."""
    from app import db

    sess = db.new_session()
    try:
        yield sess
    finally:
        sess.close()


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    """TestClient с приложением на временной БД (для тестов маршрутов)."""
    _apply_test_env(tmp_path, monkeypatch)

    # Импортируем и инициализируем ПОСЛЕ подмены окружения;
    # reset_engine гарантирует, что предыдущий движок не переиспользуется.
    from app import db
    from app.config import get_settings
    from app.main import create_app

    db.reset_engine()
    app = create_app(get_settings())
    # base_url=https — как в проде (только HTTPS): иначе cookie-ядро отклонит
    # cookie с флагом Secure и CSRF-сценарии нельзя будет прогнать честно.
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client
    db.reset_engine()
