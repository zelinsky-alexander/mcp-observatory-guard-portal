"""Open MCP Longitudinal Assurance Project portal."""

from http import HTTPStatus
from typing import Any

from .about_methodology import apply_about_methodology
from .branding import apply_branding
from .coverage_query_v2 import apply_coverage_query_v2
from .coverage_v2 import apply_coverage_v2
from .coverage_view_compat import apply_coverage_view_compat
from .performance_hotfix import apply_performance_hotfix
from .post_v2_bugfixes import apply_post_v2_bugfixes
from .post_v2_hardening import apply_post_v2_hardening
from .public_ui import install_public_intelligence_ui
from .storage_v2_compat import apply_storage_v2_compat
from .storage_v2_read_model import apply_storage_v2_read_model

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
apply_performance_hotfix()
# Storage v2 deliberately applies after the legacy coverage/performance patches:
# it keeps the latest-snapshot server search while replacing dashboard/coverage
# aggregation with compact materialized summaries when the v2 tables are present.
apply_storage_v2_read_model()
apply_storage_v2_compat()
install_public_intelligence_ui()
# Post-v2 fixes intentionally run last so they see the final public route/read
# model and can correct public semantics without changing authoritative state.
apply_post_v2_bugfixes()
# Compatibility hardening is deliberately the final layer. It may repair view
# regressions discovered by CI, but it must not alter catalog/query semantics.
apply_post_v2_hardening()
