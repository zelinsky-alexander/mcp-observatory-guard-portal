"""Small server-rendered HTML views with explicit escaping."""

from __future__ import annotations

from html import escape
from math import ceil
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

PORTAL_NAME = "Open MCP Behavioral Assurance Observatory"


def layout(title: str, body: str) -> str:
    safe_title = escape(title)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title} · {PORTAL_NAME}</title><link rel="stylesheet" href="/static/portal.css"></head>
<body><header class="site-header"><div><a class="brand" href="/">MCP Observatory</a><span class="tagline">Evidence, provenance, and change over time</span></div>
<nav aria-label="Primary navigation"><a href="/">Dashboard</a><a href="/servers">Servers</a><a href="/reports/ecosystems">Ecosystems</a><a href="/jobs">Analysis queue</a></nav></header>
<main>{body}</main><footer>Results describe exact artifacts under documented analysis profiles. They do not prove safety or author intent.</footer></body></html>"""


def dashboard_page(data: dict[str, Any]) -> str:
    latest = data["latest_snapshot"]
    totals = data["totals"]
    analysis = data["analysis"]
    snapshot_text = "No imported snapshot"
    if latest:
        snapshot_text = f"{escape(_text(latest['completed_at']))} · {escape(_short_hash(latest['snapshot_sha256']))}"
    cards = "".join(
        _card(label, value, detail)
        for label, value, detail in (
            ("Servers", totals["servers"], "Distinct registry identifiers"),
            ("Immutable records", totals["immutable_versions"], "Version and metadata variants"),
            ("Completed analyses", analysis["completed"], "Static package analysis runs"),
            ("Review queue", analysis["unreviewed_high_or_critical"], "Unreviewed high or critical findings"),
        )
    )
    changes_rows = "".join(_server_row(row) for row in data["changes"]) or '<tr><td colspan="5" class="empty">No records are linked to the latest snapshot.</td></tr>'
    analysis_rows = "".join(_analysis_row(row) for row in analysis["recent"]) or '<tr><td colspan="5" class="empty">No static analysis runs are available.</td></tr>'
    jobs = data.get("portal_jobs")
    jobs_section = ""
    if jobs:
        rows = "".join(_job_row(job) for job in jobs["recent"]) or '<tr><td colspan="5" class="empty">No on-demand jobs.</td></tr>'
        jobs_section = f"""<section class="panel"><div class="panel-heading"><div><p class="eyebrow">Local orchestration</p><h2>On-demand analysis jobs</h2></div><a href="/jobs">View queue →</a></div>
<div class="table-wrap"><table><thead><tr><th>Job</th><th>Server</th><th>Package</th><th>Status</th><th>Requested</th></tr></thead><tbody>{rows}</tbody></table></div></section>"""
    body = f"""<section class="hero"><p class="eyebrow">Research portal</p><h1>{PORTAL_NAME}</h1><p>Browse Registry history and static package-analysis evidence produced by <code>mcp-observatory</code>.</p><p class="snapshot">Latest snapshot: {snapshot_text}</p></section>
<section class="cards">{cards}</section>
<section class="panel"><div class="panel-heading"><div><p class="eyebrow">Latest refresh</p><h2>Recently imported registry records</h2></div><a href="/servers">Browse all servers →</a></div><div class="table-wrap"><table><thead><tr><th>Server</th><th>Version</th><th>Package</th><th>Status</th><th>Updated</th></tr></thead><tbody>{changes_rows}</tbody></table></div></section>
<section class="panel"><div class="panel-heading"><div><p class="eyebrow">Static evidence</p><h2>Recent analysis runs</h2></div></div><div class="table-wrap"><table><thead><tr><th>Run</th><th>Server</th><th>Package</th><th>Findings</th><th>Started</th></tr></thead><tbody>{analysis_rows}</tbody></table></div></section>
{jobs_section}<section class="notice"><strong>Interpretation boundary:</strong> static analysis records observable package properties. It does not execute the MCP server and does not establish that the server is safe or malicious.</section>"""
    return layout("Dashboard", body)


def servers_page(result: dict[str, Any]) -> str:
    query = result["query"]
    ecosystem = result["ecosystem"]
    rows = "".join(_browser_server_row(row) for row in result["rows"]) or '<tr><td colspan="6" class="empty">No matching servers.</td></tr>'
    total_pages = max(1, ceil(result["total"] / result["page_size"]))
    pagination = _pagination(
        "/servers", query, result["page"], total_pages, ecosystem=ecosystem
    )
    ecosystem_input = (
        f'<input type="hidden" name="ecosystem" value="{escape(ecosystem, quote=True)}">'
        if ecosystem
        else ""
    )
    filter_summary = ""
    if ecosystem:
        filter_summary = (
            f' · ecosystem <code>{escape(ecosystem)}</code> '
            '<a href="/servers">Clear ecosystem filter</a>'
        )
    return layout("Servers", f"""<section class="page-heading"><p class="eyebrow">Official registry catalog</p><h1>Server browser</h1><p>One row per server identifier, showing its most recently imported metadata variant.</p></section>
