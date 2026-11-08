"""T02 — схема базы: создание с нуля, ограничения, триггеры неизменяемости.

Критерий готовности T02: «База создаётся с нуля; тесты подтверждают срабатывание
ограничений и триггеров».
"""
from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app import models
from app.db import get_engine

from factories import (
    make_event,
    make_handover,
    make_hotel,
    make_issue,
    make_item,
    make_shift,
    make_user,
)

EXPECTED_TABLES = {
    "hotel", "user", "shift", "issue", "handover", "handover_item", "issue_event",
}


# --- Создание схемы ----------------------------------------------------------


def test_schema_created_with_all_tables_and_triggers(settings):
    insp = inspect(get_engine())
    assert EXPECTED_TABLES <= set(insp.get_table_names())
    trigger_names = {t["name"] for t in insp.get_check_constraints("issue")}  # smoke: есть CHECK
    assert trigger_names, "на issue ожидаем CHECK-ограничения"

    with get_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='trigger'")
        ).scalars().all()
    expected_triggers = {
        "trg_handover_item_no_update", "trg_handover_item_no_delete",
        "trg_issue_event_no_update", "trg_issue_event_no_delete",
    }
    assert expected_triggers <= set(rows)


def test_init_db_is_idempotent(settings):
    from app.db import init_db

    init_db()  # повторный вызов не роняет приложение (create_all идемпотентен)


# --- Триггеры неизменяемости (NFR-5) -----------------------------------------


def test_handover_item_update_forbidden(session):
    hotel = make_hotel(session)
    admin = make_user(session, hotel.id)
    shift = make_shift(session, hotel.id, admin.id)
    issue = make_issue(session, hotel.id, shift.id, admin.id)
    shift2 = make_shift(session, hotel.id, admin.id, status=models.ShiftStatus.PENDING)
    handover = make_handover(session, hotel.id, shift2.id, admin.id)
    item = make_item(session, handover.id, issue.id)

    item.description = "переписали"
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_handover_item_delete_forbidden(session):
    hotel = make_hotel(session)
    admin = make_user(session, hotel.id)
    shift = make_shift(session, hotel.id, admin.id)
    issue = make_issue(session, hotel.id, shift.id, admin.id)
    shift2 = make_shift(session, hotel.id, admin.id, status=models.ShiftStatus.PENDING)
    handover = make_handover(session, hotel.id, shift2.id, admin.id)
    item = make_item(session, handover.id, issue.id)

    session.delete(item)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_issue_event_update_and_delete_forbidden(session):
    hotel = make_hotel(session)
    admin = make_user(session, hotel.id)
    shift = make_shift(session, hotel.id, admin.id)
    issue = make_issue(session, hotel.id, shift.id, admin.id)
    event_row = make_event(session, hotel.id, issue.id, shift.id, admin.id)
    session.commit()  # фиксируем событие, чтобы дальше проверять UPDATE/DELETE

    event_row.type = models.EventType.EDITED  # переписываем историю
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()

    session.delete(event_row)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_issue_event_insert_allowed(session):
    """Триггеры не мешают дополнению журнала (только дополняется)."""
    hotel = make_hotel(session)
    admin = make_user(session, hotel.id)
    shift = make_shift(session, hotel.id, admin.id)
    issue = make_issue(session, hotel.id, shift.id, admin.id)
    ev = make_event(session, hotel.id, issue.id, shift.id, admin.id)
    assert ev.id is not None


# --- CHECK-ограничения --------------------------------------------------------


