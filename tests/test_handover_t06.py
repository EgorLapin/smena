"""T06 — сдача смены: снимок, статус PENDING, страница проверки, идемпотентность
(FR-2.1–2.3, AL-2; часть AC-2 и AC-3; пустая смена сдаётся)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from web import (
    create_admin_via_db,
    create_issue,
    db_rows,
    db_scalar,
    get_csrf,
    login,
    open_shift,
    register_hotel,
)


def _anna(client: TestClient) -> int:
    register_hotel(client)
    uid = create_admin_via_db(client, "anna", "Анна К.")
    client.cookies.clear()
    assert login(client, "anna").status_code == 303
    return uid


def _shift_id() -> int:
    return int(db_scalar("SELECT id FROM shift"))


def _shift_id_of(login_name: str) -> int:
    return int(db_scalar(
        "SELECT s.id FROM shift s JOIN user u ON u.id=s.admin_id "
        "WHERE u.login=:l ORDER BY s.id DESC", {"l": login_name}))


def _handover_id() -> int:
    return int(db_scalar("SELECT id FROM handover"))


def _submit_handover(client: TestClient, shift_id: int):
    return client.post(f"/shifts/{shift_id}/handover",
                       data={"csrf_token": get_csrf(client, "/journal")},
                       follow_redirects=False)


def test_handover_creates_pending_shift_and_snapshot(client):
    register_hotel(client)
    create_admin_via_db(client, "anna", "Анна К.")
    create_admin_via_db(client, "ivan", "Иван П.")
    client.cookies.clear()
    login(client, "anna")
    open_shift(client)
    create_issue(client, place="Комната 12", description="Одеяло")
    create_issue(client, place="Кухня", description="Заменить лампу")
    kitchen = db_scalar("SELECT id FROM issue WHERE place='Кухня'")
    client.post(f"/issues/{kitchen}/close",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)

    # Иван сдаёт свою смену ПЕРВОЙ — id передач и смен разъезжаются, и проверка
    # ниже не может случайно совпасть.
    client.cookies.clear()
    login(client, "ivan")
    open_shift(client)
    _submit_handover(client, int(db_scalar(
        "SELECT id FROM shift WHERE admin_id=(SELECT id FROM user WHERE login='ivan')")))

    client.cookies.clear()
    login(client, "anna")
    resp = _submit_handover(client, _shift_id_of("anna"))
    assert resp.status_code == 303
    # 303 на /handovers/{id_передачи}, а НЕ смены (маршрутная таблица ТЗ)
    anna_handover = int(db_scalar(
        "SELECT h.id FROM handover h JOIN shift s ON s.id=h.shift_id "
        "JOIN user u ON u.id=s.admin_id WHERE u.login='anna'"))
    assert resp.headers["location"] == f"/handovers/{anna_handover}"
    assert anna_handover != _shift_id_of("anna")

    anna_shift = _shift_id_of("anna")
    assert db_scalar("SELECT status FROM shift WHERE id=:i", {"i": anna_shift}) == "PENDING"
    assert db_scalar("SELECT handed_at IS NOT NULL FROM shift WHERE id=:i",
                     {"i": anna_shift}) == 1
    assert db_scalar("SELECT status FROM handover WHERE id=:i", {"i": anna_handover}) == "PENDING"
    # FR-2.1: снимок — все вопросы смены, открытые и закрытые
    snap = dict(db_rows(
        "SELECT place, status_at_handover FROM handover_item WHERE handover_id=:i",
        {"i": anna_handover}))
    assert snap == {"Комната 12": "OPEN", "Кухня": "CLOSED"}


def test_double_post_creates_one_handover(client):
    """AC-2 (в части сдачи): два быстрых POST — одна передача и один снимок."""
    _anna(client)
    open_shift(client)
    create_issue(client)
    shift_id = _shift_id()

    first = _submit_handover(client, shift_id)
    second = _submit_handover(client, shift_id)  # повтор без перезагрузки
    assert first.status_code == second.status_code == 303
    assert first.headers["location"] == second.headers["location"]
    assert db_scalar("SELECT COUNT(*) FROM handover") == 1
    assert db_scalar("SELECT COUNT(*) FROM handover_item") == 1


def test_repeat_after_reload_shows_existing_snapshot(client):
    _anna(client)
    open_shift(client)
    create_issue(client)
    shift_id = _shift_id()
    _submit_handover(client, shift_id)

    # GET страницы сдачи уже сданной смены — сразу на снимок (FR-2.3)
    resp = client.get(f"/shifts/{shift_id}/handover", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/handovers/{_handover_id()}"
    page = client.get(f"/handovers/{_handover_id()}")
    assert "Ожидает приёма" in page.text
    assert "Комната 12" in page.text


def test_handover_check_page_before_submit(client):
    _anna(client)
    open_shift(client)
    create_issue(client, place="Комната 5", description="Пропуск для гостьи")
    issue_id = db_scalar("SELECT id FROM issue")
    client.post(f"/issues/{issue_id}/close",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)
    page = client.get(f"/shifts/{_shift_id()}/handover")
    assert "После сдачи смена станет доступна только для чтения" in page.text
    assert "Комната 5" in page.text           # решённый — во втором списке
    assert "не переносятся" in page.text
    assert "Сдать смену" in page.text


def test_empty_shift_can_be_handed_over(client):
    """FR-2.1: смену без открытых вопросов сдать можно."""
    _anna(client)
    open_shift(client)
    page = client.get(f"/shifts/{_shift_id()}/handover")
    assert "Открытых вопросов нет, будет сдана пустая смена" in page.text
    resp = _submit_handover(client, _shift_id())
    assert resp.status_code == 303
    snapshot = client.get(resp.headers["location"])
    assert "Нет открытых вопросов" in snapshot.text


def test_handed_over_shift_read_only(client):
    """AC-3: изменение/закрытие вопроса сданной смены → SHIFT_NOT_EDITABLE."""
    _anna(client)
    open_shift(client)
    create_issue(client, place="Комната 9", description="Протечка")
    issue_id = db_scalar("SELECT id FROM issue")
    shift_id = _shift_id()
    _submit_handover(client, shift_id)

    # Анна вернулась в журнал: смены OPEN нет — но вопрос из её сданной смены,
    # поэтому именно SHIFT_NOT_EDITABLE (проверка состояния смены раньше NO_OPEN_SHIFT)
    resp = client.post(
        f"/issues/{issue_id}/edit",
        data={"place": "Комната 9", "description": "хак", "next_action": "",
              "csrf_token": get_csrf(client, "/journal")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    page = client.get("/journal")
    assert "смена сдана или вопрос в чужой смене" in page.text
    assert "SHIFT_NOT_EDITABLE" in page.text

    resp = client.post(f"/issues/{issue_id}/close",
                       data={"csrf_token": get_csrf(client, "/journal")},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert "SHIFT_NOT_EDITABLE" in client.get("/journal").text

    # создание нового вопроса теперь даёт NO_OPEN_SHIFT
    resp = client.post("/issues",
                       data={"place": "Х", "description": "Х", "next_action": "",
                             "csrf_token": get_csrf(client, "/journal")},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert "Сначала откройте смену" in client.get("/journal").text


def test_handover_other_hotel_or_foreign_shift_not_found(client):
    _anna(client)
    open_shift(client)
    anna_shift = _shift_id()

    # второй отель: управляющий + свой ADMIN
    register_other = client.post(
        "/register",
        data={"hotel_name": "Отель «Колос»", "timezone": "Asia/Novosibirsk",
              "display_name": "Второй", "login": "manager2",
              "password": "secret123", "password_confirm": "secret123",
              "csrf_token": get_csrf(client, "/register")},
        follow_redirects=False,
    )
    assert register_other.status_code == 303  # cookie перезаписаны
    # create_admin_via_db берёт первый отель — создадим админа второго напрямую
    from app import db, models
    from app.security import hash_password

    session = db.new_session()
    try:
        hotel2 = session.query(models.Hotel).filter_by(name="Отель «Колос»").one()
        admin2 = models.User(hotel_id=hotel2.id, login="admin2",
                             display_name="Админ 2",
                             password_hash=hash_password("secret123"),
                             role=models.Role.ADMIN)
        session.add(admin2)
        session.commit()
    finally:
        session.close()
    client.cookies.clear()
    assert login(client, "admin2").status_code == 303

    # смена отеля 1 для админа отеля 2 не существует (AC-6): GET → 404-страница
    resp = client.get(f"/shifts/{anna_shift}/handover")
    assert resp.status_code == 404
    assert "Страница не найдена" in resp.text
    resp = client.post(f"/shifts/{anna_shift}/handover",
                       data={"csrf_token": get_csrf(client, "/journal")},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert "NOT_FOUND" in client.get("/journal").text


def test_handover_foreign_shift_same_hotel_not_found(client):
    """Смена другого админа СВОЕГО отеля — тоже NOT_FOUND (AL-2: своя смена)."""
    register_hotel(client)
    create_admin_via_db(client, "anna", "Анна К.")
    create_admin_via_db(client, "ivan", "Иван П.")
    client.cookies.clear()
    login(client, "anna")
    open_shift(client)
    anna_shift = _shift_id()

    client.cookies.clear()
    login(client, "ivan")
    resp = client.get(f"/shifts/{anna_shift}/handover")
    assert resp.status_code == 404
