#!/usr/bin/env bash
#
# restore.sh — восстановление базы «СменаПринята» из резервной копии (AL-6, AC-9).
#
# Использование:
#   restore.sh /var/backups/smena/smena-2026-11-08.sql.gz
#   restore.sh --remote smena-2026-11-08.sql.gz     # скачать копию из BACKUP_REMOTE
#
# Порядок работы:
#   1) Читает конфигурацию из /etc/smena/env (формат KEY=VALUE).
#   2) Копия (локальный файл или загрузка из rclone) распаковывается во
#      временный файл и проверяется: sqlite3 "PRAGMA integrity_check;"
#      обязан вернуть РОВНО "ok". Иначе — аварийный выход БЕЗ остановки службы.
#   3) Только после успешной проверки: systemctl stop smena;
#      прежний файл базы НЕ удаляется, а сохраняется как
#      ${DATABASE_PATH}.pre-restore.<unix-time>; копия переносится на
#      DATABASE_PATH; права/владелец берутся с сохранённого файла
#      (иначе smena:smena, 0640); systemctl start smena.

set -euo pipefail

# --- Параметры -----------------------------------------------------------------
ENV_FILE="${ENV_FILE:-/etc/smena/env}"
SERVICE_NAME="smena"
BACKUP_LOG_FILE="${BACKUP_LOG_FILE:-/var/log/smena-backup.log}"

TMP_DL=""
TMP_SQL=""
STOPPED=0
PRESERVED=""
PRESERVED_FOUND=0

usage() {
    cat <<'USAGE'
Использование:
  restore.sh ПУТЬ_К_КОПИИ.sql.gz
  restore.sh --remote ИМЯ_КОПИИ.sql.gz     (загрузит копию из BACKUP_REMOTE)

Переменные читаются из /etc/smena/env:
  DATABASE_PATH   (по умолчанию /var/lib/smena/smena.db)
  BACKUP_REMOTE   (rclone remote:bucket, нужен только для --remote)
USAGE
}

log() {
    local msg="$*"
    local line
    line="$(printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$msg")"
    printf '%s\n' "$line" >> "$BACKUP_LOG_FILE" 2>/dev/null || true
    logger -t smena-restore -- "$msg" 2>/dev/null || true
    printf '%s\n' "$msg"
}

fail() {
    local reason="$1"
    local code="${2:-1}"
    log "ОШИБКА: ${reason}"
    exit "$code"
}

on_error() {
    local rc=$?
    local line="$1"
    log "ОШИБКА: restore.sh прерван на строке ${line} (код выхода ${rc})."
}

cleanup() {
    rm -f "$TMP_DL" "$TMP_SQL" 2>/dev/null || true
}

# Если сбой произошёл уже после остановки службы: возвращаем сохранённую базу
# и пытаемся снова запустить smena, чтобы не оставить отель без сервиса.
fail_after_stop() {
    local reason="$1"
    log "ОШИБКА: ${reason}"
    if [ "$PRESERVED_FOUND" -eq 1 ] && [ -e "$PRESERVED" ] && [ ! -s "$DATABASE_PATH" ]; then
        log "Откат: возвращаю сохранённый файл базы ${PRESERVED} на ${DATABASE_PATH}."
        mv -f "$PRESERVED" "$DATABASE_PATH" 2>/dev/null || log "WARNING: откат не удался — прежняя база лежит в файле ${PRESERVED}."
    fi
    if [ "$STOPPED" -eq 1 ]; then
        log "Пытаюсь снова запустить службу ${SERVICE_NAME}."
        systemctl start "$SERVICE_NAME" 2>/dev/null || log "WARNING: не удалось запустить ${SERVICE_NAME} — выполните: systemctl start ${SERVICE_NAME}"
    fi
    exit 1
}

trap cleanup EXIT
trap 'on_error $LINENO' ERR

umask 027

# --- Разбор аргументов -----------------------------------------------------------
REMOTE_NAME=""
LOCAL_SRC=""

if [ "$#" -eq 0 ]; then
    usage
    exit 2
fi

