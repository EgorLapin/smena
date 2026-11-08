"""T05 — журнал: открытие смены, создание/изменение/закрытие вопроса, события,
экран журнала (FR-1.1 — FR-1.7, DF-3/DF-4, пустые состояния)."""
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


# --- FR-1.1: открытие смены -------------------------------------------------------


def test_open_shift_creates_once_and_is_safe_on_repeat(client):
    _anna(client)
    open_shift(client)
    open_shift(client)  # повторное нажатие безопасно (FR-1.1)
    assert db_scalar("SELECT COUNT(*) FROM shift") == 1
    assert db_scalar("SELECT status FROM shift") == "OPEN"
    page = client.get("/journal")
    assert "Смена открыта в" in page.text
    assert "Сдать смену" in page.text


def test_journal_without_shift_shows_open_button(client):
    _anna(client)
    page = client.get("/journal")
    assert "Смена не открыта" in page.text
    assert "Открыть смену" in page.text


# --- FR-1.2: создание вопроса -------------------------------------------------------


def test_create_issue_with_created_event(client):
    _anna(client)
    open_shift(client)
    resp = create_issue(client)
    assert resp.status_code == 303 and resp.headers["location"] == "/journal"
    page = client.get("/journal")
    assert "Вопрос добавлен" in page.text  # green flash после фиксации (FR-1.7)
    assert "Комната 12" in page.text and "Передать дополнительное одеяло" in page.text
    assert "Далее: Принести до 20:00" in page.text

    shift_id = db_scalar("SELECT id FROM shift")
    row = db_rows(
        "SELECT status, handover_count, current_shift_id, place_norm FROM issue")[0]
    assert row == ("OPEN", 0, shift_id, "комната 12")  # place_norm по AL-1
    ev = db_rows("SELECT type, shift_id FROM issue_event")
    assert ev == [("CREATED", shift_id)]


def test_create_issue_without_open_shift(client):
    _anna(client)
    resp = create_issue(client)
    assert resp.status_code == 303  # POST-ошибка → 303 + flash (контракт ТЗ)
    page = client.get("/journal")
    assert "Сначала откройте смену" in page.text and "NO_OPEN_SHIFT" in page.text


def test_issue_validation_422_keeps_values(client):
    _anna(client)
    open_shift(client)
    resp = client.post(
        "/issues",
        data={"place": "  ", "description": "х" * 501, "next_action": "д" * 301,
              "csrf_token": get_csrf(client, "/issues/new")},
        follow_redirects=False,
    )
    assert resp.status_code == 422
    assert "Укажите место или номер комнаты" in resp.text
    assert "Не более 500 символов" in resp.text
    assert "Не более 300 символов" in resp.text
    assert "х" * 501 in resp.text  # значения сохранены в форме
    # предупреждение о персональных данных на форме (FR-A9)
    form = client.get("/issues/new")
    assert "Не вносите ФИО гостей" in form.text


# --- FR-1.3: изменение + событие EDITED ------------------------------------------------


