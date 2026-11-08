"""Сервис управления сотрудниками (FR-A3, FR-A4, AC-11).

Правила из ТЗ:
- FR-A3: MANAGER создаёт сотрудников с ролью ADMIN. Активных учётных записей
  в отеле, включая управляющего, не более ACCOUNT_LIMIT (10) — ACCOUNT_LIMIT 409.
- FR-A4: сброс пароля сотруднику своего отеля; отключение/включение. Себя
  отключить нельзя (FORBIDDEN). Отключённые записи и их история сохраняются —
  удаление не выполняется НИКОГДА (NFR-4). Включение сверх лимита — ACCOUNT_LIMIT.
- Изоляция отеля: чужая учётная запись — NOT_FOUND (404), как и везде в ТЗ.
- Запись — в транзакции BEGIN IMMEDIATE (контракт db.immediate_transaction).
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..config import Settings
from ..db import immediate_transaction
from ..errors import AppError
from ..security import hash_password
from ..validation import clean, normalize_login, validate_display_name, validate_login, validate_password


def list_users(session: Session, hotel_id: int) -> list[models.User]:
    """Все записи отеля: активные первыми, затем отключённые; внутри — по имени."""
    return list(
        session.scalars(
            select(models.User)
            .where(models.User.hotel_id == hotel_id)
            .order_by(models.User.is_active.desc(), models.User.display_name, models.User.id)
        )
    )


def count_active(session: Session, hotel_id: int) -> int:
    return session.scalar(
        select(func.count())
        .select_from(models.User)
        .where(models.User.hotel_id == hotel_id, models.User.is_active.is_(True))
    ) or 0


def _get_hotel_user_or_404(session: Session, hotel_id: int, user_id: int) -> models.User:
    """Свой отель — иначе NOT_FOUND (изоляция: чужих записей не видно)."""
    user = session.get(models.User, user_id)
    if user is None or user.hotel_id != hotel_id:
        raise AppError("NOT_FOUND", redirect="/staff")
    return user


def create_staff(
    session: Session,
    settings: Settings,
    manager: models.User,
    *,
    display_name: str,
    login: str,
    password: str,
) -> models.User:
    """FR-A3: создать ADMIN в отеле управляющего.

    Ошибки: VALIDATION_ERROR (422, текст у поля), ACCOUNT_LIMIT (409 → flash).
    """
    display_name, login = clean(display_name), normalize_login(login)

    errors: dict[str, str] = {}
    if err := validate_display_name(display_name):
        errors["display_name"] = err
    if err := validate_login(login):
        errors["login"] = err
    if err := validate_password(password):
        errors["password"] = err
    if "login" not in errors and session.scalar(
        select(models.User.id).where(models.User.login == login)
    ):
        errors["login"] = "Этот логин уже занят"
    if errors:
        exc = AppError("VALIDATION_ERROR", text="")
        exc.field_errors = errors  # type: ignore[attr-defined]
        raise exc

    # Лимит — по активным записям отеля, включая самого управляющего (FR-A3).
    # Проверка внутри IMMEDIATE-транзакции: конкурентные создания сериализуются.
    with immediate_transaction(session):
        if count_active(session, manager.hotel_id) >= settings.account_limit:
            raise AppError("ACCOUNT_LIMIT", redirect="/staff")
        user = models.User(
            hotel_id=manager.hotel_id,
            login=login,
            display_name=display_name,
            password_hash=hash_password(password),
            role=models.Role.ADMIN,
        )
        session.add(user)
        session.commit()
        return user


def reset_password(
    session: Session, manager: models.User, user_id: int, *, password: str
) -> None:
    """FR-A4: новый пароль сотруднику своего отеля. NOT_FOUND / VALIDATION_ERROR."""
    if err := validate_password(password):
        exc = AppError("VALIDATION_ERROR", text="")
        exc.field_errors = {"password": err}  # type: ignore[attr-defined]
        raise exc
    with immediate_transaction(session):
        user = _get_hotel_user_or_404(session, manager.hotel_id, user_id)
        user.password_hash = hash_password(password)
        session.commit()


def set_active(
    session: Session, settings: Settings, manager: models.User, user_id: int, *, active: bool
) -> None:
    """FR-A4: отключить/включить. FORBIDDEN (себя), ACCOUNT_LIMIT, NOT_FOUND."""
    with immediate_transaction(session):
        user = _get_hotel_user_or_404(session, manager.hotel_id, user_id)
        if not active and user.id == manager.id:
            raise AppError("FORBIDDEN_SELF_DEACTIVATE", redirect="/staff")
        if active and not user.is_active and count_active(session, manager.hotel_id) >= settings.account_limit:
            # Включение сверх лимита даёт ACCOUNT_LIMIT (FR-A4, AC-11).
            raise AppError("ACCOUNT_LIMIT", redirect="/staff")
        user.is_active = active
        session.commit()
