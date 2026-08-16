"""Post-Storage-v2 public portal correctness and UX fixes.

Applied last so the fixes see the final Storage v2/public-intelligence read
model.  Authoritative research state remains owned by Observatory; this module
adds bounded public reads and presentation only.
"""

from __future__ import annotations

from html import escape
from http import HTTPStatus
from math import ceil
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlencode, urlsplit


PROVENANCE_HTML = (
    '<section class="provenance-notice">'
    '<strong>Catalog source:</strong> Official MCP Registry, collected through '
    'the official Registry REST API. MCPLA independently preserves publication '
    'history and derives analysis, coverage, drift, and assurance observations '
    'from those records and referenced artifacts.'
    '</section>'
)


def apply_post_v2_bugfixes() -> None:
    """Install post-v2 bug fixes exactly once."""
    from . import app, public_ui, views
    from .catalog import Catalog

    if getattr(app.PortalHandler._dispatch, "_post_v2_bugfixes", False):
        return

    # #14: aggregate review counts live in the hot v2 summary while individual
    # finding rows live in history.  Route this bounded, paginated detail read
    # to history exactly like server/analysis detail already does.
    original_unreviewed = Catalog.unreviewed_high_or_critical_findings

    def unreviewed_high_or_critical_findings(
        self: Any, *, page: int, page_size: int
    ) -> dict[str, Any]:
        detail = _history_catalog(self)
        if detail is None:
            return original_unreviewed(self, page=page, page_size=page_size)
        return original_unreviewed(detail, page=page, page_size=page_size)

    Catalog.unreviewed_high_or_critical_findings = unreviewed_high_or_critical_findings

    # #20: provenance is a site-wide public boundary, not only an About-page
    # detail.  Apply after public_ui has wrapped layout/navigation.
    original_layout: Callable[..., str] = views.layout

    def provenance_layout(
        title: str,
        body: str,
        *,
        public_readonly: bool = False,
    ) -> str:
        html = original_layout(title, body, public_readonly=public_readonly)
        html = html.replace(
            "</head>",
            '<link rel="stylesheet" href="/static/post-v2.css"></head>',
            1,
        )
        return html.replace("<main>", "<main>" + PROVENANCE_HTML, 1)

    views.layout = provenance_layout

    # #19: primary dashboard counters are navigation entry points.  Existing
    # review-card behavior is preserved.  The whole card becomes a link.
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
        return (
            f'<a class="card-shell-link" href="{escape(href, quote=True)}">'
            f"{rendered}</a>"
        )

    views._card = dashboard_card

    # #18 and #17: correct lifecycle wording and make operational static metrics
    # drillable.  Human-review coverage stays hidden in public-readonly mode.
    public_ui.coverage_page = coverage_page

    # #16: make every snapshot row navigable.
    public_ui.snapshots_page = snapshots_page

    original_dispatch = app.PortalHandler._dispatch

    def dispatch(self: Any, *, include_body: bool) -> None:
        target = urlsplit(self.path)

        if target.path == "/static/post-v2.css":
            css = Path(__file__).with_name("post_v2.css").read_bytes()
            self._send_bytes(
                HTTPStatus.OK,
                css,
                "text/css; charset=utf-8",
                include_body=include_body,
                extra_headers={"Cache-Control": "public, max-age=300"},
            )
            return

        if target.path == "/servers":
            params = parse_qs(target.query, keep_blank_values=True)
            scope = params.get("scope", ["current"])[0] or "current"
            query = params.get("q", [""])[0]
            ecosystem = params.get("ecosystem", [""])[0]
            page = _positive_integer(params.get("page", ["1"])[0], 1)
            snapshot_id = _positive_integer(params.get("snapshot", ["0"])[0], 0)
            try:
                result = _search_servers(
                    self.server.catalog,
                    scope=scope,
                    snapshot_id=snapshot_id,
                    query=query,
                    ecosystem=ecosystem,
                    page=page,
                    page_size=self.server.page_size,
                )
                self._send_html(
                    HTTPStatus.OK,
                    servers_scope_page(
                        result,
                        public_readonly=self.server.config.public_readonly,
                    ),
                    include_body=include_body,
                )
            except ValueError as exc:
                self._send_html(
                    HTTPStatus.BAD_REQUEST,
                    views.error_page(
                        400,
                        "Invalid server browser request",
                        str(exc),
                        public_readonly=self.server.config.public_readonly,
                    ),
                    include_body=include_body,
                )
            return

        if target.path == "/coverage/records":
            params = parse_qs(target.query, keep_blank_values=True)
            state = params.get("state", ["eligible"])[0]
            page = _positive_integer(params.get("page", ["1"])[0], 1)
            try:
                result = _coverage_records(
                    self.server.catalog,
                    state=state,
                    page=page,
                    page_size=self.server.page_size,
                )
                self._send_html(
                    HTTPStatus.OK,
                    coverage_records_page(
                        result,
                        public_readonly=self.server.config.public_readonly,
                    ),
                    include_body=include_body,
                )
            except ValueError as exc:
                self._send_html(
                    HTTPStatus.BAD_REQUEST,
                    views.error_page(
                        400,
                        "Invalid coverage request",
                        str(exc),
                        public_readonly=self.server.config.public_readonly,
                    ),
                    include_body=include_body,
                )
            return

        if target.path == "/records":
            params = parse_qs(target.query, keep_blank_values=True)
            page = _positive_integer(params.get("page", ["1"])[0], 1)
            result = _immutable_records(
                self.server.catalog,
                page=page,
                page_size=self.server.page_size,
            )
            self._send_html(
                HTTPStatus.OK,
                immutable_records_page(
                    result,
                    public_readonly=self.server.config.public_readonly,
                ),
                include_body=include_body,
            )
            return

        if target.path == "/analyses":
            params = parse_qs(target.query, keep_blank_values=True)
            status = params.get("status", ["completed"])[0] or "completed"
            page = _positive_integer(params.get("page", ["1"])[0], 1)
            result = _analysis_runs(
                self.server.catalog,
                status=status,
                page=page,
                page_size=self.server.page_size,
            )
            self._send_html(
                HTTPStatus.OK,
                analysis_runs_page(
                    result,
                    public_readonly=self.server.config.public_readonly,
                ),
                include_body=include_body,
            )
            return

        if target.path.startswith("/snapshots/"):
            remainder = target.path[len("/snapshots/") :].strip("/")
            parts = remainder.split("/") if remainder else []
            snapshot_id = _positive_integer(parts[0], 0) if parts else 0
            if snapshot_id <= 0:
                original_dispatch(self, include_body=include_body)
                return
            if len(parts) == 2 and parts[1] == "changes":
                params = parse_qs(target.query, keep_blank_values=True)
                kind = params.get("kind", ["added"])[0]
                page = _positive_integer(params.get("page", ["1"])[0], 1)
                result = _snapshot_changes(
                    self.server.catalog,
                    snapshot_id=snapshot_id,
                    kind=kind,
                    page=page,
                    page_size=self.server.page_size,
                )
                self._send_html(
                    HTTPStatus.OK,
                    snapshot_changes_page(
                        result,
                        public_readonly=self.server.config.public_readonly,
                    ),
                    include_body=include_body,
                )
                return
            if len(parts) == 1:
                result = _snapshot_detail(self.server.catalog, snapshot_id)
                if result is None:
                    original_dispatch(self, include_body=include_body)
                    return
                self._send_html(
                    HTTPStatus.OK,
                    snapshot_detail_page(
                        result,
                        public_readonly=self.server.config.public_readonly,
                    ),
                    include_body=include_body,
                )
                return

        original_dispatch(self, include_body=include_body)

    setattr(dispatch, "_post_v2_bugfixes", True)
    app.PortalHandler._dispatch = dispatch


