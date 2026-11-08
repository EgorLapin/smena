"""Фабрики тестовых сущностей (напрямую через модели — сервисы появятся на T05+).

Хелперы принимают session первой; коммитит сам вызывающий тест (или no_autoflush
окружение), чтобы тесты могли ловить IntegrityError на flush.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app import models


def make_hotel(session, **kw) -> models.Hotel:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    kw.setdefault("name", "Отель «Тест»")
    kw.setdefault("timezone", "Asia/Novosibirsk")
    kw.setdefault("trial_ends_at", now + timedelta(days=14))
    hotel = models.Hotel(**kw)
    session.add(hotel)
    session.flush()
    return hotel


def make_user(
    session, hotel_id: int, login: str = "admin1", role: str = models.Role.ADMIN,
    **kw,
) -> models.User:
    kw.setdefault("display_name", "Иван П.")
    kw.setdefault("password_hash", "argon2:fake")
    user = models.User(hotel_id=hotel_id, login=login, role=role, **kw)
    session.add(user)
    session.flush()
    return user


def make_shift(
    session, hotel_id: int, admin_id: int, status: str = models.ShiftStatus.OPEN,
    **kw,
) -> models.Shift:
    if status != models.ShiftStatus.OPEN:
        kw.setdefault("handed_at", datetime.now(timezone.utc).replace(tzinfo=None))
    shift = models.Shift(hotel_id=hotel_id, admin_id=admin_id, status=status, **kw)
    session.add(shift)
    session.flush()
    return shift


def make_issue(session, hotel_id: int, shift_id: int, author_id: int,
               place: str = "Комната 12", **kw) -> models.Issue:
    kw.setdefault("description", "Передать дополнительное одеяло")
    issue = models.Issue(
        hotel_id=hotel_id,
        place=place,
        place_norm=place.lower().replace("ё", "е"),
        current_shift_id=shift_id,
        created_by=author_id,
        **kw,
    )
    session.add(issue)
    session.flush()
    return issue


def make_handover(
    session, hotel_id: int, shift_id: int, sender_id: int, **kw
) -> models.Handover:
    handover = models.Handover(
        hotel_id=hotel_id, shift_id=shift_id, sender_id=sender_id, **kw
    )
    session.add(handover)
    session.flush()
    return handover


def make_item(session, handover_id: int, issue_id: int,
              status_at_handover: str = models.IssueStatus.OPEN,
              **kw) -> models.HandoverItem:
    kw.setdefault("place", "Комната 12")
    kw.setdefault("description", "Передать дополнительное одеяло")
    item = models.HandoverItem(
        handover_id=handover_id,
        issue_id=issue_id,
        status_at_handover=status_at_handover,
        **kw,
    )
    session.add(item)
    session.flush()
    return item


def make_event(
    session, hotel_id: int, issue_id: int, shift_id: int, actor_id: int,
    type_: str = models.EventType.CREATED, **kw,
) -> models.IssueEvent:
    event_row = models.IssueEvent(
        hotel_id=hotel_id, issue_id=issue_id, shift_id=shift_id,
        actor_id=actor_id, type=type_, **kw,
    )
    session.add(event_row)
    session.flush()
    return event_row
