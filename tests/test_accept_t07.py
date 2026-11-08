"""T07 — приём смены (AL-3, FR-2.4–2.11): AC-1 полный цикл двумя учётными
записями, закрытые не переносятся, повторный/чужой приём, автор не принимает,
MANAGER не принимает, список ожидающих в журнале."""
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


def _two_admins(client: TestClient):
    """Отель + Анна + Иван (оба ADMIN)."""
    register_hotel(client)
    create_admin_via_db(client, "anna", "Анна К.")
    create_admin_via_db(client, "ivan", "Иван П.")


def _as(client: TestClient, name: str):
    client.cookies.clear()
    assert login(client, name).status_code == 303


def _shift_id_of(login_name: str) -> int:
    return int(db_scalar(
        "SELECT s.id FROM shift s JOIN user u ON u.id=s.admin_id "
        "WHERE u.login=:l ORDER BY s.id DESC", {"l": login_name}))


def _handover_of(login_name: str) -> int:
    """Последняя передача, сданная этим логином (сдача может быть не первая)."""
    return int(db_scalar(
        "SELECT h.id FROM handover h JOIN user u ON u.id=h.sender_id "
        "WHERE u.login=:l ORDER BY h.id DESC", {"l": login_name}))


def _accept(client: TestClient, handover_id: int, csrf_path: str = "/journal"):
    return client.post(f"/handovers/{handover_id}/accept",
                       data={"csrf_token": get_csrf(client, csrf_path)},
                       follow_redirects=False)


def _anna_hands_over(client: TestClient) -> tuple[int, int, int]:
    """Анна: смена, вопросы A и B, B закрыт, сдача.
    Возвращает (id A, id B, id передачи)."""
    _as(client, "anna")
    open_shift(client)
    create_issue(client, place="Комната 12", description="A: одеяло")
    create_issue(client, place="Кухня", description="B: лампа")
    b_id = int(db_scalar("SELECT id FROM issue WHERE place='Кухня'"))
    client.post(f"/issues/{b_id}/close",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)
    resp = client.post(f"/shifts/{_shift_id_of('anna')}/handover",
                       data={"csrf_token": get_csrf(client, "/journal")},
                       follow_redirects=False)
    assert resp.status_code == 303
    a_id = int(db_scalar("SELECT id FROM issue WHERE place='Комната 12'"))
    return a_id, b_id, _handover_of("anna")


# --- AC-1: полный цикл ---------------------------------------------------------------


def test_full_cycle_two_admins(client):
    _two_admins(client)
    a_id, b_id, handover_id = _anna_hands_over(client)
    anna_shift = _shift_id_of("anna")

    _as(client, "ivan")
    resp = _accept(client, handover_id)
    assert resp.status_code == 303 and resp.headers["location"] == "/journal"
    page = client.get("/journal")
    assert "Смена принята, перенесено 1 вопрос" in page.text
    assert "Комната 12" in page.text  # A — у Ивана

    # Тот же идентификатор, без копий, handover_count=1, смена Ивана (FR-2.5)
    row = db_rows("SELECT current_shift_id, handover_count, status FROM issue "
                  "WHERE id=:i", {"i": a_id})[0]
    ivan_shift = _shift_id_of("ivan")
    assert row == (ivan_shift, 1, "OPEN")
    assert db_scalar("SELECT COUNT(*) FROM issue") == 2  # копий нет

    # B остался в смене Анны со статусом CLOSED (FR-2.6)
    row_b = db_rows("SELECT current_shift_id, status FROM issue WHERE id=:i",
                    {"i": b_id})[0]
    assert row_b == (anna_shift, "CLOSED")

    # Событие CARRIED с.payload (FR-2.5)
    ev = db_rows("SELECT payload FROM issue_event WHERE issue_id=:i AND type='CARRIED'",
                 {"i": a_id})[0][0]
    assert f'"handover_id": {handover_id}' in ev
    assert f'"from_shift_id": {anna_shift}' in ev
    assert f'"to_shift_id": {ivan_shift}' in ev

    # Смена-источник и передача — ACCEPTED,receiver зафиксирован
    assert db_scalar("SELECT status FROM shift WHERE id=:i", {"i": anna_shift}) == "ACCEPTED"
    h = db_rows("SELECT status, receiver_id, receiver_shift_id FROM handover WHERE id=:i",
                {"i": handover_id})[0]
    assert h[0] == "ACCEPTED" and h[1] == int(db_scalar(
        "SELECT id FROM user WHERE login='ivan'")) and h[2] == ivan_shift

    # Анна не может изменить чужую теперь смену: SHIFT_NOT_EDITABLE
    _as(client, "anna")
    open_shift(client)  # своя новая смена — вопрос всё равно в чужой
    resp = client.post(
        f"/issues/{a_id}/edit",
        data={"place": "Комната 12", "description": "хак", "next_action": "",
              "csrf_token": get_csrf(client, "/journal")},
        follow_redirects=False)
    assert resp.status_code == 303
    assert "SHIFT_NOT_EDITABLE" in client.get("/journal").text


