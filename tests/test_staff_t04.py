"""T04 — сотрудники: создание, лимит 10, сброс пароля, отключение/включение.

Покрытие: FR-A3 (создание ADMIN, лимит ACCOUNT_LIMIT со счётчиком),
FR-A4 (сброс пароля, отключение/включение, запрет отключать себя, лимит при
включении), AC-11 (11-я активная запись → ACCOUNT_LIMIT), AC-7 в части POST
(ADMIN на /staff → 403), изоляция отеля (чужая запись → NOT_FOUND),
«отключённый не может войти» (формулировка T04 из плана).
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def get_csrf(client: TestClient, path: str) -> str:
    resp = client.get(path)
    assert resp.status_code == 200
    body = resp.text
    marker = 'name="csrf_token" value="'
    start = body.index(marker) + len(marker)
    return body[start:body.index('"', start)]


def register_manager(client: TestClient, login_name="manager", password="secret123"):
    return client.post(
        "/register",
        data={
            "hotel_name": "Отель «Ромашка»", "timezone": "Asia/Novosibirsk",
            "display_name": "Управляющий", "login": login_name,
            "password": password, "password_confirm": password,
            "csrf_token": get_csrf(client, "/register"),
        },
        follow_redirects=False,
    )


def create_staff(client: TestClient, login_name: str, *, name="Сотр.",
                 password="secret123"):
    return client.post(
        "/staff",
        data={"display_name": name, "login": login_name, "password": password,
              "csrf_token": get_csrf(client, "/staff")},
        follow_redirects=False,
    )


def staff_user_id(login_name: str) -> int:
    from app import db, models

    session = db.new_session()
    try:
        return session.query(models.User).filter_by(login=login_name).one().id
    finally:
        session.close()


def filled(client: TestClient, n_admins: int) -> None:
    """Довести число активных записей отеля до manager + (n_admins-1) админов."""
    for i in range(1, n_admins):
        resp = create_staff(client, f"admin{i}")
        assert resp.status_code == 303, f"admin{i} не создан: {resp.status_code}"


# --- FR-A3: создание --------------------------------------------------------------


def test_create_staff_visible_on_page(client):
    register_manager(client)
    resp = create_staff(client, "anna", name="Анна К.")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/staff"

    page = client.get("/staff")
    assert "Сотрудник добавлен" in page.text
    assert "anna" in page.text and "Анна К." in page.text
    assert "Учётных записей: 2 из 10" in page.text  # включая управляющего


def test_created_staff_role_admin_and_can_login(client):
    register_manager(client)
    create_staff(client, "anna")

    client.post("/logout", data={"csrf_token": get_csrf(client, "/staff")},
                follow_redirects=False)
    resp = client.post(
        "/login",
        data={"login": "anna", "password": "secret123",
              "csrf_token": get_csrf(client, "/login")},
        follow_redirects=False,
    )
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    # ADMIN идёт в журнал, и доступ к /staff ему закрыт (FR-A6)
    journal = client.get("/", follow_redirects=False)
    assert journal.status_code == 303 and journal.headers["location"] == "/journal"
    assert client.get("/staff").status_code == 403


def test_create_staff_validation_422_keeps_values(client):
    register_manager(client)
    resp = client.post(
        "/staff",
        data={"display_name": "", "login": "АННА!", "password": "short",
              "csrf_token": get_csrf(client, "/staff")},
        follow_redirects=False,
    )
    assert resp.status_code == 422
    assert "Укажите имя" in resp.text
    assert "3-32 символа" in resp.text
    assert "Не менее 8 символов" in resp.text
    assert 'value="АННА!"' in resp.text  # AL-1: регистр в вводимом значении сохранён


def test_create_staff_duplicate_login(client):
    register_manager(client)
    create_staff(client, "anna")
    resp = create_staff(client, "ANNA")  # AL-1: логин нормализуется → тот же занят
    assert resp.status_code == 422
    assert "Этот логин уже занят" in resp.text


# --- AC-11: лимит 10 ---------------------------------------------------------------


def test_limit_11th_active_gives_account_limit(client):
    register_manager(client)
    filled(client, 10)  # manager + 9 админов = 10 активных
    page = client.get("/staff")
    assert "Учётных записей: 10 из 10" in page.text

    resp = create_staff(client, "eleven")
    assert resp.status_code == 303  # POST-ошибка → 303 с flash
    page = client.get("/staff")
    assert "Достигнут лимит: 10 учётных записей на отель" in page.text
    assert "ACCOUNT_LIMIT" in page.text


def test_reenable_beyond_limit_gives_account_limit(client):
    register_manager(client)
    filled(client, 10)                      # 10 активных
    # отключить одного → 9 активных
    uid = staff_user_id("admin3")
    resp = client.post(
        f"/staff/{uid}/active",
        data={"active": "0", "csrf_token": get_csrf(client, "/staff")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "Учётная запись отключена" in client.get("/staff").text

    # создать нового — пройдёт (будет ровно 10)
    assert create_staff(client, "tenth").status_code == 303
    # включить отключённого сверх лимита — ACCOUNT_LIMIT (FR-A4)
    resp = client.post(
        f"/staff/{uid}/active",
        data={"active": "1", "csrf_token": get_csrf(client, "/staff")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "ACCOUNT_LIMIT" in client.get("/staff").text


# --- FR-A4: пароль и отключение ------------------------------------------------------


def test_password_reset_new_works_old_does_not(client):
    register_manager(client)
    create_staff(client, "anna")
    uid = staff_user_id("anna")
    resp = client.post(
        f"/staff/{uid}/password",
        data={"password": "brandnew9", "csrf_token": get_csrf(client, "/staff")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "Пароль изменён" in client.get("/staff").text

    client.cookies.clear()
    old = client.post("/login", data={"login": "anna", "password": "secret123",
                                      "csrf_token": get_csrf(client, "/login")},
                      follow_redirects=False)
    assert old.status_code == 401
    client.cookies.clear()
    new = client.post("/login", data={"login": "anna", "password": "brandnew9",
                                      "csrf_token": get_csrf(client, "/login")},
                      follow_redirects=False)
    assert new.status_code == 303


def test_password_reset_validation_error(client):
    register_manager(client)
    create_staff(client, "anna")
    uid = staff_user_id("anna")
    resp = client.post(
        f"/staff/{uid}/password",
        data={"password": "short", "csrf_token": get_csrf(client, "/staff")},
        follow_redirects=False,
    )
    assert resp.status_code == 303  # POST-ошибка: 303 + красный flash с кодом
    page = client.get("/staff")
    assert "VALIDATION_ERROR" in page.text
    assert "Не менее 8 символов" in page.text


def test_manager_cannot_deactivate_self(client):
    register_manager(client)
    from app import db, models

    session = db.new_session()
    try:
        mid = session.query(models.User).filter_by(login="manager").one().id
    finally:
        session.close()
    resp = client.post(
        f"/staff/{mid}/active",
        data={"active": "0", "csrf_token": get_csrf(client, "/staff")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    page = client.get("/staff")
    assert "Нельзя отключить собственную учётную запись" in page.text
    assert "FORBIDDEN" in page.text
    # и кнопки «Отключить» для себя на странице нет
    assert f'action="/staff/{mid}/active"' not in page.text


def test_deactivated_staff_cannot_login(client):
    register_manager(client)
    create_staff(client, "anna")
    uid = staff_user_id("anna")
    client.post(f"/staff/{uid}/active",
                data={"active": "0", "csrf_token": get_csrf(client, "/staff")},
                follow_redirects=False)

    client.cookies.clear()
    resp = client.post("/login", data={"login": "anna", "password": "secret123",
                                       "csrf_token": get_csrf(client, "/login")},
                       follow_redirects=False)
    assert resp.status_code == 401  # то же сообщение, что и неверный пароль (FR-A2)
    assert "Неверный логин или пароль" in resp.text


# --- изоляция отеля и роли -------------------------------------------------------------


def test_other_hotel_user_not_found(client):
    register_manager(client)
    create_staff(client, "anna")
    anna_id = staff_user_id("anna")

    # второй отель, вход его управляющим (cookie перезапишется)
    client.cookies.clear()
    client.post(
        "/register",
        data={"hotel_name": "Отель «Колос»", "timezone": "Asia/Novosibirsk",
              "display_name": "Второй", "login": "manager2",
              "password": "secret123", "password_confirm": "secret123",
              "csrf_token": get_csrf(client, "/register")},
        follow_redirects=False,
    )
    resp = client.post(
        f"/staff/{anna_id}/password",
        data={"password": "hackerpass1", "csrf_token": get_csrf(client, "/staff")},
        follow_redirects=False,
    )
    assert resp.status_code == 303  # POST → 303 + flash
    # flash одноразовый — читать страницу надо ОДИН раз после редиректа
    page = client.get("/staff").text
    assert "Страница не найдена" in page
    assert "NOT_FOUND" in page
    # и пароль Анны не изменился
    client.cookies.clear()
    still = client.post("/login", data={"login": "anna", "password": "secret123",
                                        "csrf_token": get_csrf(client, "/login")},
                        follow_redirects=False)
    assert still.status_code == 303


def test_admin_post_staff_forbidden(client):
    register_manager(client)
    create_staff(client, "anna")
    client.cookies.clear()
    client.post("/login", data={"login": "anna", "password": "secret123",
                                "csrf_token": get_csrf(client, "/login")},
                follow_redirects=False)
    # ADMIN не видит GET-страницу (403-страница по Q-002)…
    assert client.get("/staff").status_code == 403
    # …а POST-попытка — 303 с красным flash (redirect на referer)
    resp = client.post(
        "/staff",
        data={"display_name": "X", "login": "xy", "password": "secret123",
              "csrf_token": get_csrf(client, "/journal")},
        follow_redirects=False,
    )
    assert resp.status_code == 303
