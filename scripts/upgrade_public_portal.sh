#!/usr/bin/env bash
set -Eeuo pipefail

# Atomically upgrade:
#   1. mcp-observatory
#   2. mcp-native-guard
#   3. mcp-observatory-guard-portal
#
# Builds and tests run as an unprivileged user so permission-sensitive tests
# behave the same way they do in CI. Root is used only for cloning into /opt,
# ownership changes, symlink activation, service control, rollback, and cleanup.
#
# Run:
#   sudo bash upgrade_public_portal.sh
#
# Optional overrides:
#   BUILD_USER=ubuntu
#   OBSERVATORY_REF=main
#   NATIVE_GUARD_REF=main
#   PORTAL_REF=main
#   UPGRADE_KEEP_RELEASES=5
#   PORTAL_HEALTH_URL=http://127.0.0.1:8080/healthz
#   OBSERVATORY_SKIP_ANALYZE_TEST=1

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
readonly PORTAL_HEALTH_URL="${PORTAL_HEALTH_URL:-http://127.0.0.1:8080/healthz}"
readonly MAINTENANCE_HOST="${PORTAL_HOST:-127.0.0.1}"
readonly MAINTENANCE_PORT="${PORTAL_PORT:-8080}"

readonly BUILD_USER="${BUILD_USER:-ubuntu}"
readonly KEEP_RELEASES="${UPGRADE_KEEP_RELEASES:-5}"
readonly LOCK_FILE="${PUBLIC_UPGRADE_LOCK_FILE:-/run/lock/mcp-public-upgrade.lock}"
readonly RUNTIME_DIR="${PUBLIC_UPGRADE_RUNTIME_DIR:-/run/mcp-public-upgrade}"
readonly OBSERVATORY_SKIP_ANALYZE_TEST="${OBSERVATORY_SKIP_ANALYZE_TEST:-1}"

old_observatory=""
old_native_guard=""
old_portal=""

new_observatory=""
new_native_guard=""
new_portal=""

maintenance_pid=""
switched=0
upgrade_complete=0

log() {
    printf '%s %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*" >&2
}

fail() {
    log "ERROR: $*"
    exit 1
}

