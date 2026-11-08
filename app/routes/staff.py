"""Экран «Сотрудники» (FR-A3, FR-A4; DF-сотрудники; AC-11).

GET  /staff                      — таблица (имя, логин, статус), кнопки
                                   «Новый пароль», «Отключить»/«Включить»,
                                   форма создания, счётчик «Учётных записей: N из 10».
POST /staff                      — создать ADMIN (303 на /staff + «Сотрудник добавлен»;
                                   ошибки VALIDATION_ERROR 422 с текстом у поля,
                                   ACCOUNT_LIMIT 409 → flash).
POST /staff/{id}/password        — новый пароль (303 + «Пароль изменён»).
POST /staff/{id}/active          — active=0|1: отключить/включить (303 + сообщение;
                                   себя отключить нельзя — FORBIDDEN).
Все POST — с csrf_token; чужой отель — NOT_FOUND (в сервисе).
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
from ..services import staff as staff_service

router = APIRouter()


def _staff_page(
    request: Request, session: Session, manager: models.User, *,
    status: int = 200, values: dict | None = None, field_errors: dict | None = None,
) -> HTMLResponse:
    users = staff_service.list_users(session, manager.hotel_id)
    return render(
        request, "staff.html",
        {
            "page_title": "Сотрудники",
            "users": users,
            "active_count": staff_service.count_active(session, manager.hotel_id),
            "account_limit": request.app.state.settings.account_limit,
            "manager_id": manager.id,
            "values": values or {},
            "field_errors": field_errors or {},
        },
        status=status,
    )


@router.get("/staff", response_class=HTMLResponse)
def staff_page(
    request: Request,
    session: Session = Depends(get_db),
    manager: models.User = Depends(require_manager),
):
    return _staff_page(request, session, manager)


@router.post("/staff")
def staff_create(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    manager: models.User = Depends(require_manager),
    display_name: str = Form(""),
    login: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
):
    if not check_csrf(request, csrf_token):
        raise AppError("CSRF_FAILED")
    values = {"display_name": display_name, "login": login}
    try:
        staff_service.create_staff(
            session, settings, manager,
            display_name=display_name, login=login, password=password,
        )
    except AppError as exc:
        if exc.code == "VALIDATION_ERROR":
            # 422: форма с сохранёнными значениями и текстами у полей
            return _staff_page(
                request, session, manager, status=422, values=values,
                field_errors=getattr(exc, "field_errors", {}),
            )
        raise  # ACCOUNT_LIMIT и пр. — общий обработчик (303 + flash с кодом)
    redirect = RedirectResponse("/staff", status_code=303)
    flash_set(redirect, settings, "ok", "Сотрудник добавлен")
    return redirect


@router.post("/staff/{user_id}/password")
def staff_password(
    request: Request,
    user_id: int,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    manager: models.User = Depends(require_manager),
    password: str = Form(""),
    csrf_token: str = Form(""),
):
    if not check_csrf(request, csrf_token):
        raise AppError("CSRF_FAILED")
    try:
        staff_service.reset_password(session, manager, user_id, password=password)
    except AppError as exc:
        if exc.code == "VALIDATION_ERROR":
            # Короткий текст у поля в форме смены пароля на странице сотрудников
            redirect = RedirectResponse("/staff", status_code=303)
            flash_set(redirect, settings, "error",
                      f"Пароль не изменён: {exc.field_errors['password']}",  # type: ignore[attr-defined]
                      "VALIDATION_ERROR")
            return redirect
        raise
    redirect = RedirectResponse("/staff", status_code=303)
    flash_set(redirect, settings, "ok", "Пароль изменён")
    return redirect


@router.post("/staff/{user_id}/active")
def staff_active(
    request: Request,
    user_id: int,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    manager: models.User = Depends(require_manager),
    active: str = Form(""),
    csrf_token: str = Form(""),
):
    if not check_csrf(request, csrf_token):
        raise AppError("CSRF_FAILED")
    if active not in ("0", "1"):
        redirect = RedirectResponse("/staff", status_code=303)
        flash_set(redirect, settings, "error",
                  "Некорректный запрос", "VALIDATION_ERROR")
        return redirect
    staff_service.set_active(
        session, settings, manager, user_id, active=(active == "1")
    )
    redirect = RedirectResponse("/staff", status_code=303)
    flash_set(redirect, settings, "ok",
              "Учётная запись включена" if active == "1" else "Учётная запись отключена")
    return redirect