# --- FR-2.11 и роли -------------------------------------------------------------------


def test_author_cannot_accept_own_handover(client):
    _two_admins(client)
    _, _, handover_id = _anna_hands_over(client)
    _as(client, "anna")
    resp = _accept(client, handover_id)
    assert resp.status_code == 303  # POST → 303 + flash на /journal
    page = client.get("/journal")
    assert "Нельзя принять смену, которую вы сдали сами" in page.text
    assert "FORBIDDEN" in page.text
    # состояние не изменилось (AC-7)
    assert db_scalar("SELECT status FROM handover WHERE id=:i",
                     {"i": handover_id}) == "PENDING"


def test_manager_cannot_accept(client):
    _two_admins(client)
    _, _, handover_id = _anna_hands_over(client)
    _as(client, "manager")  # MANAGER, не ADMIN — csrf берёт со снимка (он ему виден)
    resp = _accept(client, handover_id, csrf_path=f"/handovers/{handover_id}")
    assert resp.status_code == 303
    assert db_scalar("SELECT status FROM handover WHERE id=:i",
                     {"i": handover_id}) == "PENDING"


# --- FR-2.7: повторный и чужой приём ------------------------------------------------


def test_second_user_gets_already_accepted_with_name_and_time(client):
    _two_admins(client)
    a_id, _, handover_id = _anna_hands_over(client)
    _as(client, "ivan")
    assert _accept(client, handover_id).status_code == 303
    carried_1 = int(db_scalar(
        "SELECT COUNT(*) FROM issue_event WHERE type='CARRIED'"))
    count_1 = db_scalar("SELECT handover_count FROM issue WHERE id=:i", {"i": a_id})

    # Питер — третий админ того же отеля
    create_admin_via_db(client, "petr", "Пётр С.")
    _as(client, "petr")
    resp = _accept(client, handover_id)
    assert resp.status_code == 303
    page = client.get("/journal")
    assert "Смена уже принята: Иван П." in page.text
    assert "ALREADY_ACCEPTED" in page.text

    # Состояние не изменилось, событий не добавилось (FR-2.7)
    assert db_scalar("SELECT handover_count FROM issue WHERE id=:i",
                     {"i": a_id}) == count_1
    assert int(db_scalar(
        "SELECT COUNT(*) FROM issue_event WHERE type='CARRIED'")) == carried_1
    assert db_scalar("SELECT receiver_id FROM handover WHERE id=:i", {"i": handover_id}) == \
        int(db_scalar("SELECT id FROM user WHERE login='ivan'"))


def test_same_user_repeat_shows_first_result(client):
    _two_admins(client)
    a_id, _, handover_id = _anna_hands_over(client)
    _as(client, "ivan")
    _accept(client, handover_id)
    carried_1 = int(db_scalar("SELECT COUNT(*) FROM issue_event WHERE type='CARRIED'"))

    resp = _accept(client, handover_id)  # повтор тем же
    assert resp.status_code == 303
    page = client.get("/journal")
    assert "Вы уже приняли эту смену" in page.text
    assert "ALREADY_ACCEPTED" not in page.text
    assert int(db_scalar(
        "SELECT COUNT(*) FROM issue_event WHERE type='CARRIED'")) == carried_1


# --- AL-3: смена получателя -------------------------------------------------------------


def test_accept_opens_receiver_shift_if_none_and_uses_existing_if_any(client):
    _two_admins(client)
    # Сценарий 1: у Ивана нет открытой смены — открытится при приёме
    _, _, handover_id = _anna_hands_over(client)
    _as(client, "ivan")
    assert db_scalar("SELECT COUNT(*) FROM shift WHERE admin_id="
                     "(SELECT id FROM user WHERE login='ivan')") == 0
    _accept(client, handover_id)
    assert db_scalar("SELECT COUNT(*) FROM shift WHERE admin_id="
                     "(SELECT id FROM user WHERE login='ivan') AND status='OPEN'") == 1

    # Сценарий 2: Анна сдаёт ещё раз, Иван уже со сменой — вопросы придут в неё
    _as(client, "anna")
    open_shift(client)
    create_issue(client, place="Комната 8", description="Сейф закрыть")
    client.post(f"/shifts/{_shift_id_of('anna')}/handover",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)
    ivan_shift_before = _shift_id_of("ivan")
    _as(client, "ivan")
    _accept(client, _handover_of("anna"))
    assert _shift_id_of("ivan") == ivan_shift_before  # новая не создана
    assert db_scalar("SELECT current_shift_id FROM issue WHERE place='Комната 8'") == \
        ivan_shift_before


