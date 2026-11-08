"""Модели SQLAlchemy — строго по разделу «Модель данных» ТЗ v1.1.2.

Сущности: Hotel, User, Shift, Issue, Handover, HandoverItem, IssueEvent.

Особенности реализации:
- Временные метки хранятся в UTC (NFR-7) как TEXT в формате ISO-8601
  ("YYYY-MM-DD HH:MM:SS", суффикс "UTC" не пишем) — так текстовый порядок
  совпадает с хронологическим и сравнения в SQLite корректны.
- Частичные уникальные индексы (SQLite `WHERE`) реализуют
  «не более одной OPEN-смены на администратора» и «один MANAGER на отель».
- CHECK-ограничения из ТЗ: CLOSED только с closed_by/closed_at;
  обязателен handed_at при PENDING/ACCEPTED; receiver_id != sender_id и др.
- Неизменяемость истории (NFR-5): триггеры no-update/no-delete на
  HandoverItem и IssueEvent создаются слушателем after_create.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Текущее время в UTC (наивное, для колонки DateTime без tz)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Role:
    """Роли (A-1). Строки в БД; Enum-таблицы нет (МVP)."""

    MANAGER = "MANAGER"
    ADMIN = "ADMIN"


class ShiftStatus:
    OPEN = "OPEN"
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"


class IssueStatus:
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class HandoverStatus:
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"


class EventType:
    CREATED = "CREATED"
    EDITED = "EDITED"
    CLOSED = "CLOSED"
    CARRIED = "CARRIED"


class Hotel(Base):
    __tablename__ = "hotel"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    trial_ends_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    paid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class User(Base):
    __tablename__ = "user"
    __table_args__ = (
        # UNIQUE login глобально (логин хранится уже в нижнем регистре — AL-1)
        UniqueConstraint("login", name="uq_user_login"),
        CheckConstraint("role IN ('MANAGER', 'ADMIN')", name="ck_user_role"),
        CheckConstraint("is_active IN (0, 1)", name="ck_user_active"),
        # Один MANAGER на отель (частичный уникальный индекс по ТЗ)
        Index(
            "uq_user_one_manager_per_hotel",
            "hotel_id",
            unique=True,
            sqlite_where=text("role = 'MANAGER'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotel.id"), nullable=False)
    login: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


class Shift(Base):
    __tablename__ = "shift"
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN', 'PENDING', 'ACCEPTED')", name="ck_shift_status"
        ),
        # handed_at обязателен при PENDING и ACCEPTED (по таблице ТЗ)
        CheckConstraint(
            "status = 'OPEN' OR handed_at IS NOT NULL", name="ck_shift_handed_at"
        ),
        CheckConstraint("is_demo IN (0, 1)", name="ck_shift_is_demo"),
        # Не более одной OPEN-смены на администратора (частичный уникальный индекс)
        Index(
            "uq_shift_one_open_per_admin",
            "admin_id",
            unique=True,
            sqlite_where=text("status = 'OPEN'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotel.id"), nullable=False)
    admin_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=ShiftStatus.OPEN)
    opened_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    handed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_demo: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Issue(Base):
    __tablename__ = "issue"
    __table_args__ = (
        CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_issue_status"),
        # CLOSED только вместе с closed_by и closed_at (ТЗ, «Ключи и ограничения»)
        CheckConstraint(
            "status = 'OPEN' OR (closed_by IS NOT NULL AND closed_at IS NOT NULL)",
            name="ck_issue_closed_fields",
        ),
        CheckConstraint("handover_count >= 0", name="ck_issue_hc_nonneg"),
        Index("ix_issue_hotel_status", "hotel_id", "status"),
        Index("ix_issue_current_shift", "current_shift_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # постоянный id (AL)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotel.id"), nullable=False)
    place: Mapped[str] = mapped_column(Text, nullable=False)
    place_norm: Mapped[str] = mapped_column(Text, nullable=False)  # поиск (AL-1)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default=IssueStatus.OPEN)
    current_shift_id: Mapped[int] = mapped_column(ForeignKey("shift.id"), nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    handover_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Handover(Base):
    __tablename__ = "handover"
    __table_args__ = (
        # Одна смена сдаётся один раз (ТЗ)
        UniqueConstraint("shift_id", name="uq_handover_shift"),
        CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED')", name="ck_handover_status"
        ),
        # При ACCEPTED receiver/accepted_at/receiver_shift_id обязательны
        CheckConstraint(
            "status = 'PENDING' OR (receiver_id IS NOT NULL AND accepted_at IS NOT NULL"
            " AND receiver_shift_id IS NOT NULL)",
            name="ck_handover_accept_fields",
        ),
        CheckConstraint("receiver_id IS NULL OR receiver_id != sender_id",
                        name="ck_handover_receiver_not_sender"),
        Index("ix_handover_hotel_status", "hotel_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # id передачи != id смены
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotel.id"), nullable=False)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shift.id"), nullable=False)
    sender_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default=HandoverStatus.PENDING
    )
    receiver_id: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    receiver_shift_id: Mapped[int | None] = mapped_column(
        ForeignKey("shift.id"), nullable=True
    )


class HandoverItem(Base):
    """Снимок вопроса на момент сдачи. Неизменяем (триггеры, NFR-5)."""

    __tablename__ = "handover_item"
    __table_args__ = (
        UniqueConstraint("handover_id", "issue_id", name="uq_handover_item"),
        CheckConstraint(
            "status_at_handover IN ('OPEN', 'CLOSED')", name="ck_hitem_status"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    handover_id: Mapped[int] = mapped_column(ForeignKey("handover.id"), nullable=False)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issue.id"), nullable=False)
    place: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    status_at_handover: Mapped[str] = mapped_column(Text, nullable=False)


class IssueEvent(Base):
    """Журнал событий вопроса. Неизменяем и только дополняется (NFR-5).

    shift_id — смена, в рамках которой выполнено действие; для CARRIED это
    смена получателя (to_shift_id), а исходная смена — в payload (ТЗ).
    Payload: EDITED {"поле": [было, стало]};
             CARRIED {"handover_id", "from_shift_id", "to_shift_id"}.
    """

    __tablename__ = "issue_event"
    __table_args__ = (
        CheckConstraint(
            "type IN ('CREATED', 'EDITED', 'CLOSED', 'CARRIED')",
            name="ck_event_type",
        ),
        Index("ix_event_issue_created", "issue_id", "created_at"),
        Index("ix_event_hotel_created", "hotel_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotel.id"), nullable=False)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issue.id"), nullable=False)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shift.id"), nullable=False)
    actor_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-строка
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


# --- Триггеры неизменяемости (NFR-5): UPDATE и DELETE запрещены -------------

_IMMUTABLE_TABLES = ("handover_item", "issue_event")


def _immutability_ddl(table: str) -> tuple[str, str]:
    return (
        f"CREATE TRIGGER trg_{table}_no_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END;",
        f"CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, '{table} is immutable'); END;",
    )


def _attach_immutability_triggers(table, connection, **_kw) -> None:
    for ddl in _immutability_ddl(table.name):
        connection.exec_driver_sql(ddl)


for _name in _IMMUTABLE_TABLES:
    event.listen(
        Base.metadata.tables[_name],
        "after_create",
        _attach_immutability_triggers,
    )
