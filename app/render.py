"""Единый рендеринг HTML: Jinja2-окружение + flash + контекст.

render() подставляет flash-сообщение (списанное из подписанной cookie) и
чистит flash-cookie; csrf-токен шаблоны берут из request.state.csrf_token
(его гарантирует middleware в main.create_app).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .deps import FLASH_COOKIE, flash_pop

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def render(
    request: Request, template: str, context: dict | None = None, *, status: int = 200
) -> HTMLResponse:
    response = templates.TemplateResponse(
        request, template, {"flash": flash_pop(request), **(context or {})},
        status_code=status,
    )
    if getattr(request.state, "flash_consumed", False):
        response.delete_cookie(FLASH_COOKIE, path="/")
    return response
