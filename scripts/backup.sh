#!/usr/bin/env bash
#
# backup.sh — суточное резервное копирование базы «СменаПринята» (AL-6, FR-A10).
#
# Порядок работы:
#   1) Читает конфигурацию из /etc/smena/env (формат KEY=VALUE).
#   2) Создаёт КОНСИСТЕНТНУЮ копию базы: sqlite3 "$DATABASE_PATH" ".backup ...".
#   3) Сжимает её (gzip) в smena-YYYY-MM-DD.sql.gz.
#   4) Отправляет копию во внешнее хранилище rclone (BACKUP_REMOTE, FR-A10).
#   5) Удаляет старые копии сверх BACKUP_KEEP (по умолчанию 14) —
#      локально и в удалённом хранилище.
#
# При любой ошибке причина пишется в журнал (syslog: t=smena-backup,
# и /var/log/smena-backup.log) и скрипт завершается с ненулевым кодом.
#
# Запуск из crontab (root):
#   15 3 * * * root /srv/smena/scripts/backup.sh >> /var/log/smena-backup.log 2>&1

set -euo pipefail

# --- Параметры (переопределяются через /etc/smena/env) -----------------------
ENV_FILE="${ENV_FILE:-/etc/smena/env}"
LOCK_FILE="${LOCK_FILE:-/run/lock/smena-backup.lock}"
BACKUP_LOG_FILE="${BACKUP_LOG_FILE:-/var/log/smena-backup.log}"

TMP_DB=""
TMP_GZ=""

# --- Журналирование и обработка ошибок ---------------------------------------
log() {
    local msg="$*"
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$msg" >> "$BACKUP_LOG_FILE" 2>/dev/null || true
    logger -t smena-backup -- "$msg" 2>/dev/null || true
}

fail() {
    local reason="$1"
    local code="${2:-1}"
    log "ERROR: ${reason}"
    exit "$code"
}

on_error() {
    local rc=$?
    local line="$1"
    log "ERROR: backup.sh прерван на строке ${line} (код выхода ${rc})."
}

cleanup() {
    rm -f "$TMP_DB" "$TMP_GZ" 2>/dev/null || true
}

trap cleanup EXIT
trap 'on_error $LINENO' ERR

umask 027

# --- Конфигурация --------------------------------------------------------------
if [ -r "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a; . "$ENV_FILE"; set +a
else
    log "WARNING: файл конфигурации ${ENV_FILE} не найден — использую значения по умолчанию."
fi

DATABASE_PATH="${DATABASE_PATH:-/var/lib/smena/smena.db}"
BACKUP_REMOTE="${BACKUP_REMOTE:-}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
BACKUP_LOCAL_DIR="${BACKUP_LOCAL_DIR:-/var/backups/smena}"
BACKUP_LOG_FILE="${BACKUP_LOG_FILE:-/var/log/smena-backup.log}"

case "$BACKUP_KEEP" in
    ''|*[!0-9]*)
        fail "BACKUP_KEEP=\"${BACKUP_KEEP}\" — должно быть целым числом не меньше 1." ;;
esac
if [ "$BACKUP_KEEP" -lt 1 ]; then
    fail "BACKUP_KEEP=${BACKUP_KEEP} — должно быть не меньше 1 (иначе будут удалены все копии)."
fi

if [ -z "$BACKUP_REMOTE" ]; then
    fail "BACKUP_REMOTE не задан в ${ENV_FILE} — внешнее хранилище (rclone) обязательно по ТЗ (AL-6, FR-A10)."
fi
case "$BACKUP_REMOTE" in
    *:*) : ;;
    *)   fail "BACKUP_REMOTE=\"${BACKUP_REMOTE}\" — неверный формат, ожидается remote:bucket (например smena-s3:smena-backups)." ;;
esac

# --- Проверка окружения ---------------------------------------------------------
for cmd in sqlite3 gzip rclone flock logger; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        fail "Не найдена утилита «${cmd}» — она обязательна для резервного копирования."
    fi
done

if [ ! -f "$DATABASE_PATH" ]; then
    fail "Файл базы данных не найден: ${DATABASE_PATH}"
fi

if [ ! -d "$BACKUP_LOCAL_DIR" ]; then
    mkdir -p -m 0750 "$BACKUP_LOCAL_DIR" 2>/dev/null || fail "Не удалось создать каталог ${BACKUP_LOCAL_DIR}."
fi
if [ ! -w "$BACKUP_LOCAL_DIR" ]; then
    fail "Каталог ${BACKUP_LOCAL_DIR} недоступен для записи."
fi

# --- Защита от параллельного запуска (flock) ------------------------------------
if ! mkdir -p "$(dirname "$LOCK_FILE")" 2>/dev/null; then
    fail "Не удалось создать каталог для lock-файла: $(dirname "$LOCK_FILE")"
