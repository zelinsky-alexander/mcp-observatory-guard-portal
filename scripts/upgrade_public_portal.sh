#!/usr/bin/env bash
set -Eeuo pipefail

# Canonical Storage v2 production upgrade.
#
# Deploys exact commits from main for all three repositories into immutable
# release directories, switches /opt/*/current atomically, keeps the persistent
# Storage v2 state under /var/lib/mcp-observatory-v2, normalizes the existing v2
# systemd service paths, restarts the v2 stack, verifies loopback health, and
# retains a bounded number of releases for rollback.
#
# This script deliberately does not touch Nginx/Cloudflare configuration and
# never recreates the retired v1 /var/lib/mcp-observatory state.

readonly OBSERVATORY_REPOSITORY_URL="${OBSERVATORY_REPOSITORY_URL:-https://github.com/zelinsky-alexander/mcp-observatory.git}"
readonly NATIVE_GUARD_REPOSITORY_URL="${NATIVE_GUARD_REPOSITORY_URL:-https://github.com/zelinsky-alexander/mcp-native-guard.git}"
readonly PORTAL_REPOSITORY_URL="${PORTAL_REPOSITORY_URL:-https://github.com/zelinsky-alexander/mcp-observatory-guard-portal.git}"

readonly OBSERVATORY_REF="${OBSERVATORY_REF:-main}"
readonly NATIVE_GUARD_REF="${NATIVE_GUARD_REF:-main}"
readonly PORTAL_REF="${PORTAL_REF:-main}"

readonly OBSERVATORY_ROOT="${OBSERVATORY_DEPLOY_ROOT:-/opt/mcp-observatory}"
readonly NATIVE_GUARD_ROOT="${NATIVE_GUARD_DEPLOY_ROOT:-/opt/mcp-native-guard}"
readonly PORTAL_ROOT="${PORTAL_DEPLOY_ROOT:-/opt/mcp-observatory-guard-portal}"
readonly STATE_ROOT="${MCPO_V2_STATE_DIR:-/var/lib/mcp-observatory-v2}"

readonly PORTAL_SERVICE="${PORTAL_SERVICE_NAME:-mcp-portal-storage-v2.service}"
readonly REFRESH_SERVICE="${REFRESH_SERVICE_NAME:-mcp-observatory-v2-refresh.service}"
readonly REFRESH_TIMER="${REFRESH_TIMER_NAME:-mcp-observatory-v2-refresh.timer}"
readonly STATIC_SERVICE="${STATIC_ANALYSIS_SERVICE_NAME:-mcp-observatory-v2-static-analysis.service}"
readonly STATIC_TIMER="${STATIC_ANALYSIS_TIMER_NAME:-mcp-observatory-v2-static-analysis.timer}"

readonly PORTAL_URL="${PORTAL_URL:-http://127.0.0.1:8081}"
readonly MAINTENANCE_HOST="${MAINTENANCE_HOST:-127.0.0.1}"
readonly MAINTENANCE_PORT="${MAINTENANCE_PORT:-8081}"
readonly BUILD_USER="${BUILD_USER:-ubuntu}"
readonly KEEP_RELEASES="${UPGRADE_KEEP_RELEASES:-3}"
readonly LOCK_FILE="${UPGRADE_LOCK_FILE:-/run/lock/mcp-v2-upgrade.lock}"
readonly RUNTIME_DIR="${UPGRADE_RUNTIME_DIR:-/run/mcp-v2-upgrade}"
readonly SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
readonly RUN_FULL_TESTS="${UPGRADE_RUN_FULL_TESTS:-1}"

old_observatory=""
old_native_guard=""
old_portal=""
new_observatory=""
new_native_guard=""
new_portal=""
maintenance_pid=""
switched=0
units_backed_up=0
upgrade_complete=0

