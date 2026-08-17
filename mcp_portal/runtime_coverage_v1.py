"""Public read-only runtime discovery coverage and canonical tool drift views.

Milestone 1 exposes only bounded data already published into the hot catalog. It
never opens the portal job database, reads runtime evidence files, or enables an
execution route. Drift is presented as an observed interface change, not a safety
finding.
"""

from __future__ import annotations

from html import escape
from math import ceil
import sqlite3
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlsplit


def apply_runtime_coverage_v1() -> None:
    """Install the final read-only runtime coverage layer once."""
    from . import app, public_intelligence, public_ui, views

    if getattr(app.PortalHandler._dispatch, "_runtime_coverage_v1", False):
        return

    original_analysis_coverage = public_intelligence.PublicIntelligence.analysis_coverage

    def analysis_coverage(self: Any) -> dict[str, Any]:
        result = original_analysis_coverage(self)
        with self._connect() as connection:
            result["runtime_discovery"] = _runtime_metrics(connection)
        return result

    public_intelligence.PublicIntelligence.analysis_coverage = analysis_coverage

    original_coverage_page: Callable[..., str] = public_ui.coverage_page

    def coverage_page(data: dict[str, Any], *, public_readonly: bool = False) -> str:
        html = original_coverage_page(data, public_readonly=public_readonly)
        runtime = data.get("runtime_discovery") or {}
        panel = _runtime_coverage_panel(runtime)
        return html.replace("</main>", panel + "</main>", 1)

    public_ui.coverage_page = coverage_page

    original_layout: Callable[..., str] = views.layout

    def runtime_layout(
        title: str,
        body: str,
        *,
        public_readonly: bool = False,
    ) -> str:
        html = original_layout(title, body, public_readonly=public_readonly)
        marker = '<a href="/coverage">Coverage</a>'
        if marker in html and 'href="/runtime-drift"' not in html:
            html = html.replace(
                marker,
                marker + '<a href="/runtime-drift">Runtime drift</a>',
                1,
            )
        return html

    views.layout = runtime_layout

    original_dispatch = app.PortalHandler._dispatch

    def runtime_dispatch(self: Any, *, include_body: bool) -> None:
        target = urlsplit(self.path)
        if target.path != "/runtime-drift" and not target.path.startswith(
            "/runtime-drift/"
        ):
            original_dispatch(self, include_body=include_body)
            return

        intelligence = public_intelligence.PublicIntelligence(
            self.server.config.database_path
        )
        try:
            with intelligence._connect() as connection:
                if target.path == "/runtime-drift":
                    parameters = parse_qs(target.query, keep_blank_values=True)
                    page = _positive_integer(
                        parameters.get("page", ["1"])[0], fallback=1
                    )
                    result = _runtime_drift_list(
                        connection,
                        page=page,
                        page_size=self.server.page_size,
                    )
                    html = runtime_drift_page(
                        result,
                        public_readonly=self.server.config.public_readonly,
                    )
                else:
                    raw = target.path[len("/runtime-drift/") :]
                    run_id = _positive_integer(raw, fallback=0)
                    result = _runtime_drift_detail(connection, run_id)
                    if result is None:
                        self._not_found(include_body)
                        return
                    html = runtime_drift_detail_page(
                        result,
                        public_readonly=self.server.config.public_readonly,
                    )
            self._send_html(200, html, include_body=include_body)
        except public_intelligence.PublicIntelligenceError as exc:
            self._send_html(
                503,
                views.error_page(
                    503,
                    "Runtime coverage unavailable",
                    str(exc),
                    public_readonly=self.server.config.public_readonly,
                ),
                include_body=include_body,
            )
        except (sqlite3.Error, ValueError, TypeError) as exc:
            self._send_html(
                400,
                views.error_page(
                    400,
                    "Invalid runtime coverage request",
                    str(exc),
                    public_readonly=self.server.config.public_readonly,
                ),
                include_body=include_body,
            )

    setattr(runtime_dispatch, "_runtime_coverage_v1", True)
    app.PortalHandler._dispatch = runtime_dispatch


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table'"
        )
    }


