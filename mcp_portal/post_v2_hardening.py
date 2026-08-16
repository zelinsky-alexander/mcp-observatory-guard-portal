"""Small compatibility hardening for post-Storage-v2 portal fixes.

Keep this layer narrow: it repairs compatibility regressions found by CI without
changing authoritative Observatory state.
"""

from __future__ import annotations

from html import escape
from typing import Any


def apply_post_v2_hardening() -> None:
    """Install compatibility repairs after the post-v2 bug-fix layer."""
    from . import post_v2_bugfixes, views
    from .catalog import Catalog

    if getattr(post_v2_bugfixes.servers_scope_page, "_post_v2_hardened", False):
        return

    # Dashboard inventory cards now drill into longitudinal views. Keep their
    # counts longitudinal too, even for non-v2/local fixtures, so card and list
    # semantics cannot disagree.
    original_dashboard = Catalog.dashboard

    def dashboard(self: Any, *, recent_limit: int = 12) -> dict[str, Any]:
        result = original_dashboard(self, recent_limit=recent_limit)
        with self._connect() as connection:
            counted = connection.execute(
                """SELECT COUNT(DISTINCT server_identifier) AS servers,
                          COUNT(*) AS immutable_versions,
                          COUNT(DISTINCT canonical_sha256) AS canonical_artifacts
                   FROM server_versions"""
            ).fetchone()
        if counted is not None:
            result["totals"].update(
                {
                    "servers": int(counted["servers"] or 0),
                    "immutable_versions": int(counted["immutable_versions"] or 0),
                    "canonical_artifacts": int(counted["canonical_artifacts"] or 0),
                }
            )
        return result

    Catalog.dashboard = dashboard

    # The first post-v2 implementation wrapped a card that could already contain
    # a detail link, producing invalid nested anchors. Render one large link
    # instead so the entire KPI card is a valid mobile-sized target.
    dashboard_targets = {
        "Servers": "/servers?scope=all",
        "Immutable records": "/records",
        "Completed analyses": "/analyses?status=completed",
    }

    def card(
        label: str,
        value: Any,
        detail: str,
        detail_href: str | None = None,
    ) -> str:
        href = detail_href or dashboard_targets.get(label)
        content = (
            '<article class="card">'
            f'<span>{escape(str(label))}</span>'
            f'<strong>{escape(str(value))}</strong>'
            f'<small>{escape(str(detail))}</small>'
            '</article>'
        )
        if href is None:
            return content
        return (
            f'<a class="card-shell-link" href="{escape(href, quote=True)}">'
            f'{content}</a>'
        )

    views._card = card

    original_servers_scope_page = post_v2_bugfixes.servers_scope_page

    def servers_scope_page(
        result: dict[str, Any], *, public_readonly: bool = False
    ) -> str:
        html = original_servers_scope_page(
            result, public_readonly=public_readonly
        )
        ecosystem = str(result.get("ecosystem") or "")
        if not ecosystem:
            return html

        scope = str(result.get("scope") or "current")
        clear_href = f"/servers?scope={escape(scope, quote=True)}"
        snapshot_id = result.get("snapshot_id")
        if scope == "snapshot" and snapshot_id:
            clear_href += f"&amp;snapshot={int(snapshot_id)}"

        marker = '<div class="result-summary">'
        replacement = (
            '<div class="filter-summary">'
            f'Ecosystem <code>{escape(ecosystem)}</code> · '
            f'<a href="{clear_href}">Clear ecosystem filter</a>'
            '</div>'
            + marker
        )
        return html.replace(marker, replacement, 1)

    setattr(servers_scope_page, "_post_v2_hardened", True)
    post_v2_bugfixes.servers_scope_page = servers_scope_page
