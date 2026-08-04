"""Public catalog-intelligence routes and server-rendered views.

This module keeps the new public pages isolated from the local analysis and job
orchestration paths. It reads only through :class:`PublicIntelligence` and
installs a narrow GET/HEAD routing wrapper around the existing portal handler.
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from math import ceil
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from .public_intelligence import PublicIntelligence, PublicIntelligenceError


def install_public_intelligence_ui() -> None:
    """Install public intelligence navigation, dashboard cards, and routes once."""
    from . import app, views

    if getattr(app.PortalHandler._dispatch, "_public_intelligence_ui", False):
        return

    original_layout: Callable[..., str] = views.layout

    def intelligence_layout(
        title: str,
        body: str,
        *,
        public_readonly: bool = False,
    ) -> str:
        html = original_layout(title, body, public_readonly=public_readonly)
        old = '<a href="/reports/ecosystems">Ecosystems</a>'
        new = (
            old
            + '<a href="/changes">Changes</a>'
            + '<a href="/snapshots">Snapshots</a>'
            + '<a href="/coverage">Coverage</a>'
        )
        return html.replace(old, new, 1)

    views.layout = intelligence_layout

    original_dispatch = app.PortalHandler._dispatch

    def intelligence_dispatch(self: Any, *, include_body: bool) -> None:
        target = urlsplit(self.path)
        if target.path not in {"/", "/status", "/snapshots", "/changes", "/coverage"}:
            original_dispatch(self, include_body=include_body)
            return

        intelligence = PublicIntelligence(self.server.config.database_path)
        try:
            if target.path == "/":
                data = self.server.catalog.dashboard()
                if self.server.jobs is not None:
                    data["portal_jobs"] = self.server.jobs.summary()
                html = views.dashboard_page(
                    data,
                    public_readonly=self.server.config.public_readonly,
                )
                summary = _dashboard_intelligence(
                    intelligence.refresh_status(),
                    intelligence.latest_changes(kind="added", page=1, page_size=1),
                    intelligence.latest_changes(kind="removed", page=1, page_size=1),
                    intelligence.analysis_coverage(),
                )
                self._send_html(
                    200,
                    _insert_after_cards(html, summary),
                    include_body=include_body,
                )
                return

            if target.path == "/status":
                html = status_page(
                    intelligence.refresh_status(),
                    public_readonly=self.server.config.public_readonly,
                )
            elif target.path == "/snapshots":
                page = _page_parameter(target.query)
                html = snapshots_page(
                    intelligence.snapshot_history(
                        page=page,
                        page_size=self.server.page_size,
                    ),
                    public_readonly=self.server.config.public_readonly,
                )
            elif target.path == "/changes":
                parameters = parse_qs(target.query, keep_blank_values=True)
                kind = parameters.get("kind", ["added"])[0]
                page = _positive_integer(parameters.get("page", ["1"])[0], fallback=1)
                html = changes_page(
                    intelligence.latest_changes(
                        kind=kind,
                        page=page,
                        page_size=self.server.page_size,
                    ),
                    public_readonly=self.server.config.public_readonly,
                )
            else:
                html = coverage_page(
                    intelligence.analysis_coverage(),
                    public_readonly=self.server.config.public_readonly,
                )
            self._send_html(200, html, include_body=include_body)
        except PublicIntelligenceError as exc:
            self._send_html(
                503,
                views.error_page(
                    503,
                    "Catalog intelligence unavailable",
                    str(exc),
                    public_readonly=self.server.config.public_readonly,
                ),
                include_body=include_body,
            )
        except (ValueError, TypeError) as exc:
            self._send_html(
                400,
                views.error_page(
                    400,
                    "Invalid catalog intelligence request",
                    str(exc),
                    public_readonly=self.server.config.public_readonly,
                ),
                include_body=include_body,
            )

    setattr(intelligence_dispatch, "_public_intelligence_ui", True)
    app.PortalHandler._dispatch = intelligence_dispatch


def status_page(data: dict[str, Any], *, public_readonly: bool = False) -> str:
    """Render the latest successfully published catalog snapshot."""
    from . import views

    snapshot = data.get("latest_snapshot")
    if snapshot is None:
        body = (
            '<section class="page-heading"><p class="eyebrow">Published catalog state</p>'
            '<h1>Latest snapshot</h1><p>No published snapshot is available.</p></section>'
        )
        return views.layout("Latest snapshot", body, public_readonly=public_readonly)

    duration = _duration(snapshot.get("started_at"), snapshot.get("completed_at"))
    body = f"""<section class="page-heading"><p class="eyebrow">Published catalog state</p><h1>Latest successfully published snapshot</h1><p>This status is derived only from the immutable catalog. It does not expose worker or runtime state.</p></section>