<form class="search" method="get" action="/servers">{ecosystem_input}<label for="q">Search identifiers, descriptions, packages, repositories, and remote URLs</label><div><input id="q" name="q" value="{escape(query)}" maxlength="200" autocomplete="off"><button type="submit">Search</button></div></form>
<div class="result-summary">{result['total']:,} server identifiers · page {result['page']} of {total_pages}{filter_summary}</div><section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Server</th><th>Latest version</th><th>Versions</th><th>Package</th><th>Repository</th><th>Updated</th></tr></thead><tbody>{rows}</tbody></table></div></section>{pagination}""")


def ecosystem_report_page(rows: list[dict[str, Any]]) -> str:
    table_rows = "".join(_ecosystem_row(row) for row in rows) or (
        '<tr><td colspan="4" class="empty">No package declarations are available.</td></tr>'
    )
    return layout(
        "Package ecosystems",
        f"""<section class="page-heading"><p class="eyebrow">Catalog report</p><h1>Package ecosystems</h1><p>Package declarations grouped by their Registry ecosystem.</p></section>
<section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Ecosystem</th><th>Package records</th><th>Unique package identifiers</th><th>Server versions</th></tr></thead><tbody>{table_rows}</tbody></table></div></section>
<section class="notice"><strong>Counting boundary:</strong> package records include repeated declarations across immutable server-version records. A server version that declares packages from multiple ecosystems is counted once in each relevant ecosystem.</section>""",
    )


def server_detail_page(data: dict[str, Any]) -> str:
    identifier = data["server_identifier"]
    sections = "".join(_version_section(version) for version in data["versions"])
    return layout(identifier, f"""<section class="page-heading"><p class="eyebrow">Server record</p><h1>{escape(identifier)}</h1><p>{escape(_text(data.get('description'), 'No description supplied by the registry.'))}</p><div class="meta">{len(data['versions'])} immutable metadata record(s)</div></section>{sections}""")


def analysis_detail_page(run: dict[str, Any]) -> str:
    findings = "".join(f"""<article class="finding severity-{escape(_text(item['severity']))}"><div class="finding-header"><span class="badge">{escape(_text(item['severity']))}</span><strong>{escape(_text(item['title']))}</strong></div><div class="meta">{escape(_text(item['rule_id']))} · {escape(_text(item['disposition']))} · confidence {escape(_text(item['confidence']))}</div><p><code>{escape(_text(item['subject_path']))}{':' + str(item['line_number']) if item['line_number'] else ''}</code></p><p>{escape(_text(item['explanation']))}</p></article>""" for item in run["findings"]) or '<p class="empty">No findings were recorded for this run.</p>'
    evidence = "".join(f"<li><code>{escape(_text(item['relative_path']))}</code> · {item['byte_size']:,} bytes · {escape(_short_hash(item['sha256']))}</li>" for item in run["evidence_files"]) or "<li>No finalized evidence rows.</li>"
    body = f"""<section class="page-heading"><p class="eyebrow">Static analysis run #{run['id']}</p><h1>{escape(_text(run['server_identifier']))} <span class="muted">{escape(_text(run['server_version']))}</span></h1><p><span class="badge status-{escape(_text(run['status']))}">{escape(_text(run['status']))}</span> package <code>{escape(_text(run['package_identifier']))}</code></p></section>
