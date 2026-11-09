"""Сервис журнала смены (FR-1.1 — FR-1.7, AL-4; DF-3, DF-4).

Транзакционная схема: любое изменение — в immediate_transaction (BEGIN IMMEDIATE
до первых записей), проверки и запись в одной транзакции (NFR-4 «всё или ничего»).
Вопросы никогда не удаляются (FR-1.6): только закрытие.
"""
from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..db import immediate_transaction
from ..errors import AppError
from ..messages_ru import error_text
from ..validation import (
    clean,
    normalize_place_norm,
    validate_description,
    validate_next_action,
    validate_place,
)


# --- Смены -------------------------------------------------------------------------


def get_open_shift(session: Session, admin_id: int) -> models.Shift | None:
    return session.scalar(
        select(models.Shift).where(
            models.Shift.admin_id == admin_id,
            models.Shift.status == models.ShiftStatus.OPEN,
        )
    )


def open_shift(session: Session, hotel_id: int, admin_id: int) -> tuple[models.Shift, bool]:
    """FR-1.1: открыть смену. Если открытая есть — ничего не создаём (FR-1.1,
    повторное нажатие безопасно). Возвращает (смена, создана_ли_она_сейчас)."""
    with immediate_transaction(session):
        shift = get_open_shift(session, admin_id)
        if shift is not None:
            return shift, False
        shift = models.Shift(hotel_id=hotel_id, admin_id=admin_id,
                             status=models.ShiftStatus.OPEN)
        session.add(shift)
        session.commit()
        return shift, True


# --- Вопросы -------------------------------------------------------------------------


def _validate_issue_fields(*, place: str, description: str, next_action: str) -> dict:
    errors: dict[str, str] = {}
    if err := validate_place(place):
        errors["place"] = err
    if err := validate_description(description):
        errors["description"] = err
    if err := validate_next_action(next_action):
        errors["next_action"] = err
    return errors


def add_event(session: Session, issue: models.Issue, shift_id: int, actor_id: int,
              type_: str, payload: dict | None = None) -> None:
    """Запись события жизни вопроса (AL-4). Единая точка для журнала и приёма:
    payload сериализуется здесь, поэтому тест сбоя подменяет одну функцию."""
    session.add(models.IssueEvent(
        hotel_id=issue.hotel_id, issue_id=issue.id, shift_id=shift_id,
        actor_id=actor_id, type=type_,
        payload=json.dumps(payload, ensure_ascii=False) if payload else None,
    ))


# Совместимость внутри пакета.
_add_event = add_event


def create_issue(
    session: Session, admin: models.User, *,
    place: str, description: str, next_action: str,
) -> models.Issue:
    """FR-1.2: вопрос получает постоянный id, статус OPEN, текущую смену;
    пишется CREATED. Без открытой смены — NO_OPEN_SHIFT (409, flash на /journal)."""
    place, description, next_action = (
        clean(place), clean(description), clean(next_action))
    errors = _validate_issue_fields(place=place, description=description,
                                    next_action=next_action)
    if errors:
        exc = AppError("VALIDATION_ERROR", text="")
        exc.field_errors = errors  # type: ignore[attr-defined]
        raise exc

    with immediate_transaction(session):
        shift = get_open_shift(session, admin.id)
        if shift is None:
            raise AppError("NO_OPEN_SHIFT", redirect="/journal")
        issue = models.Issue(
            hotel_id=admin.hotel_id,
            place=place,
            place_norm=normalize_place_norm(place),
            description=description,
            next_action=next_action or None,
            status=models.IssueStatus.OPEN,
            current_shift_id=shift.id,
            created_by=admin.id,
        )
        session.add(issue)
        session.flush()
        _add_event(session, issue, shift.id, admin.id, models.EventType.CREATED)
        session.commit()
        return issue


def get_issue_or_404(session: Session, hotel_id: int, issue_id: int, *,
                     redirect: str | None = None) -> models.Issue:
    """FR-A5/AC-6: чужого отеля не существует для пользователя.

    GET-контекст (redirect=None) — страница 404; POST-контекст — AppError с
    redirect, т.е. 303 + красный flash с кодом (контракт ошибок ТЗ)."""
    issue = session.get(models.Issue, issue_id)
    if issue is None or issue.hotel_id != hotel_id:
        raise AppError("NOT_FOUND", redirect=redirect)
    return issue


def _require_editable(
    session: Session, admin: models.User, issue: models.Issue
) -> models.Shift:
    """AL-4: вопрос открыт и находится в текущей открытой смене пользователя.

    Порядок проверок — чтобы AC-3 (правка сданной смены) давал именно
    SHIFT_NOT_EDITABLE, даже если у администратора ещё нет новой смены.
    """
    if issue.status == models.IssueStatus.CLOSED:
        raise AppError("ISSUE_CLOSED", redirect="/journal")
    source_shift = session.get(models.Shift, issue.current_shift_id)
    assert source_shift is not None
    if source_shift.status != models.ShiftStatus.OPEN:
        raise AppError("SHIFT_NOT_EDITABLE", redirect="/journal")  # смена сдана/принята
    my_shift = get_open_shift(session, admin.id)
    if my_shift is None:
        raise AppError("NO_OPEN_SHIFT", redirect="/journal")
    if source_shift.id != my_shift.id:
        raise AppError("SHIFT_NOT_EDITABLE", redirect="/journal")  # вопрос в чужой смене
    return my_shift


