"""Нормализация и валидация входных данных — AL-1 и «Правила валидации» ТЗ.

Обрезка пробелов; логин в нижний регистр; строка из пробелов = пусто;
place_norm: нижний регистр, «ё»→«е», повторные пробелы сжаты — по AL-1
(SQLite lower()/LIKE не понимают регистр русских букв, поэтому отдельное поле).
Валидаторы возвращают dict поле → текст ошибки; пустой dict = всё хорошо.
Тексты ошибок у поля — на русском; на T11 переедут в messages_ru.py целиком.
"""
from __future__ import annotations

import re

LOGIN_RE = re.compile(r"^[a-z0-9._-]{3,32}$")


def clean(value: str | None) -> str:
    """Обрезать пробелы; None → пустая строка."""
    return (value or "").strip()


def normalize_login(value: str) -> str:
    return clean(value).lower()


def normalize_place_norm(place: str) -> str:
    """place_norm по AL-1: нижний регистр, ё→е, сжатие пробелов."""
    s = place.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", s).strip()


def has_control_chars(value: str) -> bool:
    """Управляющие символы, кроме перевода строки/табуляции внутри текста."""
    return any(ord(ch) < 32 and ch not in "\n\r\t" for ch in value)


# --- Проверки полей (тексты — у поля, ТЗ: «плюс текст у поля») ----------------


def validate_place(value: str) -> str | None:
    if not value:
        return "Укажите место или номер комнаты"
    if len(value) > 50:
        return "Не более 50 символов"
    if has_control_chars(value):
        return "Текст содержит недопустимые символы"
    return None


def validate_description(value: str) -> str | None:
    if not value:
        return "Опишите, что нужно сделать или что обещано"
    if len(value) > 500:
        return "Не более 500 символов"
    return None


def validate_next_action(value: str) -> str | None:
    if len(value) > 300:
        return "Не более 300 символов"
    return None


def validate_hotel_name(value: str) -> str | None:
    if not value:
        return "Укажите название отеля"
    if len(value) > 80:
        return "Не более 80 символов"
    return None


def validate_display_name(value: str) -> str | None:
    if not value:
        return "Укажите имя"
    if len(value) > 60:
        return "Не более 60 символов"
    return None


def validate_login(value: str) -> str | None:
    if not value:
        return "Укажите логин"
    if not LOGIN_RE.match(value):
        return "3-32 символа: латиница, цифры, точка, дефис, подчёркивание"
    return None


def validate_password(value: str) -> str | None:
    # Пароль НЕ обрезается: пробелы по краям — часть пароля. Длина 8-128.
    if len(value) < 8:
        return "Не менее 8 символов"
    if len(value) > 128:
        return "Не более 128 символов"
    return None


def validate_date(value: str) -> str | None:
    """ГГГГ-ММ-ДД для фильтров истории (T10); проверка порядка — на месте."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return "Формат даты: ГГГГ-ММ-ДД"
    return None