<section class="cards">{_card('Artifact', _short_hash(run.get('artifact_sha256')), 'SHA-256 digest')}{_card('Ruleset', _text(run.get('ruleset_version')), 'Static-analysis policy')}{_card('Network', _text(run.get('network_mode')), 'Worker network profile')}{_card('Integrity', 'verified' if run.get('integrity_verified') else 'not verified', 'Published package integrity')}</section><section class="panel"><h2>Findings</h2>{findings}</section><section class="panel"><h2>Evidence manifest</h2><ul class="evidence-list">{evidence}</ul></section><section class="notice"><strong>Limit:</strong> this is static package analysis. It does not execute package entry points, lifecycle scripts, or MCP tools.</section>"""
    return layout(f"Analysis #{run['id']}", body)


def jobs_page(summary: dict[str, Any] | None) -> str:
    if summary is None:
        return layout("Analysis queue", '<section class="page-heading"><p class="eyebrow">On-demand analysis</p><h1>Analysis queue disabled</h1><p>Enable the local analysis configuration to submit static package jobs.</p></section>')
    rows = "".join(_job_row(job) for job in summary["recent"]) or '<tr><td colspan="5" class="empty">No analysis jobs have been submitted.</td></tr>'
    cards = "".join(_card(name.title(), summary[name], "Portal-owned job state") for name in ("queued", "running", "completed", "failed"))
    return layout("Analysis queue", f"""<section class="page-heading"><p class="eyebrow">On-demand analysis</p><h1>Static-analysis queue</h1><p>Only exact npm package records already present in the Observatory catalog can be submitted.</p></section><section class="cards">{cards}</section><section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Job</th><th>Server</th><th>Package</th><th>Status</th><th>Requested</th></tr></thead><tbody>{rows}</tbody></table></div></section>""")


def job_detail_page(job: dict[str, Any]) -> str:
    analysis_link = f'<a href="/analyses/{job["analysis_run_id"]}">Open Observatory analysis #{job["analysis_run_id"]}</a>' if job.get("analysis_run_id") else "No Observatory analysis run recorded."
    logs = ""
    for title, value in (("Standard output", job.get("stdout_excerpt")), ("Standard error", job.get("stderr_excerpt"))):
        if value:
            logs += f"<h3>{title}</h3><pre>{escape(_text(value))}</pre>"
    error = f'<section class="notice danger"><strong>Failure:</strong> {escape(_text(job.get("error_message")))}</section>' if job.get("error_message") else ""
    body = f"""<section class="page-heading"><p class="eyebrow">Analysis job #{job['id']}</p><h1>{escape(_text(job['server_identifier']))} <span class="muted">{escape(_text(job['server_version']))}</span></h1><p><span class="badge status-{escape(_text(job['status']))}">{escape(_text(job['status']))}</span> package <code>{escape(_text(job['package_identifier']))}</code></p></section>{error}<section class="panel"><dl class="facts"><div><dt>Requested</dt><dd>{escape(_text(job['requested_at']))}</dd></div><div><dt>Started</dt><dd>{escape(_text(job.get('started_at'), 'Not started'))}</dd></div><div><dt>Completed</dt><dd>{escape(_text(job.get('completed_at'), 'Not completed'))}</dd></div><div><dt>Artifact</dt><dd><code>{escape(_text(job.get('artifact_sha256'), 'Not available'))}</code></dd></div><div><dt>Result</dt><dd>{analysis_link}</dd></div><div><dt>Reused existing</dt><dd>{'yes' if job.get('reused_existing') else 'no'}</dd></div></dl></section><section class="panel"><h2>Bounded worker output</h2>{logs or '<p class="empty">No worker output recorded.</p>'}</section>"""
    return layout(f"Job #{job['id']}", body)


def error_page(status: int, title: str, message: str) -> str:
    return layout(title, f'<section class="page-heading"><p class="eyebrow">HTTP {status}</p><h1>{escape(title)}</h1><p>{escape(message)}</p><p><a href="/">Return to dashboard</a></p></section>')