def update_issue(
    session: Session, admin: models.User, issue_id: int, *,
    place: str, description: str, next_action: str,
) -> models.Issue:
    """FR-1.3: EDITED только если значения действительно изменились (AL-4)."""
    place, description, next_action = (
        clean(place), clean(description), clean(next_action))
    errors = _validate_issue_fields(place=place, description=description,
                                    next_action=next_action)
    if errors:
        exc = AppError("VALIDATION_ERROR", text="")
        exc.field_errors = errors  # type: ignore[attr-defined]
        raise exc

    with immediate_transaction(session):
        issue = get_issue_or_404(session, admin.hotel_id, issue_id,
                                 redirect="/journal")  # POST-контракт
        shift = _require_editable(session, admin, issue)

        changes: dict[str, list[str]] = {}
        new_next = next_action or None
        for field, new_value in (
            ("place", place), ("description", description), ("next_action", new_next)
        ):
            old_value = getattr(issue, field)
            if (old_value or None) != new_value:
                changes[field] = [old_value or "", new_value or ""]

        if changes:
            if "place" in changes:
                issue.place_norm = normalize_place_norm(place)
            issue.place, issue.description, issue.next_action = (
                place, description, new_next)
            _add_event(session, issue, shift.id, admin.id,
                       models.EventType.EDITED, changes)
        session.commit()
        return issue


def close_issue(session: Session, admin: models.User, issue_id: int) -> models.Issue:
    """FR-1.4: CLOSED + автор + время + событие CLOSED; вопрос остаётся
    в истории и в этой смене, дальше не переносится (FR-2.6)."""
    with immediate_transaction(session):
        issue = get_issue_or_404(session, admin.hotel_id, issue_id,
                                 redirect="/journal")  # POST-контракт
        shift = _require_editable(session, admin, issue)
        issue.status = models.IssueStatus.CLOSED
        issue.closed_by = admin.id
        issue.closed_at = models.utcnow()
        _add_event(session, issue, shift.id, admin.id, models.EventType.CLOSED)
        session.commit()
        return issue


# --- Данные страницы журнала ----------------------------------------------------------


def journal_open_issues(session: Session, shift_id: int) -> list[models.Issue]:
    """FR-1.5: открытые вопросы, старые выше."""
    return list(
        session.scalars(
            select(models.Issue)
            .where(
                models.Issue.current_shift_id == shift_id,
                models.Issue.status == models.IssueStatus.OPEN,
            )
            .order_by(models.Issue.created_at.asc(), models.Issue.id.asc())
        )
    )


def journal_closed_here(session: Session, shift_id: int) -> list[models.Issue]:
    """FR-1.5: «Решено в этой смене» (сворачиваемый блок)."""
    return list(
        session.scalars(
            select(models.Issue)
            .where(
                models.Issue.current_shift_id == shift_id,
                models.Issue.status == models.IssueStatus.CLOSED,
            )
            .order_by(models.Issue.closed_at.desc(), models.Issue.id.desc())
        )
    )


def pending_handovers(session: Session, hotel_id: int, exclude_user_id: int | None = None):
    """FR-2.4: непринятые передачи отеля, кроме собственных. Открывает список
    старых раньше (ждут приёма дольше всех). Третье поле — Shift.is_demo:
    журналу нужна метка «Демо» (D-027), иначе вымышленные вопросы демо-передачи
    выглядят как написанные кем-то."""
    stmt = (
        select(models.Handover, models.User, models.Shift.is_demo)
        .join(models.User, models.User.id == models.Handover.sender_id)
        .join(models.Shift, models.Shift.id == models.Handover.shift_id)
        .where(
            models.Handover.hotel_id == hotel_id,
            models.Handover.status == models.HandoverStatus.PENDING,
        )
        .order_by(models.Handover.sent_at.asc(), models.Handover.id.asc())
    )
    if exclude_user_id is not None:
        stmt = stmt.where(models.Handover.sender_id != exclude_user_id)
    return list(session.execute(stmt))


def open_issue_count(session: Session, shift_id: int) -> int:
    """FR-2.4: число открытых вопросов сданной смены (без решённых)."""
    return session.scalar(
        select(func.count())
        .select_from(models.Issue)
        .where(
            models.Issue.current_shift_id == shift_id,
            models.Issue.status == models.IssueStatus.OPEN,
        )
    ) or 0


def issue_events(session: Session, issue_id: int) -> list[models.IssueEvent]:
    return list(
        session.scalars(
            select(models.IssueEvent)
            .where(models.IssueEvent.issue_id == issue_id)
            .order_by(models.IssueEvent.created_at.asc(), models.IssueEvent.id.asc())
        )
    )
