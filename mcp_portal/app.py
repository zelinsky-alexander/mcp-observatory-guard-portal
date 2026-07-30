"""Dependency-free portal with a constrained local static-analysis queue."""

from __future__ import annotations

import hashlib
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import sys
from urllib.parse import parse_qs, unquote, urlsplit

from .analysis_catalog import AnalysisSelectionError, resolve_candidate
from .catalog import Catalog, CatalogError
from .config import Config, ConfigurationError
from .jobs import JobStore, JobStoreError
from .views import (
    analysis_detail_page,
    dashboard_page,
    ecosystem_report_page,
    error_page,
    job_detail_page,
    jobs_page,
    server_detail_page,
    servers_page,
)


class PortalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], config: Config, catalog: Catalog):
        self.config = config
        self.catalog = catalog
        self.page_size = config.page_size
        self.jobs = JobStore(config.analysis.jobs_database_path) if config.analysis else None
        self.csrf_secret = secrets.token_bytes(32)
        super().__init__(address, PortalHandler)

    def csrf_token(self, server_version_id: int, package_id: int) -> str:
        message = f"analysis:{server_version_id}:{package_id}".encode("ascii")
        return hmac.new(self.csrf_secret, message, hashlib.sha256).hexdigest()


class PortalHandler(BaseHTTPRequestHandler):
    server: PortalServer
    server_version = "McpAssurancePortal/0.2"
    sys_version = ""
    maximum_form_bytes = 4096

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch(include_body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch(include_body=False)

    def do_POST(self) -> None:  # noqa: N802
        target = urlsplit(self.path)
        if target.path != "/analysis-requests" or self.server.jobs is None:
            self._send_html(
                HTTPStatus.METHOD_NOT_ALLOWED,
                error_page(405, "Method not allowed", "This endpoint is not enabled."),
                include_body=True,
                extra_headers={"Allow": "GET, HEAD"},
            )
            return
        try:
            self._validate_same_origin()
            form = self._read_form()
            server_version_id = _positive_integer(_one(form, "server_version_id"), fallback=0)
            package_id = _positive_integer(_one(form, "package_id"), fallback=0)
            supplied_token = _one(form, "csrf_token")
            expected_token = self.server.csrf_token(server_version_id, package_id)
            if not hmac.compare_digest(supplied_token, expected_token):
                self._send_html(
                    HTTPStatus.FORBIDDEN,
                    error_page(403, "Request rejected", "The analysis request token is invalid."),
                    include_body=True,
                )
                return
            candidate = resolve_candidate(
                self.server.config.database_path, server_version_id, package_id
            )
            job, _created = self.server.jobs.enqueue(candidate)
            self._redirect(f"/jobs/{job['id']}")
        except (AnalysisSelectionError, JobStoreError, OSError, ValueError) as exc:
            self._send_html(
                HTTPStatus.BAD_REQUEST,
                error_page(400, "Analysis request rejected", str(exc)),
                include_body=True,
            )

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("[portal] " + (format % args) + "\n")

    def _dispatch(self, *, include_body: bool) -> None:
        target = urlsplit(self.path)
        try:
            if target.path == "/":
                data = self.server.catalog.dashboard()
                if self.server.jobs is not None:
                    data["portal_jobs"] = self.server.jobs.summary()
                self._send_html(
                    HTTPStatus.OK, dashboard_page(data), include_body=include_body
                )
                return
            if target.path == "/reports/ecosystems":
                rows = self.server.catalog.ecosystem_summary()
                self._send_html(
                    HTTPStatus.OK,
                    ecosystem_report_page(rows),
                    include_body=include_body,
                )
                return
            if target.path == "/servers":
                parameters = parse_qs(target.query, keep_blank_values=True)
                query = parameters.get("q", [""])[0]
                ecosystem = parameters.get("ecosystem", [""])[0]
                page = _positive_integer(parameters.get("page", ["1"])[0], fallback=1)
                result = self.server.catalog.search_servers(
                    query,
                    page=page,
                    page_size=self.server.page_size,
                    ecosystem=ecosystem,
                )
                self._send_html(
                    HTTPStatus.OK, servers_page(result), include_body=include_body
                )
                return
            if target.path.startswith("/servers/"):
                identifier = unquote(target.path[len("/servers/") :])
                detail = self.server.catalog.server_detail(identifier)
                if detail is None:
                    self._not_found(include_body)
                    return
                self._decorate_analysis_actions(detail)
                self._send_html(
                    HTTPStatus.OK,
                    server_detail_page(detail),
                    include_body=include_body,
                )
                return
            if target.path.startswith("/analyses/"):
                analysis_id = _positive_integer(
                    target.path[len("/analyses/") :], fallback=0
                )
                detail = self.server.catalog.analysis_detail(analysis_id)
                if detail is None:
                    self._not_found(include_body)
                    return
                self._send_html(
                    HTTPStatus.OK,
                    analysis_detail_page(detail),
                    include_body=include_body,
                )
                return
            if target.path == "/jobs":
                summary = None if self.server.jobs is None else self.server.jobs.summary()
                self._send_html(
                    HTTPStatus.OK, jobs_page(summary), include_body=include_body
                )
                return
            if target.path.startswith("/jobs/"):
                if self.server.jobs is None:
                    self._not_found(include_body)
                    return
                job_id = _positive_integer(target.path[len("/jobs/") :], fallback=0)
                job = self.server.jobs.get(job_id)
                if job is None:
                    self._not_found(include_body)
                    return
                self._send_html(
                    HTTPStatus.OK, job_detail_page(job), include_body=include_body
                )
                return
            if target.path == "/healthz":
                self.server.catalog.schema_status()
                self._send_bytes(
                    HTTPStatus.OK,
                    b"ok\n",
                    "text/plain; charset=utf-8",
                    include_body=include_body,
                )
                return
            if target.path == "/static/portal.css":
                css = Path(__file__).with_name("portal.css").read_bytes()
                self._send_bytes(
                    HTTPStatus.OK,
                    css,
                    "text/css; charset=utf-8",
                    include_body=include_body,
                    extra_headers={"Cache-Control": "public, max-age=300"},
                )
                return
            self._not_found(include_body)
        except (CatalogError, JobStoreError) as exc:
            self._send_html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(503, "Portal data unavailable", str(exc)),
                include_body=include_body,
            )
        except (OSError, ValueError) as exc:
            self._send_html(
                HTTPStatus.BAD_REQUEST,
                error_page(400, "Invalid request", str(exc)),
                include_body=include_body,
            )

    def _decorate_analysis_actions(self, detail: dict[str, object]) -> None:
        for version in detail["versions"]:  # type: ignore[index]
            version_id = int(version["id"])
            for package in version["packages"]:
                if self.server.jobs is None:
                    package["analysis_unavailable_reason"] = "on-demand analysis is disabled"
                elif package["registry_type"] != "npm":
                    package["analysis_unavailable_reason"] = "only npm packages are supported"
                elif not package.get("version"):
                    package["analysis_unavailable_reason"] = "no exact package version is declared"
                else:
                    package["analysis_request"] = {
                        "server_version_id": version_id,
                        "package_id": int(package["id"]),
                        "csrf_token": self.server.csrf_token(version_id, int(package["id"])),
                    }

    def _read_form(self) -> dict[str, list[str]]:
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/x-www-form-urlencoded"):
            raise ValueError("Content-Type must be application/x-www-form-urlencoded")
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        try:
            length = int(raw_length, 10)
        except ValueError as exc:
            raise ValueError("Content-Length must be a decimal integer") from exc
        if length < 0 or length > self.maximum_form_bytes:
            raise ValueError("analysis request body is too large")
        body = self.rfile.read(length)
        return parse_qs(body.decode("utf-8", errors="strict"), keep_blank_values=True)

    def _validate_same_origin(self) -> None:
        """Reject browser requests explicitly identified as cross-site.

        Analysis-enabled mode is restricted to a loopback bind, and every
        submission also requires a per-package HMAC CSRF token. Comparing the
        browser Origin header to the server address is intentionally avoided
        because Windows-to-WSL localhost forwarding can represent equivalent
        loopback origins differently.
        """
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site and fetch_site not in {"same-origin", "none"}:
            raise ValueError("cross-site analysis requests are not accepted")

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER.value)
        self._security_headers()
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _not_found(self, include_body: bool) -> None:
        self._send_html(
            HTTPStatus.NOT_FOUND,
            error_page(404, "Not found", "The requested portal resource does not exist."),
            include_body=include_body,
        )

    def _send_html(
        self,
        status: HTTPStatus,
        html: str,
        *,
        include_body: bool,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._send_bytes(
            status,
            html.encode("utf-8"),
            "text/html; charset=utf-8",
            include_body=include_body,
            extra_headers=extra_headers,
        )

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        include_body: bool,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status.value)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        if include_body:
            self.wfile.write(body)

    def _security_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'self'; img-src 'self'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        self.send_header("Cache-Control", "no-store")


def create_server(config: Config) -> PortalServer:
    catalog = Catalog(config.database_path)
    catalog.schema_status()
    return PortalServer((config.host, config.port), config, catalog)


def main() -> int:
    try:
        config = Config.from_env()
        server = create_server(config)
    except (ConfigurationError, CatalogError, JobStoreError, OSError) as exc:
        print(f"portal startup failed: {exc}", file=sys.stderr)
        return 2
    mode = "analysis-enabled" if config.analysis else "read-only"
    print(
        f"MCP assurance portal listening on http://{config.host}:{server.server_port} "
        f"using catalog {config.database_path} mode={mode}",
        file=sys.stderr,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("portal interrupted", file=sys.stderr)
    finally:
        server.server_close()
    return 0


def _positive_integer(raw: str, *, fallback: int) -> int:
    try:
        value = int(raw, 10)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def _one(form: dict[str, list[str]], name: str) -> str:
    values = form.get(name)
    if values is None or len(values) != 1:
        raise ValueError(f"form field {name} must occur exactly once")
    return values[0]
