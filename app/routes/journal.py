"""Маршруты журнала (DF-3, DF-4; экраны «Журнал смены», «Форма вопроса»,
«Карточка вопроса»).

GET  /journal                — журнал: ожидающие приёма, открытые, «решено в этой
                               смене», кнопки (FR-1.5)
POST /shifts/open            — открыть смену (FR-1.1; повтор безопасен)
GET  /issues/new             — форма вопроса (FR-A9 — предупреждение о ПД)
POST /issues                 — создать вопрос (FR-1.2; NO_OPEN_SHIFT 409-flash)
GET  /issues/{id}            — карточка вопроса с хронологией (FR-3.6)
GET  /issues/{id}/edit       — форма правки (FR-1.3)
POST /issues/{id}/edit       — сохранить правку (AL-4)
POST /issues/{id}/close      — «Решён» одним нажатием (FR-1.4/FR-1.6)

POST-ошибки — 303 + flash с кодом (контракт ТЗ), ошибки формы — 422 со
значениями в полях; «Сохранено»/«Вопрос добавлен» — green flash после
фиксации транзакции (FR-1.7).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from ..config import Settings
from ..db import get_db
from ..deps import current_user, flash_set, get_settings_dep, require_admin
from ..errors import AppError
from ..render import render
from ..security import check_csrf
from ..services import journal as j
from ..times import fmt_dt, fmt_time, humanize_age, plural_carries

router = APIRouter()


def _hotel_tz(user: models.User, session: Session) -> str:
    hotel = session.get(models.Hotel, user.hotel_id)
    assert hotel is not None
    return hotel.timezone


# --- Журнал -------------------------------------------------------------------------


@router.get("/journal", response_class=HTMLResponse)
def journal_page(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    admin: models.User = Depends(require_admin),
):
    shift = j.get_open_shift(session, admin.id)
    tz = _hotel_tz(admin, session)
    now = models.utcnow()

    pending = []
    for handover, sender in j.pending_handovers(session, admin.hotel_id,
                                                exclude_user_id=admin.id):
        pending.append({
            "handover": handover,
            "sender": sender,
            "sent_local": fmt_time(handover.sent_at, tz),
            "open_count": j.open_issue_count(session, handover.shift_id),
            "age": humanize_age(handover.sent_at, now),
        })

    context = {
        "page_title": "Журнал смены",
        "shift": shift,
        "opened_time": fmt_time(shift.opened_at, tz) if shift else "",
        "tz": tz,
        "pending": pending,
        "open_issues": j.journal_open_issues(session, shift.id) if shift else [],
        "closed_here": j.journal_closed_here(session, shift.id) if shift else [],
        "plural_carries": plural_carries,
    }
    return render(request, "journal.html", context)


@router.post("/shifts/open")
def shift_open(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    admin: models.User = Depends(require_admin),
    csrf_token: str = Form(""),
):
    if not check_csrf(request, csrf_token):
        raise AppError("CSRF_FAILED")
    _, created = j.open_shift(session, admin.hotel_id, admin.id)
    redirect = RedirectResponse("/journal", status_code=303)
    if created:
        flash_set(redirect, settings, "ok", "Смена открыта")
    return redirect


# --- Форма и создание вопроса ----------------------------------------------------------


def _issue_form_page(request: Request, *, action: str, values: dict,
                     field_errors: dict | None = None, title: str = "Новый вопрос",
                     status: int = 200, server_error: bool = False) -> HTMLResponse:
    return render(
        request, "issue_form.html",
        {"page_title": title, "action": action, "values": values,
         "field_errors": field_errors or {}, "server_error": server_error},
        status=status,
    )


@router.get("/issues/new", response_class=HTMLResponse)
def issue_new_form(request: Request, admin: models.User = Depends(require_admin)):
    return _issue_form_page(request, action="/issues", values={})


@router.post("/issues")
def issue_create(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    admin: models.User = Depends(require_admin),
    place: str = Form(""),
    description: str = Form(""),
    next_action: str = Form(""),
    csrf_token: str = Form(""),
):
    if not check_csrf(request, csrf_token):
        raise AppError("CSRF_FAILED")
    values = {"place": place, "description": description, "next_action": next_action}
    try:
        j.create_issue(session, admin, place=place, description=description,
                       next_action=next_action)
    except AppError as exc:
        if exc.code == "VALIDATION_ERROR":
            return _issue_form_page(
                request, action="/issues", values=values,
                field_errors=getattr(exc, "field_errors", {}), status=422)
        raise  # NO_OPEN_SHIFT → 303 + flash «Сначала откройте смену»
    except Exception:
        # AC-10 / FR-1.7: сбой записи — та же форма, «Запись не сохранена»,
        # введённые значения сохранены (транзакция уже откатана в сервисе).
        return _issue_form_page(request, action="/issues", values=values,
                                status=500, server_error=True)
    redirect = RedirectResponse("/journal", status_code=303)
    flash_set(redirect, settings, "ok", "Вопрос добавлен")
    return redirect


# --- Карточка вопроса --------------------------------------------------------------------


_EVENT_LABELS = {
    models.EventType.CREATED: "создан",
    models.EventType.EDITED: "изменён",
    models.EventType.CLOSED: "закрыт",
    models.EventType.CARRIED: "перенесён",
}
_EDIT_FIELDS_RU = {"place": "место", "description": "описание",
                   "next_action": "следующее действие"}


@router.get("/issues/{issue_id}", response_class=HTMLResponse)
def issue_card(
    request: Request,
    issue_id: int,
    session: Session = Depends(get_db),
    user: models.User = Depends(current_user),  # члены отеля: ADMIN и MANAGER (FR-3.6)
):
    # Карточку видят обе роли своего отеля; чужой отель — NOT_FOUND (страница 404).
    issue = j.get_issue_or_404(session, user.hotel_id, issue_id)
    tz = _hotel_tz(user, session)
    shift = session.get(models.Shift, issue.current_shift_id)
    shift_admin = session.get(models.User, shift.admin_id) if shift else None
    author = session.get(models.User, issue.created_by)

    timeline = []
    for ev in j.issue_events(session, issue.id):
        payload = json.loads(ev.payload) if ev.payload else None
        actor = session.get(models.User, ev.actor_id)
        details: list[str] = []
        if ev.type == models.EventType.EDITED and payload:
            for key, old_new in payload.items():
                name = _EDIT_FIELDS_RU.get(key, key)
                details.append(f"{name}: «{old_new[0]}» → «{old_new[1]}»")
        elif ev.type == models.EventType.CARRIED and payload:
            # Актор события — принявший (FR-3.6: «перенесён … кто принял»).
            details.append(
                f"из смены №{payload.get('from_shift_id')} в смену "
                f"№{payload.get('to_shift_id')}, передача №{payload.get('handover_id')}"
            )
        timeline.append({
            "label": _EVENT_LABELS.get(ev.type, ev.type),
            "actor": actor.display_name if actor else "?",
            "time": fmt_dt(ev.created_at, tz),
            "details": details,
        })

    return render(
        request, "issue_card.html",
        {
            "page_title": issue.place,
            "issue": issue,
            "author": author,
            "shift": shift,
            "shift_admin": shift_admin,
            "created_sh": fmt_dt(issue.created_at, tz),
            "closed_sh": fmt_dt(issue.closed_at, tz) if issue.closed_at else "",
            "closer": session.get(models.User, issue.closed_by) if issue.closed_by else None,
            "timeline": timeline,
            "plural_carries": plural_carries,
        },
    )


# --- Правка вопроса ----------------------------------------------------------------------


def _load_issue_for_edit(session: Session, admin: models.User, issue_id: int):
    issue = j.get_issue_or_404(session, admin.hotel_id, issue_id)
    return issue


@router.get("/issues/{issue_id}/edit", response_class=HTMLResponse)
def issue_edit_form(
    request: Request,
    issue_id: int,
    session: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    issue = _load_issue_for_edit(session, admin, issue_id)
    values = {"place": issue.place, "description": issue.description,
              "next_action": issue.next_action or ""}
    return _issue_form_page(request, action=f"/issues/{issue_id}/edit",
                            values=values, title="Изменить вопрос")


@router.post("/issues/{issue_id}/edit")
def issue_edit_submit(
    request: Request,
    issue_id: int,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    admin: models.User = Depends(require_admin),
    place: str = Form(""),
    description: str = Form(""),
    next_action: str = Form(""),
    csrf_token: str = Form(""),
):
    if not check_csrf(request, csrf_token):
        raise AppError("CSRF_FAILED")
    values = {"place": place, "description": description, "next_action": next_action}
    try:
        j.update_issue(session, admin, issue_id, place=place,
                       description=description, next_action=next_action)
    except AppError as exc:
        if exc.code == "VALIDATION_ERROR":
            return _issue_form_page(
                request, action=f"/issues/{issue_id}/edit", values=values,
                field_errors=getattr(exc, "field_errors", {}),
                title="Изменить вопрос", status=422)
        raise  # ISSUE_CLOSED / SHIFT_NOT_EDITABLE / NO_OPEN_SHIFT → flash
    redirect = RedirectResponse("/journal", status_code=303)
    flash_set(redirect, settings, "ok", "Сохранено")  # FR-1.7
    return redirect


@router.post("/issues/{issue_id}/close")
def issue_close(
    request: Request,
    issue_id: int,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    admin: models.User = Depends(require_admin),
    csrf_token: str = Form(""),
):
    if not check_csrf(request, csrf_token):
        raise AppError("CSRF_FAILED")
    j.close_issue(session, admin, issue_id)
    redirect = RedirectResponse("/journal", status_code=303)
    flash_set(redirect, settings, "ok", "Вопрос закрыт")
    return redirect
