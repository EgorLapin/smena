"""T09 — обзор управляющего (FR-3.1 — FR-3.3, FR-A7) и демо-передача (FR-A8).

Даты сдвигаем напрямую в БД (sent_at/created_at), не трогая код — пороги
вычисляются при отображении (AL-5).
"""
from __future__ import annotations

from datetime import datetime, timedelta

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


def _backdate(table: str, column: str, row_id: int, delta: timedelta) -> None:
    """table.column для строки row_id сдвигается на delta в прошлое.

    Метки хранятся TEXT в ISO-формате (D-009), поэтому разбираем строку."""
    from app import db
    from sqlalchemy import text

    session = db.new_session()
    try:
        raw = session.execute(
            text(f"SELECT {column} FROM {table} WHERE id=:i"), {"i": row_id}
        ).scalar()
        value = datetime.fromisoformat(str(raw)) - delta
        session.execute(
            text(f"UPDATE {table} SET {column}=:v WHERE id=:i"),
            {"i": row_id, "v": value},
        )
        session.commit()
    finally:
        session.close()


def _manager(client: TestClient):
    register_hotel(client)
    client.cookies.clear()
    assert login(client, "manager").status_code == 303


def _make_pending_handover(client: TestClient) -> int:
    """Анна: смена + вопрос, сдача. Возвращает id передачи (под MANAGER-сессией
    аннулируем cookie, входим как anna, потом возвращаемся к manager)."""
    create_admin_via_db(client, "anna", "Анна К.")
    client.cookies.clear()
    login(client, "anna")
    open_shift(client)
    create_issue(client, place="Комната 3", description="Протечка под мойкой")
    shift_id = int(db_scalar(
        "SELECT s.id FROM shift s JOIN user u ON u.id=s.admin_id WHERE u.login='anna'"))
    client.post(f"/shifts/{shift_id}/handover",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)
    handover_id = int(db_scalar(
        "SELECT h.id FROM handover h JOIN user u ON u.id=h.sender_id WHERE u.login='anna'"))
    client.cookies.clear()
    login(client, "manager")
    return handover_id


# --- FR-A7: подписка ---------------------------------------------------------------


def test_subscription_trial_line(client):
    _manager(client)
    page = client.get("/overview")
    assert "Подписка: пилот, осталось 14 дн. (790 руб./мес., до 10 учётных записей)" in page.text


def test_subscription_paid_line(client):
    from app import db, models

    _manager(client)
    session = db.new_session()
    try:
        hotel = session.query(models.Hotel).first()
        hotel.paid_until = models.utcnow() + timedelta(days=20)
        session.commit()
    finally:
        session.close()
    text = client.get("/overview").text
    assert "Подписка: оплачено до" in text and "(790 руб./мес." in text


def test_subscription_expired_line(client):
    from app import db, models

    _manager(client)
    session = db.new_session()
    try:
        hotel = session.query(models.Hotel).first()
        hotel.trial_ends_at = models.utcnow() - timedelta(days=3)
        session.commit()
    finally:
        session.close()
    text = client.get("/overview").text
    assert "Подписка: пилот закончился, ожидается оплата" in text


# --- FR-3.1: непринятые смены ----------------------------------------------------------


def test_pending_block_empty_and_filled(client):
    _manager(client)
    page = client.get("/overview")
    assert "Все смены приняты" in page.text

    handover_id = _make_pending_handover(client)
    page = client.get("/overview")
    assert f"/handovers/{handover_id}" in page.text
    assert "Смена Анна К." in page.text
    assert "открытых: 1" in page.text
    assert "прошло" in page.text
    assert "Просрочено" not in page.text  # только что сдана


def test_pending_overdue_tag_after_threshold(client):
    """A-5: PENDING_OVERDUE_HOURS=12 — метка «Просрочено» (проверка на отображении)."""
    _manager(client)
    handover_id = _make_pending_handover(client)
    _backdate("handover", "sent_at", handover_id, timedelta(hours=13))
    assert "Просрочено" in client.get("/overview").text


# --- FR-3.2: затянувшиеся вопросы --------------------------------------------------------


