"""Безопасность: хеши паролей, подписанная cookie-сессия, CSRF (NFR-2).

- Пароли — только argon2id (argon2-cffi), ТЗ допускает argon2id/bcrypt.
- Сессия на сервере не хранится (ТЗ, «Жизненный цикл данных»):
  подписанный itsdangerous-токен в cookie smena_session, срок 30 дней,
  флаги HttpOnly, Secure, SameSite=Lax. В токене — id пользователя;
  актуальные роль/отель/активность всегда читаются из БД (FR-A2).
- CSRF: двудомное токен-сочетание (двойная отправка формы): случайный токен
  хранится в отдельной cookie smena_csrf (НЕ HttpOnly — форма должна его
  читать), форма присылает его полем csrf_token; сверка на каждом POST.
"""
from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Settings

SESSION_COOKIE = "smena_session"
CSRF_COOKIE = "smena_csrf"
SESSION_MAX_AGE = 30 * 24 * 3600  # 30 дней (FR-A2)
CSRF_MAX_AGE = 24 * 3600          # сутки — запас на открытые формы

_hasher = PasswordHasher()  # дефолты argon2id: разумные и актуальные


# --- Пароли -------------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """True, если пароль подходит. Любые аномалии хеша → False (как неверный пароль)."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# --- Сессия (подписанная cookie) -----------------------------------------------


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt="smena.session.v1")


def make_session_token(settings: Settings, user_id: int) -> str:
    return _serializer(settings).dumps({"uid": user_id})


def read_session_token(settings: Settings, token: str) -> int | None:
    """Вернуть user_id из cookie; None — подпись неверна или прошло > 30 дней."""
    try:
        data = _serializer(settings).loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    uid = data.get("uid")
    return int(uid) if isinstance(uid, int) else None


def set_session_cookie(response, settings: Settings, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_MAX_AGE,
        httponly=True, secure=True, samesite="lax", path="/",
    )


def delete_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


# --- CSRF ---------------------------------------------------------------------


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def get_or_create_csrf_token(request: Request) -> str:
    """Текущий csrf-токен из cookie (для рендера формы; middleware выдаёт
    токен на каждый GET, поэтому здесь он уже есть)."""
    return request.cookies.get(CSRF_COOKIE) or new_csrf_token()


def set_csrf_cookie(response, token: str) -> None:
    # HttpOnly=False намеренно: значение нужно для скрытого поля формы.
    # Secure/SameSite=Lax — как требует NFR-2 для cookie приложения.
    response.set_cookie(
        CSRF_COOKIE, token, max_age=CSRF_MAX_AGE,
        httponly=False, secure=True, samesite="lax", path="/",
    )


def delete_csrf_cookie(response) -> None:
    response.delete_cookie(CSRF_COOKIE, path="/")


def check_csrf(request: Request, form_token: str) -> bool:
    """Сверка поля csrf_token из формы с cookie. Равны и непусты → True."""
    cookie = request.cookies.get(CSRF_COOKIE, "")
    return bool(cookie) and secrets.compare_digest(cookie, form_token or "")