<section class="cards">{_card('Snapshot', f"#{snapshot['id']}", 'Published catalog generation')}{_card('Completed', _text(snapshot.get('completed_at'), 'Not recorded'), 'Publication timestamp')}{_card('Duration', duration, 'Collection elapsed time')}{_card('Digest', _text(snapshot.get('sha256_prefix'), '—'), 'Bounded SHA-256 prefix')}</section>
<section class="panel"><h2>Collection summary</h2><dl class="facts"><div><dt>Started</dt><dd>{escape(_text(snapshot.get('started_at'), 'Not recorded'))}</dd></div><div><dt>Completed</dt><dd>{escape(_text(snapshot.get('completed_at'), 'Not recorded'))}</dd></div><div><dt>Pages</dt><dd>{int(snapshot.get('pages') or 0):,}</dd></div><div><dt>Records received</dt><dd>{int(snapshot.get('records_received') or 0):,}</dd></div><div><dt>Unique server versions</dt><dd>{int(snapshot.get('unique_server_versions') or 0):,}</dd></div><div><dt>Snapshot digest prefix</dt><dd><code>{escape(_text(snapshot.get('sha256_prefix'), '—'))}</code></dd></div></dl></section>
<section class="notice"><strong>Status boundary:</strong> this page confirms the latest snapshot published in the catalog. It does not claim that a refresh worker is currently healthy or running.</section>"""
    return views.layout("Latest snapshot", body, public_readonly=public_readonly)


def snapshots_page(result: dict[str, Any], *, public_readonly: bool = False) -> str:
    """Render paginated immutable snapshot history."""
    from . import views

    rows = "".join(
        f"<tr><td><strong>#{int(row['id'])}</strong></td><td>{escape(_text(row.get('completed_at'), '—'))}<div class=\"meta\">started {escape(_text(row.get('started_at'), '—'))}</div></td><td>{escape(_duration(row.get('started_at'), row.get('completed_at')))}</td><td>{int(row.get('pages') or 0):,}</td><td>{int(row.get('records_received') or 0):,}</td><td>{int(row.get('unique_server_versions') or 0):,}</td><td><code>{escape(_text(row.get('sha256_prefix'), '—'))}</code></td></tr>"
        for row in result["rows"]
    ) or '<tr><td colspan="7" class="empty">No published snapshots.</td></tr>'
    total_pages = max(1, ceil(result["total"] / result["page_size"]))
    pagination = _pagination("/snapshots", result["page"], total_pages)
    body = f"""<section class="page-heading"><p class="eyebrow">Immutable history</p><h1>Catalog snapshots</h1><p>Content-addressed publication history, newest first.</p></section>
<div class="result-summary">{result['total']:,} snapshots · page {result['page']} of {total_pages}</div>
<section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Snapshot</th><th>Published</th><th>Duration</th><th>Pages</th><th>Records</th><th>Unique versions</th><th>Digest</th></tr></thead><tbody>{rows}</tbody></table></div></section>{pagination}"""
    return views.layout("Catalog snapshots", body, public_readonly=public_readonly)


def changes_page(result: dict[str, Any], *, public_readonly: bool = False) -> str:
    """Render additions or removals between the latest two snapshots."""
    from . import views

    kind = result["kind"]
    rows = "".join(_change_row(row) for row in result["rows"]) or (
        f'<tr><td colspan="6" class="empty">No {escape(kind)} immutable server-version records.</td></tr>'
    )
    total_pages = max(1, ceil(result["total"] / result["page_size"]))
    pagination = _pagination("/changes", result["page"], total_pages, {"kind": kind})
    added_class = " badge" if kind == "added" else ""
    removed_class = " badge" if kind == "removed" else ""
    tabs = (
        f'<nav class="pagination" aria-label="Change kind">'
        f'<a class="{added_class.strip()}" href="/changes?kind=added">Added</a>'
        f'<a class="{removed_class.strip()}" href="/changes?kind=removed">Removed</a></nav>'
    )
    body = f"""<section class="page-heading"><p class="eyebrow">Latest catalog comparison</p><h1>{escape(kind.title())} server versions</h1><p>Exact immutable server-version membership changes between snapshots #{_text(result.get('previous_snapshot_id'), '—')} and #{_text(result.get('latest_snapshot_id'), '—')}.</p></section>{tabs}
<div class="result-summary">{result['total']:,} {escape(kind)} record{'s' if result['total'] != 1 else ''} · page {result['page']} of {total_pages}</div>
<section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Server</th><th>Version</th><th>Status</th><th>Published</th><th>Updated</th><th>Digest</th></tr></thead><tbody>{rows}</tbody></table></div></section>{pagination}
<section class="notice"><strong>Comparison boundary:</strong> these are exact immutable server-version records added to or absent from the latest snapshot. They do not by themselves prove that a project was newly created or permanently deleted.</section>"""
    return views.layout("Catalog changes", body, public_readonly=public_readonly)


def coverage_page(data: dict[str, int], *, public_readonly: bool = False) -> str:
    """Render static-analysis coverage without implying a safety verdict."""
    from . import views

    total = int(data.get("package_records", 0))
    analyzed = int(data.get("analyzed_package_records", 0))
    failed = int(data.get("failed_package_records", 0))
    never = int(data.get("never_analyzed_package_records", 0))
    percent = 0.0 if total == 0 else analyzed * 100.0 / total
    body = f"""<section class="page-heading"><p class="eyebrow">Static-analysis reach</p><h1>Analysis coverage</h1><p>Coverage is counted by exact package record, not by project name or safety status.</p></section>
