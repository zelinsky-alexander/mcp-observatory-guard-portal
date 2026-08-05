"""Open MCP Longitudinal Assurance Project portal."""

from http import HTTPStatus
from typing import Any

from .about_methodology import apply_about_methodology
from .branding import apply_branding
from .coverage_query_v2 import apply_coverage_query_v2
from .coverage_v2 import apply_coverage_v2
from .coverage_view_compat import apply_coverage_view_compat
from .public_ui import install_public_intelligence_ui

__version__ = "0.1.0"


def _accept_integer_http_statuses() -> None:
    """Normalize extension-route integer statuses to ``HTTPStatus`` values.

    The core response helpers use ``HTTPStatus`` and access ``status.value``.
    Public intelligence routes historically passed integer status codes. Keep
    the boundary strict while accepting those route values in one place.
    """
    from . import app

    original = app.PortalHandler._send_html
    if getattr(original, "_accepts_integer_http_statuses", False):
        return

    def send_html(
        self: Any,
        status: HTTPStatus | int,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        normalized = status if isinstance(status, HTTPStatus) else HTTPStatus(status)
        return original(self, normalized, *args, **kwargs)

    setattr(send_html, "_accepts_integer_http_statuses", True)
    app.PortalHandler._send_html = send_html


apply_branding()
_accept_integer_http_statuses()
apply_coverage_v2()
apply_coverage_query_v2()
apply_coverage_view_compat()
apply_about_methodology()
install_public_intelligence_ui()
