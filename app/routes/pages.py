"""Главная (/) и ролевые перенаправления (FR-A6).

Все экраны реализованы: /journal и /handovers (T05-T07), /overview (T09),
/history (T10), /staff (T04). Файл остаётся для /, где выбирается экран по роли.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..deps import current_user
from .. import models

router = APIRouter()


@router.get("/")
def index(request: Request, user: models.User = Depends(current_user)):
    """303 на /journal (ADMIN) или /overview (MANAGER) — по ТЗ."""
    target = "/journal" if user.role == models.Role.ADMIN else "/overview"
    return RedirectResponse(target, status_code=303)
