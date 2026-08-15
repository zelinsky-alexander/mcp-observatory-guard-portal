#!/usr/bin/env bash
set -Eeuo pipefail

# Atomically upgrade Observatory, Native Guard, and the public portal.
# Also preserves and resumes static-analysis backfill, enforces three rolling
# SQLite backups, prunes old releases and journals, and reports disk usage.

readonly OBSERVATORY_REPOSITORY_URL="${OBSERVATORY_REPOSITORY_URL:-https://github.com/zelinsky-alexander/mcp-observatory.git}"
readonly NATIVE_GUARD_REPOSITORY_URL="${NATIVE_GUARD_REPOSITORY_URL:-https://github.com/zelinsky-alexander/mcp-native-guard.git}"
readonly PORTAL_REPOSITORY_URL="${PORTAL_REPOSITORY_URL:-https://github.com/zelinsky-alexander/mcp-observatory-guard-portal.git}"

readonly OBSERVATORY_REF="${OBSERVATORY_REF:-main}"
readonly NATIVE_GUARD_REF="${NATIVE_GUARD_REF:-main}"
readonly PORTAL_REF="${PORTAL_REF:-main}"

readonly OBSERVATORY_ROOT="${OBSERVATORY_DEPLOY_ROOT:-/opt/mcp-observatory}"
readonly NATIVE_GUARD_ROOT="${NATIVE_GUARD_DEPLOY_ROOT:-/opt/mcp-native-guard}"
readonly PORTAL_ROOT="${PORTAL_DEPLOY_ROOT:-/opt/mcp-observatory-guard-portal}"

readonly PORTAL_SERVICE="${PORTAL_SERVICE_NAME:-mcp-portal-public.service}"
readonly STATIC_ANALYSIS_SERVICE="${STATIC_ANALYSIS_SERVICE_NAME:-mcp-observatory-static-analysis.service}"
readonly STATIC_ANALYSIS_TIMER="${STATIC_ANALYSIS_TIMER_NAME:-mcp-observatory-static-analysis.timer}"
readonly REFRESH_SERVICE="${REFRESH_SERVICE_NAME:-mcp-observatory-refresh.service}"

readonly PORTAL_HEALTH_URL="${PORTAL_HEALTH_URL:-http://127.0.0.1:8080/healthz}"
readonly MAINTENANCE_HOST="${PORTAL_HOST:-127.0.0.1}"
readonly MAINTENANCE_PORT="${PORTAL_PORT:-8080}"

readonly BUILD_USER="${BUILD_USER:-ubuntu}"
readonly KEEP_RELEASES="${UPGRADE_KEEP_RELEASES:-3}"
readonly LOCK_FILE="${PUBLIC_UPGRADE_LOCK_FILE:-/run/lock/mcp-public-upgrade.lock}"
readonly RUNTIME_DIR="${PUBLIC_UPGRADE_RUNTIME_DIR:-/run/mcp-public-upgrade}"
readonly OBSERVATORY_SKIP_ANALYZE_TEST="${OBSERVATORY_SKIP_ANALYZE_TEST:-1}"

readonly MCPO_STATE_ROOT="${MCPO_STATE_ROOT:-/var/lib/mcp-observatory}"
readonly MCPO_DATABASE="${MCPO_DATABASE:-${MCPO_STATE_ROOT}/catalog/local-registry.sqlite}"
readonly STATIC_ANALYSIS_ENV="${STATIC_ANALYSIS_ENV_FILE:-/etc/mcp-observatory/static-analysis.env}"
readonly STATIC_ANALYSIS_TMP_ROOT_DEFAULT="${MCPO_STATIC_ANALYSIS_TMP_ROOT:-${MCPO_STATE_ROOT}/tmp}"
readonly BACKUP_DIRECTORY="${MCPO_BACKUP_DIRECTORY:-${MCPO_STATE_ROOT}/registry-refresh/backups}"
readonly BACKUP_RETENTION_COUNT="${MCPO_BACKUP_RETENTION_COUNT:-3}"

readonly SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
readonly STATIC_TIMER_DROPIN_DIR="${SYSTEMD_DIR}/${STATIC_ANALYSIS_TIMER}.d"
readonly STATIC_TIMER_DROPIN="${STATIC_TIMER_DROPIN_DIR}/continuous-backfill.conf"
readonly REFRESH_DROPIN_DIR="${SYSTEMD_DIR}/${REFRESH_SERVICE}.d"
readonly REFRESH_RETENTION_DROPIN="${REFRESH_DROPIN_DIR}/backup-retention.conf"

