"""T10 — история вопросов и передач (FR-3.4, FR-3.5; AC-5, AC-8).

AC-8: фрагмент «кух» находит «Кухня» (регистр и «ё» не влияют); фильтр по дате
включает границы дня в часовом поясе отеля.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from web import (
    create_admin_via_db,
    create_issue,
    db_scalar,
    get_csrf,
    login,
    open_shift,
    register_hotel,
)


def _backdate(table: str, column: str, row_id: int, delta: timedelta) -> None:
    from app import db
    from sqlalchemy import text

    session = db.new_session()
    try:
        raw = session.execute(
            text(f"SELECT {column} FROM {table} WHERE id=:i"), {"i": row_id}
        ).scalar()
        session.execute(text(f"UPDATE {table} SET {column}=:v WHERE id=:i"),
                        {"i": row_id, "v": datetime.fromisoformat(str(raw)) - delta})
        session.commit()
    finally:
        session.close()


def _setup(client: TestClient):
    """Отель (Новосибирск, UTC+7), Анна-ADMIN; возвращает вход под manager."""
    register_hotel(client, timezone="Asia/Novosibirsk")
    create_admin_via_db(client, "anna", "Анна К.")


def _as_manager(client: TestClient):
    client.cookies.clear()
    assert login(client, "manager").status_code == 303


def _as_anna(client: TestClient):
    client.cookies.clear()
    assert login(client, "anna").status_code == 303


# --- AC-8: поиск по месту ----------------------------------------------------------


def test_search_case_insensitive_and_yo(client):
    _setup(client)
    _as_anna(client)
    open_shift(client)
    create_issue(client, place="Кухня", description="Пахнет краской")
    create_issue(client, place="Холл", description="Ожидает уборка")
    _as_manager(client)

    for query in ("кух", "КУХ", "Кух"):
        page = client.get(f"/history?tab=issues&place={query}")
        assert page.status_code == 200
        assert "Пахнет краской" in page.text, query
        assert "Ожидает уборка" not in page.text, query

    # «ё» в запросе находит «е» в данных (AL-1 нормализует и запрос) — см.
    # test_search_yo_matches_e
    page = client.get("/history?tab=issues&place=мез")
    assert "Ничего не найдено" in page.text


def test_search_yo_matches_e(client):
    _setup(client)
    _as_anna(client)
    open_shift(client)
    create_issue(client, place="Кухня", description="Пол в углу мокрый")
    # вопрос с «е», который ищем через «ё»
    create_issue(client, place="Комната-весь этаж", description="Весь этаж без воды")
    _as_manager(client)
    # «весь» с ё в запросе → «весь» в данных (нормализация ё→е с обеих сторон)
    page = client.get("/history?tab=issues&place=вёс")
    assert "Весь этаж без воды" in page.text


def test_search_empty_returns_all(client):
    _setup(client)
    _as_anna(client)
    open_shift(client)
    create_issue(client, place="Кухня", description="A")
    create_issue(client, place="Комната 2", description="B")
    _as_manager(client)
    page = client.get("/history?tab=issues")
    assert "A" in page.text and "B" in page.text
    assert "(2 записи)" in page.text  # русская плюрализация счётчика


# --- AC-8: фильтр по дате — границы дня в поясе отеля -------------------------------


def _insert_event(issue_id: int, type_: str, when: datetime, actor: str = "manager") -> None:
    """Добавить событию вопроса фиксированное время (события неизменяемы —
    триггер, поэтому вставка, а не правка)."""
    from app import db
    from sqlalchemy import text

    session = db.new_session()
    try:
        issue = session.execute(text("SELECT hotel_id, current_shift_id FROM issue "
                                     "WHERE id=:i"), {"i": issue_id}).first()
        actor_id = session.execute(
            text("SELECT id FROM user WHERE login=:l"), {"l": actor}).scalar()
        session.execute(
            text("INSERT INTO issue_event (hotel_id, issue_id, shift_id, actor_id,"
                 " type, created_at) VALUES (:h, :i, :s, :a, :t, :v)"),
            {"h": issue[0], "i": issue_id, "s": issue[1], "a": actor_id,
             "t": type_, "v": when})
        session.commit()
    finally:
        session.close()


def test_date_filter_includes_day_boundaries_hotel_tz(client):
    """Событие в 00:30 местного времени Новосибирска (=17:30 UTC предыдущего дня)
    попадает в фильтр date_from=date_to=этого дня — граница включительно, пояс отеля."""
    _setup(client)
    _as_anna(client)
    open_shift(client)
    create_issue(client, place="Ранний", description="Событие в 00:30 по Новосибирску")
    issue_id = int(db_scalar("SELECT id FROM issue"))
    _insert_event(issue_id, "EDITED", datetime(2026, 11, 8, 17, 30))  # 09.11 00:30 +07

    _as_manager(client)
    page = client.get("/history?tab=issues&date_from=2026-11-09&date_to=2026-11-09")
    assert "Событие в 00:30 по Новосибирску" in page.text
    # 07.11 по-местному — ни одного события (CREATED — сегодня, EDITED — 09.11)
    page = client.get("/history?tab=issues&date_from=2026-11-07&date_to=2026-11-07")
    assert "Событие в 00:30 по Новосибирску" not in page.text
    # Открытый интервал «от» работает
    page = client.get("/history?tab=issues&date_from=2026-11-09")
    assert "Событие в 00:30 по Новосибирску" in page.text


def test_issue_qualifies_if_any_event_in_interval(client):
    """У вопроса есть событие в интервале — он попадает в выдачу (FR-3.4),
    даже если создан вне интервала."""
    _setup(client)
    _as_anna(client)
    open_shift(client)
    create_issue(client, place="Давний", description="Событие 7 ноября")
    issue_id = int(db_scalar("SELECT id FROM issue"))
    _insert_event(issue_id, "CLOSED", datetime(2026, 11, 7, 2, 0))  # 09:00 +07

    _as_manager(client)
    page = client.get("/history?tab=issues&date_from=2026-11-07&date_to=2026-11-07")
    assert "Событие 7 ноября" in page.text
    page = client.get("/history?tab=issues&date_from=2026-11-01&date_to=2026-11-05")
    assert "Событие 7 ноября" not in page.text


# --- FR-3.4: статус, порядок, пагинация -------------------------------------------


def test_status_filter(client):
    _setup(client)
    _as_anna(client)
    open_shift(client)
    create_issue(client, place="Открытый", description="D1")
    create_issue(client, place="Закрытый", description="D2")
    closed = int(db_scalar("SELECT id FROM issue WHERE place='Закрытый'"))
    client.post(f"/issues/{closed}/close",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)

    _as_manager(client)
    only_open = client.get("/history?tab=issues&status=OPEN").text
    assert "D1" in only_open and "D2" not in only_open
    only_closed = client.get("/history?tab=issues&status=CLOSED").text
    assert "D2" in only_closed and "D1" not in only_closed
    both = client.get("/history?tab=issues&status=all").text
    assert "D1" in both and "D2" in both


def test_order_newest_event_first(client):
    _setup(client)
    _as_anna(client)
    open_shift(client)
    create_issue(client, place="Старый", description="OLD")
    create_issue(client, place="Новый", description="NEW")
    _as_manager(client)
    text_ = client.get("/history?tab=issues").text
    assert text_.index("NEW") < text_.index("OLD")  # новые события выше


def test_pagination_50_per_page(client):
    _setup(client)
    _as_anna(client)
    open_shift(client)
    for i in range(51):
        create_issue(client, place=f"№{i}", description=f"desc{i}")
    _as_manager(client)
    page1 = client.get("/history?tab=issues").text
    assert "(51 записей)" in page1
    assert "Страница 1 из 2" in page1
    assert "desc50" in page1  # новый — на первой
    # считаем карточки
    assert page1.count('class="card"') == 50
    page2 = client.get("/history?tab=issues&page=2").text
    assert page2.count('class="card"') == 1


# --- некорректные параметры ----------------------------------------------------------


def test_bad_params_show_message_and_ignore(client):
    _setup(client)
    _as_manager(client)
    page = client.get("/history?tab=issues&date_from=мусор&status=XX&page=abc")
    assert page.status_code == 200
    assert "некорректны" in page.text
    # фильтры проигнорированы, страница живая
    assert "Найти" in page.text


def test_wrong_date_order_message(client):
    _setup(client)
    _as_manager(client)
    page = client.get("/history?tab=issues&date_from=2026-11-09&date_to=2026-11-01")
    assert "интервал дат" in page.text


# --- FR-3.5: история передач ----------------------------------------------------------


def test_handovers_tab(client):
    _setup(client)
    create_admin_via_db(client, "ivan", "Иван П.")
    _as_anna(client)
    open_shift(client)
    create_issue(client, place="Комната 7", description="Ключ от 404")
    client.post("/shifts/1/handover",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)
    client.cookies.clear()
    login(client, "ivan")
    client.post(f"/handovers/{db_scalar('SELECT id FROM handover')}/accept",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)

    _as_manager(client)
    page = client.get("/history?tab=handovers")
    assert "Смена Анна К." in page.text
    assert "принял: Иван П." in page.text
    assert "вопросов: 1" in page.text
    assert "/handovers/1" in page.text
    assert "Принята" in page.text


def test_handovers_pending_status_in_history(client):
    _setup(client)
    _as_anna(client)
    open_shift(client)
    create_issue(client, place="A", description="d")
    client.post("/shifts/1/handover",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)
    _as_manager(client)
    page = client.get("/history?tab=handovers")
    assert "Ожидает приёма" in page.text


# --- AC-5: закрытый вопрос остаётся в истории и не открывается --------------------------


def test_closed_stays_in_history_not_reopened(client):
    _setup(client)
    _as_anna(client)
    open_shift(client)
    create_issue(client, place="Комната 12", description="Одеяло")
    issue_id = int(db_scalar("SELECT id FROM issue"))
    client.post(f"/issues/{issue_id}/close",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)

    _as_manager(client)
    page = client.get("/history?tab=issues&status=CLOSED")
    assert "Одеяло" in page.text and "Закрыт" in page.text
    # повторного открытия нет в интерфейсе (вне объёма ТЗ) — карточка только для чтения
    card = client.get(f"/issues/{issue_id}").text
    assert "Закрыт" in card
    assert "Изменить" not in card


# --- роли ---------------------------------------------------------------------------


def test_admin_forbidden_history(client):
    _setup(client)
    _as_anna(client)
    resp = client.get("/history")
    assert resp.status_code == 403