def _version_section(version: dict[str, Any]) -> str:
    repository = version.get("repository")
    repository_html = "Not declared"
    if repository and repository.get("url"):
        raw = _text(repository["url"])
        parsed = urlsplit(raw)
        repository_html = f'<a href="{escape(raw, quote=True)}" rel="noreferrer">{escape(raw)}</a>' if parsed.scheme in ("http", "https") and parsed.netloc else escape(raw)
    packages = "".join(_package_block(package) for package in version["packages"]) or '<p class="empty">No package declaration.</p>'
    remotes = "".join(f"<li><code>{escape(_text(remote['transport']))}</code> {escape(_text(remote['url']))}</li>" for remote in version["remotes"]) or "<li>No remote endpoint declaration.</li>"
    analyses = "".join(_analysis_card(run) for run in version["analyses"]) or '<p class="empty">No static analysis has been recorded for this metadata variant.</p>'
    return f"""<section class="panel version-panel"><div class="panel-heading"><div><p class="eyebrow">Version</p><h2>{escape(_text(version['server_version']))}</h2></div><span class="badge">{escape(_text(version['registry_status'], 'unknown'))}</span></div><dl class="facts"><div><dt>Canonical digest</dt><dd><code>{escape(_text(version['canonical_sha256']))}</code></dd></div><div><dt>Published</dt><dd>{escape(_text(version['published_at'], 'Not supplied'))}</dd></div><div><dt>Updated</dt><dd>{escape(_text(version['updated_at'], 'Not supplied'))}</dd></div><div><dt>Repository</dt><dd>{repository_html}</dd></div></dl><h3>Packages</h3>{packages}<h3>Remote endpoints</h3><ul>{remotes}</ul><h3>Static analysis history</h3><div class="analysis-grid">{analyses}</div></section>"""


def _package_block(package: dict[str, Any]) -> str:
    arguments = " ".join(f"<code>{escape(_text(item['argument_value'], '<declared without literal value>'))}</code>" for item in package["arguments"]) or "None declared"
    environment = "".join(f"<li><code>{escape(_text(item['name']))}</code> {'required' if item['required'] else 'optional'} — {escape(_text(item['description'], 'No description'))}</li>" for item in package["environment"]) or "<li>None declared</li>"
    request = package.get("analysis_request")
    action = ""
    if request:
        action = f"""<form class="analysis-form" method="post" action="/analysis-requests"><input type="hidden" name="server_version_id" value="{request['server_version_id']}"><input type="hidden" name="package_id" value="{request['package_id']}"><input type="hidden" name="csrf_token" value="{escape(request['csrf_token'], quote=True)}"><button type="submit">Queue static analysis</button><small>Compatible completed results may be reused. Force mode is not exposed.</small></form>"""
    elif package.get("analysis_unavailable_reason"):
        action = f'<p class="meta">Analysis unavailable: {escape(_text(package["analysis_unavailable_reason"]))}</p>'
    return f"""<article class="package"><strong>{escape(_text(package['identifier']))}</strong><div class="meta">{escape(_text(package['registry_type']))} · {escape(_text(package['transport']))} · declared version {escape(_text(package['version'], 'not supplied'))}</div><p><span class="label">Arguments:</span> {arguments}</p><p class="label">Environment declarations:</p><ul>{environment}</ul>{action}</article>"""


def _analysis_card(run: dict[str, Any]) -> str:
    findings = int(run.get("finding_count") or 0)
    summary = f"{findings} finding{'s' if findings != 1 else ''}"
    return f'<a class="analysis-card" href="/analyses/{run["id"]}"><span class="badge status-{escape(_text(run["status"]))}">{escape(_text(run["status"]))}</span><strong>Run #{run["id"]}</strong><span>{escape(_text(run["package_identifier"]))}</span><span class="meta">{summary} · {escape(_text(run["started_at"]))}</span></a>'


