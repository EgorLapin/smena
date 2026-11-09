# Развёртывание «СменаПринята» на чистый VPS (Ubuntu 22.04 / 24.04)

Инструкция выката T13: Ubuntu 22.04 или 24.04 LTS (наш прод — TimeWeb Cloud,
24.04, см. D-018), один VPS, домен указывает A-записью на сервер. Состав каталога `deploy/`:

| Файл | Куда кладём на VPS |
|---|---|
| `smena.service` | `/etc/systemd/system/smena.service` |
| `Caddyfile` | `/etc/caddy/Caddyfile` |
| `502.html` | `/etc/caddy/502.html` |

> ⚠️ **Python 3.11+.** Ubuntu 22.04 поставляется с Python 3.10, а проекту нужен
> 3.11+ — ставим интерпретатор из PPA **deadsnakes** (шаг 2). Не пропускайте:
> на системном 3.10 приложение не заведётся.

Все команды — от root (или через `sudo`). `{DOMAIN}` везде заменяется на ваш домен.

---

## 1. Базовые пакеты

```bash
apt update && apt upgrade -y
# software-properties-common нужен для add-apt-repository (PPA на минимальных
# образах Ubuntu 22.04 может отсутствовать)
apt install -y git curl gnupg ufw software-properties-common
```

## 2. Python 3.11 из PPA deadsnakes

```bash
add-apt-repository -y ppa:deadsnakes/ppa
apt update
apt install -y python3.11 python3.11-venv
python3.11 --version   # Python 3.11.x
```

## 3. Пользователь и каталоги

Приложение работает под выделённым непривилегированным пользователем `smena`;
БД — в `/var/lib/smena`, логи — в `/var/log/smena`, секреты — в `/etc/smena`.

```bash
useradd --system --home /srv/smena --shell /usr/sbin/nologin smena

install -d -o root  -g smena  -m 750 /srv/smena
install -d -o smena -g smena  -m 750 /var/lib/smena
install -d -o smena -g smena  -m 755 /var/log/smena
install -d -o root  -g smena  -m 750 /etc/smena
```

## 4. Код и virtualenv

Репозиторий проекта клонируем в `/srv/smena` (URL замените на свой):

```bash
git clone <URL_репозитория> /srv/smena
cd /srv/smena

python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

chown -R smena:smena /srv/smena
```

## 5. Конфигурация и SECRET_KEY

Секреты живут в `/etc/smena/env` (формат `KEY=VALUE`, читается systemd через
`EnvironmentFile`). `SECRET_KEY` **обязателен**; остальные имена — из
`.env.example` в репозитории (`DATABASE_PATH`, `BACKUP_REMOTE`, пороги
`PENDING_OVERDUE_HOURS`, `ISSUE_STALE_HOURS`, `ISSUE_STALE_CARRIES` и т. д.).

```bash
# Сгенерировать ключ подписи cookie-сессий:
python3.11 -c "import secrets; print(secrets.token_urlsafe(32))"

# Создать файл конфигурации из примера и заполнить значения:
cp /srv/smena/.env.example /etc/smena/env
nano /etc/smena/env
#  SECRET_KEY=<сгенерированный выше токен>
#  DATABASE_PATH=/var/lib/smena/smena.db
#  BACKUP_REMOTE=<значение до T12; без него backup.sh падает — для раннего
#                выката каркаса допустимо оставить пустым>

chmod 600 /etc/smena/env
chown root:smena /etc/smena/env
```

## 6. systemd unit приложения

```bash
cp /srv/smena/deploy/smena.service /etc/systemd/system/smena.service
systemctl daemon-reload
systemctl enable --now smena

# Проверки:
systemctl status smena --no-pager
journalctl -u smena -e --no-pager        # последние строки лога
curl -s http://127.0.0.1:8000/health     # ожидаем {"status":"ok"}
```

