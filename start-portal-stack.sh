#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

# Starts the portal worker in the background and keeps the portal attached to
# this terminal. Override any default by exporting it before running.

PORTAL_ROOT="${PORTAL_ROOT:-/home/alex/source/mcp-observatory-guard-portal}"
OBSERVATORY_ROOT="${OBSERVATORY_ROOT:-/home/alex/source/mcp-observatory}"

RUNTIME_DIR="${MCP_PORTAL_RUNTIME_DIR:-$PORTAL_ROOT/runtime}"
LOG_DIR="$RUNTIME_DIR/logs"

PORTAL_PID_FILE="$RUNTIME_DIR/portal.pid"
WORKER_PID_FILE="$RUNTIME_DIR/worker.pid"
LAUNCHER_PID_FILE="$RUNTIME_DIR/launcher.pid"

PORTAL_LOG="$LOG_DIR/portal.log"
WORKER_LOG="$LOG_DIR/worker.log"

export MCP_PORTAL_DATABASE="${MCP_PORTAL_DATABASE:-$OBSERVATORY_ROOT/db/local-registry.sqlite}"
export MCP_PORTAL_HOST="${MCP_PORTAL_HOST:-127.0.0.1}"
export MCP_PORTAL_PORT="${MCP_PORTAL_PORT:-8080}"
export MCP_PORTAL_PAGE_SIZE="${MCP_PORTAL_PAGE_SIZE:-50}"

export MCP_PORTAL_ENABLE_ANALYSIS="${MCP_PORTAL_ENABLE_ANALYSIS:-1}"
export MCP_PORTAL_ENABLE_EVIDENCE_VIEW="${MCP_PORTAL_ENABLE_EVIDENCE_VIEW:-1}"
export MCP_PORTAL_ENABLE_REVIEW="${MCP_PORTAL_ENABLE_REVIEW:-1}"
export MCP_PORTAL_REVIEWER="${MCP_PORTAL_REVIEWER:-${USER:-local-reviewer}}"
export MCP_PORTAL_JOBS_DATABASE="${MCP_PORTAL_JOBS_DATABASE:-$RUNTIME_DIR/portal-jobs.sqlite}"
export MCP_PORTAL_ANALYSIS_RULES="${MCP_PORTAL_ANALYSIS_RULES:-$OBSERVATORY_ROOT/rules/artifact-static-analysis-v1.json}"
export MCP_PORTAL_EVIDENCE_ROOT="${MCP_PORTAL_EVIDENCE_ROOT:-$OBSERVATORY_ROOT/evidence}"
export MCP_PORTAL_ANALYSIS_TIMEOUT_SECONDS="${MCP_PORTAL_ANALYSIS_TIMEOUT_SECONDS:-900}"
export MCP_PORTAL_EVIDENCE_TIMEOUT_SECONDS="${MCP_PORTAL_EVIDENCE_TIMEOUT_SECONDS:-10}"
export MCP_PORTAL_MAXIMUM_DOWNLOAD_BYTES="${MCP_PORTAL_MAXIMUM_DOWNLOAD_BYTES:-8388608}"
export MCP_PORTAL_REVIEW_TIMEOUT_SECONDS="${MCP_PORTAL_REVIEW_TIMEOUT_SECONDS:-30}"
export MCP_PORTAL_MAXIMUM_OUTPUT_BYTES="${MCP_PORTAL_MAXIMUM_OUTPUT_BYTES:-65536}"
export MCP_PORTAL_WORKER_POLL_SECONDS="${MCP_PORTAL_WORKER_POLL_SECONDS:-2}"

if [[ -z "${MCP_PORTAL_OBSERVATORY_BINARY:-}" ]]; then
    for candidate in \
        "$OBSERVATORY_ROOT/build/release/mcp-observatory" \
        "$OBSERVATORY_ROOT/build/mcp-observatory" \
        "$OBSERVATORY_ROOT/cmake-build-release/mcp-observatory"
    do
        if [[ -x "$candidate" ]]; then
            export MCP_PORTAL_OBSERVATORY_BINARY="$candidate"
            break
        fi
    done
fi

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

pid_from_file() {
    local pid_file="$1"
    [[ -f "$pid_file" ]] || return 1
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    printf '%s\n' "$pid"
}