def _runtime_metrics(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = _tables(connection)
    schedule_tables = {
        "runtime_discovery_schedule_profiles",
        "runtime_discovery_schedule_current",
        "runtime_discovery_schedule_state",
    }
    if schedule_tables.issubset(tables):
        profile = connection.execute(
            """SELECT p.profile_key,p.scheduler_version,p.guard_sha256,
                      p.runtime_image,p.probe_profile_sha256,p.runner_sha256
               FROM runtime_discovery_schedule_current c
               JOIN runtime_discovery_schedule_profiles p
                 ON p.profile_key=c.profile_key
               WHERE c.singleton=1"""
        ).fetchone()
        if profile is not None:
            row = connection.execute(
                """SELECT
                       SUM(state IN('eligible','running','completed','failed')) eligible,
                       SUM(state='completed') completed,
                       SUM(state='failed') failed,
                       SUM(state IN('unsupported','unresolvable')) unsupported,
                       SUM(state='eligible' AND attempt_count=0) never_attempted,
                       SUM(state='running') running,
                       COUNT(DISTINCT CASE WHEN state='completed'
                         THEN artifact_sha256 END) unique_artifacts,
                       SUM(state='completed' AND previous_compatible_run_id IS NOT NULL)
                         comparable,
                       SUM(state='completed' AND previous_compatible_run_id IS NOT NULL
                         AND (COALESCE(added_tools,0)+COALESCE(removed_tools,0)+
                              COALESCE(modified_tools,0))>0) drifted
                   FROM runtime_discovery_schedule_state
                   WHERE profile_key=?""",
                (profile["profile_key"],),
            ).fetchone()
            return {
                "available": True,
                "scheduled": True,
                "eligible": int(row["eligible"] or 0),
                "completed": int(row["completed"] or 0),
                "failed": int(row["failed"] or 0),
                "unsupported_or_unresolvable": int(row["unsupported"] or 0),
                "never_attempted": int(row["never_attempted"] or 0),
                "running": int(row["running"] or 0),
                "unique_artifacts": int(row["unique_artifacts"] or 0),
                "comparable": int(row["comparable"] or 0),
                "drifted": int(row["drifted"] or 0),
                "profile": dict(profile),
            }

    eligible = int(
        connection.execute(
            """SELECT COUNT(*) FROM packages
               WHERE registry_type='npm' AND transport='stdio'
                 AND version IS NOT NULL AND trim(version)<>''"""
        ).fetchone()[0]
    )
    completed = 0
    unique_artifacts = 0
    if "runtime_observation_runs" in tables:
        row = connection.execute(
            """SELECT COUNT(DISTINCT package_id) completed,
                      COUNT(DISTINCT artifact_sha256) unique_artifacts
               FROM runtime_observation_runs WHERE status='completed'"""
        ).fetchone()
        completed = int(row["completed"] or 0)
        unique_artifacts = int(row["unique_artifacts"] or 0)
    return {
        "available": "runtime_observation_runs" in tables,
        "scheduled": False,
        "eligible": eligible,
        "completed": completed,
        "failed": 0,
        "unsupported_or_unresolvable": 0,
        "never_attempted": max(0, eligible - completed),
        "running": 0,
        "unique_artifacts": unique_artifacts,
        "comparable": 0,
        "drifted": 0,
        "profile": None,
    }


def _runtime_drift_list(
    connection: sqlite3.Connection,
    *,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    if page < 1:
        page = 1
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")
    tables = _tables(connection)
    required = {
        "runtime_discovery_schedule_current",
        "runtime_discovery_schedule_state",
        "runtime_observation_runs",
        "server_versions",
        "packages",
    }
    if not required.issubset(tables):
        return {"page": page, "page_size": page_size, "total": 0, "rows": []}
    current = connection.execute(
        "SELECT profile_key FROM runtime_discovery_schedule_current WHERE singleton=1"
    ).fetchone()
    if current is None:
        return {"page": page, "page_size": page_size, "total": 0, "rows": []}
    key = str(current["profile_key"])
    where = """
        s.profile_key=? AND s.state='completed'
        AND s.previous_compatible_run_id IS NOT NULL
        AND (COALESCE(s.added_tools,0)+COALESCE(s.removed_tools,0)+
             COALESCE(s.modified_tools,0))>0
    """
    total = int(
        connection.execute(
            "SELECT COUNT(*) FROM runtime_discovery_schedule_state s WHERE " + where,
            (key,),
        ).fetchone()[0]
    )
    offset = (page - 1) * page_size
    rows = [
        dict(row)
        for row in connection.execute(
            """SELECT s.runtime_observation_run_id AS newer_run_id,
                      s.previous_compatible_run_id AS older_run_id,
                      s.added_tools,s.removed_tools,s.modified_tools,s.unchanged_tools,
                      nsv.server_identifier,
                      osv.server_version AS older_version,
                      nsv.server_version AS newer_version,
                      np.identifier AS package_identifier,
                      substr(orun.artifact_sha256,1,16) AS older_artifact_prefix,
                      substr(nrun.artifact_sha256,1,16) AS newer_artifact_prefix,
                      nrun.completed_at
               FROM runtime_discovery_schedule_state s
               JOIN runtime_observation_runs nrun
                 ON nrun.id=s.runtime_observation_run_id
               JOIN runtime_observation_runs orun
                 ON orun.id=s.previous_compatible_run_id
               JOIN server_versions nsv ON nsv.id=nrun.server_version_id
               JOIN server_versions osv ON osv.id=orun.server_version_id
               JOIN packages np ON np.id=nrun.package_id
               WHERE """
            + where
            + """
               ORDER BY COALESCE(nrun.completed_at,nrun.started_at,'') COLLATE BINARY DESC,
                        nrun.id DESC
               LIMIT ? OFFSET ?""",
            (key, page_size, offset),
        ).fetchall()
    ]
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "rows": rows,
    }


def _runtime_drift_detail(
    connection: sqlite3.Connection, newer_run_id: int
) -> dict[str, Any] | None:
    if newer_run_id <= 0:
        return None
    required = {
        "runtime_discovery_schedule_current",
        "runtime_discovery_schedule_state",
        "runtime_observation_runs",
        "runtime_observation_tools",
        "server_versions",
        "packages",
    }
    if not required.issubset(_tables(connection)):
        return None
    current = connection.execute(
        "SELECT profile_key FROM runtime_discovery_schedule_current WHERE singleton=1"
    ).fetchone()
    if current is None:
        return None
    row = connection.execute(
        """SELECT s.previous_compatible_run_id AS older_run_id,
                  s.runtime_observation_run_id AS newer_run_id,
                  s.added_tools,s.removed_tools,s.modified_tools,s.unchanged_tools,
                  nsv.server_identifier,
                  osv.server_version AS older_version,
                  nsv.server_version AS newer_version,
                  np.identifier AS package_identifier,
                  orun.artifact_sha256 AS older_artifact_sha256,
                  nrun.artifact_sha256 AS newer_artifact_sha256,
                  nrun.guard_version,nrun.sandbox_image,
                  nrun.completed_at
           FROM runtime_discovery_schedule_state s
           JOIN runtime_observation_runs nrun ON nrun.id=s.runtime_observation_run_id
           JOIN runtime_observation_runs orun ON orun.id=s.previous_compatible_run_id
           JOIN server_versions nsv ON nsv.id=nrun.server_version_id
           JOIN server_versions osv ON osv.id=orun.server_version_id
           JOIN packages np ON np.id=nrun.package_id
           WHERE s.profile_key=? AND s.state='completed'
             AND s.runtime_observation_run_id=?
             AND s.previous_compatible_run_id IS NOT NULL""",
        (current["profile_key"], newer_run_id),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    older_run_id = int(result["older_run_id"])

    result["added"] = [
        dict(item)
        for item in connection.execute(
            """SELECT n.name,n.definition_sha256 AS newer_sha256
               FROM runtime_observation_tools n
               LEFT JOIN runtime_observation_tools o
                 ON o.run_id=? AND o.name=n.name
               WHERE n.run_id=? AND o.name IS NULL
               ORDER BY n.name COLLATE BINARY LIMIT 256""",
            (older_run_id, newer_run_id),
        ).fetchall()
    ]
    result["removed"] = [
        dict(item)
        for item in connection.execute(
            """SELECT o.name,o.definition_sha256 AS older_sha256
               FROM runtime_observation_tools o
               LEFT JOIN runtime_observation_tools n
                 ON n.run_id=? AND n.name=o.name
               WHERE o.run_id=? AND n.name IS NULL
               ORDER BY o.name COLLATE BINARY LIMIT 256""",
            (newer_run_id, older_run_id),
        ).fetchall()
    ]
    result["modified"] = [
        dict(item)
        for item in connection.execute(
            """SELECT n.name,o.definition_sha256 AS older_sha256,
                      n.definition_sha256 AS newer_sha256
               FROM runtime_observation_tools n
               JOIN runtime_observation_tools o
                 ON o.run_id=? AND o.name=n.name
               WHERE n.run_id=? AND n.definition_json<>o.definition_json
               ORDER BY n.name COLLATE BINARY LIMIT 256""",
            (older_run_id, newer_run_id),
        ).fetchall()
    ]
    return result


def _runtime_coverage_panel(runtime: dict[str, Any]) -> str:
    eligible = int(runtime.get("eligible", 0))
    completed = int(runtime.get("completed", 0))
    failed = int(runtime.get("failed", 0))
    unsupported = int(runtime.get("unsupported_or_unresolvable", 0))
    never = int(runtime.get("never_attempted", 0))
    running = int(runtime.get("running", 0))
    drifted = int(runtime.get("drifted", 0))
    comparable = int(runtime.get("comparable", 0))
    percent = _percent(completed, eligible)
    profile = runtime.get("profile") or {}
    profile_detail = "Legacy observation counts; automatic scheduler profile not published"
    if profile:
        profile_detail = (
            "profile "
            + _short_hash(profile.get("profile_key"))
            + " · Guard "
            + _short_hash(profile.get("guard_sha256"))
            + " · probe "
            + _short_hash(profile.get("probe_profile_sha256"))
        )
    available_text = f"{percent:.1f}%" if runtime.get("available") else "Not started"
    return f"""<section class="panel"><div class="panel-heading"><div><p class="eyebrow">Native Guard discovery</p><h2>Automatic runtime coverage</h2></div><a href="/runtime-drift">View tool drift →</a></div><section class="cards">{_card('Runtime coverage', available_text, f'{completed:,} of {eligible:,} eligible npm stdio records')}{_card('Failed attempts', failed, 'Current runtime profile')}{_card('Unsupported / unresolvable', unsupported, 'Explicit terminal scheduler outcomes')}{_card('Never attempted', never, 'Eligible and not yet selected')}{_card('Running', running, 'Currently claimed scheduler records')}{_card('Interface drift', drifted, f'{comparable:,} completed observations have a prior compatible version', '/runtime-drift')}</section><progress value="{completed}" max="{max(eligible, 1)}">{percent:.1f}%</progress><p class="meta">{escape(profile_detail)}</p><p><strong>Boundary:</strong> runtime discovery sends <code>initialize</code>, <code>notifications/initialized</code>, and <code>tools/list</code> only. Tool-definition drift is observed interface change, not a vulnerability or safety verdict.</p></section>"""


def runtime_drift_page(
    result: dict[str, Any], *, public_readonly: bool = False
) -> str:
    from . import views

    rows = "".join(_drift_row(row) for row in result["rows"]) or (
        '<tr><td colspan="7" class="empty">No tool-definition drift has been recorded for the current runtime profile.</td></tr>'
    )
    total_pages = max(1, ceil(result["total"] / result["page_size"]))
    pagination = _pagination("/runtime-drift", result["page"], total_pages)
    body = f"""<section class="page-heading"><p class="eyebrow">Native Guard longitudinal observation</p><h1>Runtime tool-definition drift</h1><p>Completed discovery observations whose canonical MCP tool interface differs from the previous compatible server version.</p></section><div class="result-summary">{result['total']:,} drifted observation{'s' if result['total'] != 1 else ''} · page {result['page']} of {total_pages}</div><section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Server</th><th>Versions</th><th>Package</th><th>Added</th><th>Removed</th><th>Modified</th><th>Observed</th></tr></thead><tbody>{rows}</tbody></table></div></section>{pagination}<section class="notice"><strong>Interpretation boundary:</strong> a canonical definition changed. This is evidence of interface drift; it is not automatically a security finding.</section>"""
    return views.layout("Runtime tool drift", body, public_readonly=public_readonly)


def runtime_drift_detail_page(
    data: dict[str, Any], *, public_readonly: bool = False
) -> str:
    from . import views

    added = _tool_list(data.get("added") or [], "newer_sha256")
    removed = _tool_list(data.get("removed") or [], "older_sha256")
    modified = _modified_tool_list(data.get("modified") or [])
    server = str(data["server_identifier"])
    server_href = "/servers/" + quote(server, safe="")
    body = f"""<section class="page-heading"><p class="eyebrow">Runtime comparison</p><h1>{escape(server)}</h1><p><a href="{escape(server_href, quote=True)}">Server history</a> · package <code>{escape(str(data['package_identifier']))}</code></p></section><section class="cards">{_card('Older version', data['older_version'], 'Previous compatible observation')}{_card('Newer version', data['newer_version'], 'Current observation')}{_card('Added tools', int(data.get('added_tools') or 0), 'Canonical names newly exposed')}{_card('Removed tools', int(data.get('removed_tools') or 0), 'Canonical names no longer exposed')}{_card('Modified tools', int(data.get('modified_tools') or 0), 'Complete canonical definition changed')}{_card('Unchanged tools', int(data.get('unchanged_tools') or 0), 'Byte-equivalent canonical definitions')}</section><section class="panel"><h2>Observation identity</h2><dl class="facts"><div><dt>Older artifact SHA-256</dt><dd><code>{escape(str(data['older_artifact_sha256']))}</code></dd></div><div><dt>Newer artifact SHA-256</dt><dd><code>{escape(str(data['newer_artifact_sha256']))}</code></dd></div><div><dt>Native Guard</dt><dd><code>{escape(str(data['guard_version']))}</code></dd></div><div><dt>Sandbox image</dt><dd><code>{escape(str(data['sandbox_image']))}</code></dd></div><div><dt>Observed</dt><dd>{escape(str(data.get('completed_at') or 'Not recorded'))}</dd></div></dl></section><section class="panel"><h2>Added tools</h2>{added}</section><section class="panel"><h2>Removed tools</h2>{removed}</section><section class="panel"><h2>Modified tool definitions</h2>{modified}</section><section class="notice"><strong>Limit:</strong> this comparison is discovery-only. No MCP tool was invoked, and no safety or maliciousness conclusion is implied.</section>"""
    return views.layout(
        f"Runtime drift · {server}", body, public_readonly=public_readonly
    )


def _drift_row(row: dict[str, Any]) -> str:
    server = str(row.get("server_identifier") or "")
    detail = f"/runtime-drift/{int(row['newer_run_id'])}"
    return f"<tr><td><a href=\"{escape(detail, quote=True)}\">{escape(server)}</a></td><td><code>{escape(str(row.get('older_version') or '—'))}</code> → <code>{escape(str(row.get('newer_version') or '—'))}</code></td><td><code>{escape(str(row.get('package_identifier') or '—'))}</code></td><td>{int(row.get('added_tools') or 0):,}</td><td>{int(row.get('removed_tools') or 0):,}</td><td>{int(row.get('modified_tools') or 0):,}</td><td>{escape(str(row.get('completed_at') or '—'))}</td></tr>"


def _tool_list(items: list[dict[str, Any]], sha_field: str) -> str:
    if not items:
        return '<p class="empty">None.</p>'
    rows = "".join(
        f"<li><code>{escape(str(item['name']))}</code> · SHA-256 <code>{escape(_short_hash(item.get(sha_field)))}</code></li>"
        for item in items
    )
    return f'<ul class="evidence-list">{rows}</ul>'


def _modified_tool_list(items: list[dict[str, Any]]) -> str:
    if not items:
        return '<p class="empty">None.</p>'
    rows = "".join(
        f"<li><code>{escape(str(item['name']))}</code> · <code>{escape(_short_hash(item.get('older_sha256')))}</code> → <code>{escape(_short_hash(item.get('newer_sha256')))}</code></li>"
        for item in items
    )
    return f'<ul class="evidence-list">{rows}</ul>'


def _card(
    label: str,
    value: str | int,
    detail: str,
    href: str | None = None,
) -> str:
    detail_html = escape(str(detail))
    if href is not None:
        detail_html = f'<a href="{escape(href, quote=True)}">{detail_html}</a>'
    return f'<article class="card"><span>{escape(label)}</span><strong>{escape(str(value))}</strong><small>{detail_html}</small></article>'


def _pagination(base: str, page: int, total_pages: int) -> str:
    if total_pages <= 1:
        return ""
    links: list[str] = []
    if page > 1:
        links.append(f'<a href="{base}?page={page - 1}">← Previous</a>')
    links.append(f'<span>Page {page} of {total_pages}</span>')
    if page < total_pages:
        links.append(f'<a href="{base}?page={page + 1}">Next →</a>')
    return '<nav class="pagination" aria-label="Runtime drift pages">' + "".join(links) + "</nav>"


def _positive_integer(raw: Any, *, fallback: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def _percent(part: int, whole: int) -> float:
    return 0.0 if whole <= 0 else part * 100.0 / whole


def _short_hash(value: Any) -> str:
    text = "" if value is None else str(value)
    return text[:16] if text else "—"