require_root() {
    [[ ${EUID} -eq 0 ]] || fail "run as root: sudo bash $0"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

require_user() {
    id "$1" >/dev/null 2>&1 || fail "required user does not exist: $1"
}

resolve_commit() {
    local repository_url=$1
    local ref=$2
    local commit

    commit=$(git ls-remote "${repository_url}" "refs/heads/${ref}" | awk 'NR == 1 {print $1}')
    [[ -n "${commit}" ]] || commit=$(git ls-remote "${repository_url}" "refs/tags/${ref}^{}" | awk 'NR == 1 {print $1}')
    [[ -n "${commit}" ]] || commit=$(git ls-remote "${repository_url}" "refs/tags/${ref}" | awk 'NR == 1 {print $1}')

    [[ "${commit}" =~ ^[0-9a-f]{40}$ ]] || return 1
    printf '%s\n' "${commit}"
}

clone_release() {
    local name=$1
    local repository_url=$2
    local ref=$3
    local deploy_root=$4
    local commit short_commit timestamp release_dir checked_out

    mkdir -p "${deploy_root}/releases"

    log "resolving ${name} ref ${ref}"
    commit=$(resolve_commit "${repository_url}" "${ref}") ||
        fail "cannot resolve ${name} ref ${ref}"

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

prepare_build_tree() {
    local release_dir=$1

    log "assigning build tree to unprivileged user ${BUILD_USER}: ${release_dir}"
    chown -R "${BUILD_USER}:${BUILD_USER}" "${release_dir}"
}

seal_release_tree() {
    local release_dir=$1

    log "sealing immutable release tree: ${release_dir}"
    chown -R root:root "${release_dir}"
    chmod -R go-w "${release_dir}"
}

run_as_build_user() {
    local working_directory=$1
    shift

    runuser -u "${BUILD_USER}" -- \
        env HOME="$(getent passwd "${BUILD_USER}" | cut -d: -f6)" \
        bash -c '
            set -Eeuo pipefail
            cd "$1"
            shift
            exec "$@"
        ' bash "${working_directory}" "$@"
}

build_observatory() {
    prepare_build_tree "${new_observatory}"

    log "configuring mcp-observatory release build"
    run_as_build_user "${new_observatory}" cmake --preset release

    log "building mcp-observatory release build"
    run_as_build_user "${new_observatory}" cmake --build --preset release --parallel 2

    log "configuring mcp-observatory debug build"
    run_as_build_user "${new_observatory}" cmake --preset dev-debug

    log "building mcp-observatory debug build"
    run_as_build_user "${new_observatory}" cmake --build --preset dev-debug --parallel 2

    if [[ "${OBSERVATORY_SKIP_ANALYZE_TEST}" == "1" ]] &&
       ! command -v docker >/dev/null 2>&1; then
        log "running Observatory tests except Docker-dependent analyze CLI suite"
        run_as_build_user "${new_observatory}" \
            ctest --preset dev-debug --output-on-failure \
            -E '^mcpo_analyze_cli_tests$'
    else
        log "running complete Observatory test suite"
        run_as_build_user "${new_observatory}" \
            ctest --preset dev-debug --output-on-failure
    fi

    seal_release_tree "${new_observatory}"
}

build_native_guard() {
    prepare_build_tree "${new_native_guard}"

    log "configuring mcp-native-guard release build"
    run_as_build_user "${new_native_guard}" cmake --preset release

    log "building mcp-native-guard release build"
    run_as_build_user "${new_native_guard}" cmake --build --preset release --parallel 2

    log "configuring mcp-native-guard debug build"
    run_as_build_user "${new_native_guard}" cmake --preset dev-debug

    log "building mcp-native-guard debug build"
    run_as_build_user "${new_native_guard}" cmake --build --preset dev-debug --parallel 2

    log "running complete mcp-native-guard test suite as ${BUILD_USER}"
    run_as_build_user "${new_native_guard}" \
        ctest --preset dev-debug --output-on-failure

    seal_release_tree "${new_native_guard}"
}

build_portal() {
    prepare_build_tree "${new_portal}"

    log "compiling portal Python sources as ${BUILD_USER}"
    run_as_build_user "${new_portal}" \
        python3 -m compileall -q mcp_portal tests

    log "running portal tests as ${BUILD_USER}"
    run_as_build_user "${new_portal}" \
        python3 -m unittest discover -s tests -v

    seal_release_tree "${new_portal}"
}

switch_current_link() {
    local deploy_root=$1
    local target=$2
    local temporary_link="${deploy_root}/.current.$$.tmp"

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

PAGE = b"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive">
<title>Upgrade in progress</title>
<style>
:root { color-scheme: dark; font-family: system-ui, sans-serif; }
body {
  margin: 0; min-height: 100vh; display: grid; place-items: center;
  background: #111827; color: #f9fafb;
}
main {
  width: min(42rem, calc(100% - 2rem)); padding: 2.5rem;
  border: 1px solid #374151; border-radius: 1rem; background: #1f2937;
}
h1 { font-size: clamp(1.8rem, 5vw, 3rem); }
p { line-height: 1.6; color: #d1d5db; }
.status { font-weight: 700; color: #93c5fd; }
</style>
</head>
<body>
<main>
<p class="status">Scheduled maintenance</p>
<h1>Assurance platform upgrade in progress</h1>
<p>The catalog, assurance engine, and public read-only portal are being upgraded to their latest tested revisions.</p>
<p>No published catalog data is being modified by this deployment. Please retry in a few minutes.</p>
</main>
</body>
</html>"""

class Handler(BaseHTTPRequestHandler):
    server_version = "McpPlatformMaintenance/1.0"

    def reply(self, include_body: bool) -> None:
        self.send_response(503)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.send_header("Retry-After", "120")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        if include_body:
            self.wfile.write(PAGE)

    def do_GET(self) -> None:
        self.reply(True)

    def do_HEAD(self) -> None:
        self.reply(False)

    def do_POST(self) -> None:
        self.reply(True)

    def log_message(self, fmt: str, *args: object) -> None:
        return

ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
PY

    chmod 0644 "${RUNTIME_DIR}/maintenance_server.py"

    log "starting maintenance page on ${MAINTENANCE_HOST}:${MAINTENANCE_PORT}"
    runuser -u mcp-portal -- \
        env \
        MAINTENANCE_HOST="${MAINTENANCE_HOST}" \
        MAINTENANCE_PORT="${MAINTENANCE_PORT}" \
        python3 "${RUNTIME_DIR}/maintenance_server.py" \
        >"${RUNTIME_DIR}/maintenance.log" 2>&1 &

    maintenance_pid=$!
    printf '%s\n' "${maintenance_pid}" >"${RUNTIME_DIR}/maintenance.pid"

    for _ in $(seq 1 30); do
        if [[ "$(curl -sS -o /dev/null -w '%{http_code}' \
            "http://${MAINTENANCE_HOST}:${MAINTENANCE_PORT}/" || true)" == "503" ]]; then
            return 0
        fi
        sleep 0.2
    done

    fail "maintenance page failed to start; see ${RUNTIME_DIR}/maintenance.log"
}

stop_maintenance() {
    if [[ -n "${maintenance_pid}" ]] &&
       kill -0 "${maintenance_pid}" 2>/dev/null; then
        kill "${maintenance_pid}" 2>/dev/null || true
        wait "${maintenance_pid}" 2>/dev/null || true
    fi

    maintenance_pid=""
    rm -f "${RUNTIME_DIR}/maintenance.pid"
}

wait_for_health() {
    for _ in $(seq 1 40); do
        if curl --fail --silent --show-error --max-time 3 \
            "${PORTAL_HEALTH_URL}" >/dev/null; then
            return 0
        fi
        sleep 0.5
    done

    return 1
}

rollback() {
    local reason=${1:-upgrade failed}

    log "rolling back all components: ${reason}"

    systemctl stop "${PORTAL_SERVICE}" >/dev/null 2>&1 || true
    stop_maintenance

    [[ -n "${old_observatory}" && -d "${old_observatory}" ]] &&
        switch_current_link "${OBSERVATORY_ROOT}" "${old_observatory}"

    [[ -n "${old_native_guard}" && -d "${old_native_guard}" ]] &&
        switch_current_link "${NATIVE_GUARD_ROOT}" "${old_native_guard}"

    [[ -n "${old_portal}" && -d "${old_portal}" ]] &&
        switch_current_link "${PORTAL_ROOT}" "${old_portal}"

    systemctl start "${PORTAL_SERVICE}" || true

    if wait_for_health; then
        log "rollback restored previous deployment"
    else
        log "WARNING: rollback portal is not healthy"
    fi
}

cleanup() {
    local status=$?

    stop_maintenance

    if (( status != 0 )) &&
       (( switched == 1 )) &&
       (( upgrade_complete == 0 )); then
        rollback "unexpected command failure"
    fi

    exit "${status}"
}

prune_releases() {
    local root=$1
    local previous=$2
    local current path
    local kept=0

    [[ "${KEEP_RELEASES}" =~ ^[1-9][0-9]*$ ]] || return 0

    current=$(readlink -f "${root}/current" 2>/dev/null || true)

    while IFS= read -r path; do
        [[ -n "${path}" ]] || continue
        [[ "${path}" == "${current}" || "${path}" == "${previous}" ]] && continue

        kept=$((kept + 1))
        if (( kept >= KEEP_RELEASES )); then
            log "removing old release ${path}"
            rm -rf --one-file-system "${path}"
        fi
    done < <(
        find "${root}/releases" \
            -mindepth 1 \
            -maxdepth 1 \
            -type d \
            -printf '%T@ %p\n' |
        sort -nr |
        cut -d' ' -f2-
    )
}

main() {
    require_root
    require_user "${BUILD_USER}"
    require_user mcp-portal

    for command in \
        git cmake ctest ninja python3 systemctl curl flock runuser \
        find sort cut awk getent id chmod chown; do
        require_command "${command}"
    done

    mkdir -p "$(dirname "${LOCK_FILE}")" "${RUNTIME_DIR}"

    exec 9>"${LOCK_FILE}"
    flock -n 9 || fail "another public platform upgrade is already running"

    trap cleanup EXIT INT TERM

    systemctl cat "${PORTAL_SERVICE}" >/dev/null 2>&1 ||
        fail "systemd service not found: ${PORTAL_SERVICE}"

    old_observatory=$(readlink -f "${OBSERVATORY_ROOT}/current" 2>/dev/null || true)
    old_native_guard=$(readlink -f "${NATIVE_GUARD_ROOT}/current" 2>/dev/null || true)
    old_portal=$(readlink -f "${PORTAL_ROOT}/current" 2>/dev/null || true)

    new_observatory=$(
        clone_release \
            observatory \
            "${OBSERVATORY_REPOSITORY_URL}" \
            "${OBSERVATORY_REF}" \
            "${OBSERVATORY_ROOT}"
    )

    new_native_guard=$(
        clone_release \
            native-guard \
            "${NATIVE_GUARD_REPOSITORY_URL}" \
            "${NATIVE_GUARD_REF}" \
            "${NATIVE_GUARD_ROOT}"
    )

    new_portal=$(
        clone_release \
            portal \
            "${PORTAL_REPOSITORY_URL}" \
            "${PORTAL_REF}" \
            "${PORTAL_ROOT}"
    )

    build_observatory
    build_native_guard
    build_portal

    log "all builds and tests passed; entering maintenance mode"

    systemctl stop "${PORTAL_SERVICE}"
    start_maintenance

    switch_current_link "${OBSERVATORY_ROOT}" "${new_observatory}"
    switch_current_link "${NATIVE_GUARD_ROOT}" "${new_native_guard}"
    switch_current_link "${PORTAL_ROOT}" "${new_portal}"
    switched=1

    stop_maintenance
    systemctl start "${PORTAL_SERVICE}"

    if ! wait_for_health; then
        rollback "new portal failed health check at ${PORTAL_HEALTH_URL}"
        fail "upgrade rolled back"
    fi

    upgrade_complete=1

    log "upgrade successful"
    log "mcp-observatory: $(readlink -f "${OBSERVATORY_ROOT}/current")"
    log "mcp-native-guard: $(readlink -f "${NATIVE_GUARD_ROOT}/current")"
    log "portal: $(readlink -f "${PORTAL_ROOT}/current")"

    systemctl --no-pager --full status "${PORTAL_SERVICE}" || true

    prune_releases "${OBSERVATORY_ROOT}" "${old_observatory}"
    prune_releases "${NATIVE_GUARD_ROOT}" "${old_native_guard}"
    prune_releases "${PORTAL_ROOT}" "${old_portal}"
}

main "$@"