# --- FR-2.4: список ожидающих в журнале ----------------------------------------------


def test_pending_list_visible_to_other_admin_not_own(client):
    _two_admins(client)
    _, _, handover_id = _anna_hands_over(client)

    _as(client, "anna")
    page = client.get("/journal")
    assert "Ожидают приёма" not in page.text  # свои не показываем (FR-2.4)

    _as(client, "ivan")
    page = client.get("/journal")
    assert "Ожидают приёма (1)" in page.text
    assert "Смена Анна К." in page.text
    assert "открытых: 1" in page.text  # B решённый — не считается
    assert "прошло" in page.text
    assert f"/handovers/{handover_id}" in page.text


# --- FR-2.10 / AC-4: снимок не меняется, уточнения после приёма ------------------------


def test_snapshot_immutable_after_accept_and_edits_after(client):
    _two_admins(client)
    a_id, _, handover_id = _anna_hands_over(client)
    _as(client, "ivan")
    _accept(client, handover_id)

    item_before = db_rows("SELECT place, description, next_action FROM handover_item "
                          "WHERE issue_id=:i", {"i": a_id})[0]

    # Иван уточняет вопрос — EDITED в ЕГО смене, снимок прежний (AC-4)
    client.post(f"/issues/{a_id}/edit",
                data={"place": "Комната 12", "description": "Одеяло выдать в 19:00",
                      "next_action": "",
                      "csrf_token": get_csrf(client, f"/issues/{a_id}/edit")},
                follow_redirects=False)
    item_after = db_rows("SELECT place, description, next_action FROM handover_item "
                         "WHERE issue_id=:i", {"i": a_id})[0]
    assert item_after == item_before
    ev = db_rows("SELECT shift_id FROM issue_event WHERE issue_id=:i AND type='EDITED'",
                 {"i": a_id})[0][0]
    assert ev == _shift_id_of("ivan")

    # Иван закрывает вопрос у себя (FR-2.10)
    client.post(f"/issues/{a_id}/close",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)
    assert db_rows("SELECT status FROM issue WHERE id=:i", {"i": a_id})[0][0] == "CLOSED"
    assert db_scalar("SELECT COUNT(*) FROM issue_event WHERE type='CLOSED'") == 2

    # Снимок по-прежнему PENDING-содержимое: OPEN-статус на момент сдачи
    assert db_rows("SELECT status_at_handover FROM handover_item WHERE issue_id=:i",
                   {"i": a_id})[0][0] == "OPEN"


def test_snapshot_button_visibility(client):
    _two_admins(client)
    _, _, handover_id = _anna_hands_over(client)
    url = f"/handovers/{handover_id}"

    _as(client, "anna")     # автор — кнопки нет
    assert "Принять смену" not in client.get(url).text
    _as(client, "ivan")     # не автор, ADMIN — есть
    assert "Принять смену" in client.get(url).text
    _as(client, "manager")  # MANAGER — кнопки нет (FR-2.11)
    assert "Принять смену" not in client.get(url).text

    _as(client, "ivan")
    _accept(client, handover_id)
    page = client.get(url)      # после приёма — статус Принята, принявший
    assert "Принята" in page.text and "Принял: Иван П." in page.text
    assert "Принять смену" not in page.text


def test_accept_other_hotel_not_found(client):
    _two_admins(client)
    _, _, handover_id = _anna_hands_over(client)

    client.cookies.clear()  # регистрация только без входа
    register_hotel(client, name="Отель «Колос»", login="mgr2")  # второй отель
    from app import db, models
    from app.security import hash_password

    session = db.new_session()
    try:
        hotel2 = session.query(models.Hotel).filter_by(name="Отель «Колос»").one()
        session.add(models.User(hotel_id=hotel2.id, login="admin_x",
                                display_name="Чужой",
                                password_hash=hash_password("secret123"),
                                role=models.Role.ADMIN))
        session.commit()
    finally:
        session.close()
    _as(client, "admin_x")
    resp = _accept(client, handover_id)
    assert resp.status_code == 303
    assert db_scalar("SELECT status FROM handover WHERE id=:i",
                     {"i": handover_id}) == "PENDING"
    assert client.get(f"/handovers/{handover_id}").status_code == 404
