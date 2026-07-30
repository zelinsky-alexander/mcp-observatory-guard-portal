"""Loopback-only runtime discovery UI for the runtime-discovery-v1 MVP.

Run separately from the main portal while the runtime schema is experimental:
`python3 -m mcp_portal.runtime_dashboard`.
"""
from __future__ import annotations

import hashlib
import hmac
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
from urllib.parse import parse_qs, urlsplit


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


DATABASE = Path(required("MCP_PORTAL_DATABASE")).resolve()
RUNNER = Path(required("MCP_PORTAL_RUNTIME_DISCOVERY_RUNNER")).resolve()
GUARD = Path(required("MCP_PORTAL_NATIVE_GUARD_BINARY")).resolve()
EVIDENCE = Path(required("MCP_PORTAL_EVIDENCE_ROOT")).resolve()
IMAGE = os.environ.get("MCP_PORTAL_RUNTIME_IMAGE", "node:22-bookworm-slim").strip()
HOST = os.environ.get("MCP_PORTAL_RUNTIME_HOST", "127.0.0.1").strip()
PORT = int(os.environ.get("MCP_PORTAL_RUNTIME_PORT", "8081"))
TIMEOUT = int(os.environ.get("MCP_PORTAL_RUNTIME_TIMEOUT_SECONDS", "240"))
if HOST not in {"127.0.0.1", "localhost", "::1"}:
    raise RuntimeError("runtime discovery dashboard is restricted to loopback")
for path in (DATABASE, RUNNER, GUARD):
    if not path.is_file():
        raise RuntimeError(f"required file does not exist: {path}")
if not EVIDENCE.is_dir():
    raise RuntimeError(f"evidence directory does not exist: {EVIDENCE}")

SECRET = secrets.token_bytes(32)


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def token(server_version_id: int, package_id: int) -> str:
    return hmac.new(SECRET, f"runtime:{server_version_id}:{package_id}".encode(), hashlib.sha256).hexdigest()


def candidates() -> list[sqlite3.Row]:
    with connect() as db:
        return db.execute(
            """SELECT p.id package_id,p.server_version_id,sv.server_identifier,sv.server_version,
                      p.identifier package_identifier,p.version package_version
               FROM packages p JOIN server_versions sv ON sv.id=p.server_version_id
               WHERE p.registry_type='npm' AND p.transport='stdio' AND p.version IS NOT NULL
               ORDER BY sv.server_identifier,sv.server_version DESC LIMIT 500"""
        ).fetchall()


def runs() -> list[sqlite3.Row]:
    with connect() as db:
        try:
            return db.execute(
                """SELECT r.id,r.status,r.artifact_sha256,r.inventory_sha256,r.started_at,r.completed_at,
                          sv.server_identifier,sv.server_version,p.identifier package_identifier,
                          (SELECT count(*) FROM runtime_observation_tools t WHERE t.run_id=r.id) tool_count
                   FROM runtime_observation_runs r
                   JOIN server_versions sv ON sv.id=r.server_version_id
                   JOIN packages p ON p.id=r.package_id ORDER BY r.id DESC LIMIT 100"""
            ).fetchall()
        except sqlite3.OperationalError:
            return []


def resolve(server_version_id: int, package_id: int) -> sqlite3.Row:
    with connect() as db:
        row = db.execute(
            """SELECT p.id package_id,p.server_version_id,sv.server_identifier,sv.server_version,
                      p.identifier package_identifier,p.version package_version,p.registry_type,p.transport
               FROM packages p JOIN server_versions sv ON sv.id=p.server_version_id
               WHERE p.id=? AND p.server_version_id=?""",
            (package_id, server_version_id),
        ).fetchone()
    if row is None or row["registry_type"] != "npm" or row["transport"] != "stdio" or not row["package_version"]:
        raise ValueError("selection is not an exact npm stdio package")
    return row


def page(message: str = "") -> bytes:
    forms = []
    for row in candidates():
        forms.append(
            "<tr><td>" + escape(row["server_identifier"]) + "</td><td>" + escape(row["server_version"]) +
            "</td><td>" + escape(row["package_identifier"]) + "@" + escape(row["package_version"]) +
            "</td><td><form method='post' action='/runtime-observations'>"
            f"<input type='hidden' name='server_version_id' value='{row['server_version_id']}'>"
            f"<input type='hidden' name='package_id' value='{row['package_id']}'>"
            f"<input type='hidden' name='csrf_token' value='{token(row['server_version_id'], row['package_id'])}'>"
            "<button type='submit'>Run discovery</button></form></td></tr>"
        )
    history = []
    for row in runs():
        history.append(
            f"<tr><td>{row['id']}</td><td>{escape(row['status'])}</td>"
            f"<td>{escape(row['server_identifier'])}@{escape(row['server_version'])}</td>"
            f"<td>{escape(row['package_identifier'])}</td><td>{row['tool_count']}</td>"
            f"<td><code>{escape((row['inventory_sha256'] or '')[:16])}</code></td></tr>"
        )
    html = f"""<!doctype html><meta charset='utf-8'><title>Runtime discovery</title>
<style>body{{font-family:system-ui;max-width:1200px;margin:2rem auto}}table{{border-collapse:collapse;width:100%}}td,th{{padding:.45rem;border-bottom:1px solid #ddd;text-align:left}}code{{font-size:.85em}}.message{{padding:.8rem;background:#eef}}</style>
<h1>MCP runtime discovery v1</h1><p>Discovery only: install scripts disabled, install and runtime offline, no tools invoked.</p>
{('<p class="message">'+escape(message)+'</p>') if message else ''}
<h2>Recent observations</h2><table><tr><th>ID</th><th>Status</th><th>Server</th><th>Package</th><th>Tools</th><th>Inventory</th></tr>{''.join(history)}</table>
<h2>Eligible exact npm stdio packages</h2><table><tr><th>Server</th><th>Version</th><th>Package</th><th></th></tr>{''.join(forms)}</table>"""
    return html.encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != "/":
            self.send_error(404)
            return
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
        body = page(); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if urlsplit(self.path).path != "/runtime-observations":
            self.send_error(404); return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 4096:
            self.send_error(400); return
        form = parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
        try:
            server_version_id = int(form["server_version_id"][0]); package_id = int(form["package_id"][0])
            supplied = form["csrf_token"][0]
            if not hmac.compare_digest(supplied, token(server_version_id, package_id)):
                raise ValueError("invalid request token")
            row = resolve(server_version_id, package_id)
            argv = [
                "python3", str(RUNNER), "observe", "--database", str(DATABASE),
                "--server", row["server_identifier"], "--version", row["server_version"],
                "--package", row["package_identifier"], "--guard-binary", str(GUARD),
                "--evidence-root", str(EVIDENCE), "--runtime-image", IMAGE,
                "--timeout", str(TIMEOUT),
            ]
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=TIMEOUT + 60, check=False,
                                    start_new_session=True, env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"})
            detail = result.stdout.decode("utf-8", "replace") if result.returncode == 0 else result.stderr.decode("utf-8", "replace")
            message = detail[-2000:]
            status = HTTPStatus.OK if result.returncode == 0 else HTTPStatus.BAD_REQUEST
        except (KeyError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
            status, message = HTTPStatus.BAD_REQUEST, str(exc)
        body = page(message)
        self.send_response(status); self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print("[runtime-portal] " + fmt % args)


def main() -> None:
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
