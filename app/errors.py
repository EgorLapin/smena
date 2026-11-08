"""Бизнес-ошибки приложения (AppError) — коды по таблице ошибок ТЗ.

Способ показа определяется контекстом (контракт ТЗ, «Спецификация контрактов»):
- POST с redirect → 303 на страницу-источник с красным flash-сообщением и кодом;
- GET → страница ошибки с текстом, кодом и кнопкой «На главную»;
- AUTH_REQUIRED → 303 на /login без сообщения (обрабатывается в обработчике).
"""
from __future__ import annotations

from .messages_ru import error_status, error_text


class AppError(Exception):
    def __init__(
        self,
        code: str,
        *,
        text: str | None = None,
        params: dict | None = None,
        redirect: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.text = text if text is not None else error_text(code, **(params or {}))
        self.redirect = redirect  # не None — значит показывать через flash на 303
        self.http_status = http_status if http_status is not None else error_status(code)
