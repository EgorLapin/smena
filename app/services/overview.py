"""Сервис обзора управляющего (FR-3.1 — FR-3.3, FR-A7) и демо-передачи (FR-A8).

Пороги (PENDING_OVERDUE_HOURS / ISSUE_STALE_HOURS / ISSUE_STALE_CARRIES)
вычисляются при отображении, в базе не хранятся (AL-5, A-5).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..config import Settings
from ..db import immediate_transaction
from ..errors import AppError
from ..validation import normalize_place_norm

# Вымышленные вопросы демо-передачи (FR-A8): без персональных данных (FR-A9).
_DEMO_ISSUES = [
    {
        "place": "Комната 5",
        "description": "В торшере перегорела лампа, гость просил заменить",
        "next_action": "Принести лампу E27 на стойку до 18:00",
    },
    {
        "place": "Кухня",
        "description": "Под мойкой медленно капает кран",
        "next_action": "Завтра до 10:00 прийти сантехнику, ключ на стойке",
    },
]


def create_demo_handover(
    session: Session, manager: models.User
) -> models.Handover:
    """FR-A8: «Создать демо-передачу» — сданная демонстрационная смена
    (владелец — управляющий, is_demo=1) с двумя вымышленными открытыми
    вопросами; принять может любой ADMIN отеля. Одновременно допускается одна
    непринятая демо-передача (DEMO_EXISTS)."""
    with immediate_transaction(session):
        now = models.utcnow()
        existing = session.scalar(
            select(models.Handover)
            .join(models.Shift, models.Shift.id == models.Handover.shift_id)
            .where(
                models.Handover.hotel_id == manager.hotel_id,
                models.Handover.status == models.HandoverStatus.PENDING,
                models.Shift.is_demo == 1,
            )
        )
        if existing is not None:
            raise AppError("DEMO_EXISTS", redirect="/overview")

        shift = models.Shift(
            hotel_id=manager.hotel_id, admin_id=manager.id,
            status=models.ShiftStatus.PENDING,
            opened_at=now, handed_at=now, is_demo=1,
        )
        session.add(shift)
        session.flush()

        handover = models.Handover(
            hotel_id=manager.hotel_id, shift_id=shift.id,
            sender_id=manager.id, sent_at=now,
            status=models.HandoverStatus.PENDING,
        )
        session.add(handover)
        session.flush()

        for data in _DEMO_ISSUES:
            issue = models.Issue(
                hotel_id=manager.hotel_id,
                place=data["place"],
                place_norm=normalize_place_norm(data["place"]),
                description=data["description"],
                next_action=data["next_action"],
                status=models.IssueStatus.OPEN,
                current_shift_id=shift.id,
                created_by=manager.id,
            )
            session.add(issue)
            session.flush()
            session.add(models.IssueEvent(
                hotel_id=manager.hotel_id, issue_id=issue.id,
                shift_id=shift.id, actor_id=manager.id,
                type=models.EventType.CREATED,
            ))
            session.add(models.HandoverItem(
                handover_id=handover.id, issue_id=issue.id,
                place=issue.place, description=issue.description,
                next_action=issue.next_action,
                status_at_handover=models.IssueStatus.OPEN,
            ))
        session.commit()
        return handover


def pending_for_overview(session: Session, hotel_id: int):
    """FR-3.1: все PENDING-передачи отеля (старые раньше)."""
    return list(session.execute(
        select(models.Handover, models.User)
        .join(models.User, models.User.id == models.Handover.sender_id)
        .where(
            models.Handover.hotel_id == hotel_id,
            models.Handover.status == models.HandoverStatus.PENDING,
        )
        .order_by(models.Handover.sent_at.asc(), models.Handover.id.asc())
    ))


def stale_issues(session: Session, hotel_id: int, settings: Settings,
                 now: datetime):
    """FR-3.2: открытые вопросы с handover_count >= ISSUE_STALE_CARRIES или
    открытые дольше ISSUE_STALE_HOURS. Возвращает (issue, админ текущей смены)."""
    cutoff = now - timedelta(hours=settings.issue_stale_hours)
    rows = session.execute(
        select(models.Issue, models.User)
        .join(models.Shift, models.Shift.id == models.Issue.current_shift_id)
        .join(models.User, models.User.id == models.Shift.admin_id)
        .where(
            models.Issue.hotel_id == hotel_id,
            models.Issue.status == models.IssueStatus.OPEN,
            (models.Issue.handover_count >= settings.issue_stale_carries)
            | (models.Issue.created_at <= cutoff),
        )
        .order_by(models.Issue.created_at.asc(), models.Issue.id.asc())
    )
    return list(rows)


def acceptance_stats(session: Session, hotel_id: int, now: datetime) -> tuple[int, int]:
    """FR-3.3: за 30 дней (сдано, принято) без демо-передач."""
    since = now - timedelta(days=30)
    rows = session.execute(
        select(models.Handover.status)
        .join(models.Shift, models.Shift.id == models.Handover.shift_id)
        .where(
            models.Handover.hotel_id == hotel_id,
            models.Handover.sent_at >= since,
            models.Shift.is_demo == 0,
        )
    ).all()
    handed = len(rows)
    accepted = sum(1 for (status,) in rows if status == models.HandoverStatus.ACCEPTED)
    return handed, accepted


def subscription_line(hotel: models.Hotel, now: datetime) -> str:
    """FR-A7: строка подписки — один из трёх фиксированных видов."""
    suffix = "790 руб./мес., до 10 учётных записей"
    if hotel.paid_until and hotel.paid_until >= now:
        return f"Подписка: оплачено до {hotel.paid_until:%d.%m.%Y} ({suffix})"
    if hotel.trial_ends_at > now:
        # ceil: «осталось N дн.» — полное число дней до окончания пилота
        days_left = max(1, math.ceil((hotel.trial_ends_at - now).total_seconds() / 86400))
        return f"Подписка: пилот, осталось {days_left} дн. ({suffix})"
    return f"Подписка: пилот закончился, ожидается оплата ({suffix})"