log() { printf '%s %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*" >&2; }
warn() { log "WARNING: $*"; }
fail() { log "ERROR: $*"; exit 1; }

require_root() { [[ ${EUID} -eq 0 ]] || fail "run as root: sudo $0"; }
require_command() { command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"; }
require_user() { id "$1" >/dev/null 2>&1 || fail "required user does not exist: $1"; }

current_target() {
    local root=$1
    [[ -L "$root/current" ]] || return 0
    readlink -f "$root/current" || true
}

resolve_commit() {
    local repository_url=$1 ref=$2 commit
    commit=$(git ls-remote "$repository_url" "refs/heads/$ref" | awk 'NR==1 {print $1}')
    [[ "$commit" =~ ^[0-9a-f]{40}$ ]] || return 1
    printf '%s\n' "$commit"
}

clone_release() {
    local name=$1 repository_url=$2 ref=$3 root=$4
    local commit short timestamp release checked

    install -d -m 0755 "$root" "$root/releases"
    commit=$(resolve_commit "$repository_url" "$ref") || fail "cannot resolve $name ref $ref"
    short=${commit:0:12}
    timestamp=$(date -u +'%Y%m%dT%H%M%SZ')
    release="$root/releases/${name}-${timestamp}-${short}"

    log "cloning $name $ref ($commit)"
    git clone --quiet --no-tags --filter=blob:none "$repository_url" "$release"
    git -C "$release" checkout --quiet --detach "$commit"
    checked=$(git -C "$release" rev-parse HEAD)
    [[ "$checked" == "$commit" ]] || fail "$name checkout mismatch"
    printf '%s\n' "$commit" >"$release/DEPLOYED_COMMIT"
    printf '%s\n' "$release"
}

prepare_build_tree() {
    chown -R "$BUILD_USER:$BUILD_USER" "$1"
}

seal_release_tree() {
    chown -R root:root "$1"
    chmod -R go-w "$1"
    chmod 0444 "$1/DEPLOYED_COMMIT"
}

run_as_build_user() {
    local dir=$1
    shift
    runuser -u "$BUILD_USER" -- env HOME="$(getent passwd "$BUILD_USER" | cut -d: -f6)" \
        bash -c 'set -Eeuo pipefail; cd "$1"; shift; exec "$@"' bash "$dir" "$@"
}

build_observatory() {
    prepare_build_tree "$new_observatory"
    run_as_build_user "$new_observatory" cmake --preset release
    run_as_build_user "$new_observatory" cmake --build --preset release --parallel 2
    if [[ "$RUN_FULL_TESTS" == 1 ]]; then
        run_as_build_user "$new_observatory" cmake --preset dev-debug
        run_as_build_user "$new_observatory" cmake --build --preset dev-debug --parallel 2
        run_as_build_user "$new_observatory" ctest --preset dev-debug --output-on-failure
    fi
    seal_release_tree "$new_observatory"
}

build_native_guard() {
    prepare_build_tree "$new_native_guard"
    run_as_build_user "$new_native_guard" cmake --preset release
    run_as_build_user "$new_native_guard" cmake --build --preset release --parallel 2
    if [[ "$RUN_FULL_TESTS" == 1 ]]; then
        run_as_build_user "$new_native_guard" cmake --preset dev-debug
        run_as_build_user "$new_native_guard" cmake --build --preset dev-debug --parallel 2
        run_as_build_user "$new_native_guard" ctest --preset dev-debug --output-on-failure
    fi
    seal_release_tree "$new_native_guard"
}

build_portal() {
    prepare_build_tree "$new_portal"
    run_as_build_user "$new_portal" python3 -m compileall -q mcp_portal tests
    if [[ "$RUN_FULL_TESTS" == 1 ]]; then
        run_as_build_user "$new_portal" python3 -m unittest discover -s tests -v
    fi
    seal_release_tree "$new_portal"
}

switch_current() {
    local root=$1 target=$2 tmp="$root/.current.$$.tmp"
    rm -f "$tmp"
    ln -s "$target" "$tmp"
    mv -Tf "$tmp" "$root/current"
}

remove_current_if_absent_before() {
    local root=$1 old=$2
    if [[ -n "$old" ]]; then
        switch_current "$root" "$old"
    else
        rm -f "$root/current"
    fi
}

backup_units() {
    install -d -m 0700 "$RUNTIME_DIR/unit-backup"
    local unit
    for unit in "$PORTAL_SERVICE" "$REFRESH_SERVICE" "$STATIC_SERVICE"; do
        [[ -f "$SYSTEMD_DIR/$unit" ]] || fail "required v2 service unit missing: $SYSTEMD_DIR/$unit"
        cp -a "$SYSTEMD_DIR/$unit" "$RUNTIME_DIR/unit-backup/$unit"
    done
    for unit in "$REFRESH_TIMER" "$STATIC_TIMER"; do
        [[ -f "$SYSTEMD_DIR/$unit" ]] || fail "required v2 timer unit missing: $SYSTEMD_DIR/$unit"
    done
    units_backed_up=1
}

normalize_unit_paths() {
    local file
    for file in \
        "$SYSTEMD_DIR/$REFRESH_SERVICE" \
        "$SYSTEMD_DIR/$STATIC_SERVICE" \
        "$SYSTEMD_DIR/$PORTAL_SERVICE"
    do
        sed -i \
            -e "s#/opt/mcp-storage-v2-test/mcp-observatory-guard-portal#$PORTAL_ROOT/current#g" \
            -e "s#/opt/mcp-storage-v2-test/mcp-native-guard#$NATIVE_GUARD_ROOT/current#g" \
            -e "s#/opt/mcp-storage-v2-test/mcp-observatory#$OBSERVATORY_ROOT/current#g" \
            "$file"
    done

    # The canonical v2 services must not retain the migration checkout path.
    if grep -Hn '/opt/mcp-storage-v2-test' \
        "$SYSTEMD_DIR/$REFRESH_SERVICE" \
        "$SYSTEMD_DIR/$STATIC_SERVICE" \
        "$SYSTEMD_DIR/$PORTAL_SERVICE"; then
        fail "migration path remains in a v2 service unit"
    fi
}

restore_units() {
    [[ "$units_backed_up" == 1 ]] || return 0
    local unit
    for unit in "$PORTAL_SERVICE" "$REFRESH_SERVICE" "$STATIC_SERVICE"; do
        cp -a "$RUNTIME_DIR/unit-backup/$unit" "$SYSTEMD_DIR/$unit"
    done
    systemctl daemon-reload || true
}

start_maintenance() {
    install -d -m 0755 "$RUNTIME_DIR"
    cat >"$RUNTIME_DIR/maintenance.py" <<'PY'
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8081"))
PAGE = b"<!doctype html><html><head><meta charset=utf-8><meta name=robots content='noindex,nofollow'><title>Upgrade</title></head><body><h1>Assurance platform upgrade in progress</h1><p>Please retry shortly.</p></body></html>"
class Handler(BaseHTTPRequestHandler):
    def reply(self, body):
        self.send_response(503)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.send_header("Retry-After", "120")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(PAGE)
    def do_GET(self): self.reply(True)
    def do_HEAD(self): self.reply(False)
    def log_message(self, fmt, *args): return
ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
PY
    runuser -u mcp-portal -- env HOST="$MAINTENANCE_HOST" PORT="$MAINTENANCE_PORT" \
        python3 "$RUNTIME_DIR/maintenance.py" >"$RUNTIME_DIR/maintenance.log" 2>&1 &
    maintenance_pid=$!

    for _ in $(seq 1 30); do
        if [[ "$(curl -sS -o /dev/null -w '%{http_code}' "$PORTAL_URL/" || true)" == 503 ]]; then
            return 0
        fi
        sleep 0.2
    done
    fail "maintenance server failed to start"
}

stop_maintenance() {
    if [[ -n "$maintenance_pid" ]] && kill -0 "$maintenance_pid" 2>/dev/null; then
        kill "$maintenance_pid" 2>/dev/null || true
        wait "$maintenance_pid" 2>/dev/null || true
    fi
    maintenance_pid=""
}

wait_for_portal() {
    local path code
    for _ in $(seq 1 40); do
        code=$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' "$PORTAL_URL/" || true)
        [[ "$code" == 200 ]] && break
        sleep 0.5
    done
    [[ "$code" == 200 ]] || return 1

    for path in / /servers /coverage; do
        code=$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' "$PORTAL_URL$path" || true)
        [[ "$code" == 200 ]] || { warn "$path returned $code"; return 1; }
    done
}

prune_releases() {
    local root=$1
    mapfile -t releases < <(find "$root/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null | sort -nr | awk '{print $2}')
    local i
    for ((i=KEEP_RELEASES; i<${#releases[@]}; i++)); do
        [[ "${releases[$i]}" == "$(readlink -f "$root/current" 2>/dev/null || true)" ]] && continue
        rm -rf -- "${releases[$i]}"
    done
}

show_summary() {
    echo
    echo "=== Active releases ==="
    readlink -f "$OBSERVATORY_ROOT/current"
    readlink -f "$NATIVE_GUARD_ROOT/current"
    readlink -f "$PORTAL_ROOT/current"
    echo
    echo "=== Deployed commits ==="
    cat "$OBSERVATORY_ROOT/current/DEPLOYED_COMMIT"
    cat "$NATIVE_GUARD_ROOT/current/DEPLOYED_COMMIT"
    cat "$PORTAL_ROOT/current/DEPLOYED_COMMIT"
    echo
    echo "=== Services / timers ==="
    systemctl --no-pager --full status "$PORTAL_SERVICE" "$REFRESH_TIMER" "$STATIC_TIMER" || true
    echo
    echo "=== Storage v2 ==="
    du -sh "$STATE_ROOT" 2>/dev/null || true
    df -h /
}

rollback() {
    local rc=$?
    [[ "$upgrade_complete" == 1 ]] && return 0
    warn "upgrade failed; attempting rollback"
    stop_maintenance

    if [[ "$switched" == 1 ]]; then
        remove_current_if_absent_before "$OBSERVATORY_ROOT" "$old_observatory"
        remove_current_if_absent_before "$NATIVE_GUARD_ROOT" "$old_native_guard"
        remove_current_if_absent_before "$PORTAL_ROOT" "$old_portal"
    fi

    restore_units
    systemctl restart "$PORTAL_SERVICE" >/dev/null 2>&1 || true
    systemctl start "$REFRESH_TIMER" >/dev/null 2>&1 || true
    systemctl start "$STATIC_TIMER" >/dev/null 2>&1 || true
    warn "rollback attempted"
    exit "$rc"
}

main() {
    require_root
    for cmd in git cmake python3 curl systemctl systemd-analyze runuser awk sed grep find sort; do
        require_command "$cmd"
    done
    require_user "$BUILD_USER"
    require_user mcp-portal
    [[ -d "$STATE_ROOT" ]] || fail "Storage v2 state root missing: $STATE_ROOT"
    [[ -f "$STATE_ROOT/catalog/local-registry.sqlite" ]] || fail "hot v2 database missing"
    [[ -f "$STATE_ROOT/history/assurance-history.sqlite" ]] || fail "history v2 database missing"
    [[ "$KEEP_RELEASES" =~ ^[1-9][0-9]*$ ]] || fail "UPGRADE_KEEP_RELEASES must be positive"

    install -d -m 0755 "$(dirname "$LOCK_FILE")"
    exec 9>"$LOCK_FILE"
    flock -n 9 || fail "another MCP upgrade is already running"

    trap rollback ERR INT TERM

    old_observatory=$(current_target "$OBSERVATORY_ROOT")
    old_native_guard=$(current_target "$NATIVE_GUARD_ROOT")
    old_portal=$(current_target "$PORTAL_ROOT")

    backup_units

    # Build and test before touching the live services.
    new_observatory=$(clone_release observatory "$OBSERVATORY_REPOSITORY_URL" "$OBSERVATORY_REF" "$OBSERVATORY_ROOT")
    new_native_guard=$(clone_release native-guard "$NATIVE_GUARD_REPOSITORY_URL" "$NATIVE_GUARD_REF" "$NATIVE_GUARD_ROOT")
    new_portal=$(clone_release portal "$PORTAL_REPOSITORY_URL" "$PORTAL_REF" "$PORTAL_ROOT")

    build_observatory
    build_native_guard
    build_portal

    log "stopping v2 timers and portal"
    systemctl stop "$REFRESH_TIMER" "$STATIC_TIMER" >/dev/null 2>&1 || true
    systemctl stop "$REFRESH_SERVICE" "$STATIC_SERVICE" >/dev/null 2>&1 || true
    systemctl stop "$PORTAL_SERVICE"
    start_maintenance

    switch_current "$OBSERVATORY_ROOT" "$new_observatory"
    switch_current "$NATIVE_GUARD_ROOT" "$new_native_guard"
    switch_current "$PORTAL_ROOT" "$new_portal"
    switched=1

    normalize_unit_paths
    systemd-analyze verify \
        "$SYSTEMD_DIR/$PORTAL_SERVICE" \
        "$SYSTEMD_DIR/$REFRESH_SERVICE" \
        "$SYSTEMD_DIR/$STATIC_SERVICE"
    systemctl daemon-reload

    stop_maintenance
    systemctl restart "$PORTAL_SERVICE"
    systemctl enable --now "$REFRESH_TIMER" "$STATIC_TIMER" >/dev/null

    wait_for_portal || fail "portal smoke test failed after switch"

    # Fast structural checks only; expensive SQLite integrity checks remain an
    # explicit operator action through verify_storage_v2_sidecar*.sh.
    [[ -x "$OBSERVATORY_ROOT/current/build/release/mcp-observatory" ]] || fail "Observatory release binary missing"
    [[ -f "$PORTAL_ROOT/current/mcp_portal/__init__.py" ]] || fail "portal release incomplete"

    prune_releases "$OBSERVATORY_ROOT"
    prune_releases "$NATIVE_GUARD_ROOT"
    prune_releases "$PORTAL_ROOT"

    upgrade_complete=1
    trap - ERR INT TERM
    rm -rf "$RUNTIME_DIR/unit-backup"
    show_summary

    log "Storage v2 production upgrade complete"
    log "all active releases were resolved from main by default"
}

main "$@"
