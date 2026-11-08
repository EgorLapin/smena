"""Общие фикстуры pytest.

Конвенция для всех задач: каждый тест получает приложение с изолированной
временной БД (файл в tmp_path), поэтому тесты не зависят друг от друга
и не трогают боевую базу.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    """TestClient с приложением на временной БД в tmp_path."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("DEBUG", "1")

    # Импортируем и инициализируем ПОСЛЕ подмены окружения;
    # reset_engine гарантирует, что предыдущий движок не переиспользуется.
    from app import db
    from app.config import get_settings
    from app.main import create_app

    db.reset_engine()
    app = create_app(get_settings())
    with TestClient(app) as test_client:
        yield test_client
    db.reset_engine()