def _history_catalog(catalog: Any) -> Any | None:
    from .catalog import Catalog

    history_path = getattr(catalog, "_storage_v2_history_path", None)
    if history_path is None:
        return None
    detail = Catalog(Path(history_path))
    detail._storage_v2_history_path = None
    return detail


def _detail_or_hot(catalog: Any) -> Any:
    return _history_catalog(catalog) or catalog


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_servers(
    catalog: Any,
    *,
    scope: str,
    snapshot_id: int,
    query: str,
    ecosystem: str,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    if scope not in {"current", "all", "snapshot"}:
        raise ValueError("scope must be current, all, or snapshot")
    if scope == "snapshot" and snapshot_id <= 0:
        raise ValueError("snapshot scope requires a positive snapshot id")

    normalized = query.strip()[:200]
    normalized_ecosystem = ecosystem.strip()[:200]
    pattern = "%" + _escape_like(normalized) + "%"
    offset = (max(page, 1) - 1) * page_size
    source = catalog if scope == "current" else _detail_or_hot(catalog)

    if scope == "current":
        result = source.search_servers(
            normalized,
            page=max(page, 1),
            page_size=page_size,
            ecosystem=normalized_ecosystem,
        )
        result["scope"] = "current"
        result["snapshot_id"] = None
        return result

    params: dict[str, Any] = {
        "query": normalized,
        "pattern": pattern,
        "ecosystem": normalized_ecosystem,
        "page_size": page_size,
        "offset": offset,
        "snapshot_id": snapshot_id,
    }
    membership = ""
    if scope == "snapshot":
        membership = (
            "EXISTS(SELECT 1 FROM snapshot_server_versions ssl "
            "WHERE ssl.server_version_id=sv.id AND ssl.snapshot_id=:snapshot_id) AND "
        )
    where_sql = membership + """
        (:query='' OR
         sv.server_identifier LIKE :pattern ESCAPE '\\' OR
         COALESCE(sv.description,'') LIKE :pattern ESCAPE '\\' OR
         EXISTS(SELECT 1 FROM packages sp
                WHERE sp.server_version_id=sv.id
                  AND sp.identifier LIKE :pattern ESCAPE '\\') OR
         EXISTS(SELECT 1 FROM repositories sr
                WHERE sr.server_version_id=sv.id
                  AND COALESCE(sr.url,'') LIKE :pattern ESCAPE '\\') OR
         EXISTS(SELECT 1 FROM remotes sm
                WHERE sm.server_version_id=sv.id
                  AND sm.url LIKE :pattern ESCAPE '\\'))
        AND (:ecosystem='' OR EXISTS(
            SELECT 1 FROM packages ep
            WHERE ep.server_version_id=sv.id
              AND ep.registry_type=:ecosystem COLLATE BINARY))
    """

    with source._connect() as connection:
        total = int(
            connection.execute(
                f"SELECT COUNT(DISTINCT sv.server_identifier) FROM server_versions sv WHERE {where_sql}",
                params,
            ).fetchone()[0]
        )
        rows = [
            dict(row)
            for row in connection.execute(
                f"""
                WITH matching AS (
                    SELECT sv.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY sv.server_identifier
                               ORDER BY COALESCE(sv.updated_at,sv.published_at,'') COLLATE BINARY DESC,
                                        sv.id DESC
                           ) AS row_number
                    FROM server_versions sv
                    WHERE {where_sql}
                )
                SELECT m.id,m.server_identifier,m.server_version,m.description,
                       m.registry_status,m.published_at,m.updated_at,m.canonical_sha256,
                       (SELECT COUNT(DISTINCT av.server_version)
                        FROM server_versions av
                        WHERE av.server_identifier=m.server_identifier) AS version_count,
                       (SELECT p.identifier FROM packages p
                        WHERE p.server_version_id=m.id
                          AND (:ecosystem='' OR p.registry_type=:ecosystem COLLATE BINARY)
                        ORDER BY p.position LIMIT 1) AS package_identifier,
                       (SELECT p.transport FROM packages p
                        WHERE p.server_version_id=m.id
                          AND (:ecosystem='' OR p.registry_type=:ecosystem COLLATE BINARY)
                        ORDER BY p.position LIMIT 1) AS package_transport,
                       (SELECT r.host FROM repositories r
                        WHERE r.server_version_id=m.id LIMIT 1) AS repository_host
                FROM matching m
                WHERE m.row_number=1
                ORDER BY COALESCE(m.updated_at,m.published_at,'') COLLATE BINARY DESC,
                         m.server_identifier COLLATE BINARY
                LIMIT :page_size OFFSET :offset
                """,
                params,
            ).fetchall()
        ]

    return {
        "query": normalized,
        "ecosystem": normalized_ecosystem,
        "page": max(page, 1),
        "page_size": page_size,
        "total": total,
        "rows": rows,
        "scope": scope,
        "snapshot_id": snapshot_id if scope == "snapshot" else None,
    }


def servers_scope_page(result: dict[str, Any], *, public_readonly: bool = False) -> str:
    from . import views

    scope = result["scope"]
    snapshot_id = result.get("snapshot_id")
    scope_title = {
        "current": "Current snapshot",
        "all": "All observed servers",
        "snapshot": f"Snapshot #{snapshot_id}",
    }[scope]
    scope_text = {
        "current": "Server identifiers present in the latest published Official MCP Registry snapshot.",
        "all": "Every distinct server identifier retained in MCPLA longitudinal Registry history.",
        "snapshot": f"Server identifiers present in immutable snapshot #{snapshot_id}.",
    }[scope]

    rows = "".join(views._browser_server_row(row) for row in result["rows"]) or (
        '<tr><td colspan="6" class="empty">No matching servers.</td></tr>'
    )
    total_pages = max(1, ceil(result["total"] / result["page_size"]))
    base_params = {"scope": scope}
    if snapshot_id:
        base_params["snapshot"] = str(snapshot_id)
    if result["ecosystem"]:
        base_params["ecosystem"] = result["ecosystem"]
    pagination = _pagination(
        "/servers",
        result["page"],
        total_pages,
        base_params | ({"q": result["query"]} if result["query"] else {}),
    )

    hidden = f'<input type="hidden" name="scope" value="{escape(scope, quote=True)}">'
    if snapshot_id:
        hidden += f'<input type="hidden" name="snapshot" value="{int(snapshot_id)}">'
    if result["ecosystem"]:
        hidden += (
            '<input type="hidden" name="ecosystem" value="'
            + escape(result["ecosystem"], quote=True)
            + '">'
        )

    tabs = (
        '<nav class="scope-tabs" aria-label="Server browser scope">'
        '<a href="/servers?scope=current">Current snapshot</a>'
        '<a href="/servers?scope=all">All observed servers</a>'
        '</nav>'
    )
    body = f"""<section class="page-heading"><p class="eyebrow">Official Registry catalog</p><h1>Server browser</h1><p><strong>{escape(scope_title)}.</strong> {escape(scope_text)}</p></section>
{tabs}
<form class="search" method="get" action="/servers">{hidden}<label for="q">Search identifiers, descriptions, packages, repositories, and remote URLs</label><div><input id="q" name="q" value="{escape(result['query'], quote=True)}" maxlength="200" autocomplete="off"><button type="submit">Search</button></div></form>
<div class="result-summary">{result['total']:,} server identifiers · {escape(scope_title)} · page {result['page']} of {total_pages}</div>
<section class="panel compact server-browser-panel"><div class="table-wrap server-browser-table-wrap"><table class="server-browser-table"><thead><tr><th>Server</th><th>Latest version</th><th>Versions</th><th>Package</th><th>Repository</th><th>Updated</th></tr></thead><tbody>{rows}</tbody></table></div></section>{pagination}"""
    return views.layout("Servers", body, public_readonly=public_readonly)


def snapshots_page(result: dict[str, Any], *, public_readonly: bool = False) -> str:
    from . import views

    rows = "".join(
        f'<tr class="click-row"><td><a href="/snapshots/{int(row["id"])}"><strong>#{int(row["id"])}</strong></a></td>'
        f'<td>{escape(_text(row.get("completed_at"), "—"))}<div class="meta">started {escape(_text(row.get("started_at"), "—"))}</div></td>'
        f'<td>{escape(_duration(row.get("started_at"), row.get("completed_at")))}</td>'
        f'<td>{int(row.get("pages") or 0):,}</td><td>{int(row.get("records_received") or 0):,}</td>'
        f'<td>{int(row.get("unique_server_versions") or 0):,}</td><td><code>{escape(_text(row.get("sha256_prefix"), "—"))}</code><div><a href="/snapshots/{int(row["id"])}">View snapshot →</a></div></td></tr>'
        for row in result["rows"]
    ) or '<tr><td colspan="7" class="empty">No published snapshots.</td></tr>'
    total_pages = max(1, ceil(result["total"] / result["page_size"]))
    pagination = _pagination("/snapshots", result["page"], total_pages)
    body = f"""<section class="page-heading"><p class="eyebrow">Immutable history</p><h1>Catalog snapshots</h1><p>Content-addressed publication history, newest first. Open a snapshot to inspect its membership and change set.</p></section><div class="result-summary">{result['total']:,} snapshots · page {result['page']} of {total_pages}</div><section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Snapshot</th><th>Published</th><th>Duration</th><th>Pages</th><th>Records</th><th>Unique versions</th><th>Digest</th></tr></thead><tbody>{rows}</tbody></table></div></section>{pagination}"""
    return views.layout("Catalog snapshots", body, public_readonly=public_readonly)


def _snapshot_detail(catalog: Any, snapshot_id: int) -> dict[str, Any] | None:
    source = _detail_or_hot(catalog)
    with source._connect() as connection:
        row = connection.execute(
            """SELECT id,snapshot_sha256,completed_at,started_at,registry_base_url,
                      bundle_version,pages,records_received,unique_server_versions,imported_at
               FROM snapshots WHERE id=?""",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None
        snapshot = dict(row)
        previous = connection.execute(
            """SELECT id FROM snapshots
               WHERE completed_at < ? OR (completed_at=? AND id<?)
               ORDER BY completed_at COLLATE BINARY DESC,id DESC LIMIT 1""",
            (snapshot["completed_at"], snapshot["completed_at"], snapshot_id),
        ).fetchone()
        snapshot["previous_id"] = int(previous["id"]) if previous else None
        snapshot["membership_count"] = int(
            connection.execute(
                "SELECT COUNT(*) FROM snapshot_server_versions WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()[0]
        )
        snapshot["server_count"] = int(
            connection.execute(
                """SELECT COUNT(DISTINCT sv.server_identifier)
                   FROM snapshot_server_versions l
                   JOIN server_versions sv ON sv.id=l.server_version_id
                   WHERE l.snapshot_id=?""",
                (snapshot_id,),
            ).fetchone()[0]
        )
    return snapshot


def snapshot_detail_page(data: dict[str, Any], *, public_readonly: bool = False) -> str:
    from . import views

    sid = int(data["id"])
    previous = data.get("previous_id")
    compare = ""
    if previous:
        compare = (
            f'<a href="/snapshots/{sid}/changes?kind=added">Added vs #{int(previous)}</a> · '
            f'<a href="/snapshots/{sid}/changes?kind=removed">Removed vs #{int(previous)}</a>'
        )
    body = f"""<section class="page-heading"><p class="eyebrow">Immutable snapshot</p><h1>Snapshot #{sid}</h1><p>Official MCP Registry REST API publication captured and content-addressed by MCPLA.</p></section>
<section class="cards">{_plain_card('Published', _text(data.get('completed_at'),'—'), 'Publication timestamp')}{_plain_card('Duration', _duration(data.get('started_at'),data.get('completed_at')), 'Collection elapsed time')}{_plain_card('Server identifiers', f"{int(data.get('server_count') or 0):,}", 'Distinct identifiers in this snapshot')}{_plain_card('Immutable records', f"{int(data.get('membership_count') or 0):,}", 'Server-version membership')}</section>
<section class="panel"><h2>Snapshot identity</h2><dl class="facts"><div><dt>Started</dt><dd>{escape(_text(data.get('started_at'),'—'))}</dd></div><div><dt>Published</dt><dd>{escape(_text(data.get('completed_at'),'—'))}</dd></div><div><dt>Pages</dt><dd>{int(data.get('pages') or 0):,}</dd></div><div><dt>Records received</dt><dd>{int(data.get('records_received') or 0):,}</dd></div><div><dt>Digest</dt><dd><code>{escape(_text(data.get('snapshot_sha256'),'—'))}</code></dd></div><div><dt>Registry API base</dt><dd><code>{escape(_text(data.get('registry_base_url'),'—'))}</code></dd></div></dl></section>
<section class="panel"><h2>Explore</h2><p><a href="/servers?scope=snapshot&amp;snapshot={sid}">Browse servers in this snapshot →</a></p><p>{compare}</p></section>"""
    return views.layout(f"Snapshot #{sid}", body, public_readonly=public_readonly)


def _snapshot_changes(
    catalog: Any,
    *,
    snapshot_id: int,
    kind: str,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    if kind not in {"added", "removed"}:
        raise ValueError("kind must be added or removed")
    source = _detail_or_hot(catalog)
    with source._connect() as connection:
        current = connection.execute(
            "SELECT id,completed_at FROM snapshots WHERE id=?", (snapshot_id,)
        ).fetchone()
        if current is None:
            raise ValueError("snapshot does not exist")
        previous = connection.execute(
            """SELECT id FROM snapshots
               WHERE completed_at < ? OR (completed_at=? AND id<?)
               ORDER BY completed_at COLLATE BINARY DESC,id DESC LIMIT 1""",
            (current["completed_at"], current["completed_at"], snapshot_id),
        ).fetchone()
        previous_id = int(previous["id"]) if previous else None
        if previous_id is None:
            return {"snapshot_id": snapshot_id, "previous_id": None, "kind": kind, "page": 1, "page_size": page_size, "total": 0, "rows": []}
        left, right = (snapshot_id, previous_id) if kind == "added" else (previous_id, snapshot_id)
        total = int(
            connection.execute(
                """SELECT COUNT(*) FROM (
                     SELECT server_version_id FROM snapshot_server_versions WHERE snapshot_id=?
                     EXCEPT
                     SELECT server_version_id FROM snapshot_server_versions WHERE snapshot_id=?
                   )""",
                (left, right),
            ).fetchone()[0]
        )
        offset = (max(page, 1) - 1) * page_size
        rows = [
            dict(row)
            for row in connection.execute(
                """WITH diff AS (
                     SELECT server_version_id FROM snapshot_server_versions WHERE snapshot_id=?
                     EXCEPT
                     SELECT server_version_id FROM snapshot_server_versions WHERE snapshot_id=?
                   )
                   SELECT sv.id,sv.server_identifier,sv.server_version,sv.registry_status,
                          sv.published_at,sv.updated_at,sv.canonical_sha256
                   FROM diff JOIN server_versions sv ON sv.id=diff.server_version_id
                   ORDER BY sv.server_identifier COLLATE BINARY,sv.server_version COLLATE BINARY
                   LIMIT ? OFFSET ?""",
                (left, right, page_size, offset),
            ).fetchall()
        ]
    return {"snapshot_id": snapshot_id, "previous_id": previous_id, "kind": kind, "page": max(page, 1), "page_size": page_size, "total": total, "rows": rows}


def snapshot_changes_page(result: dict[str, Any], *, public_readonly: bool = False) -> str:
    from . import views

    rows = "".join(
        f'<tr><td><a href="/servers/{quote(_text(row.get("server_identifier")), safe="")}">{escape(_text(row.get("server_identifier")))}</a></td><td><code>{escape(_text(row.get("server_version"),"—"))}</code></td><td>{escape(_text(row.get("registry_status"),"—"))}</td><td><code>{escape(_short_hash(row.get("canonical_sha256")))}</code></td></tr>'
        for row in result["rows"]
    ) or '<tr><td colspan="4" class="empty">No matching changes.</td></tr>'
    total_pages = max(1, ceil(result["total"] / result["page_size"]))
    pagination = _pagination(
        f'/snapshots/{result["snapshot_id"]}/changes',
        result["page"], total_pages, {"kind": result["kind"]},
    )
    body = f"""<section class="page-heading"><p class="eyebrow">Snapshot comparison</p><h1>{escape(result['kind'].title())} records</h1><p>Exact immutable membership difference between snapshot #{result['snapshot_id']} and #{result.get('previous_id') or '—'}.</p></section><div class="result-summary">{result['total']:,} records · page {result['page']} of {total_pages}</div><section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Server</th><th>Version</th><th>Status</th><th>Digest</th></tr></thead><tbody>{rows}</tbody></table></div></section>{pagination}"""
    return views.layout("Snapshot changes", body, public_readonly=public_readonly)


def _coverage_records(catalog: Any, *, state: str, page: int, page_size: int) -> dict[str, Any]:
    predicates = {
        "eligible": "s.state IN('eligible','running','completed','failed')",
        "completed": "s.state='completed'",
        "failed": "s.state='failed'",
        "unsupported": "s.state IN('unsupported','unresolvable')",
        "never": "s.state='eligible' AND s.attempt_count=0",
    }
    if state not in predicates:
        raise ValueError("unsupported coverage state")
    with catalog._connect() as connection:
        profile = connection.execute(
            "SELECT profile_key FROM static_analysis_schedule_current WHERE singleton=1"
        ).fetchone()
        if profile is None:
            return {"state": state, "page": 1, "page_size": page_size, "total": 0, "rows": []}
        profile_key = profile["profile_key"]
        predicate = predicates[state]
        total = int(connection.execute(
            f"SELECT COUNT(*) FROM static_analysis_schedule_state s WHERE s.profile_key=? AND {predicate}",
            (profile_key,),
        ).fetchone()[0])
        offset = (max(page, 1) - 1) * page_size
        rows = [dict(row) for row in connection.execute(
            f"""SELECT s.package_id,s.state,s.reason_code,s.reason_message,s.attempt_count,
                       s.analysis_run_id,s.artifact_sha256,s.updated_at,
                       p.identifier AS package_identifier,p.version AS package_version,
                       p.registry_type,p.transport,sv.server_identifier,sv.server_version
                FROM static_analysis_schedule_state s
                JOIN packages p ON p.id=s.package_id
                JOIN server_versions sv ON sv.id=p.server_version_id
                WHERE s.profile_key=? AND {predicate}
                ORDER BY s.updated_at COLLATE BINARY DESC,s.package_id DESC
                LIMIT ? OFFSET ?""",
            (profile_key, page_size, offset),
        ).fetchall()]
    return {"state": state, "page": max(page, 1), "page_size": page_size, "total": total, "rows": rows}


def coverage_records_page(result: dict[str, Any], *, public_readonly: bool = False) -> str:
    from . import views

    rows = "".join(
        f'<tr><td><a href="/servers/{quote(_text(row.get("server_identifier")), safe="")}">{escape(_text(row.get("server_identifier")))}</a></td><td>{escape(_text(row.get("package_identifier")))}</td><td><code>{escape(_text(row.get("package_version"),"—"))}</code></td><td><span class="badge">{escape(_text(row.get("state")))}</span></td><td>{escape(_text(row.get("reason_code"),"—"))}</td><td>{int(row.get("attempt_count") or 0):,}</td></tr>'
        for row in result["rows"]
    ) or '<tr><td colspan="6" class="empty">No records.</td></tr>'
    total_pages = max(1, ceil(result["total"] / result["page_size"]))
    pagination = _pagination("/coverage/records", result["page"], total_pages, {"state": result["state"]})
    body = f"""<section class="page-heading"><p class="eyebrow">Static coverage drill-down</p><h1>{escape(result['state'].title())} package records</h1><p>The list uses the same current-profile scheduler-state predicate as the aggregate coverage metric.</p></section><div class="result-summary">{result['total']:,} records · page {result['page']} of {total_pages}</div><section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Server</th><th>Package</th><th>Version</th><th>State</th><th>Reason</th><th>Attempts</th></tr></thead><tbody>{rows}</tbody></table></div></section>{pagination}"""
    return views.layout("Coverage records", body, public_readonly=public_readonly)


def _immutable_records(catalog: Any, *, page: int, page_size: int) -> dict[str, Any]:
    source = _detail_or_hot(catalog)
    with source._connect() as connection:
        total = int(connection.execute("SELECT COUNT(*) FROM server_versions").fetchone()[0])
        offset = (max(page, 1) - 1) * page_size
        rows = [dict(row) for row in connection.execute(
            """SELECT id,server_identifier,server_version,registry_status,published_at,updated_at,canonical_sha256
               FROM server_versions
               ORDER BY COALESCE(updated_at,published_at,'') COLLATE BINARY DESC,id DESC
               LIMIT ? OFFSET ?""",
            (page_size, offset),
        ).fetchall()]
    return {"page": max(page, 1), "page_size": page_size, "total": total, "rows": rows}


def immutable_records_page(result: dict[str, Any], *, public_readonly: bool = False) -> str:
    from . import views

    rows = "".join(
        f'<tr><td><a href="/servers/{quote(_text(row.get("server_identifier")), safe="")}">{escape(_text(row.get("server_identifier")))}</a></td><td><code>{escape(_text(row.get("server_version"),"—"))}</code></td><td>{escape(_text(row.get("registry_status"),"—"))}</td><td>{escape(_text(row.get("updated_at"),"—"))}</td><td><code>{escape(_short_hash(row.get("canonical_sha256")))}</code></td></tr>'
        for row in result["rows"]
    ) or '<tr><td colspan="5" class="empty">No immutable records.</td></tr>'
    total_pages = max(1, ceil(result["total"] / result["page_size"]))
    pagination = _pagination("/records", result["page"], total_pages)
    body = f"""<section class="page-heading"><p class="eyebrow">Longitudinal catalog</p><h1>Immutable records</h1><p>Version and metadata variants retained from Official MCP Registry snapshots.</p></section><div class="result-summary">{result['total']:,} records · page {result['page']} of {total_pages}</div><section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Server</th><th>Version</th><th>Status</th><th>Updated</th><th>Digest</th></tr></thead><tbody>{rows}</tbody></table></div></section>{pagination}"""
    return views.layout("Immutable records", body, public_readonly=public_readonly)


def _analysis_runs(catalog: Any, *, status: str, page: int, page_size: int) -> dict[str, Any]:
    if status not in {"completed", "failed", "running"}:
        raise ValueError("unsupported analysis status")
    source = _detail_or_hot(catalog)
    with source._connect() as connection:
        total = int(connection.execute("SELECT COUNT(*) FROM analysis_runs WHERE status=?", (status,)).fetchone()[0])
        offset = (max(page, 1) - 1) * page_size
        rows = [dict(row) for row in connection.execute(
            """SELECT ar.id,ar.status,ar.started_at,ar.completed_at,ar.artifact_sha256,
                      ar.analyzer_name,ar.analyzer_version,ar.ruleset_version,
                      sv.server_identifier,sv.server_version,p.identifier AS package_identifier,p.version AS package_version
               FROM analysis_runs ar
               JOIN server_versions sv ON sv.id=ar.server_version_id
               JOIN packages p ON p.id=ar.package_id
               WHERE ar.status=?
               ORDER BY ar.started_at COLLATE BINARY DESC,ar.id DESC
               LIMIT ? OFFSET ?""",
            (status, page_size, offset),
        ).fetchall()]
    return {"status": status, "page": max(page, 1), "page_size": page_size, "total": total, "rows": rows}


def analysis_runs_page(result: dict[str, Any], *, public_readonly: bool = False) -> str:
    from . import views

    rows = "".join(
        f'<tr><td><a href="/analyses/{int(row["id"])}">#{int(row["id"])}</a></td><td><a href="/servers/{quote(_text(row.get("server_identifier")), safe="")}">{escape(_text(row.get("server_identifier")))}</a></td><td>{escape(_text(row.get("package_identifier")))}</td><td><code>{escape(_short_hash(row.get("artifact_sha256")))}</code></td><td>{escape(_text(row.get("ruleset_version"),"—"))}</td><td>{escape(_text(row.get("completed_at") or row.get("started_at"),"—"))}</td></tr>'
        for row in result["rows"]
    ) or '<tr><td colspan="6" class="empty">No analysis runs.</td></tr>'
    total_pages = max(1, ceil(result["total"] / result["page_size"]))
    pagination = _pagination("/analyses", result["page"], total_pages, {"status": result["status"]})
    body = f"""<section class="page-heading"><p class="eyebrow">Static evidence</p><h1>{escape(result['status'].title())} analyses</h1><p>Bounded list of static package-analysis runs.</p></section><div class="result-summary">{result['total']:,} runs · page {result['page']} of {total_pages}</div><section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Run</th><th>Server</th><th>Package</th><th>Artifact</th><th>Ruleset</th><th>Completed</th></tr></thead><tbody>{rows}</tbody></table></div></section>{pagination}"""
    return views.layout("Analysis runs", body, public_readonly=public_readonly)


def coverage_page(data: dict[str, Any], *, public_readonly: bool = False) -> str:
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
        runtime_detail = f"{runtime_completed:,} of {runtime_eligible:,} eligible npm stdio records"
    else:
        runtime_value = "Planned next"
        runtime_detail = "Automatic Native Guard runtime observation is not yet enabled."

    static_cards = "".join(
        _linked_card(label, value, detail, href)
        for label, value, detail, href in (
            ("Eligible package records", f"{eligible:,}", "Supported registry with an exact version", "/coverage/records?state=eligible"),
            ("Successfully analyzed", f"{analyzed:,}", f"{static_percent:.1f}% of eligible records", "/coverage/records?state=completed"),
            ("Failed attempts", f"{failed:,}", "Current profile; no compatible completion", "/coverage/records?state=failed"),
            ("Unsupported / unresolvable", f"{unsupported:,}", "Not currently schedulable", "/coverage/records?state=unsupported"),
            ("Never attempted", f"{never:,}", "Eligible and not yet selected", "/coverage/records?state=never"),
            ("Unique artifacts analyzed", f"{unique_artifacts:,}", "Distinct completed artifact SHA-256 values", None),
        )
    )
    assurance_cards = (
        _linked_card("Static artifact coverage", f"{static_percent:.1f}%", f"{analyzed:,} of {eligible:,} eligible records", "/coverage/records?state=completed")
        + _linked_card("Runtime discovery", runtime_value, runtime_detail, None)
        + _linked_card("Controlled behavioral analysis", "Planned later", "MCP tool invocation and host-effect observation are not part of the current pipeline.", None)
    )
    review_note = ""
    if not public_readonly:
        review = data.get("human_review") or {}
        reviewed = int(review.get("reviewed", 0))
        findings = int(review.get("total", 0))
        review_note = _linked_card("Human-review coverage", f"{_percent(reviewed, findings):.1f}%" if findings else "Not started", f"{reviewed:,} of {findings:,} findings have a disposition", None)

    body = f"""<section class="page-heading"><p class="eyebrow">Assurance reach</p><h1>Coverage</h1><p>Operational coverage and planned assurance layers are reported separately.</p></section><section class="panel"><div class="panel-heading"><div><p class="eyebrow">Current baseline</p><h2>Static artifact coverage</h2></div></div><section class="cards">{static_cards}</section><p><strong>{analyzed:,}</strong> of <strong>{eligible:,}</strong> eligible package records are covered by the current static-analysis profile.</p><progress value="{analyzed}" max="{max(eligible, 1)}">{static_percent:.1f}%</progress><p class="meta">{static_percent:.1f}% static artifact coverage · {total:,} total package records</p></section><section class="panel"><div class="panel-heading"><div><p class="eyebrow">Assurance roadmap</p><h2>Coverage layers</h2></div></div><section class="cards">{assurance_cards}{review_note}</section></section><section class="notice"><strong>Status boundary:</strong> planned runtime or behavioral capabilities are not reported as failed coverage. Static completion records observable properties of an exact artifact and is not a safety certification.</section>"""
    return views.layout("Coverage", body, public_readonly=public_readonly)


def _linked_card(label: str, value: str, detail: str, href: str | None) -> str:
    content = f'<article class="card"><span>{escape(label)}</span><strong>{escape(value)}</strong><small>{escape(detail)}</small></article>'
    if href is None:
        return content
    return f'<a class="card-shell-link" href="{escape(href, quote=True)}">{content}</a>'


def _plain_card(label: str, value: str, detail: str) -> str:
    return f'<article class="card"><span>{escape(label)}</span><strong>{escape(value)}</strong><small>{escape(detail)}</small></article>'


def _pagination(base: str, page: int, total_pages: int, parameters: dict[str, str] | None = None) -> str:
    if total_pages <= 1:
        return ""
    params = dict(parameters or {})
    links: list[str] = []
    if page > 1:
        params["page"] = str(page - 1)
        links.append(f'<a href="{escape(base + "?" + urlencode(params), quote=True)}">← Previous</a>')
    if page < total_pages:
        params["page"] = str(page + 1)
        links.append(f'<a href="{escape(base + "?" + urlencode(params), quote=True)}">Next →</a>')
    return '<nav class="pagination" aria-label="Pagination">' + "".join(links) + "</nav>"


def _positive_integer(raw: str, fallback: int) -> int:
    try:
        value = int(raw, 10)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def _percent(part: int, whole: int) -> float:
    return 0.0 if whole <= 0 else part * 100.0 / whole


def _short_hash(value: Any) -> str:
    text = _text(value, "—")
    return text if len(text) <= 12 else text[:12] + "…"


def _duration(started: Any, completed: Any) -> str:
    from datetime import datetime

    try:
        start = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
        seconds = max(0, int((end - start).total_seconds()))
        return f"{seconds}s"
    except (TypeError, ValueError):
        return "—"


def _text(value: Any, fallback: str = "") -> str:
    return fallback if value is None else str(value)
