"""Обзор управляющего (DF-6; FR-3.1 — FR-3.3, FR-A7, FR-A8).

GET  /overview       — строка подписки; блок «Непринятые смены» (возраст,
                       «Просрочено», «Демо»); «Затянувшиеся вопросы» со ссылкой
                       на карточку; показатель принятия за 30 дней; кнопка
                       «Создать демо-передачу».
POST /demo/handover  — создать демо-передачу (303 на /overview; DEMO_EXISTS).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from ..config import Settings
from ..db import get_db
from ..deps import flash_set, get_settings_dep, require_manager
from ..errors import AppError
from ..render import render
from ..security import check_csrf
from ..services import overview as ov
from ..services.journal import open_issue_count
from ..times import fmt_dt, humanize_age

router = APIRouter()


@router.get("/overview", response_class=HTMLResponse)
def overview_page(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    manager: models.User = Depends(require_manager),
):
    hotel = session.get(models.Hotel, manager.hotel_id)
    assert hotel is not None
    tz = hotel.timezone
    now = models.utcnow()

    pending = []
    for handover, sender in ov.pending_for_overview(session, manager.hotel_id):
        shift = session.get(models.Shift, handover.shift_id)
        age_hours = (now - handover.sent_at).total_seconds() / 3600
        pending.append({
            "handover": handover,
            "sender_name": sender.display_name,
            "sent_local": fmt_dt(handover.sent_at, tz),
            "open_count": open_issue_count(session, handover.shift_id),
            "age": humanize_age(handover.sent_at, now),
            # FR-3.1: демо-смена никогда не помечается «Просрочено» (FR-A8)
            "overdue": bool(not shift.is_demo
                            and age_hours >= settings.pending_overdue_hours),
            "is_demo": bool(shift.is_demo),
        })

    stale = []
    for issue, current_admin in ov.stale_issues(session, manager.hotel_id,
                                                settings, now):
        stale.append({
            "issue": issue,
            "admin_name": current_admin.display_name,
            "age": humanize_age(issue.created_at, now),
        })

    handed, accepted = ov.acceptance_stats(session, manager.hotel_id, now)
    share = f"{round(accepted * 100 / handed)} %" if handed else ""

    return render(
        request, "overview.html",
        {
            "page_title": "Обзор",
            "subscription": ov.subscription_line(hotel, now),
            "pending": pending,
            "stale": stale,
            "handed": handed,
            "accepted": accepted,
            "share": share,
        },
    )


@router.post("/demo/handover")
def demo_handover_submit(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    manager: models.User = Depends(require_manager),
    csrf_token: str = Form(""),
):
    if not check_csrf(request, csrf_token):
        raise AppError("CSRF_FAILED")
    ov.create_demo_handover(session, manager)
    redirect = RedirectResponse("/overview", status_code=303)
    flash_set(redirect, settings, "ok",
              "Демо-передача создана — примите её любой учётной записью ADMIN")
    return redirect