def test_stale_by_age_and_by_carries(client):
    from app import db
    from sqlalchemy import text

    _manager(client)
    create_admin_via_db(client, "anna", "Анна К.")
    client.cookies.clear()
    login(client, "anna")
    open_shift(client)
    create_issue(client, place="Комната 1", description="Свежий вопрос")
    fresh_id = int(db_scalar("SELECT id FROM issue"))
    create_issue(client, place="Кухня", description="Капает кран")
    old_id = int(db_scalar("SELECT id FROM issue WHERE place='Кухня'"))
    _backdate("issue", "created_at", old_id, timedelta(hours=25))

    client.cookies.clear()
    login(client, "manager")
    page = client.get("/overview")
    assert "Капает кран" in page.text          # старше 24 ч (FR-3.2)
    assert "Сейчас у: Анна К." in page.text     # кто ведёт вопрос сейчас
    assert "Свежий вопрос" not in page.text

    # Второй путь: перенесённых >= 2 раз — even свежий
    session = db.new_session()
    try:
        session.execute(text("UPDATE issue SET handover_count=2 WHERE id=:i"),
                        {"i": fresh_id})
        session.commit()
    finally:
        session.close()
    assert "Свежий вопрос" in client.get("/overview").text


# --- FR-3.3: показатель принятия --------------------------------------------------------


def test_acceptance_metric_excludes_demo(client):
    """3 обычные сдачи (1 принята) + 1 демо принятая → «Сдано 3, принято 1»."""
    _manager(client)
    create_admin_via_db(client, "anna", "Анна К.")
    create_admin_via_db(client, "ivan", "Иван П.")

    # 3 передачи от Анны, одну принимает Иван
    client.cookies.clear()
    login(client, "anna")
    ids = []
    for n in range(3):
        open_shift(client)
        create_issue(client, place=f"№{n}", description="d")
        shift_id = int(db_scalar(
            "SELECT id FROM shift WHERE status='OPEN' AND admin_id="
            "(SELECT id FROM user WHERE login='anna')"))
        client.post(f"/shifts/{shift_id}/handover",
                    data={"csrf_token": get_csrf(client, "/journal")},
                    follow_redirects=False)
        ids.append(int(db_scalar(
            f"SELECT h.id FROM handover h JOIN shift s ON s.id=h.shift_id "
            f"WHERE s.id={shift_id}")))
    client.cookies.clear()
    login(client, "ivan")
    client.post(f"/handovers/{ids[0]}/accept",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)

    # демо-передача от управляющего — и сразу принята Иваном
    client.cookies.clear()
    login(client, "manager")
    client.post("/demo/handover",
                data={"csrf_token": get_csrf(client, "/overview")},
                follow_redirects=False)
    demo_handover = int(db_scalar(
        "SELECT h.id FROM handover h JOIN shift s ON s.id=h.shift_id "
        "WHERE s.is_demo=1"))
    client.cookies.clear()
    login(client, "ivan")
    client.post(f"/handovers/{demo_handover}/accept",
                data={"csrf_token": get_csrf(client, "/journal")},
                follow_redirects=False)

    client.cookies.clear()
    login(client, "manager")
    page = client.get("/overview")
    assert "Сдано 3, принято 1 — доля принятых 33 %" in page.text


def test_acceptance_metric_no_data(client):
    _manager(client)
    assert "Нет данных" in client.get("/overview").text


# --- FR-A8: демо-передача ------------------------------------------------------------


