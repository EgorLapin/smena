"""FastAPI-зависимости доступа: текущий пользователь, роли, flash-сообщения.

- current_user — чтение подписанной cookie + проверка is_active на КАЖДОМ
  запросе (FR-A2); нет сессии → AUTH_REQUIRED (303 на /login, без сообщения).
- require_admin / require_manager — серверная проверка роли на каждом запросе
  (FR-A6); не та роль → FORBIDDEN (на GET — страница 403 по таблице ошибок,
  Q-002; на POST с redirect — 303 с красным flash).
- flash — одноразовое сообщение в подписанной cookie (переживает 303-редирект,
  ТЗ: «результат показывается сообщением, хранящимся в сессии»).
"""
from __future__ import annotations

from fastapi import Depends, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from . import models
from .config import Settings
from .db import get_db
from .errors import AppError
from .security import SESSION_COOKIE, read_session_token
from .services.auth import load_user

FLASH_COOKIE = "smena_flash"
FLASH_MAX_AGE = 120  # секунд — больше, чем любой редирект-цикл


# --- Текущий пользователь ------------------------------------------------------


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def current_user(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> models.User:
    token = request.cookies.get(SESSION_COOKIE, "")
    user_id = read_session_token(settings, token) if token else None
    user = load_user_safe(session, user_id)
    if user is None:
        raise AppError("AUTH_REQUIRED")
    request.state.user = user
    return user


def load_user_safe(session: Session, user_id: int | None) -> models.User | None:
    if user_id is None:
        return None
    return load_user(session, user_id)


def optional_user(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> models.User | None:
    """Как current_user, но не требует входа (страницы /login, /register)."""
    token = request.cookies.get(SESSION_COOKIE, "")
    user_id = read_session_token(settings, token) if token else None
    user = load_user_safe(session, user_id)
    request.state.user = user
    return user


def require_admin(
    request: Request, user: models.User = Depends(current_user)
) -> models.User:
    if user.role != models.Role.ADMIN:
        # POST-действия редиректят с flash; GET отдаёт страницу 403 (Q-002).
        is_post = request.method == "POST"
        raise AppError(
            "FORBIDDEN",
            redirect=(request.headers.get("referer") or "/") if is_post else None,
        )
    return user


def require_manager(
    request: Request, user: models.User = Depends(current_user)
) -> models.User:
    if user.role != models.Role.MANAGER:
        # POST-действия редиректят с flash; GET отдаёт страницу 403 (Q-002).
        is_post = request.method == "POST"
        raise AppError(
            "FORBIDDEN",
            redirect=(request.headers.get("referer") or "/") if is_post else None,
        )
    return user


# --- Flash (подписанная одноразовая cookie) ------------------------------------


def _flash_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="smena.flash.v1")


def flash_set(response, settings: Settings, kind: str, text: str, code: str = "") -> None:
    token = _flash_serializer(settings).dumps({"k": kind, "t": text, "c": code})
    response.set_cookie(
        FLASH_COOKIE, token, max_age=FLASH_MAX_AGE,
        httponly=True, secure=True, samesite="lax", path="/",
    )


def flash_pop(request: Request) -> dict | None:
    """Прочитать и «списать» flash (cookie удаляет render()/redirect-хелпер)."""
    token = request.cookies.get(FLASH_COOKIE, "")
    if not token:
        return None
    settings = request.app.state.settings
    try:
        data = _flash_serializer(settings).loads(token, max_age=FLASH_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    request.state.flash_consumed = True
    return {"kind": data.get("k", "ok"), "text": data.get("t", ""), "code": data.get("c", "")}