def test_edit_issue_records_edited_event(client):
    _anna(client)
    open_shift(client)
    create_issue(client)
    issue_id = db_scalar("SELECT id FROM issue")

    resp = client.post(
        f"/issues/{issue_id}/edit",
        data={"place": "Комната 12", "description": "Одеяло в стирку",
              "next_action": "", "csrf_token": get_csrf(client, f"/issues/{issue_id}/edit")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    page = client.get("/journal")
    assert "Сохранено" in page.text  # FR-1.7
    assert "Одеяло в стирку" in page.text

    events = [r[0] for r in db_rows("SELECT type FROM issue_event ORDER BY id")]
    assert events == ["CREATED", "EDITED"]
    payload = db_rows("SELECT payload FROM issue_event WHERE type='EDITED'")[0][0]
    assert "Одеяло в стирку" in payload and "Передать дополнительное одеяло" in payload

    # редактирование без изменений — нового EDITED-события нет (AL-4)
    client.post(
        f"/issues/{issue_id}/edit",
        data={"place": "Комната 12", "description": "Одеяло в стирку",
              "next_action": "", "csrf_token": get_csrf(client, f"/issues/{issue_id}/edit")},
        follow_redirects=False,
    )
    events = [r[0] for r in db_rows("SELECT type FROM issue_event ORDER BY id")]
    assert events == ["CREATED", "EDITED"]


def test_edit_prefills_current_values(client):
    _anna(client)
    open_shift(client)
    create_issue(client, place="Кухня", description="Заменить лампу")
    issue_id = db_scalar("SELECT id FROM issue")
    form = client.get(f"/issues/{issue_id}/edit")
    assert 'value="Кухня"' in form.text
    assert "Заменить лампу" in form.text


# --- FR-1.4/1.6: закрытие ------------------------------------------------------------


def test_close_issue_moves_to_resolved_block_with_event(client):
    _anna(client)
    open_shift(client)
    create_issue(client, place="Кухня", description="Заменить лампу")
    create_issue(client, place="Комната 7", description="Не работает кондиционер")
    kitchen_id = db_scalar(
        "SELECT id FROM issue WHERE place='Кухня'")
    resp = client.post(
        f"/issues/{kitchen_id}/close",
        data={"csrf_token": get_csrf(client, "/journal")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    page = client.get("/journal")
    assert "Решено в этой смене (1)" in page.text
    assert "Комната 7" in page.text  # открытый остался

    row = db_rows("SELECT status, closed_by, closed_at FROM issue WHERE id=:i",
                  {"i": kitchen_id})[0]
    assert row[0] == "CLOSED" and row[1] is not None and row[2] is not None
    assert db_rows("SELECT type FROM issue_event WHERE issue_id=:i",
                   {"i": kitchen_id}) == [("CREATED",), ("CLOSED",)]

    # FR-1.6: закрытый нельзя изменить/закрыть повторно — ISSUE_CLOSED (flash)
    resp = client.post(
        f"/issues/{kitchen_id}/edit",
        data={"place": "Кухня", "description": "хак", "next_action": "",
              "csrf_token": get_csrf(client, "/journal")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    page_text = client.get("/journal").text  # flash одноразовый — один get
    assert "Вопрос уже закрыт" in page_text
    assert "ISSUE_CLOSED" in page_text


# --- FR-1.5: отображение -----------------------------------------------------------


def test_journal_order_old_issues_first_and_empty_state(client):
    _anna(client)
    open_shift(client)
    assert "Открытых вопросов нет" in client.get("/journal").text
    create_issue(client, place="Первая", description="A")
    create_issue(client, place="Вторая", description="B")
    text = client.get("/journal").text
    assert text.index("Первая") < text.index("Вторая")  # старые выше


def test_manual_shift_close_edit_and_events(client):
    """Ручная приёмка FR-1.1–FR-1.6 скриптом (критерий T05): 2 вопроса,
    один правится, один закрывается — все события в базе."""
    _anna(client)
    open_shift(client)
    create_issue(client, place="Комната 3", description="Полотенца")
    create_issue(client, place="Комната 4", description="Ключ от 404")
    i3 = db_scalar("SELECT id FROM issue WHERE place='Комната 3'")
    i4 = db_scalar("SELECT id FROM issue WHERE place='Комната 4'")
    client.post(f"/issues/{i3}/edit",
                data={"place": "Комната 3", "description": "2 полотенца",
                      "next_action": "До 18:00",
                      "csrf_token": get_csrf(client, f"/issues/{i3}/edit")},
                follow_redirects=False)
    client.post(f"/issues/{i4}/close",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)
    types = [r[0] for r in db_rows(
        "SELECT type FROM issue_event ORDER BY id")]
    assert types.count("CREATED") == 2
    assert types == ["CREATED", "CREATED", "EDITED", "CLOSED"]
