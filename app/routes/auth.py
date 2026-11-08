"""Маршруты входа, регистрации отеля и выхода (FR-A1, FR-A2; DF-1).

CSRF (контракт ТЗ, NFR-2): GET страниц с формами выдаёт cookie smena_csrf и
кладёт тот же токен в скрытое поле формы; POST сверяет поле с cookie
(secrets.compare_digest). Несовпадение/отсутствие — CSRF_FAILED.
POST-успех — всегда 303 (PRG); результат — flash-сообщение в подписанной cookie.
Ошибки формы — повторный рендер со статусом из таблицы ошибок и значениями в полях.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from .. import models
from ..config import Settings, get_timezones
from ..db import get_db
from ..deps import current_user, get_settings_dep, optional_user
from ..errors import AppError
from ..render import render
from ..security import (
    check_csrf,
    delete_csrf_cookie,
    delete_session_cookie,
    make_session_token,
    set_session_cookie,
)
from ..services.auth import authenticate, register_hotel

router = APIRouter()


def _render(
    request: Request, template: str, context: dict, *,
    status: int = 200,
) -> HTMLResponse:
    """Страница формы: flash списывается (render), csrf-токен шаблоны берут
    из request.state.csrf_token (его гарантирует middleware)."""
    return render(request, template, context, status=status)


# --- Вход (DF-1) ----------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, user=Depends(optional_user)):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return _render(request, "login.html",
                   {"page_title": "Вход", "values": {}})


@router.post("/login")
def login_submit(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    login: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
):
    if not check_csrf(request, csrf_token):
        raise AppError("CSRF_FAILED")
    try:
        user = authenticate(session, login=login, password=password)
    except AppError as exc:
        # AUTH_FAILED — 401, форма с красным сообщением, введённое сохранено
        return _render(
            request, "login.html",
            {"page_title": "Вход", "values": {"login": login},
             "error_code": exc.code, "error_text": exc.text},
            status=exc.http_status,
        )

    redirect = RedirectResponse("/", status_code=303)
    set_session_cookie(redirect, settings, make_session_token(settings, user.id))
    delete_csrf_cookie(redirect)
    return redirect


# --- Регистрация отеля (FR-A1) ---------------------------------------------------


@router.get("/register", response_class=HTMLResponse)
def register_form(request: Request, user=Depends(optional_user)):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return _render(
        request, "register.html",
        {"page_title": "Регистрация отеля", "values": {}, "timezones": get_timezones(),
         "default_timezone": request.app.state.settings.default_timezone},
    )


@router.post("/register")
def register_submit(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    hotel_name: str = Form(""),
    timezone: str = Form(""),
    display_name: str = Form(""),
    login: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
    csrf_token: str = Form(""),
):
    if not check_csrf(request, csrf_token):
        raise AppError("CSRF_FAILED")
    values = {"hotel_name": hotel_name, "display_name": display_name, "login": login}
    try:
        manager = register_hotel(
            session, settings,
            hotel_name=hotel_name, timezone=timezone, display_name=display_name,
            login=login, password=password, password_confirm=password_confirm,
        )
    except AppError as exc:
        # VALIDATION_ERROR — 422, значения сохранены, тексты у полей
        return _render(
            request, "register.html",
            {"page_title": "Регистрация отеля", "values": values,
             "field_errors": getattr(exc, "field_errors", {}),
             "error_code": "VALIDATION_ERROR",
             "timezones": get_timezones(),
             "default_timezone": timezone or settings.default_timezone},
            status=422,
        )

    # После регистрации — сразу вход, 303 на /overview (FR-A1)
    redirect = RedirectResponse("/overview", status_code=303)
    set_session_cookie(redirect, settings, make_session_token(settings, manager.id))
    delete_csrf_cookie(redirect)
    return redirect


# --- Выход -------------------------------------------------------------------------


@router.post("/logout")
def logout(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    user: models.User = Depends(current_user),
    csrf_token: str = Form(""),
):
    if not check_csrf(request, csrf_token):
        raise AppError("CSRF_FAILED")
    redirect = RedirectResponse("/login", status_code=303)
    delete_session_cookie(redirect)
    delete_csrf_cookie(redirect)
    return redirect
