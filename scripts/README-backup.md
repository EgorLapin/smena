# Резервное копирование и восстановление («СменаПринята», AL-6 / FR-A10)

Два скрипта в этом каталоге:

- `backup.sh` — суточная консистентная копия базы SQLite
  (`sqlite3 "$DATABASE_PATH" ".backup ..."`), сжатие gzip в
  `smena-YYYY-MM-DD.sql.gz`, отправка во внешнее S3-совместимое хранилище
  через rclone, хранение 14 последних копий локально и в удалённом
  хранилище (старые удаляются). При любой ошибке — запись причины в журнал
  и ненулевой код выхода.
- `restore.sh` — восстановление из копии: сначала проверка
  `PRAGMA integrity_check;` (вывод обязан быть ровно `ok`), и только потом
  остановка службы `smena`, сохранение прежней базы (не удаление) и
  перенос копии на `DATABASE_PATH`.

## Конфигурация

Оба скрипта читают `/etc/smena/env` (формат `KEY=VALUE`):

| Переменная         | По умолчанию                  | Назначение                                        |
|--------------------|-------------------------------|---------------------------------------------------|
| `DATABASE_PATH`    | `/var/lib/smena/smena.db`     | файл базы SQLite                                   |
| `BACKUP_REMOTE`    | *(пусто — ошибка)*            | rclone-приёмник `remote:bucket`, обязателен        |
| `BACKUP_KEEP`      | `14`                          | сколько копий хранить локально и в удалённом хранилище |
| `BACKUP_LOCAL_DIR` | `/var/backups/smena`          | каталог локальных копий                            |

Пример `/etc/smena/env` (права `0640 root:smena`):

```bash
DATABASE_PATH=/var/lib/smena/smena.db
BACKUP_REMOTE=smena-s3:smena-backups
BACKUP_KEEP=14
BACKUP_LOCAL_DIR=/var/backups/smena
```

Без `BACKUP_REMOTE` `backup.sh` фиксирует причину в журнале и завершается
ненулевым кодом — это требование ТЗ (копия обязана храниться отдельно от
сервера).

## Подготовка VPS (один раз)

```bash
apt install sqlite3 rclone
rclone config          # создать remote, например smena-s3 (S3-совместимое хранилище)
mkdir -p /var/backups/smena
chmod 750 /var/backups/smena
cp scripts/backup.sh scripts/restore.sh /srv/smena/scripts/
chmod 750 /srv/smena/scripts/backup.sh /srv/smena/scripts/restore.sh
touch /var/log/smena-backup.log && chown root:adm /var/log/smena-backup.log
```

## Ежедневный запуск (cron)

Файл `/etc/cron.d/smena-backup`. ВАЖНО: cron смотрит время сервера (обычно
UTC), а не местное время отеля — для «03:15 Новосибирск» на UTC-машине пишем
`15 20`. Вызов явным `/usr/bin/bash` (после scp у файла может не быть +x):

```cron
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
15 20 * * * root /usr/bin/bash /srv/smena/scripts/backup.sh >> /var/log/smena-backup.log 2>&1
```

Защита от наложения запусков встроена (`flock`), повторный cron-запуск
прервётся без изменений.

## Как проверить, что копия долетела

```bash
# список копий в удалённом хранилище (их должно быть не больше BACKUP_KEEP)
rclone lsf smena-s3:smena-backups --include 'smena-*.sql.gz'

# размер свежей копии
rclone lsl smena-s3:smena-backups --include "smena-$(date +%F).sql.gz"

# или быстро: размер + дата последних объектов
rclone ls --files-only smena-s3:smena-backups | tail -n 3

# журнал за сегодня
grep "$(date +%F)" /var/log/smena-backup.log
journalctl -t smena-backup --since today --no-pager
```

Признак успеха в журнале: строка `ГОТОВО: smena-YYYY-MM-DD.sql.gz (<N> байт) …`.
Любая строка `ERROR:` означает, что запуск завершился ненулевым кодом.

## Восстановление

```bash
# из локальной копии
sudo /srv/smena/scripts/restore.sh /var/backups/smena/smena-2026-11-08.sql.gz

# или прямо из удалённого хранилища (имя — как у rclone lsf)
sudo /srv/smena/scripts/restore.sh --remote smena-2026-11-08.sql.gz
```

Что делает скрипт: распаковывает копию во временный файл, проверяет
`PRAGMA integrity_check;` (при результате, отличном от `ok`, работа
останавливается, служба НЕ трогается), затем останавливает `smena`,
сохраняет прежнюю базу в `DATABASE_PATH.pre-restore.<unix-время>` (не
удаляет), переносит копию на место базы, восстанавливает права/владельца и
запускает `smena`. В конце скрипт печатает список команд для проверки.

## Порядок проверки AC-9 (демонстрация на стенде)

1. Войдите в приложение и добавьте тестовую запись (например, смену
   «AC9-TEST-1»).
2. Зафиксируйте количество записей до копии:
   `sqlite3 /var/lib/smena/smena.db "SELECT COUNT(*) FROM shift;"`
   (таблицу подставьте по схеме; главное — считать одну и ту же таблицу).
3. Выполните копию вручную: `sudo /srv/smena/scripts/backup.sh`
   и проверьте появление `smena-$(date +%F).sql.gz` локально и в
   `rclone lsf <BACKUP_REMOTE>`.
4. Добавьте ещё одну запись («AC9-TEST-2») — она появится только в «живой»
   базе, но не во вчерашней копии.
5. Восстановите утреннюю копию:
   `sudo /srv/smena/scripts/restore.sh --remote smena-$(date +%F).sql.gz`
   (если сегодня копия уже создавалась на шаге 3 — возьмите вчерашнее имя).
6. Сверьте количество записей: оно должно вернуться к значению из шага 2,
   «AC9-TEST-2» исчезнуть, «AC9-TEST-1» остаться. Прежняя база при этом
   сохранена в `smena.db.pre-restore.*` — удалите её после проверки.
7. Проверьте журнал (`/var/log/smena-backup.log`) — успешные строки
   «ГОТОВО»/«ВОССТАНОВЛЕНИЕ ВЫПОЛНЕНО», ошибок `ERROR:` нет.