while [ "$#" -gt 0 ]; do
    case "$1" in
        --remote)
            if [ -n "$REMOTE_NAME" ] || [ -n "$LOCAL_SRC" ]; then
                usage
                fail "Источник копии уже задан: укажите либо --remote ИМЯ, либо путь к файлу." 2
            fi
            if [ "$#" -lt 2 ] || [ -z "${2:-}" ]; then
                usage
                fail "После --remote требуется имя файла копии из BACKUP_REMOTE." 2
            fi
            REMOTE_NAME="$2"
            shift 2
            ;;
        --remote=*)
            if [ -n "$REMOTE_NAME" ] || [ -n "$LOCAL_SRC" ]; then
                usage
                fail "Источник копии уже задан: укажите либо --remote ИМЯ, либо путь к файлу." 2
            fi
            REMOTE_NAME="${1#--remote=}"
            if [ -z "$REMOTE_NAME" ]; then
                usage
                fail "После --remote= требуется имя файла копии из BACKUP_REMOTE." 2
            fi
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            usage
            fail "Неизвестная опция: $1" 2
            ;;
        *)
            if [ -n "$REMOTE_NAME" ] || [ -n "$LOCAL_SRC" ]; then
                usage
                fail "Источник копии уже задан: укажите либо --remote ИМЯ, либо путь к файлу." 2
            fi
            LOCAL_SRC="$1"
            shift
            ;;
    esac
done

