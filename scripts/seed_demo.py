#!/usr/bin/env python3
"""Заполнение базы вымышленными данными для демонстрации (FR-A8, T14).

Создаёт (если ещё нет) отель «Северная усадьба» с управляющим и двумя
администраторами, одну принятую передачу с перенесённым вопросом, один
просроченный вопрос и одну непринятую передачу — обзор управляющего выглядит
живым. Все данные вымышленные, персональных данных гостей нет (FR-A9).

    python scripts/seed_demo.py            # DATABASE_PATH из .env/окружения

Пароли всех учётных записей: demo-pass-123. Вывод — список логинов.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db, models  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services import journal as journal_service  # noqa: E402
from app.validation import normalize_place_norm  # noqa: E402

PASSWORD = "demo-pass-123"
NOW = models.utcnow()


def _user(session, hotel_id, login, name, role):
    existing = session.query(models.User).filter_by(login=login).first()
    if existing is not None:
        return existing
    user = models.User(hotel_id=hotel_id, login=login, display_name=name,
                       password_hash=hash_password(PASSWORD), role=role)
    session.add(user)
    session.flush()
    return user


def _issue(session, hotel_id, shift_id, author_id, place, description,
           next_action=None, status=models.IssueStatus.OPEN, created_at=None):
    issue = models.Issue(
        hotel_id=hotel_id, place=place, place_norm=normalize_place_norm(place),
        description=description, next_action=next_action, status=status,
        current_shift_id=shift_id, created_by=author_id,
        created_at=created_at or models.utcnow(),
    )
    if status == models.IssueStatus.CLOSED:
        issue.closed_by = author_id
        issue.closed_at = models.utcnow()
    session.add(issue)
    session.flush()
    session.add(models.IssueEvent(
        hotel_id=hotel_id, issue_id=issue.id, shift_id=shift_id,
        actor_id=author_id, type=models.EventType.CREATED,
        created_at=issue.created_at,
    ))
    return issue


def main() -> int:
    settings = get_settings()
    db.init_engine(settings)
    db.init_db()

    session = db.new_session()
    try:
        if session.query(models.Hotel).filter_by(name="Северная усадьба").first():
            print("Демо-отель уже существует — ничего не добавляем")
            return 0

        with db.immediate_transaction(session):
            hotel = models.Hotel(
                name="Северная усадьба", timezone="Asia/Novosibirsk",
                created_at=NOW - timedelta(days=5),
                trial_ends_at=NOW + timedelta(days=9),
            )
            session.add(hotel)
            session.flush()

            manager = _user(session, hotel.id, "demo-manager", "Мария Управляющая",
                            models.Role.MANAGER)
            anna = _user(session, hotel.id, "demo-anna", "Анна Смена",
                         models.Role.ADMIN)
            ivan = _user(session, hotel.id, "demo-ivan", "Иван Ночной",
                         models.Role.ADMIN)

            # Смена 1 (Анна, вчера вечером) — сдана и принята Иваном
            shift1 = models.Shift(hotel_id=hotel.id, admin_id=anna.id,
                                  status=models.ShiftStatus.ACCEPTED,
                                  opened_at=NOW - timedelta(hours=20),
                                  handed_at=NOW - timedelta(hours=18))
            session.add(shift1)
            session.flush()

            i1 = _issue(session, hotel.id, shift1.id, anna.id,
                        "Комната 7", "Гость просит рано разбудить к поезду",
                        "Будильник на 06:15, записать в журнал",
                        created_at=NOW - timedelta(hours=26))
            i2 = _issue(session, hotel.id, shift1.id, anna.id,
                        "Котельная", "Давление ниже нормы, мастер обещал заехать",
                        "Проверить в 22:00 и в 06:00",
                        created_at=NOW - timedelta(hours=19))
            _issue(session, hotel.id, shift1.id, anna.id,
                   "Комната 2", "Забытое зарядное устройство на стойке",
                   "Выдать владельцу при звонке",
                   status=models.IssueStatus.CLOSED,
                   created_at=NOW - timedelta(hours=19))

            handover1 = models.Handover(hotel_id=hotel.id, shift_id=shift1.id,
                                        sender_id=anna.id,
                                        sent_at=NOW - timedelta(hours=18),
                                        status=models.HandoverStatus.PENDING)
            session.add(handover1)
            session.flush()
            for issue in (i1, i2):
                session.add(models.HandoverItem(
                    handover_id=handover1.id, issue_id=issue.id,
                    place=issue.place, description=issue.description,
                    next_action=issue.next_action,
                    status_at_handover=models.IssueStatus.OPEN))

            # Смена 2 (Иван) — текущая открытая: i1 перенесён 1 раз (открыт 26 ч —
            # «затянулся»), i2 закрыт ночью. Смена не сдана — журнал живой.
            shift2 = models.Shift(hotel_id=hotel.id, admin_id=ivan.id,
                                  status=models.ShiftStatus.OPEN,
                                  opened_at=NOW - timedelta(hours=18))
            session.add(shift2)
            session.flush()
            handover1.status = models.HandoverStatus.ACCEPTED
            handover1.receiver_id = ivan.id
            handover1.accepted_at = NOW - timedelta(hours=18)
            handover1.receiver_shift_id = shift2.id

            i1.current_shift_id = shift2.id
            i1.handover_count = 1
            session.add(models.IssueEvent(
                hotel_id=hotel.id, issue_id=i1.id, shift_id=shift2.id,
                actor_id=ivan.id, type=models.EventType.CARRIED,
                created_at=NOW - timedelta(hours=18),
                payload=f'{{"handover_id": {handover1.id}, "from_shift_id": '
                        f'{shift1.id}, "to_shift_id": {shift2.id}}}'))
            i2.status = models.IssueStatus.CLOSED
            i2.closed_by = ivan.id
            i2.closed_at = NOW - timedelta(hours=6)
            session.add(models.IssueEvent(
                hotel_id=hotel.id, issue_id=i2.id, shift_id=shift2.id,
                actor_id=ivan.id, type=models.EventType.CLOSED,
                created_at=NOW - timedelta(hours=6)))

            # Просроченная передача: Анна открыла смену 14 ч назад и сдала 13 ч
            # назад — на обзоре «Просрочено» (порог 12 ч), журнал Ивана живой.
            shift3 = models.Shift(hotel_id=hotel.id, admin_id=anna.id,
                                  status=models.ShiftStatus.PENDING,
                                  opened_at=NOW - timedelta(hours=14),
                                  handed_at=NOW - timedelta(hours=13))
            session.add(shift3)
            session.flush()
            i3 = _issue(session, hotel.id, shift3.id, anna.id,
                        "Холл", "Протечность у окна, ведёрко подставлено",
                        "Ждём мастера днём",
                        created_at=NOW - timedelta(hours=13, minutes=30))
            handover2 = models.Handover(hotel_id=hotel.id, shift_id=shift3.id,
                                        sender_id=ivan.id,
                                        sent_at=NOW - timedelta(hours=13),
                                        status=models.HandoverStatus.PENDING)
            session.add(handover2)
            session.flush()
            session.add(models.HandoverItem(
                handover_id=handover2.id, issue_id=i3.id,
                place=i3.place, description=i3.description,
                next_action=i3.next_action,
                status_at_handover=models.IssueStatus.OPEN))

            session.commit()

        print("Демо-данные созданы. Логины (пароль все: demo-pass-123):")
        print("  demo-manager — управляющий (обзор, история, сотрудники)")
        print("  demo-anna    — администратор смены")
        print("  demo-ivan    — администратор смены")
        print("Отель: «Северная усадьба», часовой пояс Asia/Novosibirsk")
        return 0
    finally:
        session.close()
        db.reset_engine()


if __name__ == "__main__":
    raise SystemExit(main())
