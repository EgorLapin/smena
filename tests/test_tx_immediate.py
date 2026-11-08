"""Проверка механизма BEGIN IMMEDIATE (ТЗ, «Режим транзакций SQLite»).

Смысл: транзакция записи, открытая через db.immediate_transaction, обязана
СРАЗУ брать write-замок (RESERVED), не дожидаясь первого DML. Иначе две
параллельные сдачи/приёма могли бы «разъехаться» на проверках (AL-2/AL-3).

Тест моделирует второй процесс ВНС: отдельное raw-соединение sqlite3 с
busy_timeout=0. Если первый держит только отложенный READ-замок, вторая
BEGIN IMMEDIATE прошла бы — тест бы упал.
"""
from __future__ import annotations

import sqlite3

import pytest

from app import models
from app.db import get_engine, immediate_transaction, new_session

from factories import make_hotel, make_shift, make_user


def test_immediate_transaction_holds_write_lock_immediately(settings):
    # сессия 1: транзакция записи открыта, но DML ещё НЕ выполнялся
    session = new_session()
    ctx = immediate_transaction(session)
    ctx.__enter__()

    hotel = make_hotel(session)      # add + flush: первый DML внутри IMMEDIATE
    admin = make_user(session, hotel.id)
    make_shift(session, hotel.id, admin.id)

    # сессия 2 (модель конкурента): tries BEGIN IMMEDIATE без ожидания
    raw_path = str(settings.db_file)
    other = sqlite3.connect(raw_path, timeout=0)
    try:
        with pytest.raises(sqlite3.OperationalError):
            other.execute("BEGIN IMMEDIATE")
    finally:
        other.close()

    session.commit()
    ctx.__exit__(None, None, None)
    session.close()

    # после коммита замок освобождён — конкурент заходит свободно
    other = sqlite3.connect(raw_path, timeout=0)
    try:
        other.execute("BEGIN IMMEDIATE")
        other.execute("COMMIT")
    finally:
        other.close()


def test_immediate_transaction_rolls_back_on_error(session):
    from app.db import immediate_transaction as it

    # базовые данные — зафиксированы ДО транзакции (как делает сервис)
    hotel = make_hotel(session)
    admin = make_user(session, hotel.id)
    admin2 = make_user(session, hotel.id, login="admin2")
    make_shift(session, hotel.id, admin.id)
    session.commit()
    before = session.query(models.Shift).count()
    assert before == 1

    with pytest.raises(RuntimeError):
        with it(session):
            make_shift(session, hotel.id, admin2.id)  # внутри блока — запись
            raise RuntimeError("сбой в середине транзакции")

    session.rollback()
    assert session.query(models.Shift).count() == before


def test_immediate_transaction_requires_write_free_session(session):
    """Контракт:helper обязан отказаться, если сессия уже писала без commit."""
    from app.db import immediate_transaction as it

    hotel = make_hotel(session)  # flush = запись без commit
    with pytest.raises(RuntimeError, match="до первых записей"):
        with it(session):
            pass
