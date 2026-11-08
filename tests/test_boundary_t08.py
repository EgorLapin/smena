"""T08 — граничные случаи и конкурентность (NFR-10): AC-2 (параллельные сдача и
приём — реальными потоками), AC-5, AC-6 (прямые адреса чужого отеля), AC-7
(матрица ролей), AC-10 (искусственный сбой в транзакции).

AC-3/AC-4 покрыты в test_handover_t06/test_accept_t07, AC-8/AC-5(история) — в
T10, AC-11 — в test_staff_t04; здесь сводные проверки инвариантов.
"""
from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from web import (
    PASSWORD,
    create_admin_via_db,
    create_issue,
    db_rows,
    db_scalar,
    get_csrf,
    login,
    open_shift,
    register_hotel,
)


def _hotel_with_admins(client: TestClient, *logins: str):
    register_hotel(client)
    for lg in logins:
        create_admin_via_db(client, lg, lg.title())
    client.cookies.clear()


def _fresh_client(app) -> TestClient:
    return TestClient(app, base_url="https://testserver")


def _login_as(app, name: str) -> TestClient:
    c = _fresh_client(app)
    assert login(c, name).status_code == 303
    return c


def _shift_id_of(login_name: str) -> int:
    return int(db_scalar(
        "SELECT s.id FROM shift s JOIN user u ON u.id=s.admin_id "
        "WHERE u.login=:l ORDER BY s.id DESC", {"l": login_name}))


# --- AC-2: параллельная сдача одной смены (тот же пользователь) -----------------------


def test_parallel_double_submit_creates_one_handover(client):
    app = client.app
    _hotel_with_admins(client, "anna")
    anna = _login_as(app, "anna")
    open_shift(anna)
    create_issue(anna, place="Комната 1", description="d")
    shift_id = _shift_id_of("anna")

    barrier = threading.Barrier(2)
    results: list = []

    def submit():
        c = _login_as(app, "anna")
        barrier.wait()
        resp = c.post(f"/shifts/{shift_id}/handover",
                      data={"csrf_token": get_csrf(c, "/journal")},
                      follow_redirects=False)
        results.append(resp)

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert len(results) == 2
    assert all(r.status_code == 303 for r in results)
    # обе получают одну и ту же передачу — инвариант «не более одной на смену»
    assert results[0].headers["location"] == results[1].headers["location"]
    assert db_scalar("SELECT COUNT(*) FROM handover") == 1
    assert db_scalar("SELECT COUNT(*) FROM handover_item") == 1


# --- AC-2: параллельный приём двумя пользователями --------------------------------------