def _server_row(row: dict[str, Any]) -> str:
    href = "/servers/" + quote(_text(row["server_identifier"]), safe="")
    return f'<tr><td><a href="{href}">{escape(_text(row["server_identifier"]))}</a><div class="truncate">{escape(_text(row["description"]))}</div></td><td><code>{escape(_text(row["server_version"]))}</code></td><td>{escape(_text(row["package_identifier"], "—"))}</td><td><span class="badge">{escape(_text(row["registry_status"], "unknown"))}</span></td><td>{escape(_text(row["updated_at"] or row["published_at"], "—"))}</td></tr>'


def _browser_server_row(row: dict[str, Any]) -> str:
    href = "/servers/" + quote(_text(row["server_identifier"]), safe="")
    return f'<tr><td><a href="{href}">{escape(_text(row["server_identifier"]))}</a><div class="truncate">{escape(_text(row["description"]))}</div></td><td><code>{escape(_text(row["server_version"]))}</code></td><td>{int(row["version_count"]):,}</td><td>{escape(_text(row["package_identifier"], "—"))}<div class="meta">{escape(_text(row["package_transport"]))}</div></td><td>{escape(_text(row["repository_host"], "—"))}</td><td>{escape(_text(row["updated_at"] or row["published_at"], "—"))}</td></tr>'


def _ecosystem_row(row: dict[str, Any]) -> str:
    ecosystem = _text(row["ecosystem"])
    href = "/servers?" + urlencode({"ecosystem": ecosystem})
    return (
        f'<tr><td><a href="{escape(href, quote=True)}"><code>{escape(ecosystem)}</code></a></td>'
        f"<td>{int(row['package_records']):,}</td>"
        f"<td>{int(row['unique_packages']):,}</td>"
        f"<td>{int(row['server_versions']):,}</td></tr>"
    )


def _analysis_row(row: dict[str, Any]) -> str:
    findings = f"{int(row.get('critical_count') or 0)} critical · {int(row.get('high_count') or 0)} high · {int(row.get('medium_count') or 0)} medium"
    return f'<tr><td><a href="/analyses/{row["id"]}">#{row["id"]}</a> <span class="badge status-{escape(_text(row["status"]))}">{escape(_text(row["status"]))}</span></td><td>{escape(_text(row["server_identifier"]))}<div class="meta">{escape(_text(row["server_version"]))}</div></td><td>{escape(_text(row["package_identifier"]))}</td><td>{findings}</td><td>{escape(_text(row["started_at"]))}</td></tr>'


def _job_row(job: dict[str, Any]) -> str:
    return f'<tr><td><a href="/jobs/{job["id"]}">#{job["id"]}</a></td><td>{escape(_text(job["server_identifier"]))}<div class="meta">{escape(_text(job["server_version"]))}</div></td><td>{escape(_text(job["package_identifier"]))}</td><td><span class="badge status-{escape(_text(job["status"]))}">{escape(_text(job["status"]))}</span></td><td>{escape(_text(job["requested_at"]))}</td></tr>'


def _card(label: str, value: Any, detail: str) -> str:
    return f'<article class="card"><span>{escape(str(label))}</span><strong>{escape(str(value))}</strong><small>{escape(detail)}</small></article>'


def _pagination(
    base: str,
    query: str,
    page: int,
    total_pages: int,
    *,
    ecosystem: str = "",
) -> str:
    if total_pages <= 1:
        return ""
    links = []
    if page > 1:
        links.append(
            _page_link(base, query, page - 1, "← Previous", ecosystem=ecosystem)
        )
    links.append(f"<span>Page {page} of {total_pages}</span>")
    if page < total_pages:
        links.append(_page_link(base, query, page + 1, "Next →", ecosystem=ecosystem))
    return '<nav class="pagination" aria-label="Server pages">' + "".join(links) + "</nav>"


def _page_link(
    base: str, query: str, page: int, label: str, *, ecosystem: str = ""
) -> str:
    params: dict[str, Any] = {"page": page}
    if query:
        params["q"] = query
    if ecosystem:
        params["ecosystem"] = ecosystem
    href = base + "?" + urlencode(params)
    return f'<a href="{escape(href, quote=True)}">{escape(label)}</a>'


def _short_hash(value: Any) -> str:
    text = _text(value, "—")
    return text if len(text) <= 16 else text[:12] + "…"


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value)
    return text if text else fallback
