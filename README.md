# СменаПринята

Веб-приложение — журнал передачи смены для мини-отелей (10–30 номеров).
Учебный MVP. Статус: **T01–T07 готовы** (каркас, схема БД, доступ, сотрудники,
журнал, сдача и приём смены). Конфиги деплоя (T13) и скрипты копий (T12) — в
`deploy/`, `scripts/`.

## Документация проекта

- **ТЗ v1.1.2 (02.11.2026, утверждено)** — единственный источник требований (внешний документ).
- [DECISIONS.md](DECISIONS.md) — журнал решений и открытых вопросов. **Правило: каждое
  принятое/отсечённое решение фиксируется там же в этот же день.**
- Роли: Участник 1 — бизнес-гипотеза (Лошманов), Участник 2 — архитектура/ТЗ (Сотников),
  Участник 3 — реализация (Лапин).

## Стек

Python 3.11+, FastAPI + uvicorn (1 воркер), Jinja2, SQLAlchemy 2.x, SQLite
(WAL, BEGIN IMMEDIATE, busy_timeout=5000), argon2-cffi, pytest.
Прод: Ubuntu 22.04+, Caddy (Let's Encrypt), systemd, cron; бэкапы sqlite3 .backup + rclone → S3.

## Быстрый старт (разработка)

```powershell
# Windows (этот репозиторий уже содержит .venv)
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env   # и заполнить SECRET_KEY

uvicorn app.main:app --reload
# http://127.0.0.1:8000
```

```bash
# Linux/macOS
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
```

## Тесты

```bash
pytest            # все автотесты (на сейчас — smoke каркаса)
```

Автотестами покрываются AC-2…AC-8, AC-10, AC-11 (добавляются с задачами T02–T14);
AC-1, AC-9, AC-12, AC-13 — ручная приёмка.

## Структура

```
app/
  main.py        # фабрика create_app, middleware CSRF, обработчики ошибок
  config.py      # настройки из env (имена — по таблице «Конфигурация» ТЗ)
  db.py          # движок, сессии, PRAGMA (WAL/busy_timeout/FK), BEGIN IMMEDIATE, init_db
  models.py      # T02: таблицы, частичные индексы, CHECK, триггеры неизменяемости
  security.py    # T03: argon2, подписанная cookie-сессия (30 дней), CSRF
  validation.py  # T03: AL-1 (обрезка, lower, place_norm) и правила валидации
  times.py       # показ времени в поясе отеля, возраст «1 ч 20 мин», плюрализация
  messages_ru.py # тексты ошибок по таблице ТЗ (расширяется на T11)
  errors.py      # AppError: код + статус + redirect/страница
  deps.py        # current_user/require_admin/require_manager, flash
  render.py      # единый рендеринг Jinja2 + flash
  services/      # бизнес-правила и транзакции: auth (T03), staff (T04), journal (T05)
  routes/        # страницы: auth (T03), staff (T04), journal+карточка (T05), заглушки
  templates/     # base, login/register/error/staff/journal/issue_form/issue_card/stub
  static/        # css/js (JS блокирует кнопку при отправке — NFR-6)
scripts/         # T12: backup.sh (sqlite3 .backup + gzip + rclone), restore.sh
deploy/          # T13: Caddyfile, systemd unit, 502.html, инструкция выката
config/          # timezones.txt (список поясов для регистрации)
docs/            # текст ТЗ v1.1.2 (извлечение из DOCX)
tests/           # pytest (фикстура client — временная БД на тест)
scripts/         # (T12) backup.sh, restore.sh, seed_demo
deploy/          # (T13) systemd unit, Caddyfile, cron
```

## План

15 задач / 60 ч, критический путь 35 ч:
T01→T02→T03→T05→T06→T07→T10→T14. T15 — резерв, первый кандидат на отсечение (Q-003).
