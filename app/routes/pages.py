"""Временные (заглушечные) маршруты каркаса T01.

Реальные страницы появятся по задачам: вход — T03, журнал — T06/T07,
передачи — T08/T09. На T01 здесь только индекс и служебная 501-страница,
чтобы проверить pipeline Jinja2 → базовый шаблон → статика.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Индекс: на T01 — заглушка «проезд далее», далее — редирект в журнал."""
    from ..main import templates

    return templates.TemplateResponse(
        request,
        "index.html",
        {"page_title": "СменаПринята"},
    )


@router.get("/stub/{feature}", response_class=HTMLResponse)
def stub(request: Request, feature: str) -> HTMLResponse:
    """Заглушка для функций, которые ещё не реализованы (красивый placeholder)."""
    from ..main import templates

    return templates.TemplateResponse(
        request,
        "stub.html",
        {"page_title": "В разработке", "feature": feature},
        status_code=501,
    )
