#!/usr/bin/env bash
set -Eeuo pipefail

PORTAL_ROOT="${PORTAL_ROOT:-/home/alex/source/mcp-observatory-guard-portal}"
RUNTIME_DIR="${MCP_PORTAL_RUNTIME_DIR:-$PORTAL_ROOT/runtime}"

PORTAL_PID_FILE="$RUNTIME_DIR/portal.pid"
WORKER_PID_FILE="$RUNTIME_DIR/worker.pid"
LAUNCHER_PID_FILE="$RUNTIME_DIR/launcher.pid"

read_pid() {
    local pid_file="$1"
    [[ -f "$pid_file" ]] || return 1
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    printf '%s\n' "$pid"
}

pid_is_running() {
    local pid="$1"
    [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

stop_process() {
    local label="$1"
    local pid="$2"

    if ! pid_is_running "$pid"; then
        printf '%s is not running (stale PID %s).\n' "$label" "$pid"
        return 0
    fi

    printf 'Stopping %s (PID %s)...\n' "$label" "$pid"
    kill -TERM "$pid" 2>/dev/null || true

    for _ in {1..100}; do
        pid_is_running "$pid" || {
            printf '%s stopped.\n' "$label"
            return 0
        }
        sleep 0.1
    done

    printf '%s did not stop after 10 seconds; sending SIGKILL.\n' "$label" >&2
    kill -KILL "$pid" 2>/dev/null || true
}

PORTAL_PID="$(read_pid "$PORTAL_PID_FILE" || true)"
WORKER_PID="$(read_pid "$WORKER_PID_FILE" || true)"
LAUNCHER_PID="$(read_pid "$LAUNCHER_PID_FILE" || true)"

if [[ -n "$PORTAL_PID" ]]; then
    stop_process "portal" "$PORTAL_PID"
else
    printf 'No portal PID file found.\n'
fi

if [[ -n "$WORKER_PID" ]] && pid_is_running "$WORKER_PID"; then
    while read -r child_pid; do
        [[ "$child_pid" =~ ^[0-9]+$ ]] || continue
        printf 'Stopping active analyzer child/process group %s...\n' "$child_pid"
        kill -TERM -- "-$child_pid" 2>/dev/null || kill -TERM "$child_pid" 2>/dev/null || true
    done < <(ps -o pid= --ppid "$WORKER_PID" 2>/dev/null | tr -d ' ')
    stop_process "worker" "$WORKER_PID"
elif [[ -n "$WORKER_PID" ]]; then
    printf 'worker is not running (stale PID %s).\n' "$WORKER_PID"
else
    printf 'No worker PID file found.\n'
fi

if [[ -n "$LAUNCHER_PID" ]] && pid_is_running "$LAUNCHER_PID" && [[ "$LAUNCHER_PID" != "$$" ]]; then
    stop_process "launcher" "$LAUNCHER_PID"
fi

rm -f "$PORTAL_PID_FILE" "$WORKER_PID_FILE" "$LAUNCHER_PID_FILE"

printf 'Portal stack is stopped.\n'
printf 'Logs remain under: %s/logs\n' "$RUNTIME_DIR"
