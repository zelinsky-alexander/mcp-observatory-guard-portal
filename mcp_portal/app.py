"""Dependency-free read-only HTTP portal for MCP Observatory data."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
from urllib.parse import parse_qs, unquote, urlsplit

from .catalog import Catalog, CatalogError
from .config import Config, ConfigurationError
from .views import (
    analysis_detail_page,
    dashboard_page,
    error_page,
    server_detail_page,
    servers_page,
)


class PortalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], catalog: Catalog, page_size: int):
        self.catalog = catalog
        self.page_size = page_size
        super().__init__(address, PortalHandler)


class PortalHandler(BaseHTTPRequestHandler):
    server: PortalServer
    server_version = "McpAssurancePortal/0.1"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802 - standard library handler contract
        self._dispatch(include_body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch(include_body=False)

    def do_POST(self) -> None:  # noqa: N802
        self._send_html(
            HTTPStatus.METHOD_NOT_ALLOWED,
            error_page(405, "Read-only portal", "This milestone accepts GET and HEAD requests only."),
            include_body=True,
            extra_headers={"Allow": "GET, HEAD"},
        )

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("[portal] " + (format % args) + "\n")

    def _dispatch(self, *, include_body: bool) -> None:
        target = urlsplit(self.path)
        try:
            if target.path == "/":
                self._send_html(
                    HTTPStatus.OK,
                    dashboard_page(self.server.catalog.dashboard()),
                    include_body=include_body,
                )
                return
            if target.path == "/servers":
                parameters = parse_qs(target.query, keep_blank_values=True)
                query = parameters.get("q", [""])[0]
                page = _positive_integer(parameters.get("page", ["1"])[0], fallback=1)
                result = self.server.catalog.search_servers(
                    query,
                    page=page,
                    page_size=self.server.page_size,
                )
                self._send_html(HTTPStatus.OK, servers_page(result), include_body=include_body)
                return
            if target.path.startswith("/servers/"):
                identifier = unquote(target.path[len("/servers/") :])
                detail = self.server.catalog.server_detail(identifier)
                if detail is None:
                    self._not_found(include_body)
                    return
                self._send_html(
                    HTTPStatus.OK,
                    server_detail_page(detail),
                    include_body=include_body,
                )
                return
            if target.path.startswith("/analyses/"):
                raw_id = target.path[len("/analyses/") :]
                analysis_id = _positive_integer(raw_id, fallback=0)
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
        except CatalogError as exc:
            self._send_html(
                HTTPStatus.SERVICE_UNAVAILABLE,
                error_page(503, "Catalog unavailable", str(exc)),
                include_body=include_body,
            )
        except (OSError, ValueError) as exc:
            self._send_html(
                HTTPStatus.BAD_REQUEST,
                error_page(400, "Invalid request", str(exc)),
                include_body=include_body,
            )

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
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if extra_headers:
            for name, value in extra_headers.items():
                self.send_header(name, value)
        self.end_headers()
        if include_body:
            self.wfile.write(body)


def create_server(config: Config) -> PortalServer:
    catalog = Catalog(config.database_path)
    catalog.schema_status()
    return PortalServer((config.host, config.port), catalog, config.page_size)


def main() -> int:
    try:
        config = Config.from_env()
        server = create_server(config)
    except (ConfigurationError, CatalogError, OSError) as exc:
        print(f"portal startup failed: {exc}", file=sys.stderr)
        return 2

    print(
        f"MCP assurance portal listening on http://{config.host}:{server.server_port} "
        f"using read-only catalog {config.database_path}",
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
