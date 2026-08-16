"""Small compatibility hardening for post-Storage-v2 portal fixes.

Keep this layer narrow: it repairs compatibility regressions found by CI without
changing the authoritative catalog/read-model semantics introduced by
``post_v2_bugfixes``.
"""

from __future__ import annotations

from html import escape
from typing import Any


def apply_post_v2_hardening() -> None:
    """Install compatibility repairs after the post-v2 bug-fix layer."""
    from . import post_v2_bugfixes, views

    if getattr(post_v2_bugfixes.servers_scope_page, "_post_v2_hardened", False):
        return

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
