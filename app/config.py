"""Конфигурация приложения.

Источник значений — переменные окружения; для локальной разработки
подхватывается файл .env в корне проекта (простой парсер без зависимостей,
см. DECISIONS.md D-004). На VPS значения задаются в systemd unit.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Корень проекта (каталог, где лежит pyproject/README, — родитель app/)
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


def _as_bool(value: str) -> bool:
    return value.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///data/smena.db"
    secret_key: str = "change-me"
    debug: bool = False
    pilot_days: int = 14
    max_active_issues: int = 10
    base_dir: Path = field(default_factory=lambda: BASE_DIR)

    @property
    def database_path(self) -> Path:
        """Файл SQLite как путь (из sqlite:///... URL)."""
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError(f"ожидается sqlite URL, получено: {self.database_url!r}")
        rel = self.database_url[len(prefix):]
        path = Path(rel)
        return path if path.is_absolute() else self.base_dir / path


def get_settings() -> Settings:
    """Собрать настройки из окружения (лениво — чтобы тесты могли подменить)."""
    _load_dotenv(BASE_DIR / ".env")
    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite:///data/smena.db"),
        secret_key=os.getenv("SECRET_KEY", "change-me"),
        debug=_as_bool(os.getenv("DEBUG", "0")),
        pilot_days=int(os.getenv("PILOT_DAYS", "14")),
        max_active_issues=int(os.getenv("MAX_ACTIVE_ISSUES", "10")),
    )
