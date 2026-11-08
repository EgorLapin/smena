"""Сервис передачи смены: сдача (T06, AL-2) и приём (T07, AL-3).

AL-2: сдать можно только свою смену отеля в статусе OPEN. В одной транзакции
смена переходит в PENDING, создаются Handover и HandoverItem-снимок всех
вопросов смены (открытых и закрытых) копией полей на момент сдачи. У смены не
более одной передачи (uq_handover_shift + проверка в транзакции): повторная
сдача возвращает существующую передачу и ничего не создаёт (FR-2.3).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..db import immediate_transaction
from ..errors import AppError
from .journal import get_open_shift


def handover_shift(
    session: Session, admin: models.User, shift_id: int
) -> tuple[models.Handover, bool]:
    """Сдать смену. Возвращает (передача, создана_ли_сейчас).

    Ошибки: NOT_FOUND — чужая/несуществующая смена (AL-2 «свою смену отеля»;
    изоляция по ТЗ = 404, а не 403).
    """
    with immediate_transaction(session):
        shift = session.get(models.Shift, shift_id)
        if shift is None or shift.hotel_id != admin.hotel_id or shift.admin_id != admin.id:
            raise AppError("NOT_FOUND", redirect="/journal")

        existing = session.scalar(
            select(models.Handover).where(models.Handover.shift_id == shift.id)
        )
        if existing is not None:
            # FR-2.3/AL-2: двойной клик и повтор запроса — та же передача.
            return existing, False

        if shift.status != models.ShiftStatus.OPEN:
            raise AppError("NOT_FOUND", redirect="/journal")  # непригодное состояние

        now = models.utcnow()
        shift.status = models.ShiftStatus.PENDING
        shift.handed_at = now
        handover = models.Handover(
            hotel_id=admin.hotel_id, shift_id=shift.id, sender_id=admin.id,
            sent_at=now, status=models.HandoverStatus.PENDING,
        )
        session.add(handover)
        session.flush()  # нужен id передачи для снимка

        # FR-2.1: снимок — ВСЕ вопросы смены, открытые и закрытые.
        issues = session.scalars(
            select(models.Issue).where(models.Issue.current_shift_id == shift.id)
        )
        for issue in issues:
            session.add(models.HandoverItem(
                handover_id=handover.id, issue_id=issue.id,
                place=issue.place, description=issue.description,
                next_action=issue.next_action,
                status_at_handover=issue.status,
            ))
        session.commit()
        return handover, True


def handover_for_shift(
    session: Session, user: models.User, shift_id: int
) -> models.Handover | None:
    """Передача, уже созданная для смены (для GET-идемпотентности)."""
    shift = session.get(models.Shift, shift_id)
    if shift is None or shift.hotel_id != user.hotel_id:
        return None
    return session.scalar(
        select(models.Handover).where(models.Handover.shift_id == shift.id)
    )