def test_demo_handover_creation_and_flags(client):
    _manager(client)
    resp = client.post("/demo/handover",
                       data={"csrf_token": get_csrf(client, "/overview")},
                       follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/overview"
    page = client.get("/overview")
    assert "Демо-передача создана" in page.text
    assert "Демо" in page.text
    assert "Просрочено" not in page.text

    demo_shift = db_rows("SELECT is_demo, status, admin_id FROM shift WHERE is_demo=1")[0]
    manager_id = int(db_scalar("SELECT id FROM user WHERE login='manager'"))
    assert demo_shift == (1, "PENDING", manager_id)
    assert db_rows("SELECT COUNT(*) FROM issue WHERE next_action IS NOT NULL")[0][0] == 2
    # questions вымышленные, без ПД: предупреждение на форме не связано — тут просто
    # проверяем, что их два и оба открытые
    assert db_scalar("SELECT COUNT(*) FROM issue WHERE status='OPEN'") == 2

    # Повтор — DEMO_EXISTS, второй демо-смены нет
    resp = client.post("/demo/handover",
                       data={"csrf_token": get_csrf(client, "/overview")},
                       follow_redirects=False)
    assert resp.status_code == 303
    page = client.get("/overview")
    assert "Демо-передача уже создана и ждёт приёма" in page.text
    assert "DEMO_EXISTS" in page.text
    assert db_scalar("SELECT COUNT(*) FROM shift WHERE is_demo=1") == 1


def test_demo_acceptable_by_any_admin(client):
    _manager(client)
    create_admin_via_db(client, "ivan", "Иван П.")
    client.post("/demo/handover",
                data={"csrf_token": get_csrf(client, "/overview")},
                follow_redirects=False)
    demo_handover = int(db_scalar(
        "SELECT h.id FROM handover h JOIN shift s ON s.id=h.shift_id "
        "WHERE s.is_demo=1"))

    client.cookies.clear()
    login(client, "ivan")
    resp = client.post(f"/handovers/{demo_handover}/accept",
                       data={"csrf_token": get_csrf(client, "/journal")},
                       follow_redirects=False)
    assert resp.status_code == 303
    page = client.get("/journal")
    assert "Смена принята, перенесено 2 вопроса" in page.text
    # у Ивана открылась смена (AL-3), вопросы у него
    assert db_scalar("SELECT COUNT(*) FROM shift WHERE status='OPEN' AND admin_id="
                     "(SELECT id FROM user WHERE login='ivan')") == 1
    assert db_scalar("SELECT COUNT(*) FROM issue WHERE handover_count=1") == 2


def test_demo_pending_never_overdue(client):
    _manager(client)
    client.post("/demo/handover",
                data={"csrf_token": get_csrf(client, "/overview")},
                follow_redirects=False)
    demo_handover = int(db_scalar(
        "SELECT h.id FROM handover h JOIN shift s ON s.id=h.shift_id "
        "WHERE s.is_demo=1"))
    _backdate("handover", "sent_at", demo_handover, timedelta(days=2))
    page = client.get("/overview")
    assert "Демо" in page.text
    assert "Просрочено" not in page.text  # FR-A8: демо не помечается просроченной


def test_demo_visible_in_journal_and_snapshot(client):
    """D-027 (AC-12, прогон 09.11): принимающий должен видеть, что передача
    демо- («откуда вопросы, которые никто не писал?»), а без кнопки — кто
    вообще может принять («как принять демо-передачу?»)."""
    _manager(client)
    create_admin_via_db(client, "ivan", "Иван П.")
    client.post("/demo/handover",
                data={"csrf_token": get_csrf(client, "/overview")},
                follow_redirects=False)
    demo_handover = int(db_scalar(
        "SELECT h.id FROM handover h JOIN shift s ON s.id=h.shift_id "
        "WHERE s.is_demo=1"))

    client.cookies.clear()
    login(client, "ivan")
    assert "Демо" in client.get("/journal").text  # метка в карточке журнала
    snapshot = client.get(f"/handovers/{demo_handover}").text
    assert "Демо" in snapshot
    assert "вопросы вымышленные" in snapshot      # снимок объясняет природу
    assert "Принять смену" in snapshot

    # Автор (MANAGER, сдавший демо) кнопки не видит — и ему сказано, кто примет
    client.cookies.clear()
    login(client, "manager")
    snapshot = client.get(f"/handovers/{demo_handover}").text
    assert "Принять смену" not in snapshot
    assert "примет администратор отеля" in snapshot


# --- AC-7: ADMIN на /overview — 403 (реализовано ранее, закрепляем на живом экране) ------


def test_admin_forbidden_on_overview(client):
    register_hotel(client)
    create_admin_via_db(client, "anna", "Анна")
    client.cookies.clear()
    login(client, "anna")
    resp = client.get("/overview")
    assert resp.status_code == 403
    assert "FORBIDDEN" in resp.text
    resp = client.post("/demo/handover",
                       data={"csrf_token": get_csrf(client, "/login")},
                       follow_redirects=False)
    assert resp.status_code == 303