def test_issue_closed_requires_closed_fields(session):
    hotel = make_hotel(session)
    admin = make_user(session, hotel.id)
    shift = make_shift(session, hotel.id, admin.id)
    issue = make_issue(session, hotel.id, shift.id, admin.id)

    issue.status = models.IssueStatus.CLOSED  # без closed_by/closed_at — нельзя
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_shift_pending_requires_handed_at(session):
    hotel = make_hotel(session)
    admin = make_user(session, hotel.id)
    shift = models.Shift(hotel_id=hotel.id, admin_id=admin.id,
                         status=models.ShiftStatus.PENDING)  # handed_at нет
    session.add(shift)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_handover_receiver_must_differ_from_sender(session):
    hotel = make_hotel(session)
    admin = make_user(session, hotel.id)
    shift = make_shift(session, hotel.id, admin.id, status=models.ShiftStatus.PENDING)
    handover = make_handover(session, hotel.id, shift.id, admin.id)

    handover.receiver_id = admin.id  # автор принял свою передачу — CHECK не даст
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_handover_accepted_requires_receiver_fields(session):
    hotel = make_hotel(session)
    admin = make_user(session, hotel.id)
    admin2 = make_user(session, hotel.id, login="admin2")
    shift = make_shift(session, hotel.id, admin.id, status=models.ShiftStatus.PENDING)
    shift2 = make_shift(session, hotel.id, admin2.id)
    handover = make_handover(session, hotel.id, shift.id, admin.id)

    handover.status = models.HandoverStatus.ACCEPTED  # без receiver_id/accepted_at
    handover.receiver_shift_id = shift2.id
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_handover_one_per_shift_unique(session):
    """UNIQUE shift_id: одна смена сдаётся один раз (AL-2)."""
    hotel = make_hotel(session)
    admin = make_user(session, hotel.id)
    shift = make_shift(session, hotel.id, admin.id, status=models.ShiftStatus.PENDING)
    make_handover(session, hotel.id, shift.id, admin.id)

    with pytest.raises(IntegrityError):
        make_handover(session, hotel.id, shift.id, admin.id)


def test_handover_item_unique_per_issue(session):
    hotel = make_hotel(session)
    admin = make_user(session, hotel.id)
    shift = make_shift(session, hotel.id, admin.id)
    issue = make_issue(session, hotel.id, shift.id, admin.id)
    shift2 = make_shift(session, hotel.id, admin.id, status=models.ShiftStatus.PENDING)
    handover = make_handover(session, hotel.id, shift2.id, admin.id)
    make_item(session, handover.id, issue.id)

    with pytest.raises(IntegrityError):
        make_item(session, handover.id, issue.id)  # второй снимок того же вопроса


# --- Частичные уникальные индексы ---------------------------------------------


def test_one_open_shift_per_admin(session):
    hotel = make_hotel(session)
    admin = make_user(session, hotel.id)
    first = make_shift(session, hotel.id, admin.id)
    session.commit()

    with pytest.raises(IntegrityError):
        make_shift(session, hotel.id, admin.id)  # вторая OPEN-смена — нельзя

    session.rollback()
    # А вот вторая OPEN-смена после сдачи первой — можно (A-2):
    first.status = models.ShiftStatus.PENDING
    first.handed_at = models.utcnow()
    session.commit()
    second = make_shift(session, hotel.id, admin.id)
    assert second.id is not None


def test_one_manager_per_hotel(session):
    hotel = make_hotel(session)
    make_user(session, hotel.id, login="boss", role=models.Role.MANAGER)

    with pytest.raises(IntegrityError):
        make_user(session, hotel.id, login="boss2", role=models.Role.MANAGER)

    session.rollback()
    # Второй MANAGER в ДРУГОМ отеле — можно
    hotel2 = make_hotel(session, name="Отель «Второй»")
    make_user(session, hotel2.id, login="boss2", role=models.Role.MANAGER)


def test_login_unique_globally(session):
    hotel1 = make_hotel(session)
    hotel2 = make_hotel(session, name="Отель «Второй»")
    make_user(session, hotel1.id, login="anna")
    with pytest.raises(IntegrityError):
        make_user(session, hotel2.id, login="anna")  # логин уникален во всей системе


# --- Внешние ключи --------------------------------------------------------------


def test_foreign_keys_enforced(session):
    with pytest.raises(IntegrityError):
        make_shift(session, hotel_id=9999, admin_id=9999)


def test_issue_place_norm_and_defaults(session):
    hotel = make_hotel(session)
    admin = make_user(session, hotel.id)
    shift = make_shift(session, hotel.id, admin.id)
    issue = make_issue(session, hotel.id, shift.id, admin.id, place="Ёлка  Ёлка")
    assert issue.status == models.IssueStatus.OPEN
    assert issue.handover_count == 0
