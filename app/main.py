"""Точка входа приложения «СменаПринята».

Запуск для разработки:
    uvicorn app.main:app --reload
На VPS — systemd unit c одним воркером uvicorn (ТЗ, раздел «Развёртывание»).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings, get_settings
from .db import init_db, init_engine
from .routes import pages

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app(settings: Settings | None = None) -> FastAPI:
    """Фабрика приложения (нужна тестам с временной БД)."""
    settings = settings or get_settings()
    init_engine(settings)
    init_db()  # схема с нуля: таблицы, индексы, CHECK, триггеры (T02)

    app = FastAPI(
        title="СменаПринята",
        description="Журнал передачи смены для мини-отелей",
        docs_url=None,   # учебный MVP: публичных API-схем не публикуем
        redoc_url=None,
    )
    app.state.settings = settings

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(pages.router)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        """Проверка живости для Caddy/systemd (без БД и сессий)."""
        return {"status": "ok"}

    return app


app = create_app()