readonly JOURNAL_RETENTION="${UPGRADE_JOURNAL_RETENTION:-14d}"
readonly JOURNAL_MAX_SIZE="${UPGRADE_JOURNAL_MAX_SIZE:-128M}"
readonly DISK_BUDGET_BYTES="${UPGRADE_DISK_BUDGET_BYTES:-3221225472}"
readonly FAIL_ON_DISK_BUDGET="${UPGRADE_FAIL_ON_DISK_BUDGET:-0}"

old_observatory=""
old_native_guard=""
old_portal=""
new_observatory=""
new_native_guard=""
new_portal=""
maintenance_pid=""
switched=0
upgrade_complete=0
static_scheduler_paused=0

static_batch_size=""
static_maximum_run_seconds=""
static_child_timeout_seconds=""
static_tmp_root=""

log() { printf '%s %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*" >&2; }
warn() { log "WARNING: $*"; }
fail() { log "ERROR: $*"; exit 1; }

require_root() { [[ ${EUID} -eq 0 ]] || fail "run as root: sudo bash $0"; }
require_command() { command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"; }
require_user() { id "$1" >/dev/null 2>&1 || fail "required user does not exist: $1"; }
validate_positive_integer() { [[ "$2" =~ ^[1-9][0-9]*$ ]] || fail "$1 must be a positive integer: $2"; }
validate_nonnegative_integer() { [[ "$2" =~ ^[0-9]+$ ]] || fail "$1 must be a non-negative integer: $2"; }

read_env_value() {
    local file=$1 key=$2 fallback=$3 value=""
    if [[ -r "${file}" ]]; then
        value=$(awk -F= -v wanted="${key}" '$1 == wanted {sub(/^[^=]*=/, ""); print; exit}' "${file}")
    fi
    printf '%s\n' "${value:-${fallback}}"
}

capture_static_analysis_settings() {
    static_batch_size="${MCPO_STATIC_ANALYSIS_BATCH_SIZE:-$(read_env_value "${STATIC_ANALYSIS_ENV}" MCPO_STATIC_ANALYSIS_BATCH_SIZE 1000)}"
    static_maximum_run_seconds="${MCPO_STATIC_ANALYSIS_MAXIMUM_RUN_SECONDS:-$(read_env_value "${STATIC_ANALYSIS_ENV}" MCPO_STATIC_ANALYSIS_MAXIMUM_RUN_SECONDS 3000)}"
    static_child_timeout_seconds="${MCPO_STATIC_ANALYSIS_CHILD_TIMEOUT_SECONDS:-$(read_env_value "${STATIC_ANALYSIS_ENV}" MCPO_STATIC_ANALYSIS_CHILD_TIMEOUT_SECONDS 300)}"
    static_tmp_root="${MCPO_STATIC_ANALYSIS_TMP_ROOT:-$(read_env_value "${STATIC_ANALYSIS_ENV}" TMPDIR "${STATIC_ANALYSIS_TMP_ROOT_DEFAULT}")}"

    validate_positive_integer MCPO_STATIC_ANALYSIS_BATCH_SIZE "${static_batch_size}"
    validate_positive_integer MCPO_STATIC_ANALYSIS_MAXIMUM_RUN_SECONDS "${static_maximum_run_seconds}"
    validate_positive_integer MCPO_STATIC_ANALYSIS_CHILD_TIMEOUT_SECONDS "${static_child_timeout_seconds}"
    [[ "${static_tmp_root}" == /* ]] || fail "static-analysis temporary directory must be absolute"

    log "preserving static-analysis settings: batch=${static_batch_size}, max_run=${static_maximum_run_seconds}, child_timeout=${static_child_timeout_seconds}, tmp=${static_tmp_root}"
}

resolve_commit() {
    local repository_url=$1 ref=$2 commit
    commit=$(git ls-remote "${repository_url}" "refs/heads/${ref}" | awk 'NR == 1 {print $1}')
    [[ -n "${commit}" ]] || commit=$(git ls-remote "${repository_url}" "refs/tags/${ref}^{}" | awk 'NR == 1 {print $1}')
    [[ -n "${commit}" ]] || commit=$(git ls-remote "${repository_url}" "refs/tags/${ref}" | awk 'NR == 1 {print $1}')
    [[ "${commit}" =~ ^[0-9a-f]{40}$ ]] || return 1
    printf '%s\n' "${commit}"
}

clone_release() {
    local name=$1 repository_url=$2 ref=$3 deploy_root=$4
    local commit short_commit timestamp release_dir checked_out
    mkdir -p "${deploy_root}/releases"
    log "resolving ${name} ref ${ref}"
    commit=$(resolve_commit "${repository_url}" "${ref}") || fail "cannot resolve ${name} ref ${ref}"
    short_commit=${commit:0:12}
    timestamp=$(date -u +'%Y%m%dT%H%M%SZ')
    release_dir="${deploy_root}/releases/${name}-${timestamp}-${short_commit}"
    log "cloning ${name} commit ${commit} into ${release_dir}"
    git clone --quiet --no-tags --filter=blob:none "${repository_url}" "${release_dir}"
    git -C "${release_dir}" checkout --quiet --detach "${commit}"
    checked_out=$(git -C "${release_dir}" rev-parse HEAD)
    [[ "${checked_out}" == "${commit}" ]] || fail "${name} checkout mismatch"
    printf '%s\n' "${checked_out}" >"${release_dir}/DEPLOYED_COMMIT"
    chmod 0444 "${release_dir}/DEPLOYED_COMMIT"
    printf '%s\n' "${release_dir}"
}

prepare_build_tree() { chown -R "${BUILD_USER}:${BUILD_USER}" "$1"; }
seal_release_tree() { chown -R root:root "$1"; chmod -R go-w "$1"; }

run_as_build_user() {
    local working_directory=$1
    shift
    runuser -u "${BUILD_USER}" -- env HOME="$(getent passwd "${BUILD_USER}" | cut -d: -f6)" \
        bash -c 'set -Eeuo pipefail; cd "$1"; shift; exec "$@"' bash "${working_directory}" "$@"
}

build_observatory() {
    prepare_build_tree "${new_observatory}"
    run_as_build_user "${new_observatory}" cmake --preset release
    run_as_build_user "${new_observatory}" cmake --build --preset release --parallel 2
    run_as_build_user "${new_observatory}" cmake --preset dev-debug
    run_as_build_user "${new_observatory}" cmake --build --preset dev-debug --parallel 2
    if [[ "${OBSERVATORY_SKIP_ANALYZE_TEST}" == 1 ]] && ! command -v docker >/dev/null 2>&1; then
        run_as_build_user "${new_observatory}" ctest --preset dev-debug --output-on-failure -E '^mcpo_analyze_cli_tests$'
    else
        run_as_build_user "${new_observatory}" ctest --preset dev-debug --output-on-failure
    fi
    seal_release_tree "${new_observatory}"
}

build_native_guard() {
    prepare_build_tree "${new_native_guard}"
    run_as_build_user "${new_native_guard}" cmake --preset release
    run_as_build_user "${new_native_guard}" cmake --build --preset release --parallel 2
    run_as_build_user "${new_native_guard}" cmake --preset dev-debug
    run_as_build_user "${new_native_guard}" cmake --build --preset dev-debug --parallel 2
    run_as_build_user "${new_native_guard}" ctest --preset dev-debug --output-on-failure
    seal_release_tree "${new_native_guard}"
}

build_portal() {
    prepare_build_tree "${new_portal}"
    run_as_build_user "${new_portal}" python3 -m compileall -q mcp_portal tests
    run_as_build_user "${new_portal}" python3 -m unittest discover -s tests -v
    seal_release_tree "${new_portal}"
}

switch_current_link() {
    local deploy_root=$1 target=$2 temporary_link="${deploy_root}/.current.$$.tmp"
    rm -f "${temporary_link}"
    ln -s "${target}" "${temporary_link}"
    mv -Tf "${temporary_link}" "${deploy_root}/current"
}

start_maintenance() {
    mkdir -p "${RUNTIME_DIR}"
    chmod 0755 "${RUNTIME_DIR}"
    cat >"${RUNTIME_DIR}/maintenance_server.py" <<'PY'
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
HOST = os.environ.get("MAINTENANCE_HOST", "127.0.0.1")
PORT = int(os.environ.get("MAINTENANCE_PORT", "8080"))
PAGE = b"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><meta name=robots content='noindex,nofollow,noarchive'><title>Upgrade in progress</title><style>:root{color-scheme:dark;font-family:system-ui,sans-serif}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#111827;color:#f9fafb}main{width:min(42rem,calc(100% - 2rem));padding:2.5rem;border:1px solid #374151;border-radius:1rem;background:#1f2937}p{line-height:1.6;color:#d1d5db}.status{font-weight:700;color:#93c5fd}</style><main><p class=status>Scheduled maintenance</p><h1>Assurance platform upgrade in progress</h1><p>The catalog, assurance engine, and public read-only portal are being upgraded.</p></main>"""
class Handler(BaseHTTPRequestHandler):
    server_version = "McpPlatformMaintenance/1.0"
    def reply(self, body):
        self.send_response(503)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.send_header("Retry-After", "120")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body: self.wfile.write(PAGE)
    def do_GET(self): self.reply(True)
    def do_HEAD(self): self.reply(False)
    def do_POST(self): self.reply(True)
    def log_message(self, fmt, *args): return
ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
PY
    chmod 0644 "${RUNTIME_DIR}/maintenance_server.py"
    runuser -u mcp-portal -- env MAINTENANCE_HOST="${MAINTENANCE_HOST}" MAINTENANCE_PORT="${MAINTENANCE_PORT}" \
        python3 "${RUNTIME_DIR}/maintenance_server.py" >"${RUNTIME_DIR}/maintenance.log" 2>&1 &
    maintenance_pid=$!
    printf '%s\n' "${maintenance_pid}" >"${RUNTIME_DIR}/maintenance.pid"
    for _ in $(seq 1 30); do
        [[ "$(curl -sS -o /dev/null -w '%{http_code}' "http://${MAINTENANCE_HOST}:${MAINTENANCE_PORT}/" || true)" == 503 ]] && return 0
        sleep 0.2
    done
    fail "maintenance page failed to start"
}

stop_maintenance() {
    if [[ -n "${maintenance_pid}" ]] && kill -0 "${maintenance_pid}" 2>/dev/null; then
        kill "${maintenance_pid}" 2>/dev/null || true
        wait "${maintenance_pid}" 2>/dev/null || true
    fi
    maintenance_pid=""
    rm -f "${RUNTIME_DIR}/maintenance.pid"
}

wait_for_health() {
    for _ in $(seq 1 40); do
        curl --fail --silent --show-error --max-time 3 "${PORTAL_HEALTH_URL}" >/dev/null && return 0
        sleep 0.5
    done
    return 1
}

pause_static_analysis() {
    log "pausing static-analysis timer and worker"
    systemctl stop "${STATIC_ANALYSIS_TIMER}" >/dev/null 2>&1 || true
    systemctl stop "${STATIC_ANALYSIS_SERVICE}" >/dev/null 2>&1 || true
    static_scheduler_paused=1
}

write_continuous_backfill_dropin() {
    install -d -m 0755 "${STATIC_TIMER_DROPIN_DIR}"
    cat >"${STATIC_TIMER_DROPIN}" <<'EOF_TIMER'
[Timer]
OnBootSec=
OnCalendar=
OnUnitInactiveSec=
OnBootSec=2min
OnUnitInactiveSec=1min
RandomizedDelaySec=0
AccuracySec=10s
Persistent=no
EOF_TIMER
    chmod 0644 "${STATIC_TIMER_DROPIN}"
}

write_backup_retention_dropin() {
    install -d -m 0755 "${REFRESH_DROPIN_DIR}"
    cat >"${REFRESH_RETENTION_DROPIN}" <<EOF_RETENTION
[Service]
Environment=MCPO_BACKUP_RETENTION_COUNT=${BACKUP_RETENTION_COUNT}
EOF_RETENTION
    chmod 0644 "${REFRESH_RETENTION_DROPIN}"
}

install_static_analysis_scheduler_from() {
    local release_dir=$1 installer="${release_dir}/scripts/install_static_analysis_scheduler.sh"
    [[ -f "${installer}" ]] || fail "static-analysis installer missing: ${installer}"
    env MCPO_INSTALL_ROOT="${release_dir}" MCPO_STATE_ROOT="${MCPO_STATE_ROOT}" MCPO_DATABASE="${MCPO_DATABASE}" \
        MCPO_STATIC_ANALYSIS_BATCH_SIZE="${static_batch_size}" \
        MCPO_STATIC_ANALYSIS_MAXIMUM_RUN_SECONDS="${static_maximum_run_seconds}" \
        MCPO_STATIC_ANALYSIS_CHILD_TIMEOUT_SECONDS="${static_child_timeout_seconds}" \
        MCPO_STATIC_ANALYSIS_TMP_ROOT="${static_tmp_root}" \
        bash "${installer}" --accept-docker-root-equivalent --no-start
    write_continuous_backfill_dropin
    write_backup_retention_dropin
    systemctl daemon-reload
    systemd-analyze verify "${STATIC_ANALYSIS_TIMER}" "${STATIC_ANALYSIS_SERVICE}" "${REFRESH_SERVICE}"
}

resume_static_analysis() {
    systemctl enable "${STATIC_ANALYSIS_TIMER}" >/dev/null
    systemctl restart "${STATIC_ANALYSIS_TIMER}"
    systemctl start --no-block "${STATIC_ANALYSIS_SERVICE}"
    sleep 1
    systemctl is-failed --quiet "${STATIC_ANALYSIS_TIMER}" && fail "static-analysis timer failed"
    systemctl is-failed --quiet "${STATIC_ANALYSIS_SERVICE}" && fail "static-analysis service failed"
    static_scheduler_paused=0
}

restore_old_scheduler_best_effort() {
    [[ -n "${old_observatory}" && -d "${old_observatory}" ]] || return 0
    local installer="${old_observatory}/scripts/install_static_analysis_scheduler.sh"
    if [[ -f "${installer}" ]]; then
        env MCPO_INSTALL_ROOT="${old_observatory}" MCPO_STATE_ROOT="${MCPO_STATE_ROOT}" MCPO_DATABASE="${MCPO_DATABASE}" \
            MCPO_STATIC_ANALYSIS_BATCH_SIZE="${static_batch_size}" \
            MCPO_STATIC_ANALYSIS_MAXIMUM_RUN_SECONDS="${static_maximum_run_seconds}" \
            MCPO_STATIC_ANALYSIS_CHILD_TIMEOUT_SECONDS="${static_child_timeout_seconds}" \
            MCPO_STATIC_ANALYSIS_TMP_ROOT="${static_tmp_root}" \
            bash "${installer}" --accept-docker-root-equivalent --no-start || true
    fi
    write_continuous_backfill_dropin
    write_backup_retention_dropin
    systemctl daemon-reload || true
    systemctl enable "${STATIC_ANALYSIS_TIMER}" >/dev/null 2>&1 || true
    systemctl restart "${STATIC_ANALYSIS_TIMER}" >/dev/null 2>&1 || true
    systemctl start --no-block "${STATIC_ANALYSIS_SERVICE}" >/dev/null 2>&1 || true
    static_scheduler_paused=0
}

rollback() {
    local reason=${1:-upgrade failed}
    log "rolling back: ${reason}"
    systemctl stop "${STATIC_ANALYSIS_TIMER}" >/dev/null 2>&1 || true
    systemctl stop "${STATIC_ANALYSIS_SERVICE}" >/dev/null 2>&1 || true
    systemctl stop "${PORTAL_SERVICE}" >/dev/null 2>&1 || true
    stop_maintenance
    [[ -n "${old_observatory}" && -d "${old_observatory}" ]] && switch_current_link "${OBSERVATORY_ROOT}" "${old_observatory}"
    [[ -n "${old_native_guard}" && -d "${old_native_guard}" ]] && switch_current_link "${NATIVE_GUARD_ROOT}" "${old_native_guard}"
    [[ -n "${old_portal}" && -d "${old_portal}" ]] && switch_current_link "${PORTAL_ROOT}" "${old_portal}"
    restore_old_scheduler_best_effort
    systemctl start "${PORTAL_SERVICE}" || true
    wait_for_health && log "rollback restored previous deployment" || warn "rollback portal is not healthy"
}

cleanup() {
    local status=$?
    stop_maintenance
    if (( status != 0 )) && (( switched == 1 )) && (( upgrade_complete == 0 )); then
        rollback "unexpected command failure"
    elif (( status != 0 )) && (( static_scheduler_paused == 1 )); then
        restore_old_scheduler_best_effort
    fi
    exit "${status}"
}

prune_releases() {
    local root=$1 previous=$2 current path retained=0
    current=$(readlink -f "${root}/current" 2>/dev/null || true)
    while IFS= read -r path; do
        [[ -n "${path}" ]] || continue
        [[ "${path}" == "${current}" || "${path}" == "${previous}" ]] && continue
        retained=$((retained + 1))
        if (( retained >= KEEP_RELEASES )); then
            log "removing old release ${path}"
            rm -rf --one-file-system "${path}"
        fi
    done < <(find "${root}/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -nr | cut -d' ' -f2-)
}

prune_refresh_backups() {
    [[ -d "${BACKUP_DIRECTORY}" ]] || { warn "backup directory missing: ${BACKUP_DIRECTORY}"; return 0; }
    local -a backups=()
    local count remove_count index backup
    mapfile -t backups < <(find "${BACKUP_DIRECTORY}" -maxdepth 1 -type f -name 'local-registry.sqlite.*.sqlite' -printf '%f\n' | sort)
    count=${#backups[@]}
    remove_count=$((count - BACKUP_RETENTION_COUNT))
    (( remove_count > 0 )) || { log "backup retention already satisfied: ${count} dated backups"; return 0; }
    for ((index=0; index<remove_count; ++index)); do
        backup="${BACKUP_DIRECTORY}/${backups[index]}"
        rm -f -- "${backup}.json" "${backup}"
        log "removed backup ${backup}"
    done
    sync -f "${BACKUP_DIRECTORY}" 2>/dev/null || true
}

vacuum_journal() {
    journalctl --vacuum-time="${JOURNAL_RETENTION}" --vacuum-size="${JOURNAL_MAX_SIZE}" >/dev/null || warn "journal vacuum failed"
}

path_bytes() { [[ -e "$1" ]] && du -sb -- "$1" | awk '{print $1}' || printf '0\n'; }
human_bytes() { numfmt --to=iec-i --suffix=B "$1"; }

report_disk_usage() {
    local state observatory guard portal nginx journal total
    state=$(path_bytes "${MCPO_STATE_ROOT}")
    observatory=$(path_bytes "${OBSERVATORY_ROOT}/releases")
    guard=$(path_bytes "${NATIVE_GUARD_ROOT}/releases")
    portal=$(path_bytes "${PORTAL_ROOT}/releases")
    nginx=$(path_bytes /var/log/nginx)
    journal=$(( $(path_bytes /var/log/journal) + $(path_bytes /run/log/journal) ))
    total=$((state + observatory + guard + portal + nginx + journal))
    log "disk usage: state=$(human_bytes "${state}"), observatory=$(human_bytes "${observatory}"), guard=$(human_bytes "${guard}"), portal=$(human_bytes "${portal}"), nginx=$(human_bytes "${nginx}"), journal=$(human_bytes "${journal}"), total=$(human_bytes "${total}"), budget=$(human_bytes "${DISK_BUDGET_BYTES}")"
    if (( total > DISK_BUDGET_BYTES )); then
        [[ "${FAIL_ON_DISK_BUDGET}" == 1 ]] && fail "deployment exceeds disk budget"
        warn "deployment exceeds disk budget; evidence is not deleted automatically"
    fi
}

verify_post_upgrade() {
    systemctl is-active --quiet "${PORTAL_SERVICE}" || fail "portal is not active"
    systemctl is-enabled --quiet "${STATIC_ANALYSIS_TIMER}" || fail "static-analysis timer is not enabled"
    systemctl is-active --quiet "${STATIC_ANALYSIS_TIMER}" || fail "static-analysis timer is not active"
    grep -q "^MCPO_STATIC_ANALYSIS_BATCH_SIZE=${static_batch_size}$" "${STATIC_ANALYSIS_ENV}" || fail "batch size not preserved"
    grep -q "^MCPO_STATIC_ANALYSIS_MAXIMUM_RUN_SECONDS=${static_maximum_run_seconds}$" "${STATIC_ANALYSIS_ENV}" || fail "maximum runtime not preserved"
    grep -q "^TMPDIR=${static_tmp_root}$" "${STATIC_ANALYSIS_ENV}" || fail "TMPDIR not preserved"
    grep -q "^Environment=MCPO_BACKUP_RETENTION_COUNT=${BACKUP_RETENTION_COUNT}$" "${REFRESH_RETENTION_DROPIN}" || fail "backup retention drop-in incorrect"
    systemctl cat "${STATIC_ANALYSIS_TIMER}" | grep -qF "${STATIC_TIMER_DROPIN}" || fail "continuous timer drop-in not loaded"
    systemctl is-failed --quiet "${STATIC_ANALYSIS_SERVICE}" && fail "static-analysis service failed"
}

main() {
    require_root
    require_user "${BUILD_USER}"
    require_user mcp-portal
    require_user mcp-refresh
    for command in git cmake ctest ninja python3 systemctl systemd-analyze curl flock runuser find sort cut awk getent id chmod chown grep install journalctl du numfmt sync sqlite3 docker; do
        require_command "${command}"
    done
    validate_positive_integer UPGRADE_KEEP_RELEASES "${KEEP_RELEASES}"
    validate_nonnegative_integer MCPO_BACKUP_RETENTION_COUNT "${BACKUP_RETENTION_COUNT}"
    validate_positive_integer UPGRADE_DISK_BUDGET_BYTES "${DISK_BUDGET_BYTES}"
    capture_static_analysis_settings

    mkdir -p "$(dirname "${LOCK_FILE}")" "${RUNTIME_DIR}"
    exec 9>"${LOCK_FILE}"
    flock -n 9 || fail "another public platform upgrade is already running"
    trap cleanup EXIT INT TERM

    systemctl cat "${PORTAL_SERVICE}" >/dev/null 2>&1 || fail "missing service: ${PORTAL_SERVICE}"
    systemctl cat "${STATIC_ANALYSIS_SERVICE}" >/dev/null 2>&1 || fail "missing service: ${STATIC_ANALYSIS_SERVICE}"
    systemctl cat "${STATIC_ANALYSIS_TIMER}" >/dev/null 2>&1 || fail "missing timer: ${STATIC_ANALYSIS_TIMER}"

    old_observatory=$(readlink -f "${OBSERVATORY_ROOT}/current" 2>/dev/null || true)
    old_native_guard=$(readlink -f "${NATIVE_GUARD_ROOT}/current" 2>/dev/null || true)
    old_portal=$(readlink -f "${PORTAL_ROOT}/current" 2>/dev/null || true)

    new_observatory=$(clone_release observatory "${OBSERVATORY_REPOSITORY_URL}" "${OBSERVATORY_REF}" "${OBSERVATORY_ROOT}")
    new_native_guard=$(clone_release native-guard "${NATIVE_GUARD_REPOSITORY_URL}" "${NATIVE_GUARD_REF}" "${NATIVE_GUARD_ROOT}")
    new_portal=$(clone_release portal "${PORTAL_REPOSITORY_URL}" "${PORTAL_REF}" "${PORTAL_ROOT}")

    build_observatory
    build_native_guard
    build_portal

    pause_static_analysis
    systemctl stop "${PORTAL_SERVICE}"
    start_maintenance

    switch_current_link "${OBSERVATORY_ROOT}" "${new_observatory}"
    switch_current_link "${NATIVE_GUARD_ROOT}" "${new_native_guard}"
    switch_current_link "${PORTAL_ROOT}" "${new_portal}"
    switched=1

    install_static_analysis_scheduler_from "${new_observatory}"

    stop_maintenance
    systemctl start "${PORTAL_SERVICE}"
    if ! wait_for_health; then
        rollback "new portal failed health check"
        fail "upgrade rolled back"
    fi

    resume_static_analysis
    verify_post_upgrade
    upgrade_complete=1

    log "upgrade successful"
    log "mcp-observatory: $(readlink -f "${OBSERVATORY_ROOT}/current")"
    log "mcp-native-guard: $(readlink -f "${NATIVE_GUARD_ROOT}/current")"
    log "portal: $(readlink -f "${PORTAL_ROOT}/current")"

    prune_releases "${OBSERVATORY_ROOT}" "${old_observatory}"
    prune_releases "${NATIVE_GUARD_ROOT}" "${old_native_guard}"
    prune_releases "${PORTAL_ROOT}" "${old_portal}"
    prune_refresh_backups
    vacuum_journal
    report_disk_usage

    systemctl --no-pager --full status "${PORTAL_SERVICE}" || true
    systemctl --no-pager --full status "${STATIC_ANALYSIS_TIMER}" || true
    systemctl --no-pager --full status "${STATIC_ANALYSIS_SERVICE}" || true
}

main "$@"