fi
if ! : >>"$LOCK_FILE" 2>/dev/null; then
    fail "Не удалось создать lock-файл ${LOCK_FILE}."
fi
# (записедоступность lock-файла проверена выше, поэтому exec здесь не падает)
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    fail "Другой экземпляр backup.sh уже выполняется (lock: ${LOCK_FILE}) — выход без изменений."
fi

# --- Создание конcистентной копии ------------------------------------------------
STAMP="$(date +%F)"
BACKUP_NAME="smena-${STAMP}.sql.gz"
LOCAL_FILE="${BACKUP_LOCAL_DIR}/${BACKUP_NAME}"

log "НАЧАЛО: резервное копирование ${DATABASE_PATH} -> ${LOCAL_FILE} (remote: ${BACKUP_REMOTE})."

if ! TMP_DB="$(mktemp "${BACKUP_LOCAL_DIR}/.smena-backup-db.XXXXXX")"; then
    fail "Не удалось создать временный файл в ${BACKUP_LOCAL_DIR}."
fi
if ! TMP_GZ="$(mktemp "${BACKUP_LOCAL_DIR}/.smena-backup-gz.XXXXXX")"; then
    fail "Не удалось создать временный файл в ${BACKUP_LOCAL_DIR}."
fi

# Именно ".backup", а не cp: копия согласована даже при работающей службе.
if ! sqlite3 "$DATABASE_PATH" ".backup \"$TMP_DB\""; then
    fail "sqlite3 \".backup\" завершился с ошибкой (см. вывод выше)."
fi

if ! gzip -c "$TMP_DB" > "$TMP_GZ"; then
    fail "gzip не смог сжать копию."
fi
if ! gzip -t "$TMP_GZ"; then
    fail "Проверка целостности архива (gzip -t) не пройдена."
fi

SIZE="$(stat -c %s "$TMP_GZ" 2>/dev/null || printf '?')"

if ! mv -f "$TMP_GZ" "$LOCAL_FILE"; then
    fail "Не удалось перенести архив в ${LOCAL_FILE}."
fi
chmod 0640 "$LOCAL_FILE" 2>/dev/null || log "WARNING: не удалось выставить 0640 для ${LOCAL_FILE}."

# --- Отправка во внешнее хранилище (FR-A10) --------------------------------------
if ! rclone copyto "$LOCAL_FILE" "${BACKUP_REMOTE}/${BACKUP_NAME}"; then
    fail "Не удалось отправить копию в ${BACKUP_REMOTE}/${BACKUP_NAME} (rclone copyto)."
fi
log "Копия ${BACKUP_NAME} (${SIZE} байт) отправлена в ${BACKUP_REMOTE}."

# --- Прунинг: удалённые старые копии сверх BACKUP_KEEP -----------------------------
if ! REMOTE_LIST="$(rclone lsf "${BACKUP_REMOTE}/" --include 'smena-*.sql.gz')"; then
    fail "Не удалось получить список копий из ${BACKUP_REMOTE} (rclone lsf)."
fi

OLD_REMOTE="$(printf '%s\n' "$REMOTE_LIST" | sed '/^[[:space:]]*$/d' | sort | head -n -"$BACKUP_KEEP" || true)"
if [ -n "$OLD_REMOTE" ]; then
    while IFS= read -r old_name; do
        [ -n "$old_name" ] || continue
        if ! rclone deletefile "${BACKUP_REMOTE}/${old_name}"; then
            fail "Не удалось удалить старую удалённую копию ${BACKUP_REMOTE}/${old_name}."
        fi
        log "Удалена старая удалённая копия: ${old_name}"
    done <<< "$OLD_REMOTE"
fi

# --- Прунинг: локальные старые копии сверх BACKUP_KEEP ------------------------------
if ! LOCAL_LIST="$(find "$BACKUP_LOCAL_DIR" -maxdepth 1 -type f -name 'smena-*.sql.gz' -printf '%f\n')"; then
    fail "Не удалось получить список локальных копий в ${BACKUP_LOCAL_DIR}."
fi

OLD_LOCAL="$(printf '%s\n' "$LOCAL_LIST" | sed '/^[[:space:]]*$/d' | sort | head -n -"$BACKUP_KEEP" || true)"
if [ -n "$OLD_LOCAL" ]; then
    while IFS= read -r old_name; do
        [ -n "$old_name" ] || continue
        if ! rm -f "${BACKUP_LOCAL_DIR}/${old_name}"; then
            fail "Не удалось удалить старую локальную копию ${BACKUP_LOCAL_DIR}/${old_name}."
        fi
        log "Удалена старая локальная копия: ${old_name}"
    done <<< "$OLD_LOCAL"
fi

log "ГОТОВО: ${BACKUP_NAME} (${SIZE} байт). Хранится по ${BACKUP_KEEP} последних копий локально и в ${BACKUP_REMOTE}."
exit 0
