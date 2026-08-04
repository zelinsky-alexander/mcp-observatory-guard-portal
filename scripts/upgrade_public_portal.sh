#!/usr/bin/env bash
set -Eeuo pipefail

# Upgrade the public read-only portal from the latest selected Git ref.
#
# Deployment assumptions:
#   /opt/mcp-observatory-guard-portal/releases/<release>
#   /opt/mcp-observatory-guard-portal/current -> active release
#   systemd unit: mcp-portal-public.service
#   portal listens on 127.0.0.1:8080
#
# Run as root:
#   sudo bash scripts/upgrade_public_portal.sh
#
# Optional overrides:
#   PORTAL_REF=main
#   PORTAL_KEEP_RELEASES=5
#   PORTAL_HEALTH_URL=http://127.0.0.1:8080/healthz

readonly REPOSITORY_URL="${PORTAL_REPOSITORY_URL:-https://github.com/zelinsky-alexander/mcp-observatory-guard-portal.git}"
readonly DEPLOY_ROOT="${PORTAL_DEPLOY_ROOT:-/opt/mcp-observatory-guard-portal}"
readonly RELEASES_DIR="${DEPLOY_ROOT}/releases"
readonly CURRENT_LINK="${DEPLOY_ROOT}/current"
readonly SERVICE_NAME="${PORTAL_SERVICE_NAME:-mcp-portal-public.service}"
readonly PORTAL_REF="${PORTAL_REF:-main}"
readonly HEALTH_URL="${PORTAL_HEALTH_URL:-http://127.0.0.1:8080/healthz}"
readonly KEEP_RELEASES="${PORTAL_KEEP_RELEASES:-5}"
readonly LOCK_FILE="${PORTAL_UPGRADE_LOCK_FILE:-/run/lock/mcp-portal-upgrade.lock}"
readonly RUNTIME_DIR="${PORTAL_UPGRADE_RUNTIME_DIR:-/run/mcp-portal-upgrade}"
readonly MAINTENANCE_PORT="${PORTAL_PORT:-8080}"
readonly MAINTENANCE_HOST="${PORTAL_HOST:-127.0.0.1}"

old_target=""
new_release=""
maintenance_pid=""
switched=0
upgrade_complete=0