pid_file_is_running() {
    local pid_file="$1"
    local pid
    pid="$(pid_from_file "$pid_file" || true)"
    [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

remove_stale_pid_file() {
    local pid_file="$1"
    if [[ -f "$pid_file" ]] && ! pid_file_is_running "$pid_file"; then
        rm -f "$pid_file"
    fi
}

require_file() {
    local label="$1"
    local path="$2"
    [[ -f "$path" ]] || die "$label does not exist: $path"
}

require_directory() {
    local label="$1"
    local path="$2"
    [[ -d "$path" ]] || die "$label does not exist: $path"
}

require_executable() {
    local label="$1"
    local path="$2"
    [[ -n "$path" ]] || die "$label was not resolved"
    [[ -f "$path" ]] || die "$label does not exist: $path"
    [[ -x "$path" ]] || die "$label is not executable: $path"
}

port_is_listening() {
    command -v ss >/dev/null 2>&1 || return 1
    ss -H -ltn "sport = :$MCP_PORTAL_PORT" 2>/dev/null | grep -q .
}

terminate_pid() {
    local label="$1"
    local pid="$2"

    [[ "$pid" =~ ^[0-9]+$ ]] || return 0
    kill -0 "$pid" 2>/dev/null || return 0

    printf 'Stopping %s (PID %s)...\n' "$label" "$pid"
    kill -TERM "$pid" 2>/dev/null || true

    for _ in {1..50}; do
        kill -0 "$pid" 2>/dev/null || return 0
        sleep 0.1
    done

    printf '%s did not stop after 5 seconds; sending SIGKILL.\n' "$label" >&2
    kill -KILL "$pid" 2>/dev/null || true
}

PORTAL_PID=""
WORKER_PID=""
CLEANUP_STARTED=0

cleanup() {
    local status=$?

    if [[ "$CLEANUP_STARTED" -eq 1 ]]; then
        return "$status"
    fi
    CLEANUP_STARTED=1

    if [[ -n "$PORTAL_PID" ]]; then
        terminate_pid "portal" "$PORTAL_PID"
    elif pid_file_is_running "$PORTAL_PID_FILE"; then
        terminate_pid "portal" "$(cat "$PORTAL_PID_FILE")"
    fi

    local worker_to_stop=""
    if [[ -n "$WORKER_PID" ]]; then
        worker_to_stop="$WORKER_PID"
    elif pid_file_is_running "$WORKER_PID_FILE"; then
        worker_to_stop="$(cat "$WORKER_PID_FILE")"
    fi

    if [[ -n "$worker_to_stop" ]] && kill -0 "$worker_to_stop" 2>/dev/null; then
        while read -r child_pid; do
            [[ "$child_pid" =~ ^[0-9]+$ ]] || continue
            kill -TERM -- "-$child_pid" 2>/dev/null || kill -TERM "$child_pid" 2>/dev/null || true
        done < <(ps -o pid= --ppid "$worker_to_stop" 2>/dev/null | tr -d ' ')
        terminate_pid "worker" "$worker_to_stop"
    fi

    rm -f "$PORTAL_PID_FILE" "$WORKER_PID_FILE" "$LAUNCHER_PID_FILE"
    printf 'Portal stack stopped.\n'
    return "$status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

command -v python3 >/dev/null 2>&1 || die "python3 is not installed"
command -v docker >/dev/null 2>&1 || die "docker is not installed"

require_directory "Portal repository" "$PORTAL_ROOT"
require_directory "Portal Python package" "$PORTAL_ROOT/mcp_portal"
require_file "Observatory catalog" "$MCP_PORTAL_DATABASE"
require_file "Static-analysis rules" "$MCP_PORTAL_ANALYSIS_RULES"
require_executable "Observatory binary" "${MCP_PORTAL_OBSERVATORY_BINARY:-}"

mkdir -p "$RUNTIME_DIR" "$LOG_DIR" "$MCP_PORTAL_EVIDENCE_ROOT"
require_directory "Evidence root" "$MCP_PORTAL_EVIDENCE_ROOT"

remove_stale_pid_file "$PORTAL_PID_FILE"
remove_stale_pid_file "$WORKER_PID_FILE"
remove_stale_pid_file "$LAUNCHER_PID_FILE"

pid_file_is_running "$PORTAL_PID_FILE" && die "portal already appears to be running with PID $(cat "$PORTAL_PID_FILE")"
pid_file_is_running "$WORKER_PID_FILE" && die "worker already appears to be running with PID $(cat "$WORKER_PID_FILE")"

if port_is_listening; then
    die "TCP port $MCP_PORTAL_PORT is already in use"
fi

if ! docker info >/dev/null 2>&1; then
    die "Docker daemon is unavailable. Verify Docker Desktop/WSL integration and run: docker info"
fi

cd "$PORTAL_ROOT"

python3 - <<'PY'
import mcp_portal.app
import mcp_portal.worker
print("Portal modules import successfully")
print("Loaded app:", mcp_portal.app.__file__)
PY

printf '%s\n' "$$" > "$LAUNCHER_PID_FILE"

printf '\nConfiguration\n'
printf '  Portal root:       %s\n' "$PORTAL_ROOT"
printf '  Observatory root:  %s\n' "$OBSERVATORY_ROOT"
printf '  Catalog:           %s\n' "$MCP_PORTAL_DATABASE"
printf '  Jobs database:     %s\n' "$MCP_PORTAL_JOBS_DATABASE"
printf '  Analyzer binary:   %s\n' "$MCP_PORTAL_OBSERVATORY_BINARY"
printf '  Rules:             %s\n' "$MCP_PORTAL_ANALYSIS_RULES"
printf '  Evidence root:     %s\n' "$MCP_PORTAL_EVIDENCE_ROOT"
printf '  Portal URL:        http://%s:%s\n' "$MCP_PORTAL_HOST" "$MCP_PORTAL_PORT"
printf '  Analysis enabled:  %s\n' "$MCP_PORTAL_ENABLE_ANALYSIS"
printf '  Evidence enabled:  %s\n' "$MCP_PORTAL_ENABLE_EVIDENCE_VIEW"
printf '  Review enabled:    %s\n' "$MCP_PORTAL_ENABLE_REVIEW"
printf '  Reviewer:          %s\n' "$MCP_PORTAL_REVIEWER"
printf '  Analysis timeout:  %s seconds\n' "$MCP_PORTAL_ANALYSIS_TIMEOUT_SECONDS"
printf '  Evidence timeout:  %s seconds\n' "$MCP_PORTAL_EVIDENCE_TIMEOUT_SECONDS"
printf '  Download limit:    %s bytes\n' "$MCP_PORTAL_MAXIMUM_DOWNLOAD_BYTES"
printf '  Review timeout:    %s seconds\n' "$MCP_PORTAL_REVIEW_TIMEOUT_SECONDS"
printf '  Worker poll:       %s seconds\n' "$MCP_PORTAL_WORKER_POLL_SECONDS"
printf '\nLogs\n'
printf '  Portal: %s\n' "$PORTAL_LOG"
printf '  Worker: %s\n\n' "$WORKER_LOG"

python3 -u -m mcp_portal.worker \
    > >(sed -u 's/^/[worker] /' | tee -a "$WORKER_LOG") \
    2>&1 &
WORKER_PID=$!
printf '%s\n' "$WORKER_PID" > "$WORKER_PID_FILE"

sleep 0.5
if ! kill -0 "$WORKER_PID" 2>/dev/null; then
    die "worker exited during startup; inspect $WORKER_LOG"
fi
printf 'Worker started with PID %s\n' "$WORKER_PID"

python3 -u -m mcp_portal \
    > >(tee -a "$PORTAL_LOG") \
    2>&1 &
PORTAL_PID=$!
printf '%s\n' "$PORTAL_PID" > "$PORTAL_PID_FILE"

sleep 0.5
if ! kill -0 "$PORTAL_PID" 2>/dev/null; then
    die "portal exited during startup; inspect $PORTAL_LOG"
fi

printf 'Portal started with PID %s\n' "$PORTAL_PID"
printf 'Open http://%s:%s\n' "$MCP_PORTAL_HOST" "$MCP_PORTAL_PORT"
printf 'Press Ctrl+C to stop both portal and worker.\n\n'

set +e
wait "$PORTAL_PID"
PORTAL_STATUS=$?
set -e

if [[ "$PORTAL_STATUS" -ne 0 && "$PORTAL_STATUS" -ne 130 && "$PORTAL_STATUS" -ne 143 ]]; then
    printf 'Portal exited with status %s. See %s\n' "$PORTAL_STATUS" "$PORTAL_LOG" >&2
fi

exit "$PORTAL_STATUS"