def test_parallel_accept_two_users_one_winner(client):
    app = client.app
    _hotel_with_admins(client, "anna", "ivan", "petr")
    anna = _login_as(app, "anna")
    open_shift(anna)
    create_issue(anna, place="Комната 1", description="d")
    a_id = int(db_scalar("SELECT id FROM issue"))
    anna.post(f"/shifts/{_shift_id_of('anna')}/handover",
              data={"csrf_token": get_csrf(anna, "/journal")},
              follow_redirects=False)
    handover_id = int(db_scalar("SELECT id FROM handover"))
    client.cookies.clear()

    ivan = _login_as(app, "ivan")
    petr = _login_as(app, "petr")

    barrier = threading.Barrier(2)
    outcomes: dict = {}

    def accept(name, c):
        barrier.wait()
        resp = c.post(f"/handovers/{handover_id}/accept",
                      data={"csrf_token": get_csrf(c, "/journal")},
                      follow_redirects=False)
        page = c.get("/journal").text  # flash одноразовый — читаем сразу
        outcomes[name] = (resp.status_code, resp.headers.get("location"), page)

    threads = [threading.Thread(target=accept, args=("ivan", ivan)),
               threading.Thread(target=accept, args=("petr", petr))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert set(outcomes) == {"ivan", "petr"}
    winners = [n for n, (_, _, page) in outcomes.items() if "Смена уже принята" not in page]
    losers = [n for n, (_, _, page) in outcomes.items() if "Смена уже принята" in page]
    assert len(winners) == 1 and len(losers) == 1  # ровно один победитель

    # Проигравший получил ALREADY_ACCEPTED с именем победителя (FR-2.7)
    _, _, loser_page = outcomes[losers[0]]
    assert "ALREADY_ACCEPTED" in loser_page
    assert f"Смена уже принята: {winners[0].title()}" in loser_page

    # Инварианты: один приём, один набор CARRIED, handover_count == 1 (AC-2)
    assert db_scalar("SELECT status FROM handover") == "ACCEPTED"
    assert int(db_scalar("SELECT COUNT(*) FROM issue_event WHERE type='CARRIED'")) == 1
    assert db_scalar("SELECT handover_count FROM issue WHERE id=:i", {"i": a_id}) == 1
    # Смена открыта только у победителя
    assert db_scalar("SELECT COUNT(*) FROM shift WHERE status='OPEN'") == 1


def test_parallel_accept_same_user_single_result(client):
    """Тот же пользователь дважды параллельно — один приём, повтор видит
    результат первого (FR-2.7)."""
    app = client.app
    _hotel_with_admins(client, "anna", "ivan")
    anna = _login_as(app, "anna")
    open_shift(anna)
    create_issue(anna, place="Комната 1", description="d")
    anna.post(f"/shifts/{_shift_id_of('anna')}/handover",
              data={"csrf_token": get_csrf(anna, "/journal")},
              follow_redirects=False)
    handover_id = int(db_scalar("SELECT id FROM handover"))
    client.cookies.clear()

    barrier = threading.Barrier(2)
    pages: list = []

    def accept():
        c = _login_as(app, "ivan")  # одна и та же учётка — cookie-сессия общая
        barrier.wait()
        c.post(f"/handovers/{handover_id}/accept",
               data={"csrf_token": get_csrf(c, "/journal")},
               follow_redirects=False)
        pages.append(c.get("/journal").text)

    threads = [threading.Thread(target=accept) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert all("Смена уже принята" not in p for p in pages)  # себе не ALREADY_ACCEPTED
    assert int(db_scalar("SELECT COUNT(*) FROM issue_event WHERE type='CARRIED'")) == 1


# --- Инвариант: одна открытая смена на администратора ----------------------------------


def test_parallel_open_shift_one_shift(client):
    app = client.app
    _hotel_with_admins(client, "anna")
    barrier = threading.Barrier(3)

    def open_it():
        c = _login_as(app, "anna")
        barrier.wait()
        c.post("/shifts/open", data={"csrf_token": get_csrf(c, "/journal")},
               follow_redirects=False)

    threads = [threading.Thread(target=open_it) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert db_scalar("SELECT COUNT(*) FROM shift") == 1


# --- AC-5: карточка после полного цикла --------------------------------------------------


def test_issue_card_shows_everything(client):
    register_hotel(client)
    create_admin_via_db(client, "anna", "Анна К.")
    create_admin_via_db(client, "ivan", "Иван П.")
    client.cookies.clear()
    login(client, "anna")
    open_shift(client)
    create_issue(client, place="Комната 12", description="Одеяло")
    a_id = int(db_scalar("SELECT id FROM issue"))
    client.post(f"/issues/{a_id}/edit",
                data={"place": "Комната 12", "description": "Одеяло в стирку",
                      "next_action": "",
                      "csrf_token": get_csrf(client, f"/issues/{a_id}/edit")},
                follow_redirects=False)
    client.post(f"/shifts/{_shift_id_of('anna')}/handover",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)
    client.cookies.clear()
    login(client, "ivan")
    client.post(f"/handovers/{db_scalar('SELECT id FROM handover')}/accept",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)
    client.post(f"/issues/{a_id}/close",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)

    card = client.get(f"/issues/{a_id}").text
    assert "Анна К." in card            # автор
    assert "создан" in card and "изменён" in card
    assert "перенесён" in card          # передача видна
    assert "закрыт" in card and "Иван П." in card
    assert "Одеяло в стирку" in card    # текущие значения
    # история правки: было → стало
    assert "Одеяло в стирку" in card and "изменён" in card


# --- AC-6: изоляция отелей по прямым адресам ----------------------------------------------


def test_hotel_isolation_direct_urls(client):
    # Отель 1: Анна сдаёт смену с вопросом и передачей
    register_hotel(client)
    create_admin_via_db(client, "anna", "Анна")
    client.cookies.clear()
    login(client, "anna")
    open_shift(client)
    create_issue(client, place="Комната 12", description="Одеяло")
    issue_id = int(db_scalar("SELECT id FROM issue"))
    client.post(f"/shifts/{_shift_id_of('anna')}/handover",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)
    handover_id = int(db_scalar("SELECT id FROM handover"))
    shift_id = _shift_id_of("anna")
    client.cookies.clear()

    # Отель 2: свой управляющий и админ — лезем попрямым id в отель 1
    register_hotel(client, name="Отель «Колос»", login="mgr2")
    from app import db, models
    from app.security import hash_password

    session = db.new_session()
    try:
        hotel2 = session.query(models.Hotel).filter_by(name="Отель «Колос»").one()
        session.add(models.User(hotel_id=hotel2.id, login="admin2",
                                display_name="Админ 2",
                                password_hash=hash_password(PASSWORD),
                                role=models.Role.ADMIN))
        session.commit()
    finally:
        session.close()
    client.cookies.clear()
    login(client, "admin2")
    open_shift(client)  # своя смена, чтобы csrf-страницы открывались

    # GET — страница 404
    for url in (f"/issues/{issue_id}", f"/handovers/{handover_id}",
                f"/shifts/{shift_id}/handover", f"/issues/{issue_id}/edit"):
        resp = client.get(url)
        assert resp.status_code == 404, url

    # POST — 303 + flash NOT_FOUND, состояние чужих сущностей не изменилось
    for url in (f"/issues/{issue_id}/edit", f"/issues/{issue_id}/close",
                f"/handovers/{handover_id}/accept", f"/shifts/{shift_id}/handover"):
        resp = client.post(url,
                           data={"place": "x", "description": "x", "next_action": "",
                                 "csrf_token": get_csrf(client, "/journal")},
                           follow_redirects=False)
        assert resp.status_code == 303, url
        page = client.get("/journal").text
        assert "NOT_FOUND" in page, url
    assert db_rows("SELECT status FROM issue WHERE id=:i", {"i": issue_id})[0][0] == "OPEN"
    assert db_rows("SELECT status FROM handover WHERE id=:i",
                   {"i": handover_id})[0][0] == "PENDING"


# --- AC-7: матрица ролей ---------------------------------------------------------------


def test_admin_forbidden_pages(client):
    register_hotel(client)
    create_admin_via_db(client, "anna", "Анна")
    client.cookies.clear()
    login(client, "anna")
    # ADMIN: обзор/история/сотрудники — страница 403 FORBIDDEN (Q-002)
    for path in ("/overview", "/history", "/staff"):
        resp = client.get(path)
        assert resp.status_code == 403, path
        assert "FORBIDDEN" in resp.text


def test_manager_forbidden_actions(client):
    register_hotel(client)
    create_admin_via_db(client, "anna", "Анна")
    client.cookies.clear()
    login(client, "manager")  # MANAGER из регистрации отеля

    # GET журнала — 403 страница
    assert client.get("/journal").status_code == 403

    # POST-действия — 303 + FORBIDDEN, в базе ничего не появилось
    csrf = get_csrf(client, "/overview")  # MANAGER видит /overview (заглушка до T09)
    for url, body in (
        ("/shifts/open", {}),
        ("/issues", {"place": "x", "description": "x", "next_action": ""}),
    ):
        resp = client.post(url, data={**body, "csrf_token": csrf},
                           follow_redirects=False)
        assert resp.status_code == 303, url
    assert db_scalar("SELECT COUNT(*) FROM shift") == 0
    assert db_scalar("SELECT COUNT(*) FROM issue") == 0

    # Сдача/приём чужих сущностей MANAGER'ом тоже запрещены ролью раньше прочего:
    # создадим смену и передачу админом
    client.cookies.clear()
    login(client, "anna")
    open_shift(client)
    create_issue(client, place="A", description="d")
    client.post(f"/shifts/{_shift_id_of('anna')}/handover",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)
    handover_id = int(db_scalar("SELECT id FROM handover"))
    client.cookies.clear()
    login(client, "manager")
    csrf = get_csrf(client, "/overview")
    for url in (f"/shifts/{_shift_id_of('anna')}/handover",
                f"/handovers/{handover_id}/accept"):
        resp = client.post(url, data={"csrf_token": csrf}, follow_redirects=False)
        assert resp.status_code == 303, url
    assert db_rows("SELECT status FROM handover WHERE id=:i",
                   {"i": handover_id})[0][0] == "PENDING"


# --- AC-10: искусственная ошибка в транзакции ------------------------------------------


def test_write_failure_rolls_back_everything(client, monkeypatch):
    """POST вопроса с падением после add(issue): страница 500 с «Запись не
    сохранена» и введёнными значениями; в базе — ни вопроса, ни события."""
    from app.services import journal as journal_service

    register_hotel(client)
    create_admin_via_db(client, "anna", "Анна")
    client.cookies.clear()
    login(client, "anna")
    open_shift(client)

    def boom(*args, **kwargs):
        raise RuntimeError("искусственный сбой")

    monkeypatch.setattr(journal_service, "_add_event", boom)
    resp = client.post("/issues",
                       data={"place": "Комната 9", "description": "Сбой",
                             "next_action": "Проверить",
                             "csrf_token": get_csrf(client, "/issues/new")},
                       follow_redirects=False)
    assert resp.status_code == 500
    assert "Запись не сохранена" in resp.text
    assert "INTERNAL_ERROR" in resp.text
    assert "Комната 9" in resp.text and "Сбой" in resp.text  # значения в форме
    assert "Проверить" in resp.text
    assert db_scalar("SELECT COUNT(*) FROM issue") == 0
    assert db_scalar("SELECT COUNT(*) FROM issue_event") == 0


def test_close_failure_keeps_issue_open(client, monkeypatch):
    """Действие без формы при сбое — страница 500 «Запись не сохранена»;
    статус/автор/время закрытия не записаны, события CLOSED нет."""
    from app.services import journal as journal_service

    register_hotel(client)
    create_admin_via_db(client, "anna", "Анна")
    client.cookies.clear()
    login(client, "anna")
    open_shift(client)
    create_issue(client, place="A", description="d")
    issue_id = int(db_scalar("SELECT id FROM issue"))

    def boom(*args, **kwargs):
        raise RuntimeError("сбой записи события")

    monkeypatch.setattr(journal_service, "_add_event", boom)
    resp = client.post(f"/issues/{issue_id}/close",
                       data={"csrf_token": get_csrf(client, "/journal")},
                       follow_redirects=False)
    assert resp.status_code == 500
    assert "Запись не сохранена" in resp.text
    row = db_rows("SELECT status, closed_at, closed_by FROM issue WHERE id=:i",
                  {"i": issue_id})[0]
    assert row == ("OPEN", None, None)  # частичных данных нет
    assert db_scalar("SELECT COUNT(*) FROM issue_event WHERE type='CLOSED'") == 0


def test_accept_failure_rolls_back_transfer(client, monkeypatch):
    """Сбой при приёме: ни переноса, ни ACCEPTED, ни смены получателя — откат (AL-3)."""
    from app.services import handover as handover_service

    register_hotel(client)
    create_admin_via_db(client, "anna", "Анна")
    create_admin_via_db(client, "ivan", "Иван")
    client.cookies.clear()
    login(client, "anna")
    open_shift(client)
    create_issue(client, place="Комната 12", description="Одеяло")
    a_id = int(db_scalar("SELECT id FROM issue"))
    client.post(f"/shifts/{_shift_id_of('anna')}/handover",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)
    handover_id = int(db_scalar("SELECT id FROM handover"))
    client.cookies.clear()
    login(client, "ivan")

    def boom(*args, **kwargs):
        raise RuntimeError("сбой записи CARRIED")

    monkeypatch.setattr(handover_service, "add_event", boom)
    resp = client.post(f"/handovers/{handover_id}/accept",
                       data={"csrf_token": get_csrf(client, "/journal")},
                       follow_redirects=False)
    assert resp.status_code == 500
    assert "Запись не сохранена" in resp.text
    # откат: вопрос в прежней смене, счётчик не тронут, передача PENDING
    assert db_rows("SELECT current_shift_id, handover_count FROM issue WHERE id=:i",
                   {"i": a_id})[0][1] == 0
    assert db_rows("SELECT status FROM handover WHERE id=:i",
                   {"i": handover_id})[0][0] == "PENDING"
    assert db_scalar("SELECT COUNT(*) FROM shift WHERE status='OPEN' AND "
                     "admin_id=(SELECT id FROM user WHERE login='ivan')") == 0