log() {
    printf '%s %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

fail() {
    log "ERROR: $*"
    exit 1
}

require_root() {
    [[ ${EUID} -eq 0 ]] || fail "run this script as root, for example: sudo bash $0"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

stop_maintenance() {
    if [[ -n "${maintenance_pid}" ]] && kill -0 "${maintenance_pid}" 2>/dev/null; then
        kill "${maintenance_pid}" 2>/dev/null || true
        wait "${maintenance_pid}" 2>/dev/null || true
    fi
    maintenance_pid=""
    rm -f "${RUNTIME_DIR}/maintenance.pid"
}

start_maintenance() {
    mkdir -p "${RUNTIME_DIR}"
    chmod 0755 "${RUNTIME_DIR}"

    cat >"${RUNTIME_DIR}/maintenance_server.py" <<'PY'
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os

HOST = os.environ.get("MAINTENANCE_HOST", "127.0.0.1")
PORT = int(os.environ.get("MAINTENANCE_PORT", "8080"))

PAGE = b"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <meta name=\"robots\" content=\"noindex,nofollow,noarchive\">
  <title>Portal upgrade in progress</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #111827; color: #f9fafb; }
    main { width: min(42rem, calc(100% - 2rem)); padding: 2.5rem; border: 1px solid #374151; border-radius: 1rem; background: #1f2937; box-shadow: 0 1rem 3rem rgba(0,0,0,.3); }
    .eyebrow { letter-spacing: .08em; text-transform: uppercase; color: #93c5fd; font-weight: 700; font-size: .8rem; }
    h1 { margin: .5rem 0 1rem; font-size: clamp(1.8rem, 5vw, 3rem); }
    p { line-height: 1.6; color: #d1d5db; }
    .status { display: inline-flex; align-items: center; gap: .6rem; margin-top: 1rem; font-weight: 700; }
    .dot { width: .8rem; height: .8rem; border-radius: 999px; background: #60a5fa; animation: pulse 1.4s infinite; }
    @keyframes pulse { 50% { opacity: .3; transform: scale(.85); } }
  </style>
</head>
<body>
  <main>
    <div class=\"eyebrow\">Scheduled maintenance</div>
    <h1>Public portal upgrade in progress</h1>
    <p>The read-only catalog portal is being upgraded to the latest tested repository revision. No catalog data is being modified by this portal deployment.</p>
    <p>Please retry in a few minutes.</p>
    <div class=\"status\"><span class=\"dot\"></span>Upgrade running</div>
  </main>
</body>
</html>
"""

class Handler(BaseHTTPRequestHandler):
    server_version = "McpPortalMaintenance/1.0"

    def _reply(self, include_body: bool) -> None:
        self.send_response(503)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.send_header("Retry-After", "120")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        if include_body:
            self.wfile.write(PAGE)

    def do_GET(self) -> None:
        self._reply(True)

    def do_HEAD(self) -> None:
        self._reply(False)

    def do_POST(self) -> None:
        self._reply(True)

    def log_message(self, fmt: str, *args: object) -> None:
        return

ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
PY

    chown root:root "${RUNTIME_DIR}/maintenance_server.py"
    chmod 0644 "${RUNTIME_DIR}/maintenance_server.py"

    log "starting temporary maintenance page on ${MAINTENANCE_HOST}:${MAINTENANCE_PORT}"
    runuser -u mcp-portal -- env \
        MAINTENANCE_HOST="${MAINTENANCE_HOST}" \
        MAINTENANCE_PORT="${MAINTENANCE_PORT}" \
        python3 "${RUNTIME_DIR}/maintenance_server.py" \
        >"${RUNTIME_DIR}/maintenance.log" 2>&1 &
    maintenance_pid=$!
    printf '%s\n' "${maintenance_pid}" >"${RUNTIME_DIR}/maintenance.pid"

    for _ in $(seq 1 30); do
        if curl --silent --show-error --output /dev/null \
            --write-out '%{http_code}' \
            "http://${MAINTENANCE_HOST}:${MAINTENANCE_PORT}/" | grep -qx '503'; then
            return 0
        fi
        sleep 0.2
    done

    fail "maintenance page failed to start; see ${RUNTIME_DIR}/maintenance.log"
}

switch_current_link() {
    local target=$1
    local temporary_link="${DEPLOY_ROOT}/.current.$$.tmp"

    ln -s "${target}" "${temporary_link}"
    mv -Tf "${temporary_link}" "${CURRENT_LINK}"
}

wait_for_health() {
    local attempt
    for attempt in $(seq 1 40); do
        if curl --fail --silent --show-error --max-time 3 "${HEALTH_URL}" >/dev/null; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

rollback() {
    local reason=${1:-upgrade failed}

    log "rolling back: ${reason}"
    systemctl stop "${SERVICE_NAME}" >/dev/null 2>&1 || true
    stop_maintenance

    if [[ -n "${old_target}" && -d "${old_target}" ]]; then
        switch_current_link "${old_target}"
        systemctl start "${SERVICE_NAME}" || true
        if wait_for_health; then
            log "rollback restored ${old_target}"
        else
            log "WARNING: rollback target did not become healthy"
        fi
    else
        log "WARNING: no previous release was available for rollback"
    fi
}

cleanup() {
    local status=$?

    stop_maintenance

    if (( status != 0 )) && (( switched == 1 )) && (( upgrade_complete == 0 )); then
        rollback "unexpected command failure"
    fi

    exit "${status}"
}

prune_old_releases() {
    local current_target
    local kept=0
    local path

    [[ "${KEEP_RELEASES}" =~ ^[1-9][0-9]*$ ]] || {
        log "WARNING: invalid PORTAL_KEEP_RELEASES=${KEEP_RELEASES}; skipping release pruning"
        return 0
    }

    current_target=$(readlink -f "${CURRENT_LINK}")

    while IFS= read -r path; do
        [[ -n "${path}" ]] || continue
        if [[ "${path}" == "${current_target}" || "${path}" == "${old_target}" ]]; then
            continue
        fi
        kept=$((kept + 1))
        if (( kept >= KEEP_RELEASES )); then
            log "removing old release ${path}"
            rm -rf --one-file-system "${path}"
        fi
    done < <(
        find "${RELEASES_DIR}" -mindepth 1 -maxdepth 1 -type d \
            -printf '%T@ %p\n' | sort -nr | cut -d' ' -f2-
    )
}

main() {
    local remote_commit
    local short_commit
    local timestamp
    local release_name
    local deployed_commit

    require_root
    for command in git python3 systemctl curl flock runuser find sort cut grep; do
        require_command "${command}"
    done

    mkdir -p "$(dirname "${LOCK_FILE}")" "${RELEASES_DIR}" "${RUNTIME_DIR}"
    exec 9>"${LOCK_FILE}"
    flock -n 9 || fail "another portal upgrade is already running"

    trap cleanup EXIT INT TERM

    systemctl cat "${SERVICE_NAME}" >/dev/null 2>&1 \
        || fail "systemd service not found: ${SERVICE_NAME}"

    if [[ -L "${CURRENT_LINK}" ]]; then
        old_target=$(readlink -f "${CURRENT_LINK}")
    fi

    log "resolving ${REPOSITORY_URL} ref ${PORTAL_REF}"
    remote_commit=$(git ls-remote "${REPOSITORY_URL}" "refs/heads/${PORTAL_REF}" | awk 'NR == 1 {print $1}')
    if [[ -z "${remote_commit}" ]]; then
        remote_commit=$(git ls-remote "${REPOSITORY_URL}" "refs/tags/${PORTAL_REF}^{}" | awk 'NR == 1 {print $1}')
    fi
    if [[ -z "${remote_commit}" ]]; then
        remote_commit=$(git ls-remote "${REPOSITORY_URL}" "refs/tags/${PORTAL_REF}" | awk 'NR == 1 {print $1}')
    fi
    [[ "${remote_commit}" =~ ^[0-9a-f]{40}$ ]] || fail "cannot resolve ref ${PORTAL_REF}"

    short_commit=${remote_commit:0:12}
    timestamp=$(date -u +'%Y%m%dT%H%M%SZ')
    release_name="portal-${timestamp}-${short_commit}"
    new_release="${RELEASES_DIR}/${release_name}"

    log "cloning commit ${remote_commit} into ${new_release}"
    git clone --quiet --no-tags --filter=blob:none "${REPOSITORY_URL}" "${new_release}"
    git -C "${new_release}" checkout --quiet --detach "${remote_commit}"

    deployed_commit=$(git -C "${new_release}" rev-parse HEAD)
    [[ "${deployed_commit}" == "${remote_commit}" ]] \
        || fail "checked-out commit ${deployed_commit} does not match expected ${remote_commit}"

    printf '%s\n' "${deployed_commit}" >"${new_release}/DEPLOYED_COMMIT"
    chmod 0444 "${new_release}/DEPLOYED_COMMIT"

    log "compiling Python sources"
    python3 -m compileall -q "${new_release}/mcp_portal" "${new_release}/tests"

    log "running portal unit tests before downtime"
    (
        cd "${new_release}"
        python3 -m unittest discover -s tests -v
    )

    log "tests passed; entering maintenance mode"
    systemctl stop "${SERVICE_NAME}"
    start_maintenance

    log "activating ${new_release}"
    switch_current_link "${new_release}"
    switched=1

    stop_maintenance
    systemctl start "${SERVICE_NAME}"

    if ! wait_for_health; then
        rollback "new release failed health check at ${HEALTH_URL}"
        fail "upgrade rolled back"
    fi

    upgrade_complete=1
    log "upgrade successful: ${old_target:-none} -> ${new_release}"

    systemctl --no-pager --full status "${SERVICE_NAME}" || true
    prune_old_releases

    log "deployed commit: ${deployed_commit}"
    log "current release: $(readlink -f "${CURRENT_LINK}")"
}

main "$@"
