"""Общие хелперы HTTP-тестов (csrf, регистрация отеля, вход).

TestClient в conftest работает с base_url=https://testserver — как прод:
Secure-cookie принимаются, сценарии CSRF честные.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

PASSWORD = "secret123"


def get_csrf(client: TestClient, path: str = "/login") -> str:
    resp = client.get(path)
    assert resp.status_code == 200, f"{path} → {resp.status_code}"
    body = resp.text
    marker = 'name="csrf_token" value="'
    start = body.index(marker) + len(marker)
    return body[start:body.index('"', start)]


def register_hotel(client: TestClient, *, name="Отель «Ромашка»", login="manager",
                   timezone="Asia/Novosibirsk"):
    return client.post(
        "/register",
        data={"hotel_name": name, "timezone": timezone, "display_name": "Упр.",
              "login": login, "password": PASSWORD, "password_confirm": PASSWORD,
              "csrf_token": get_csrf(client, "/register")},
        follow_redirects=False,
    )


def login(client: TestClient, login_name: str, password: str = PASSWORD):
    return client.post(
        "/login",
        data={"login": login_name, "password": password,
              "csrf_token": get_csrf(client, "/login")},
        follow_redirects=False,
    )


def create_staff(client: TestClient, login_name: str, *, name="Сотр."):
    return client.post(
        "/staff",
        data={"display_name": name, "login": login_name, "password": PASSWORD,
              "csrf_token": get_csrf(client, "/staff")},
        follow_redirects=False,
    )


def create_admin_via_db(client: TestClient, login_name: str = "admin1",
                        display: str = "Анна К.") -> int:
    """Создать ADMIN напрямую (через сервис сотрудников он тоже создаётся —
    здесь быстрее для тестов журнала). Возвращает id."""
    from app import db, models
    from app.security import hash_password

    session = db.new_session()
    try:
        hotel = session.query(models.Hotel).first()
        admin = models.User(hotel_id=hotel.id, login=login_name,
                            display_name=display,
                            password_hash=hash_password(PASSWORD),
                            role=models.Role.ADMIN)
        session.add(admin)
        session.commit()
        return admin.id
    finally:
        session.close()


def open_shift(client: TestClient) -> None:
    resp = client.post("/shifts/open",
                       data={"csrf_token": get_csrf(client, "/journal")},
                       follow_redirects=False)
    assert resp.status_code == 303


def create_issue(client: TestClient, *, place="Комната 12",
                 description="Передать дополнительное одеяло",
                 next_action="Принести до 20:00"):
    return client.post(
        "/issues",
        data={"place": place, "description": description,
              "next_action": next_action,
              "csrf_token": get_csrf(client, "/journal")},
        follow_redirects=False,
    )


def db_rows(sql: str, params: dict | None = None) -> list[tuple]:
    """Строки напрямую из БД (проверка «что в базе», как на приёмке)."""
    from app import db
    from sqlalchemy import text

    session = db.new_session()
    try:
        return list(session.execute(text(sql), params or {}).all())
    finally:
        session.close()


def db_scalar(sql: str, params: dict | None = None):
    return db_rows(sql, params)[0][0]


def user_id_of(login_name: str) -> int:
    return int(db_scalar("SELECT id FROM user WHERE login = :l", {"l": login_name}))
