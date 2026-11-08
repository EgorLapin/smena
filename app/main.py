"""Точка входа приложения «СменаПринята».

Запуск для разработки:
    uvicorn app.main:app --reload
На VPS — systemd unit с одним воркером uvicorn (ТЗ, раздел «Развёртывание»).
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from .config import Settings, get_settings
from .db import init_db, init_engine
from .errors import AppError
from .render import STATIC_DIR, render, templates
from .routes import auth, handover, journal, overview, pages, snapshot, staff
from .security import new_csrf_token


def create_app(settings: Settings | None = None) -> FastAPI:
    """Фабрика приложения (нужна тестам с временной БД, D-004)."""
    settings = settings or get_settings()
    init_engine(settings)
    init_db()  # схема с нуля: таблицы, индексы, CHECK, триггеры (T02)

    app = FastAPI(
        title="СменаПринята",
        description="Журнал передачи смены для мини-отелей",
        docs_url=None,   # учебный MVP: публичных API-схем не публикуем (D-005)
        redoc_url=None,
    )
    app.state.settings = settings

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(auth.router)
    app.include_router(journal.router)
    app.include_router(handover.router)
    app.include_router(snapshot.router)
    app.include_router(overview.router)
    app.include_router(pages.router)
    app.include_router(staff.router)

    # --- CSRF-cookie: токен есть на каждый запрос (для скрытых полей форм) ------

    @app.middleware("http")
    async def csrf_cookie_middleware(request: Request, call_next):
        token = request.cookies.get("smena_csrf") or new_csrf_token()
        request.state.csrf_token = token
        request.state.user = None  # перезапишет current_user, если пользователь есть
        request.state.user_view = None  # снимок для шаблонов (deps.set_user_view)
        response = await call_next(request)
        if "smena_csrf" not in request.cookies:
            from .security import set_csrf_cookie

            set_csrf_cookie(response, token)
        return response

    # --- Обработчики бизнес-ошибок (контракт ТЗ, «Спецификация контрактов») ----

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        from fastapi.responses import RedirectResponse

        if exc.code == "AUTH_REQUIRED":
            # 303 на /login без сообщения (таблица ошибок)
            return RedirectResponse("/login", status_code=303)

        if exc.redirect is not None:
            # POST-ошибки 403/409: 303 на страницу-источник с красным flash и кодом
            from .deps import flash_set

            response = RedirectResponse(exc.redirect, status_code=303)
            flash_set(response, request.app.state.settings, "error", exc.text, exc.code)
            return response

        # GET-ошибки (403, 404, 409…) — страница ошибки по таблице (Q-002)
        return render(
            request, "error.html",
            {"page_title": "Ошибка", "code": exc.code, "text": exc.text},
            status=exc.http_status,
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        # INTERNAL_ERROR (500): «Запись не сохранена…» — общий текст по таблице
        from .messages_ru import error_text

        return render(
            request, "error.html",
            {"page_title": "Ошибка", "code": "INTERNAL_ERROR",
             "text": error_text("INTERNAL_ERROR")},
            status=500,
        )

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        """Проверка живости для Caddy/systemd (D-006; без БД и сессий)."""
        return {"status": "ok"}

    return app


app = create_app()
