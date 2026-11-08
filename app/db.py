"""Слой доступа к БД: движок, сессии, PRAGMA SQLite.

Правила ТЗ (раздел «Хранение данных»):
- журнал WAL, synchronous=NORMAL;
- busy_timeout = 5000 мс;
- внешние ключи включены;
- запись — транзакциями BEGIN IMMEDIATE (это применят сервисы на T05+;
  здесь подготовлен helper immediate_transaction).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import Engine as SAEngine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _make_engine(settings: Settings) -> Engine:
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},  # uvicorn 1 воркер, но пул потоков
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - тривиально
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_engine(settings: Settings | None = None) -> Engine:
    """Создать (или вернуть существующий) движок. Вызывается из create_app."""
    global _engine, _session_factory
    if _engine is None:
        _engine = _make_engine(settings or get_settings())
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    assert _engine is not None, "движок БД не инициализирован (init_engine)"
    return _engine


def reset_engine() -> None:
    """Закрыть движок (между тестами / при остановке приложения)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def new_session() -> Session:
    assert _session_factory is not None, "движок БД не инициализирован (init_engine)"
    return _session_factory()


def get_db() -> Iterator[Session]:
    """FastAPI-зависимость: сессия на время запроса."""
    session = new_session()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def immediate_transaction(session: Session) -> Iterator[Session]:
    """Транзакция BEGIN IMMEDIATE (ТЗ: сериализация конкурентных записей).

    Использование (T05+):
        with immediate_transaction(session):
            ...изменения...
            session.commit()
    """
    connection = session.connection()
    connection.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        yield session
    except BaseException:
        session.rollback()
        raise
