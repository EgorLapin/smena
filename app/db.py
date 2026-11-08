"""Слой доступа к БД: движок, сессии, PRAGMA SQLite, создание схемы.

Правила ТЗ (архитектурная схема + NFR-1/NFR-4):
- журнал WAL, synchronous=NORMAL, busy_timeout=5000, внешние ключи ON;
- операции записи — транзакциями BEGIN IMMEDIATE (helper immediate_transaction,
  применяют сервисы на T05+);
- схема (таблицы, индексы, триггеры) создаётся init_db() с нуля (критерий T02).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from . import models  # noqa: F401 — регистрация таблиц в Base.metadata
from .config import Settings, get_settings
from .models import Base

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _database_url(settings: Settings) -> str:
    db_file = settings.db_file
    db_file.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_file.as_posix()}"


def _make_engine(settings: Settings) -> Engine:
    engine = create_engine(
        _database_url(settings),
        connect_args={"check_same_thread": False},  # uvicorn 1 воркер, но пул потоков
        pool_pre_ping=True,
        # ВНИМАНИЕ (проверено на поведении pysqlite): isolation_level остаётся
        # отложенным — чтение не берёт write-замок. Транзакции ЗАПИСИ обёрнуты
        # в immediate_transaction() (BEGIN IMMEDIATE до первого DML), и только
        # так пишут сервисы (AL-2/AL-3). pysqlite не вставит свой BEGIN внутрь
        # уже открытой транзакции (проверяет sqlite3_get_autocommit).
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


def init_db() -> None:
    """Создать схему с нуля: таблицы, индексы, CHECK-ограничения, триггеры.

    create_all идемпотентен (проверяет наличие таблиц); триггеры создаются
    слушателем after_create в models.py.
    """
    Base.metadata.create_all(get_engine())


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
    """Транзакция BEGIN IMMEDIATE (ТЗ: записи выполняются строго по очереди).

    Контракт использования (сервисы T04+):
        with immediate_transaction(session):
            ...изменения...
            session.commit()
    - вызывать ДО первых записей сессии (чтение — можно: pysqlite открывает
      транзакцию только на DML, read-запросы не мешают);
    - BEGIN IMMEDIATE берёт write-замок сразу — конкурентная запись встанет
      в очередь на busy_timeout=5000 вместо гонки на проверках;
    - при исключении — rollback.
    """
    connection = session.connection()
    dbapi = connection.connection.dbapi_connection
    if dbapi.in_transaction:
        raise RuntimeError(
            "immediate_transaction() должен вызываться до первых записей сессии "
            "(сначала service-чтения, затем — этот блок)"
        )
    connection.exec_driver_sql("BEGIN IMMEDIATE")
    try:
        yield session
    except BaseException:
        session.rollback()
        raise
