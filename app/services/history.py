"""Сервис истории (FR-3.4 — FR-3.5; экран «История», T10; AC-5, AC-8).

Поиск по месту — подстрока place_norm без учёта регистра (AL-1: поиск тоже
нормализуется, потому «кух» находит «Кухня», а «ё» совпадает с «е»).

Фильтр по датам — включительно и в часовом поясе отеля: интервал [начало дня
date_from, конец дня date_to] пересчитывается в UTC, вопрос подходит, если у
него есть событие в интервале (FR-3.4).

Выдача — 50 записей на страницу, новые события выше (упорядочивание по времени
последнего события вопроса).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models
from ..times import to_local
from ..validation import clean, normalize_place_norm

PAGE_SIZE = 50  # FR-3.4


def last_event_at_expr():
    """Время последнего события вопроса (для порядка «новые события выше»)."""
    return (
        select(func.max(models.IssueEvent.created_at))
        .where(models.IssueEvent.issue_id == models.Issue.id)
        .correlate(models.Issue)
        .scalar_subquery()
    )


def day_bounds_utc(day: date, tz_name: str) -> tuple[str, str]:
    """Границы местного дня в UTC-строках формата меток БД (D-009).

    Начало дня → конец дня включительно: [00:00:00, 23:59:59.999999] местного
    времени, переведённые в UTC. compare с TEXT-метками корректен, потому что
    текстовый порядок совпадает с хронологическим."""
    start_local = datetime.combine(day, time.min)
    end_local = start_local + timedelta(days=1)
    zone = ZoneInfo(tz_name)
    start_utc = start_local.replace(tzinfo=zone).astimezone(
        timezone.utc).replace(tzinfo=None)
    end_utc = end_local.replace(tzinfo=zone).astimezone(
        timezone.utc).replace(tzinfo=None)
    # конец дня — эксклюзивная граница: < end_utc
    return start_utc.strftime("%Y-%m-%d %H:%M:%S"), end_utc.strftime("%Y-%m-%d %H:%M:%S")


def search_issues(
    session: Session, hotel_id: int, tz_name: str, *,
    place: str = "", date_from: date | None = None, date_to: date | None = None,
    status: str = "all", page: int = 1,
) -> tuple[list[tuple[models.Issue, datetime, models.User | None]], int]:
    """Возвращает (строки [(issue, last_event_at, текущий админ)], всего_записей)."""
    stmt = select(models.Issue, last_event_at_expr())
    conditions = [models.Issue.hotel_id == hotel_id]

    q = normalize_place_norm(clean(place))
    if q:
        conditions.append(models.Issue.place_norm.contains(q))

    if status in (models.IssueStatus.OPEN, models.IssueStatus.CLOSED):
        conditions.append(models.Issue.status == status)

    # Дата: у вопроса есть событие в интервале (FR-3.4).
    if date_from is not None or date_to is not None:
        start = None
        end = None
        if date_from is not None:
            start, _ = day_bounds_utc(date_from, tz_name)
        if date_to is not None:
            _, end = day_bounds_utc(date_to, tz_name)
        event_conditions = []
        if start:
            event_conditions.append(models.IssueEvent.created_at >= start)
        if end:
            event_conditions.append(models.IssueEvent.created_at < end)
        sub = (
            select(models.IssueEvent.id)
            .where(models.IssueEvent.issue_id == models.Issue.id, *event_conditions)
            .correlate(models.Issue)
            .exists()
        )
        conditions.append(sub)

    stmt = stmt.where(*conditions)
    total = session.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0

    page = max(1, page)
    rows = session.execute(
        stmt.order_by(last_event_at_expr().desc(), models.Issue.id.desc())
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
    ).all()

    # Текущий администратор смены вопроса (для cột «сейчас у»).
    result = []
    for issue, last_event in rows:
        shift = session.get(models.Shift, issue.current_shift_id)
        admin = session.get(models.User, shift.admin_id) if shift else None
        result.append((issue, last_event, admin))
    return result, total


def history_handovers(session: Session, hotel_id: int) -> list[dict]:
    """FR-3.5: передачи отеля, новые выше: кто сдал, время сдачи, статус,
    получатель и время приёма, число вопросов, ссылка на снимок."""
    from sqlalchemy.orm import aliased

    sender = aliased(models.User)
    receiver = aliased(models.User)
    rows = session.execute(
        select(models.Handover, sender, receiver)
        .join(sender, sender.id == models.Handover.sender_id)
        .outerjoin(receiver, receiver.id == models.Handover.receiver_id)
        .where(models.Handover.hotel_id == hotel_id)
        .order_by(models.Handover.sent_at.desc(), models.Handover.id.desc())
    ).all()

    result = []
    for handover, sender_user, receiver_user in rows:
        count = session.scalar(
            select(func.count())
            .select_from(models.HandoverItem)
            .where(models.HandoverItem.handover_id == handover.id)
        ) or 0
        result.append({
            "handover": handover,
            "sender_name": sender_user.display_name,
            "receiver_name": receiver_user.display_name if receiver_user else "",
            # имя не items: в Jinja row.items резолвится в метод dict.items
            "questions_count": count,
        })
    return result
