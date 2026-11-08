"""Сервис аутентификации: регистрация отеля, вход, загрузка текущего пользователя.

Правила из ТЗ:
- FR-A1: регистрация создаёт отель + MANAGER + пилот TRIAL_DAYS, затем вход.
- FR-A2: для несуществующей и отключённой учётной записи — одинаковое
  AUTH_FAILED; is_active проверяется на каждом запросе (load_user).
- A-1: MANAGER создаётся ровно один на отель (частичный индекс — подстраховка).
- Логин уникален глобально, хранится в нижнем регистре (AL-1).
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..config import Settings, get_timezones
from ..errors import AppError
from ..security import hash_password, verify_password
from ..validation import (
    clean,
    normalize_login,
    validate_display_name,
    validate_hotel_name,
    validate_login,
    validate_password,
)


def register_hotel(
    session: Session,
    settings: Settings,
    *,
    hotel_name: str,
    timezone: str,
    display_name: str,
    login: str,
    password: str,
    password_confirm: str,
) -> models.User:
    """Создать отель и учётную запись MANAGER. Возвращает созданного пользователя.

    Ошибки: VALIDATION_ERROR (errors: dict) — форма возвращается с 422.
    """
    hotel_name, display_name = clean(hotel_name), clean(display_name)
    login = normalize_login(login)

    errors: dict[str, str] = {}
    if err := validate_hotel_name(hotel_name):
        errors["hotel_name"] = err
    if timezone not in get_timezones():
        errors["timezone"] = "Выберите часовой пояс из списка"
    if err := validate_display_name(display_name):
        errors["display_name"] = err
    if err := validate_login(login):
        errors["login"] = err
    if err := validate_password(password):
        errors["password"] = err
    elif password != password_confirm:
        errors["password_confirm"] = "Пароли не совпадают"

    # Занятость логина — тоже VALIDATION_ERROR («в том числе занятый логин» в ТЗ)
    if "login" not in errors and session.scalar(
        select(models.User.id).where(models.User.login == login)
    ):
        errors["login"] = "Этот логин уже занят"

    if errors:
        exc = AppError("VALIDATION_ERROR", text="")
        exc.field_errors = errors  # type: ignore[attr-defined]
        raise exc

    now = models.utcnow()
    hotel = models.Hotel(
        name=hotel_name,
        timezone=timezone,
        trial_ends_at=now + timedelta(days=settings.trial_days),
    )
    session.add(hotel)
    session.flush()

    manager = models.User(
        hotel_id=hotel.id,
        login=login,
        display_name=display_name,
        password_hash=hash_password(password),
        role=models.Role.MANAGER,
    )
    session.add(manager)
    session.commit()
    return manager


def authenticate(session: Session, *, login: str, password: str) -> models.User:
    """Проверить логин/пароль. Возвращает активного пользователя.

    Одно сообщение для всех случаев (FR-A2): нет такого логина, отключённая
    запись, неверный пароль → AUTH_FAILED.
    """
    login = normalize_login(login)
    user = session.scalar(select(models.User).where(models.User.login == login))
    # Сравнение пароля выполняем и для отключённой записи — поведение идентично.
    ok = user is not None and verify_password(user.password_hash, password)
    if not ok or not user.is_active:
        raise AppError("AUTH_FAILED")
    return user


def load_user(session: Session, user_id: int) -> models.User | None:
    """Текущий пользователь по id из cookie; None — сессия недействительна.

    is_active проверяется на каждом запросе (FR-A2): отключение записи
    мгновенно гасит её сессии, история и записи сохраняются (FR-A4).
    """
    user = session.get(models.User, user_id)
    if user is None or not user.is_active:
        return None
    return user
