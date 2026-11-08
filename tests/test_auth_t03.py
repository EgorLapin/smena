"""T03 — аутентификация и доступ.

Покрытие: FR-A1 (регистрация), FR-A2 (вход/выход/сессия/is_active на каждом
запросе), матрица прав FR-A6 (AC-7 в части GET-маршрутов), NFR-2 (хеш пароля,
флаги cookie, CSRF на POST — техчасть AC-13), изоляция через редирект без входа.

Хелпер login() забирает csrf-токен со страницы формы (как браузер).
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def get_csrf(client: TestClient, path: str = "/login") -> str:
    resp = client.get(path)
    assert resp.status_code == 200
    body = resp.text
    marker = 'name="csrf_token" value="'
    start = body.index(marker) + len(marker)
    return body[start:body.index('"', start)]


def register_hotel(client: TestClient, *, name="Отель «Ромашка»", login="manager",
                   password="secret123", timezone="Asia/Novosibirsk"):
    return client.post(
        "/register",
        data={
            "hotel_name": name, "timezone": timezone, "display_name": "Упр.",
            "login": login, "password": password, "password_confirm": password,
            "csrf_token": get_csrf(client, "/register"),
        },
        follow_redirects=False,
    )


def login(client: TestClient, login_name: str, password: str):
    return client.post(
        "/login",
        data={"login": login_name, "password": password,
              "csrf_token": get_csrf(client, "/login")},
        follow_redirects=False,
    )


def create_admin_via_db(client: TestClient, login_name="admin1", display="Анна К."):
    """Создать ADMIN напрямую (экран сотрудников — на T04)."""
    from app import db, models
    from app.security import hash_password

    session = db.new_session()
    try:
        hotel = session.query(models.Hotel).first()
        admin = models.User(hotel_id=hotel.id, login=login_name,
                            display_name=display,
                            password_hash=hash_password("secret123"),
                            role=models.Role.ADMIN)
        session.add(admin)
        session.commit()
        return hotel
    finally:
        session.close()


# --- FR-A1: регистрация отеля --------------------------------------------------


def test_register_creates_hotel_manager_and_logs_in(client):
    resp = register_hotel(client)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/overview"
    # Вход выполнен: MANAGER попадает на обзор
    overview = client.get("/overview", follow_redirects=False)
    assert overview.status_code == 200


def test_register_validation_errors_422(client):
    resp = client.post(
        "/register",
        data={"hotel_name": "", "timezone": "Mars/Olympus", "display_name": "",
              "login": "ЙО!", "password": "short", "password_confirm": "other",
              "csrf_token": get_csrf(client, "/register")},
        follow_redirects=False,
    )
    assert resp.status_code == 422
    assert "Укажите название отеля" in resp.text
    assert "Пароли не совпадают" in resp.text or "Не менее 8" in resp.text
    # Введённые значения сохранены (контракт ТЗ)
    assert 'value="ЙО!"' in resp.text


def test_register_login_case_normalized_and_unique(client):
    assert register_hotel(client, login="Boss").status_code == 303
    # Повторный логин в другом регистре — занят (хранится в нижнем регистре)
    resp = client.post(
        "/register",
        data={"hotel_name": "Второй", "timezone": "Asia/Novosibirsk",
              "display_name": "Два", "login": "BOSS", "password": "secret123",
              "password_confirm": "secret123",
              "csrf_token": get_csrf(client, "/register")},
        follow_redirects=False,
    )
    assert resp.status_code == 422
    assert "уже занят" in resp.text

    # И вход по верхнему регистру работает (нормализация AL-1)
    assert login(client, "BOSS", "secret123").status_code == 303


# --- FR-A2: вход/выход/сессия ----------------------------------------------------


def test_login_wrong_password_same_message(client):
    register_hotel(client)
    client.post("/logout", data={"csrf_token": get_csrf(client, "/overview")},
                follow_redirects=False)
    for user in ("manager", "net-takogo"):
        resp = client.post(
            "/login",
            data={"login": user, "password": "wrong-pass",
                  "csrf_token": get_csrf(client, "/login")},
            follow_redirects=False,
        )
        assert resp.status_code == 401
        assert "Неверный логин или пароль" in resp.text
        assert "AUTH_FAILED" in resp.text  # код показывается мелким шрифтом


def test_deactivated_user_cannot_login_and_session_dies(client):
    from app import db, models
    from sqlalchemy import select

    register_hotel(client)
    create_admin_via_db(client)
    assert login(client, "admin1", "secret123").status_code == 303
    assert client.get("/journal", follow_redirects=False).status_code == 200

    # Отключаем запись — is_active проверяется на каждом запросе (FR-A2)
    session = db.new_session()
    admin = session.scalar(select(models.User).where(models.User.login == "admin1"))
    admin.is_active = 0
    session.commit()
    session.close()

    assert client.get("/journal", follow_redirects=False).status_code == 303
    assert client.get("/journal", follow_redirects=False).headers["location"] == "/login"
    assert login(client, "admin1", "secret123").status_code == 401


def test_logout_destroys_session(client):
    register_hotel(client)
    resp = client.post("/logout", data={"csrf_token": get_csrf(client, "/overview")},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
    assert client.get("/overview", follow_redirects=False).status_code == 303


# --- Маршрут / и AUTH_REQUIRED ----------------------------------------------------


def test_index_redirects_by_role(client):
    register_hotel(client)
    assert client.get("/", follow_redirects=False).headers["location"] == "/overview"

    create_admin_via_db(client)
    login(client, "admin1", "secret123")
    assert client.get("/", follow_redirects=False).headers["location"] == "/journal"


def test_anonymous_redirected_to_login_without_message(client):
    client.cookies.clear()
    resp = client.get("/journal", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


# --- AC-7 (часть): матрица прав на GET-маршрутах -----------------------------------


def test_admin_forbidden_on_manager_pages(client):
    register_hotel(client)
    create_admin_via_db(client)
    login(client, "admin1", "secret123")
    for path in ("/overview", "/history", "/staff"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 403, path
        assert "FORBIDDEN" in resp.text
        assert "Это действие вам недоступно" in resp.text


def test_manager_forbidden_on_journal(client):
    register_hotel(client)
    resp = client.get("/journal", follow_redirects=False)
    assert resp.status_code == 403
    assert "FORBIDDEN" in resp.text


# --- NFR-2: пароли, cookie, CSRF (техчасть AC-13) ------------------------------------


def test_password_stored_only_as_argon2_hash(client):
    from app import db, models
    from sqlalchemy import select

    register_hotel(client, password="secret123")
    session = db.new_session()
    try:
        user = session.scalar(select(models.User).where(models.User.login == "manager"))
        assert user.password_hash.startswith("$argon2")
        assert "secret123" not in user.password_hash
    finally:
        session.close()


def test_session_cookie_flags(client):
    register_hotel(client)
    client.cookies.clear()
    resp = login(client, "manager", "secret123")
    assert resp.status_code == 303
    set_cookie = [v for k, v in resp.headers.items() if k.lower() == "set-cookie"
                  and "smena_session" in v]
    assert set_cookie, "smena_session не установлена"
    cookie_header = set_cookie[0]
    assert "HttpOnly" in cookie_header
    assert "Secure" in cookie_header
    assert "samesite=lax" in cookie_header.lower()
    # В одном заголовке мог оказаться и сброс smena_csrf — проверяем флаги на части
    # того же заголовка, относящейся к smena_session.
    session_part = cookie_header.split(",")[0]
    assert "HttpOnly" in session_part and "Secure" in session_part
    assert "samesite=lax" in session_part.lower()


def test_post_without_csrf_token_fails(client):
    resp = client.post("/login", data={"login": "manager", "password": "secret123"},
                       follow_redirects=False)
    assert resp.status_code == 403
    assert "CSRF_FAILED" in resp.text
    assert "Страница устарела" in resp.text


def test_post_with_forged_csrf_token_fails(client):
    register_hotel(client)
    resp = client.post("/logout", data={"csrf_token": "poddelka"},
                       follow_redirects=False)
    assert resp.status_code == 403
    assert "CSRF_FAILED" in resp.text