if [ -n "$REMOTE_NAME" ]; then
    case "$REMOTE_NAME" in
        */*)
            fail "Имя удалённой копии не должно содержать «/»: «${REMOTE_NAME}»." 2
            ;;
    esac
fi

# --- Конфигурация ------------------------------------------------------------------
if [ -r "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a; . "$ENV_FILE"; set +a
else
    log "ПРЕДУПРЕЖДЕНИЕ: файл конфигурации ${ENV_FILE} не найден — использую значения по умолчанию."
fi

DATABASE_PATH="${DATABASE_PATH:-/var/lib/smena/smena.db}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
BACKUP_LOG_FILE="${BACKUP_LOG_FILE:-/var/log/smena-backup.log}"

if [ "$(id -u)" -ne 0 ]; then
    fail "Скрипт нужно запускать от root (нужен systemctl stop/start): sudo $0 ..." 2
fi

for cmd in sqlite3 gunzip systemctl; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        fail "Не найдена утилита «${cmd}» — восстановление невозможно, служба НЕ тронута."
    fi
done

if [ -n "$REMOTE_NAME" ]; then
    if ! command -v rclone >/dev/null 2>&1; then
        fail "Для опции --remote нужна утилита rclone."
    fi
    if [ -z "$BACKUP_REMOTE" ]; then
        fail "BACKUP_REMOTE не задан в ${ENV_FILE} — копию неоткуда скачивать."
    fi
    case "$BACKUP_REMOTE" in
        *:*) : ;;
        *)
            fail "BACKUP_REMOTE=\"${BACKUP_REMOTE}\" — неверный формат, ожидается remote:bucket."
            ;;
    esac
fi

# --- Подготовка исходной копии -------------------------------------------------------
SRC_GZ=""
if [ -n "$REMOTE_NAME" ]; then
    if ! TMP_DL="$(mktemp /tmp/smena-restore-dl.XXXXXXXX)"; then
        fail "Не удалось создать временный файл в /tmp."
    fi
    log "Скачиваю копию ${BACKUP_REMOTE}/${REMOTE_NAME} ..."
    if ! rclone copyto "${BACKUP_REMOTE}/${REMOTE_NAME}" "$TMP_DL"; then
        fail "rclone не смог скачать ${BACKUP_REMOTE}/${REMOTE_NAME} — служба НЕ остановлена."
    fi
    SRC_GZ="$TMP_DL"
else
    if [ ! -f "$LOCAL_SRC" ]; then
        fail "Файл копии не найден: ${LOCAL_SRC}"
    fi
    if [ ! -r "$LOCAL_SRC" ]; then
        fail "Файл копии недоступен для чтения: ${LOCAL_SRC}"
    fi
    SRC_GZ="$LOCAL_SRC"
fi

case "$SRC_GZ" in
    *.gz) : ;;
    *) log "ПРЕДУПРЕЖДЕНИЕ: имя файла не похоже на .sql.gz — пробую распаковать gunzip'ом." ;;
esac

if ! gzip -t "$SRC_GZ"; then
    fail "Файл ${SRC_GZ} повреждён (gzip -t) — восстановление прервано, служба НЕ остановлена."
fi

if ! TMP_SQL="$(mktemp /tmp/smena-restore-db.XXXXXXXX)"; then
    fail "Не удалось создать временный файл в /tmp."
fi
if ! gunzip -c "$SRC_GZ" > "$TMP_SQL"; then
    fail "Не удалось распаковать ${SRC_GZ} (gunzip) — служба НЕ остановлена."
fi

if [ ! -s "$TMP_SQL" ]; then
    fail "Распакованная база пуста (0 байт) — копия непригодна, служба НЕ остановлена."
fi

# --- Проверка целостности ДО остановки службы -----------------------------------------
log "Проверяю целостность копии: PRAGMA integrity_check;"
CHECK_OUT=""
if ! CHECK_OUT="$(sqlite3 "$TMP_SQL" "PRAGMA integrity_check;")"; then
    fail "sqlite3 вернул ошибку при проверке копии — служба НЕ остановлена."
fi
if [ "$CHECK_OUT" != "ok" ]; then
    log "Вывод PRAGMA integrity_check: ${CHECK_OUT}"
    fail "Копия повреждена (ожидался вывод ровно «ok») — восстановление прервано, служба НЕ остановлена."
fi
log "Проверка integrity_check пройдена: ok."

# --- Остановка службы и подстановка копии ------------------------------------------------
log "Останавливаю службу ${SERVICE_NAME} ..."
STOPPED=1
if ! systemctl stop "$SERVICE_NAME"; then
    STOPPED=0
    fail "Не удалось остановить службу ${SERVICE_NAME} — база НЕ изменена."
fi

TS="$(date +%s)"
PRESERVED="${DATABASE_PATH}.pre-restore.${TS}"

if [ -e "$DATABASE_PATH" ]; then
    if ! mv -f "$DATABASE_PATH" "$PRESERVED"; then
        fail_after_stop "Не удалось сохранить прежний файл базы в ${PRESERVED}."
    fi
    PRESERVED_FOUND=1
    log "Прежняя база сохранена (не удалена): ${PRESERVED}"
    # Убираю сопутствующие WAL/SHM-файлы прежней базы, чтобы они не «пережили» замену.
    for ext in -wal -shm; do
        if [ -e "${DATABASE_PATH}${ext}" ]; then
            if ! mv -f "${DATABASE_PATH}${ext}" "${PRESERVED}${ext}" 2>/dev/null; then
                rm -f "${DATABASE_PATH}${ext}" 2>/dev/null || log "WARNING: не удалось убрать ${DATABASE_PATH}${ext}."
            fi
        fi
    done
else
    log "Прежней базы на ${DATABASE_PATH} нет — сохранять нечего."
fi

if ! mv -f "$TMP_SQL" "$DATABASE_PATH"; then
    fail_after_stop "Не удалось разместить восстановленную базу на ${DATABASE_PATH}."
fi
TMP_SQL=""

# Права и владелец: со сохранённого файла, иначе smena:smena и 0640.
if [ "$PRESERVED_FOUND" -eq 1 ]; then
    if chown --reference="$PRESERVED" "$DATABASE_PATH" 2>/dev/null && chmod --reference="$PRESERVED" "$DATABASE_PATH" 2>/dev/null; then
        log "Права и владелец скопированы с сохранённого файла ${PRESERVED}."
    elif chown smena:smena "$DATABASE_PATH" 2>/dev/null && chmod 0640 "$DATABASE_PATH" 2>/dev/null; then
        log "Права выставлены по умолчанию: smena:smena 0640."
    else
        fail_after_stop "Не удалось задать права на ${DATABASE_PATH} — проверьте вручную: ls -l ${DATABASE_PATH}"
    fi
else
    if chown smena:smena "$DATABASE_PATH" 2>/dev/null && chmod 0640 "$DATABASE_PATH" 2>/dev/null; then
        log "Права выставлены по умолчанию: smena:smena 0640."
    else
        fail_after_stop "Не удалось задать права на ${DATABASE_PATH} — проверьте вручную: ls -l ${DATABASE_PATH}"
    fi
fi

log "Запускаю службу ${SERVICE_NAME} ..."
if ! systemctl start "$SERVICE_NAME"; then
    STOPPED=0
    log "Служба ${SERVICE_NAME} НЕ запустилась: journalctl -u ${SERVICE_NAME} -n 50"
    log "Новая база уже на месте ${DATABASE_PATH}; прежняя — ${PRESERVED}."
    exit 1
fi
STOPPED=0

log "ВОССТАНОВЛЕНИЕ ВЫПОЛНЕНО: ${SRC_GZ} -> ${DATABASE_PATH}"

cat <<TIPS

Проверьте после восстановления:
  1) systemctl status ${SERVICE_NAME} --no-pager
  2) journalctl -u ${SERVICE_NAME} -n 50 --no-pager
  3) sqlite3 "${DATABASE_PATH}" "PRAGMA integrity_check;"                 # должно быть ok
  4) sqlite3 "${DATABASE_PATH}" "SELECT COUNT(*) FROM sqlite_master;"      # объекты базы на месте
  5) Откройте приложение в браузере и сверьте данные (смены, смены-приёмки, учётные записи).

Прежняя база сохранена здесь: ${PRESERVED} (если существовала).
Удаляйте её только убедившись, что восстановление успешно.
Откат назад:
  systemctl stop ${SERVICE_NAME}
  mv -f "${PRESERVED}" "${DATABASE_PATH}"
  systemctl start ${SERVICE_NAME}
TIPS

exit 0
