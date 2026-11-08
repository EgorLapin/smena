"""Конфигурация приложения.

Имена и значения по умолчанию — строго по разделу «Конфигурация» ТЗ v1.1.2:
SECRET_KEY, DATABASE_PATH, TRIAL_DAYS, ACCOUNT_LIMIT, PENDING_OVERDUE_HOURS,
ISSUE_STALE_HOURS, ISSUE_STALE_CARRIES, DEFAULT_TIMEZONE, BACKUP_REMOTE.

Источник значений — переменные окружения; для локальной разработки
подхватывается файл .env в корне проекта (простой парсер без зависимостей,
см. DECISIONS.md D-002). На VPS значения задаются в systemd unit.
Список часовых поясов для регистрации — config/timezones.txt.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Корень проекта (каталог с app/, config/, tests/)
BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Загрузить KEY=VALUE из .env, если файл существует.

    Уже выставленные переменные окружения НЕ перезаписываются:
    окружение (systemd) всегда важнее файла.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    # Поля соответствуют таблице «Конфигурация» ТЗ (имена env — в скобках).
    secret_key: str = ""                     # SECRET_KEY (обязателен)
    database_path: str = "/var/lib/smena/smena.db"  # DATABASE_PATH
    trial_days: int = 14                     # TRIAL_DAYS
    account_limit: int = 10                  # ACCOUNT_LIMIT
    pending_overdue_hours: int = 12          # PENDING_OVERDUE_HOURS (A-5)
    issue_stale_hours: int = 24              # ISSUE_STALE_HOURS (A-5)
    issue_stale_carries: int = 2             # ISSUE_STALE_CARRIES (A-5)
    default_timezone: str = "Asia/Novosibirsk"  # DEFAULT_TIMEZONE (A-8)
    backup_remote: str = ""                  # BACKUP_REMOTE (обязателен до T12)

    @property
    def db_file(self) -> Path:
        """Файл SQLite как путь; относительный путь считается от корня проекта."""
        path = Path(self.database_path)
        return path if path.is_absolute() else BASE_DIR / path


def get_settings() -> Settings:
    """Собрать настройки из окружения (лениво — тесты подменяют env через monkeypatch)."""
    _load_dotenv(BASE_DIR / ".env")

    secret_key = os.getenv("SECRET_KEY", "")
    if not secret_key or secret_key == "change-me":
        # ТЗ: SECRET_KEY обязателен. Не даём стартнуть с пустым/шаблонным ключом.
        raise RuntimeError(
            "SECRET_KEY не задан. Скопируйте .env.example в .env и сгенерируйте ключ: "
            "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    return Settings(
        secret_key=secret_key,
        database_path=os.getenv("DATABASE_PATH", "/var/lib/smena/smena.db"),
        trial_days=int(os.getenv("TRIAL_DAYS", "14")),
        account_limit=int(os.getenv("ACCOUNT_LIMIT", "10")),
        pending_overdue_hours=int(os.getenv("PENDING_OVERDUE_HOURS", "12")),
        issue_stale_hours=int(os.getenv("ISSUE_STALE_HOURS", "24")),
        issue_stale_carries=int(os.getenv("ISSUE_STALE_CARRIES", "2")),
        default_timezone=os.getenv("DEFAULT_TIMEZONE", "Asia/Novosibirsk"),
        backup_remote=os.getenv("BACKUP_REMOTE", ""),
    )


def get_timezones() -> list[str]:
    """Список российских часовых поясов из config/timezones.txt (для формы регистрации)."""
    path = BASE_DIR / "config" / "timezones.txt"
    lines = path.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]
