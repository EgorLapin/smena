# СменаПринята

Веб-приложение — журнал передачи смены для мини-отелей (10–30 номеров).
Учебный MVP по ТЗ v1.1.2. Статус: **T01–T13 закрыты, T15 выполнен (D-025),
выкат на VPS работает** — журнал, сдача/приём смены, обзор управляющего,
история с поиском, сотрудники, доступ, экраны и тексты. Автотесты и живой
автопрогон AC-1…AC-8/AC-10/AC-11 зелёные (D-024); AC-1 и AC-12 пройдены
вручную на проде 09.11 (AC-12 — с замечаниями, правки D-027); остаётся
«на глазами» AC-13 (чек-лист T14).

## Документация проекта

- **ТЗ v1.1.2 (02.11.2026, утверждено)** — единственный источник требований
  (текст: `docs/TZ-v1.1.2-izvlechenie-teksta.txt`).
- [DECISIONS.md](DECISIONS.md) — журнал решений и открытых вопросов. **Правило:
  каждое принятое/отсечённое решение фиксируется там же в этот же день.**
- Роли: Участник 1 — бизнес-гипотеза (Лошманов), Участник 2 — архитектура/ТЗ
  (Сотников), Участник 3 — реализация (Лапин).

## Стек

Python 3.11+, FastAPI + uvicorn (1 воркер), Jinja2, SQLAlchemy 2.x, SQLite
(WAL, BEGIN IMMEDIATE, busy_timeout=5000), argon2-cffi, pytest.
Прод: Ubuntu 22.04+, Caddy (Let's Encrypt), systemd, cron; копии
`sqlite3 .backup` + gzip + rclone → внешнее хранилище.

## Быстрый старт (разработка)

```powershell
# Windows
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env   # и заполнить SECRET_KEY (команда — в комментарии файла)
uvicorn app.main:app --reload
# открыть http://127.0.0.1:8000
```

```bash
# Linux/macOS
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env     # и заполнить SECRET_KEY
uvicorn app.main:app --reload
```

Первый шаг на пустой базе — регистрация отеля (создаётся управляющий).
Демонстрационные данные без регистрации:

```bash
python scripts/seed_demo.py     # отель «Северная усадьба», логины demo-*
python scripts/set_paid.py --list              # подписки отелей
python scripts/set_paid.py "Северная усадьба" 2026-12-01   # подтвердить оплату (FR-A7)
```

## Тесты

```bash
pytest            # полный набор; каждый тест — изолированная временная БД
```

Покрываются: AC-2…AC-8, AC-10, AC-11 (NFR-10), плюс регрессионные по FR/T02.
Ручная приёмка: AC-1 (цикл двумя учётками), AC-9 (восстановление из копии),
AC-12 (15 минут без подсказок), AC-13 (HTTPS, флаги cookie, 360 px).

## Развёртывание

Пошагово — [deploy/README-deploy.md](deploy/README-deploy.md): VPS Ubuntu,
Caddy (авто-HTTPS + HTTP→HTTPS + статическая 502 с текстом ТЗ), systemd
(`deploy/smena.service`), `.env` с SECRET_KEY/DATABASE_PATH, cron копии.
После включения systemd служба поднимается после перезагрузки (проверка из AC-13).

## Резервная копия и восстановление

`scripts/backup.sh` (cron ежедневно): `sqlite3 .backup` → gzip → rclone во
внешнее хранилище, локально и удалённо хранится 14 копий (AL-6, FR-A10).
Восстановление — `scripts/restore.sh КОПИЯ.db.gz`: проверка
`PRAGMA integrity_check` на временной базе, остановка службы, замена файла,
старая база сохраняется как `*.pre-restore.<ts>`. Подробности и разбор типичных
ошибок — [scripts/README-backup.md](scripts/README-backup.md). Сценарий AC-9
проверяется на тестовой базе перед вводом в эксплуатацию.

## Порядок при сбое (бумажная передача, NFR-9)

При недоступности приложения («Запись не сохранена» повторяется) смена
ведётся на бумаге: открытые вопросы передаются вручную под подпись принявшего.
После восстановления администратор вносит бумажные записи в журнал как новые
вопросы своей текущей смены; бумажный лист подшивается к журналу отеля.
Восстановление базы — раздел выше.

## Структура

```
app/
  main.py        # фабрика create_app, middleware CSRF, обработчики ошибок
  config.py      # настройки из env (имена — по таблице «Конфигурация» ТЗ)
  db.py          # движок, сессии, PRAGMA (WAL/busy_timeout/FK), BEGIN IMMEDIATE
  models.py      # T02: таблицы, частичные индексы, CHECK, триггеры неизменяемости
  security.py    # argon2, подписанная cookie-сессия (30 дней), CSRF
  validation.py  # AL-1 (обрезка, lower, place_norm) и правила валидации
  times.py       # показ времени в поясе отеля, возраст, плюрализация
  messages_ru.py # тексты ошибок по таблице ТЗ (все коды, T11)
  errors.py deps.py render.py   # AppError, доступ/flash, рендеринг Jinja2
  services/      # auth (T03), staff (T04), journal (T05), handover (T06/T07),
                 # overview (T09), history (T10) — бизнес-правила и транзакции
  routes/        # страницы тех же модулей; / — выбор экрана по роли
  templates/     # login/register/error/journal/issue_form/issue_card/handover_*
                 # /overview/history/staff + base
  static/        # css/js (адаптив от 360px; JS блокирует кнопку — NFR-6)
scripts/         # backup.sh, restore.sh (T12); set_paid.py (FR-A7); seed_demo.py;
                 # smoke_live.py — приёмо-сдаточный прогон по живому стенду;
                 # live_acceptance.py — AC-1…8/11/12 по HTTP на развёрнутом стенде
deploy/          # Caddyfile, 502.html, smena.service, README-deploy.md (T13)
config/          # timezones.txt — пояса для регистрации
docs/            # текст ТЗ v1.1.2
tests/           # pytest (временная БД на каждый тест)
```

## План

15 задач / 60 ч, критический путь 35 ч (T01→…→T14). T15 (S-фильтр в истории) —
закрыт выполнением, не отсечением (Q-003, D-025). По T14 приняты вручную
AC-1/AC-9 (репетиция restore)/AC-12 (с замечаниями, D-027); остаётся «на
глазами» AC-13 (мобильные экраны) и git push (автопрогон AC-2…AC-8/AC-10/
AC-11 на VPS-стенде — 38/38 PASS, D-024).
