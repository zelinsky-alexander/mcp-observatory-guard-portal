"""Post-Storage-v2 public portal correctness and UX fixes.

This module is intentionally applied last.  It fixes public-read behavior after
all legacy compatibility, Storage v2, and public-intelligence wrappers are in
place, without changing authoritative Observatory state.
"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit


PROVENANCE_HTML = (
    '<section class="provenance-notice">'
    '<strong>Catalog source:</strong> Official MCP Registry, collected through '
    'the official Registry REST API. MCPLA independently preserves publication '
    'history and derives analysis, coverage, drift, and assurance observations '
    'from those records and referenced artifacts.'
    '</section>'
)


def apply_post_v2_bugfixes() -> None:
    """Install the first post-v2 bug-fix batch exactly once."""
    from . import app, public_ui, views
    from .catalog import Catalog

    if getattr(app.PortalHandler._dispatch, "_post_v2_bugfixes", False):
        return

    # Issue #14: Storage v2 keeps review aggregates in hot storage while the
    # full finding rows live in history.  The review queue must use that bounded
    # history reader just like server/analysis detail does.
    original_unreviewed = Catalog.unreviewed_high_or_critical_findings

    def unreviewed_high_or_critical_findings(
        self: Any, *, page: int, page_size: int
    ) -> dict[str, Any]:
        history_path = getattr(self, "_storage_v2_history_path", None)
        if history_path is None:
            return original_unreviewed(self, page=page, page_size=page_size)

        detail = Catalog(Path(history_path))
        detail._storage_v2_history_path = None
        return original_unreviewed(detail, page=page, page_size=page_size)

    Catalog.unreviewed_high_or_critical_findings = (
        unreviewed_high_or_critical_findings
    )

    # Issue #20: provenance is a site-wide public boundary, not only an About
    # page detail.  Apply after public_ui has already wrapped layout/navigation.
    original_layout: Callable[..., str] = views.layout

    def provenance_layout(
        title: str,
        body: str,
        *,
        public_readonly: bool = False,
    ) -> str:
        html = original_layout(title, body, public_readonly=public_readonly)
        html = html.replace(
            '</head>',
            '<link rel="stylesheet" href="/static/post-v2.css"></head>',
            1,
        )
        return html.replace("<main>", "<main>" + PROVENANCE_HTML, 1)

    views.layout = provenance_layout

    # Issue #19: make the primary dashboard inventory counters actual entry
    # points.  Existing review-card behavior is preserved.
    original_card = views._card
    dashboard_targets = {
        "Servers": "/servers?scope=all",
        "Immutable records": "/records",
        "Completed analyses": "/analyses?status=completed",
    }

    def dashboard_card(
        label: str,
        value: Any,
        detail: str,
        detail_href: str | None = None,
    ) -> str:
        href = detail_href or dashboard_targets.get(label)
        rendered = original_card(label, value, detail, href)
        if href is None:
            return rendered
        # Keep the existing escaped card contents and make a large, accessible
        # card-level target without JavaScript.
        return (
            '<a class="card-shell-link" href="'
            + escape(href, quote=True)
            + '">'
            + rendered
            + "</a>"
        )

    views._card = dashboard_card

    # Issue #18: until runtime discovery is actually publishing observations,
    # it is a planned capability rather than a misleading 0.0% operational KPI.
    # Human-review coverage remains private until a public review workflow is
    # deliberately enabled.
    public_ui.coverage_page = coverage_page

    original_dispatch = app.PortalHandler._dispatch

    def dispatch(self: Any, *, include_body: bool) -> None:
        target = urlsplit(self.path)
        if target.path == "/static/post-v2.css":
            css = Path(__file__).with_name("post_v2.css").read_bytes()
            self._send_bytes(
                200,
                css,
                "text/css; charset=utf-8",
                include_body=include_body,
                extra_headers={"Cache-Control": "public, max-age=300"},
            )
            return
        original_dispatch(self, include_body=include_body)

    setattr(dispatch, "_post_v2_bugfixes", True)
    app.PortalHandler._dispatch = dispatch


def coverage_page(data: dict[str, Any], *, public_readonly: bool = False) -> str:
    """Render operational and planned coverage states without conflating them."""
    from . import views

    total = int(data.get("package_records", 0))
    eligible = int(data.get("eligible_package_records", 0))
    analyzed = int(data.get("analyzed_package_records", 0))
    failed = int(data.get("failed_package_records", 0))
    unsupported = int(data.get("unsupported_or_unresolvable_package_records", 0))
    never = int(data.get("never_analyzed_package_records", 0))
    unique_artifacts = int(data.get("unique_artifacts_analyzed", 0))
    static_percent = _percent(analyzed, eligible)

    runtime = data.get("runtime_discovery") or {}
    runtime_completed = int(runtime.get("completed", 0))
    runtime_eligible = int(runtime.get("eligible", 0))
    runtime_operational = bool(runtime.get("available")) and runtime_completed > 0

    if runtime_operational:
        runtime_value = f"{_percent(runtime_completed, runtime_eligible):.1f}%"
        runtime_detail = (
            f"{runtime_completed:,} of {runtime_eligible:,} eligible npm stdio records"
        )
    else:
        runtime_value = "Planned next"
        runtime_detail = (
            "Automatic Native Guard runtime observation is not yet enabled."
        )

    static_cards = "".join(
        _linked_card(label, value, detail, href)
        for label, value, detail, href in (
            (
                "Eligible package records",
                f"{eligible:,}",
                "Supported registry with an exact version",
                "/coverage/records?state=eligible",
            ),
            (
                "Successfully analyzed",
                f"{analyzed:,}",
                f"{static_percent:.1f}% of eligible records",
                "/coverage/records?state=completed",
            ),
            (
                "Failed attempts",
                f"{failed:,}",
                "Current profile; no compatible completion",
                "/coverage/records?state=failed",
            ),
            (
                "Unsupported / unresolvable",
                f"{unsupported:,}",
                "Not currently schedulable",
                "/coverage/records?state=unsupported",
            ),
            (
                "Never attempted",
                f"{never:,}",
                "Eligible and not yet selected",
                "/coverage/records?state=never",
            ),
            (
                "Unique artifacts analyzed",
                f"{unique_artifacts:,}",
                "Distinct completed artifact SHA-256 values",
                None,
            ),
        )
    )

    assurance_cards = (
        _linked_card(
            "Static artifact coverage",
            f"{static_percent:.1f}%",
            f"{analyzed:,} of {eligible:,} eligible records",
            "/coverage/records?state=completed",
        )
        + _linked_card(
            "Runtime discovery",
            runtime_value,
            runtime_detail,
            None,
        )
        + _linked_card(
            "Controlled behavioral analysis",
            "Planned later",
            "MCP tool invocation and host-effect observation are not part of the current pipeline.",
            None,
        )
    )

    review_note = ""
    if not public_readonly:
        review = data.get("human_review") or {}
        reviewed = int(review.get("reviewed", 0))
        findings = int(review.get("total", 0))
        review_note = _linked_card(
            "Human-review coverage",
            f"{_percent(reviewed, findings):.1f}%" if findings else "Not started",
            f"{reviewed:,} of {findings:,} findings have a disposition",
            None,
        )

    body = f"""<section class="page-heading"><p class="eyebrow">Assurance reach</p><h1>Coverage</h1><p>Operational coverage and planned assurance layers are reported separately.</p></section>
