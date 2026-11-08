"""Отображение времени: хранение — naive UTC (D-009), показ — в поясе отеля.

Форматы по макетам ТЗ: «Смена открыта в 09:02», «сдана 08:55»,
«01.11.2026 09:03», возраст — «1 ч 20 мин», «14 ч», «31 ч» (часами до 48 ч).
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FALLBACK_TZ = "UTC"


def _zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        # Пояс из БД всегда из списка ТЗ, но рендер страницы не должен падать.
        return ZoneInfo(FALLBACK_TZ)


def to_local(dt_utc: datetime, tz_name: str) -> datetime:
    """naive UTC из БД → aware-время в поясе отеля."""
    return dt_utc.replace(tzinfo=timezone.utc).astimezone(_zone(tz_name))


def fmt_dt(dt_utc: datetime, tz_name: str) -> str:
    """01.11.2026 09:03"""
    return to_local(dt_utc, tz_name).strftime("%d.%m.%Y %H:%M")


def fmt_time(dt_utc: datetime, tz_name: str) -> str:
    """09:02"""
    return to_local(dt_utc, tz_name).strftime("%H:%M")


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русская плюрализация счётных форм: 1 раз / 2 раза / 5 раз."""
    n10, n100 = n % 10, n % 100
    if n100 >= 11 or n10 >= 5 or n10 == 0:
        return many
    if n10 == 1:
        return one
    return few


def humanize_age(dt_utc: datetime, now_utc: datetime) -> str:
    """Возраст с момента dt: «5 мин», «1 ч 20 мин», «14 ч», «31 ч», «3 дн»."""
    minutes = max(0, int((now_utc - dt_utc).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} {plural(minutes, 'минута', 'минуты', 'минут')}"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч" + (f" {mins} мин" if mins else "")
    if hours < 48:
        return f"{hours} ч"
    days = hours // 24
    return f"{days} {plural(days, 'день', 'дня', 'дней')}"


def plural_carries(n: int) -> str:
    return f"Перенесён {n} {plural(n, 'раз', 'раза', 'раз')}"
