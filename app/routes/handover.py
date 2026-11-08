"""Сдача смены: страница проверки списка и POST-сдача (FR-2.1–2.3, AL-2, DF-4).

GET  /shifts/{id}/handover — ADMIN, владелец смены: список открытых вопросов
     (их получит следующая смена) и решённых (не переносятся); текст «После
     сдачи смена станет доступна только для чтения». Если передача уже есть —
     303 на снимок (идемпотентность GET, FR-2.3).
POST /shifts/{id}/handover — сдать: 303 на /handovers/{handover_id}; id
     передачи известен только после фиксации транзакции (AL-2).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from ..config import Settings
from ..db import get_db
from ..deps import flash_set, get_settings_dep, require_admin
from ..errors import AppError
from ..render import render
from ..security import check_csrf
from ..services import journal as j
from ..services.handover import accept_handover, handover_for_shift, handover_shift
from ..times import plural

router = APIRouter()


@router.get("/shifts/{shift_id}/handover", response_class=HTMLResponse)
def handover_check_page(
    request: Request,
    shift_id: int,
    session: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    shift = session.get(models.Shift, shift_id)
    # AL-2: сдаётся только своя смена своего отеля; чужая — NOT_FOUND.
    if shift is None or shift.hotel_id != admin.hotel_id or shift.admin_id != admin.id:
        raise AppError("NOT_FOUND")
    existing = handover_for_shift(session, admin, shift.id)
    if existing is not None:
        return RedirectResponse(f"/handovers/{existing.id}", status_code=303)
    if shift.status != models.ShiftStatus.OPEN:
        raise AppError("NOT_FOUND")

    return render(
        request, "handover_check.html",
        {
            "page_title": "Сдача смены",
            "shift": shift,
            "open_issues": j.journal_open_issues(session, shift.id),
            "closed_here": j.journal_closed_here(session, shift.id),
        },
    )


@router.post("/shifts/{shift_id}/handover")
def handover_submit(
    request: Request,
    shift_id: int,
    session: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
    csrf_token: str = Form(""),
):
    if not check_csrf(request, csrf_token):
        raise AppError("CSRF_FAILED")
    handover, _created = handover_shift(session, admin, shift_id)
    # 303 на снимок по id ПЕРЕДАЧИ (не смены) — FR маршрутной таблицы ТЗ.
    return RedirectResponse(f"/handovers/{handover.id}", status_code=303)


@router.post("/handovers/{handover_id}/accept")
def accept_submit(
    request: Request,
    handover_id: int,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    admin: models.User = Depends(require_admin),
    csrf_token: str = Form(""),
):
    if not check_csrf(request, csrf_token):
        raise AppError("CSRF_FAILED")
    result = accept_handover(session, admin, handover_id)
    redirect = RedirectResponse("/journal", status_code=303)
    if result.created:
        # FR-2.5: сообщение с числом перенесённых вопросов (FR-2.7 про параллельность
        # обслуживает транзакция IMMEDIATE — второй приём уйдёт в ALREADY_ACCEPTED).
        flash_set(redirect, settings, "ok",
                  f"Смена принята, перенесено {result.carried} "
                  f"{plural(result.carried, 'вопрос', 'вопроса', 'вопросов')}")
    else:
        # Повтор тем же пользователем: состояние не меняется (FR-2.7).
        flash_set(redirect, settings, "ok", "Вы уже приняли эту смену")
    return redirect
