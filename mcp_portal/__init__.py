"""Open MCP Longitudinal Assurance Project portal."""

from http import HTTPStatus
from typing import Any

from .about_methodology import apply_about_methodology
from .branding import apply_branding
from .coverage_query_v2 import apply_coverage_query_v2
from .coverage_v2 import apply_coverage_v2
from .coverage_view_compat import apply_coverage_view_compat
from .hot_list_performance import apply_hot_list_performance
from .performance_hotfix import apply_performance_hotfix
from .post_v2_bugfixes import apply_post_v2_bugfixes
from .post_v2_hardening import apply_post_v2_hardening
from .post_v2_visual_fixes import apply_post_v2_visual_fixes
from .public_ui import install_public_intelligence_ui
from .remote_runtime_coverage_v1 import apply_remote_runtime_coverage_v1
from .review_queue_performance import apply_review_queue_performance
from .runtime_coverage_v1 import apply_runtime_coverage_v1
from .runtime_outcomes_v2 import apply_runtime_outcomes_v2
from .storage_v2_compat import apply_storage_v2_compat
from .storage_v2_read_model import apply_storage_v2_read_model

__version__ = "0.1.0"


def _accept_integer_http_statuses() -> None:
    """Normalize extension-route integer statuses to ``HTTPStatus`` values."""
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
apply_storage_v2_read_model()
apply_storage_v2_compat()
install_public_intelligence_ui()
apply_post_v2_bugfixes()
apply_post_v2_hardening()
apply_hot_list_performance()
apply_review_queue_performance()
apply_post_v2_visual_fixes()
apply_runtime_coverage_v1()
apply_runtime_outcomes_v2()
# Declared-remote coverage is the final read-only layer. It exposes only already
# published scheduler/observation summaries and cannot start probes from HTTP.
apply_remote_runtime_coverage_v1()