<section class="cards">{_card('Package records', f'{total:,}', 'Exact catalog package declarations')}{_card('Successfully analyzed', f'{analyzed:,}', f'{percent:.1f}% of package records')}{_card('Failed at least once', f'{failed:,}', 'May overlap completed records')}{_card('Never attempted', f'{never:,}', 'No analysis run recorded')}</section>
<section class="panel"><h2>Coverage interpretation</h2><p><strong>{analyzed:,}</strong> of <strong>{total:,}</strong> exact package records have at least one completed static-analysis run.</p><progress value="{analyzed}" max="{max(total, 1)}">{percent:.1f}%</progress><p class="meta">{percent:.1f}% successfully analyzed</p></section>
<section class="notice"><strong>Counting boundary:</strong> completed and failed counts are not mutually exclusive. A package record may have both a completed run and a failed run. Analysis completion is not a safety certification.</section>"""
    return views.layout("Analysis coverage", body, public_readonly=public_readonly)


def _dashboard_intelligence(
    status: dict[str, Any],
    added: dict[str, Any],
    removed: dict[str, Any],
    coverage: dict[str, int],
) -> str:
    snapshot = status.get("latest_snapshot") or {}
    total = int(coverage.get("package_records", 0))
    analyzed = int(coverage.get("analyzed_package_records", 0))
    percent = 0.0 if total == 0 else analyzed * 100.0 / total
    return f"""<section class="panel"><div class="panel-heading"><div><p class="eyebrow">Catalog intelligence</p><h2>Latest published change summary</h2></div><a href="/status">View publication status →</a></div><section class="cards">{_card('Snapshot', f"#{_text(snapshot.get('id'), '—')}", _text(snapshot.get('completed_at'), 'No published snapshot'), '/snapshots')}{_card('Added', f"{int(added.get('total') or 0):,}", 'Exact server-version records', '/changes?kind=added')}{_card('Removed', f"{int(removed.get('total') or 0):,}", 'Exact server-version records', '/changes?kind=removed')}{_card('Analysis coverage', f'{percent:.1f}%', f'{analyzed:,} of {total:,} package records', '/coverage')}</section></section>"""


def _insert_after_cards(html: str, addition: str) -> str:
    marker = '<section class="cards">'
    start = html.find(marker)
    if start < 0:
        return html.replace("</main>", addition + "</main>", 1)
    end = html.find("</section>", start)
    if end < 0:
        return html.replace("</main>", addition + "</main>", 1)
    end += len("</section>")
    return html[:end] + addition + html[end:]


def _change_row(row: dict[str, Any]) -> str:
    identifier = _text(row.get("server_identifier"))
    href = "/servers/" + quote(identifier, safe="")
    return f"<tr><td><a href=\"{escape(href, quote=True)}\">{escape(identifier)}</a></td><td><code>{escape(_text(row.get('server_version'), '—'))}</code></td><td><span class=\"badge\">{escape(_text(row.get('registry_status'), 'unknown'))}</span></td><td>{escape(_text(row.get('published_at'), '—'))}</td><td>{escape(_text(row.get('updated_at'), '—'))}</td><td><code>{escape(_text(row.get('sha256_prefix'), '—'))}</code></td></tr>"


def _card(label: str, value: str, detail: str, href: str | None = None) -> str:
    detail_html = escape(detail)
    if href is not None:
        detail_html = f'<a href="{escape(href, quote=True)}">{detail_html}</a>'
    return f'<article class="card"><span>{escape(label)}</span><strong>{escape(value)}</strong><small>{detail_html}</small></article>'


def _pagination(
    base: str,
    page: int,
    total_pages: int,
    parameters: dict[str, str] | None = None,
) -> str:
    if total_pages <= 1:
        return ""
    parameters = dict(parameters or {})
    links: list[str] = []
    if page > 1:
        links.append(_page_link(base, page - 1, "← Previous", parameters))
    links.append(f"<span>Page {page} of {total_pages}</span>")
    if page < total_pages:
        links.append(_page_link(base, page + 1, "Next →", parameters))
    return '<nav class="pagination" aria-label="Result pages">' + "".join(links) + "</nav>"


def _page_link(base: str, page: int, label: str, parameters: dict[str, str]) -> str:
    query = dict(parameters)
    query["page"] = str(page)
    href = base + "?" + urlencode(query)
    return f'<a href="{escape(href, quote=True)}">{escape(label)}</a>'


def _page_parameter(query: str) -> int:
    parameters = parse_qs(query, keep_blank_values=True)
    return _positive_integer(parameters.get("page", ["1"])[0], fallback=1)


def _positive_integer(value: str, *, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _duration(started: Any, completed: Any) -> str:
    try:
        start = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "Not available"
    seconds = max(0, int((end - start).total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value)
    return text if text else fallback
