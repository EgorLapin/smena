"""Сервис передачи смены: сдача (T06, AL-2) и приём (T07, AL-3).

AL-2: сдать можно только свою смену отеля в статусе OPEN. В одной транзакции
смена переходит в PENDING, создаются Handover и HandoverItem-снимок всех
вопросов смены (открытых и закрытых) копией полей на момент сдачи. У смены не
более одной передачи (uq_handover_shift + проверка в транзакции): повторная
сдача возвращает существующую передачу и ничего не создаёт (FR-2.3).
"""
from __future__ import annotations

import json

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


class AcceptResult:
    """Итог приёма: created=True — приём выполнен сейчас, carried — сколько
    вопросов перенесено; created=False — повтор тем же пользователем."""

    def __init__(self, *, created: bool, carried: int, handover: models.Handover):
        self.created = created
        self.carried = carried
        self.handover = handover


def accept_handover(
    session: Session, admin: models.User, handover_id: int,
) -> AcceptResult:
    """AL-3 / FR-2.5–2.9: приём смены одной транзакцией BEGIN IMMEDIATE.

    Проверки в транзакции (записи сериализуются, поэтому «первый принявший»
    определён даже при параллельных нажатиях — FR-2.7):
    - передача существует в отеле (иначе NOT_FOUND);
    - пользователь не автор (FORBIDDEN «Нельзя принять смену, которую вы сдали сами»);
    - если уже принята: тем же пользователем — результат первого приёма
      (created=False), другим — ALREADY_ACCEPTED с именем и временем.
    При приёме: у получателя открывается смена (если нет открытой), передача и
    смена-источник → ACCEPTED, открытые вопросы переходят тому же идентификатору
    (handover_count+1, событие CARRIED), закрытые остаются (FR-2.6).
    """
    with immediate_transaction(session):
        handover = session.get(models.Handover, handover_id)
        if handover is None or handover.hotel_id != admin.hotel_id:
            raise AppError("NOT_FOUND", redirect="/journal")
        if handover.sender_id == admin.id:
            raise AppError("FORBIDDEN_SELF_ACCEPT", redirect=f"/handovers/{handover.id}")

        if handover.status == models.HandoverStatus.ACCEPTED:
            if handover.receiver_id == admin.id:
                # FR-2.7: повтор тем же — состояние не меняется, без второй волны
                # событий; показываем результат первого приёма.
                return AcceptResult(created=False, carried=0, handover=handover)
            raise AppError(
                "ALREADY_ACCEPTED",
                params=_accepted_params(session, handover),
                redirect=f"/handovers/{handover.id}",
            )

        # Смена получателя: существующая открытая или новая (AL-3).
        receiver_shift = get_open_shift(session, admin.id)
        if receiver_shift is None:
            receiver_shift = models.Shift(
                hotel_id=admin.hotel_id, admin_id=admin.id,
                status=models.ShiftStatus.OPEN,
            )
            session.add(receiver_shift)
            session.flush()

        source_shift = session.get(models.Shift, handover.shift_id)
        assert source_shift is not None

        open_issues = session.scalars(
            select(models.Issue).where(
                models.Issue.current_shift_id == source_shift.id,
                models.Issue.status == models.IssueStatus.OPEN,
            )
        )
        carried = 0
        now = models.utcnow()
        for issue in open_issues:
            issue.current_shift_id = receiver_shift.id
            issue.handover_count += 1
            session.add(models.IssueEvent(
                hotel_id=issue.hotel_id, issue_id=issue.id,
                shift_id=receiver_shift.id, actor_id=admin.id,
                type=models.EventType.CARRIED,
                payload=json.dumps({
                    "handover_id": handover.id,
                    "from_shift_id": source_shift.id,
                    "to_shift_id": receiver_shift.id,
                }, ensure_ascii=False),
            ))
            carried += 1

        handover.status = models.HandoverStatus.ACCEPTED
        handover.receiver_id = admin.id
        handover.accepted_at = now
        handover.receiver_shift_id = receiver_shift.id
        source_shift.status = models.ShiftStatus.ACCEPTED
        session.commit()
        return AcceptResult(created=True, carried=carried, handover=handover)


def _accepted_params(session: Session, handover: models.Handover) -> dict:
    """Параметры сообщения ALREADY_ACCEPTED: «Смена уже принята: {name}, {time}».
    Время — в поясе отеля, формат макетов (01.11.2026 09:03)."""
    from ..times import fmt_dt

    receiver = session.get(models.User, handover.receiver_id)
    hotel = session.get(models.Hotel, handover.hotel_id)
    return {
        "name": receiver.display_name if receiver else "?",
        "time": fmt_dt(handover.accepted_at, hotel.timezone) if handover.accepted_at else "",
    }