<section class="panel"><div class="panel-heading"><div><p class="eyebrow">Current baseline</p><h2>Static artifact coverage</h2></div></div><section class="cards">{static_cards}</section><p><strong>{analyzed:,}</strong> of <strong>{eligible:,}</strong> eligible package records are covered by the current static-analysis profile.</p><progress value="{analyzed}" max="{max(eligible, 1)}">{static_percent:.1f}%</progress><p class="meta">{static_percent:.1f}% static artifact coverage · {total:,} total package records</p></section>
<section class="panel"><div class="panel-heading"><div><p class="eyebrow">Assurance roadmap</p><h2>Coverage layers</h2></div></div><section class="cards">{assurance_cards}{review_note}</section></section>
<section class="notice"><strong>Status boundary:</strong> planned runtime or behavioral capabilities are not reported as failed coverage. Static completion records observable properties of an exact artifact and is not a safety certification.</section>"""
    return views.layout("Coverage", body, public_readonly=public_readonly)


def _linked_card(
    label: str,
    value: str,
    detail: str,
    href: str | None,
) -> str:
    content = (
        f'<article class="card"><span>{escape(label)}</span>'
        f'<strong>{escape(value)}</strong><small>{escape(detail)}</small></article>'
    )
    if href is None:
        return content
    return (
        f'<a class="card-shell-link" href="{escape(href, quote=True)}">'
        f"{content}</a>"
    )


def _percent(part: int, whole: int) -> float:
    return 0.0 if whole <= 0 else part * 100.0 / whole