Приложение слушает **только** `127.0.0.1:8000`, строго 1 воркер uvicorn
(SQLite WAL, единый писатель). Наружу оно не доступно — только через Caddy.

## 7. Caddy из официального репозитория

```bash
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  > /etc/apt/sources.list.d/caddy-stable.list
apt update
apt install -y caddy
```

Кладём нашу конфигурацию и страницу ошибки, вписываем домен вместо `{DOMAIN}`:

```bash
cp /srv/smena/deploy/Caddyfile /etc/caddy/Caddyfile
cp /srv/smena/deploy/502.html  /etc/caddy/502.html

sed -i 's/{DOMAIN}/домен.пример.рф/g' /etc/caddy/Caddyfile   # <- ваш домен

# Обязательная проверка конфигурации перед запуском:
caddy validate --config /etc/caddy/Caddyfile

systemctl enable --now caddy
systemctl status caddy --no-pager
```

Caddy сам получит сертификат Let's Encrypt (порты 80/443 должны быть открыты)
и включит редирект http→https. При смене конфига после валидации:
`systemctl reload caddy`.

## 8. Файрвол

```bash
ufw allow OpenSSH
ufw allow 'Caddy Full'   # 80 + 443; если образа не даёт такой профиль
                         # (бывает на образах хостинга, напр. TimeWeb):
ufw allow 80/tcp && ufw allow 443/tcp
ufw enable
```

Порт 8000 наружу не открываем — приложение доступно только с localhost.

## 9. Проверки приёмки

```bash
# 1) HTTP отдаёт редирект на HTTPS (ожидаем HTTP/1.1 308, Location: https://...):
curl -I http://{DOMAIN}

# 2) HTTPS открывается: / требует вход → 303 на /login → страница входа 200
#    (ожидаем финальный HTTP/2 200 после редиректов):
curl -IL https://{DOMAIN}

# 3) Юниты включены и взлетят после перезагрузки:
systemctl is-enabled smena caddy        # оба: enabled
sudo systemctl reboot
# после возвращения:
systemctl is-active smena caddy         # оба: active
curl -s http://127.0.0.1:8000/health

# 4) CSRF: POST без csrf_token отклоняется 403 (контракт ТЗ, NFR-2):
curl -sk -o /dev/null -w '%{http_code}\n' \
  -X POST https://{DOMAIN}/login \
  -d 'login=x&password=x'               # ожидаем 403

# 5) Страница 502: остановить приложение и открыть сайт —
#    ожидаем 502 и текст «Запись не сохранена…» + код UPSTREAM_DOWN:
sudo systemctl stop smena
curl -sk -o /dev/null -w '%{http_code}\n' https://{DOMAIN}/login   # 502
sudo systemctl start smena
```

## 10. Ранний выкат и перекатка

Порядок по плану проекта:

1. **Ранний выкат** — этот минимум на текущем каркасе (T01–T03): убеждаемся,
   что HTTPS, systemd, /health и 502-страница работают на живом VPS.
2. **Перекатка полной версии** — по мере готовности задач T04–T14:

```bash
cd /srv/smena
sudo -u smena git pull
.venv/bin/pip install -r requirements.txt   # если зависимости менялись
sudo systemctl restart smena
curl -s http://127.0.0.1:8000/health
```

Миграции схемы (когда появятся) выполняются тем же путём до `restart`.

## 11. Что дальше (не входит в этот минимум)

- cron-бэкапы `scripts/backup.sh` (sqlite3 `.backup` + rclone → `BACKUP_REMOTE`) — T12;
  расписание `/etc/cron.d/smena-backup`: `15 20 * * *` UTC = 03:15 Новосибирска
  (сервер в UTC — локальное «15 3» было бы днём по НСК; D-026), от root,
  backup.sh: root:root 755, в строке явные `/usr/bin/bash` и PATH.
- logrotate для `/var/log/smena`;
- мониторинг/алерты — вне объёма MVP.
