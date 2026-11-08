"""Главная (/) и временные страницы, защищённые по ролям (FR-A6).

Реальные экраны появятся по задачам: журнал — T05, сдача — T06, приём — T07,
обзор/история/сотрудники — T04/T09/T10. Уже здесь GET-защита ролей даёт
AC-7 (часть): ADMIN на /overview|/history|/staff|/demo-страницы → 403 FORBIDDEN,
MANAGER на /journal → 403 FORBIDDEN. Это позволяет проверить матрицу прав
до написания самих экранов.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..deps import current_user, require_admin, require_manager
from .. import models

router = APIRouter()

# Экраны, пришедшие в ТЗ позже T03: временно заглушка, но уже под ролью.
# /journal реализован в T05 (routes/journal.py), /staff — в T04 (routes/staff.py).
_PLACEHOLDERS = {
    "overview": ("Обзор управляющего", "T09"),
    "history": ("История", "T10"),
}


@router.get("/", response_class=HTMLResponse)
def index(request: Request, user: models.User = Depends(current_user)):
    """303 на /journal (ADMIN) или /overview (MANAGER) — по ТЗ."""
    target = "/journal" if user.role == models.Role.ADMIN else "/overview"
    return RedirectResponse(target, status_code=303)


def _placeholder(request: Request, title: str, task: str) -> HTMLResponse:
    from ..render import render

    return render(
        request, "stub.html",
        {"page_title": title, "feature": title, "task": task},
    )


@router.get("/overview", response_class=HTMLResponse)
def overview(request: Request, user: models.User = Depends(require_manager)):
    return _placeholder(request, _PLACEHOLDERS["overview"][0], _PLACEHOLDERS["overview"][1])


@router.get("/history", response_class=HTMLResponse)
def history(request: Request, user: models.User = Depends(require_manager)):
    return _placeholder(request, _PLACEHOLDERS["history"][0], _PLACEHOLDERS["history"][1])
