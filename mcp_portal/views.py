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
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title} · {PORTAL_NAME}</title>
  <link rel="stylesheet" href="/static/portal.css">
</head>
<body>
  <header class="site-header">
    <div>
      <a class="brand" href="/">MCP Observatory</a>
      <span class="tagline">Evidence, provenance, and change over time</span>
    </div>
    <nav aria-label="Primary navigation">
      <a href="/">Dashboard</a>
      <a href="/servers">Servers</a>
    </nav>
  </header>
  <main>{body}</main>
  <footer>
    Results describe exact artifacts under documented analysis profiles. They do not prove safety or author intent.
  </footer>
</body>
</html>"""


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
            ("Immutable records", totals["immutable_versions"], "Version and canonical metadata variants"),
            ("Completed analyses", analysis["completed"], "Static package analysis runs"),
            ("Review queue", analysis["unreviewed_high_or_critical"], "Unreviewed high or critical findings"),
        )
    )

    changes_rows = "".join(_server_row(row) for row in data["changes"])
    if not changes_rows:
        changes_rows = '<tr><td colspan="5" class="empty">No records are linked to the latest snapshot.</td></tr>'

    analysis_rows = "".join(_analysis_row(row) for row in analysis["recent"])
    if not analysis_rows:
        analysis_rows = '<tr><td colspan="5" class="empty">No static analysis runs are available.</td></tr>'

    body = f"""
<section class="hero">
  <p class="eyebrow">Read-only research portal</p>
  <h1>{PORTAL_NAME}</h1>
  <p>Browse Official MCP Registry history and static package-analysis evidence produced by <code>mcp-observatory</code>.</p>
  <p class="snapshot">Latest snapshot: {snapshot_text}</p>
</section>
<section class="cards">{cards}</section>
<section class="panel">
  <div class="panel-heading">
    <div><p class="eyebrow">Latest refresh</p><h2>Recently imported registry records</h2></div>
    <a href="/servers">Browse all servers →</a>
  </div>
  <div class="table-wrap"><table>
    <thead><tr><th>Server</th><th>Version</th><th>Package</th><th>Status</th><th>Updated</th></tr></thead>
    <tbody>{changes_rows}</tbody>
  </table></div>
</section>
<section class="panel">
  <div class="panel-heading"><div><p class="eyebrow">Static evidence</p><h2>Recent analysis runs</h2></div></div>
  <div class="table-wrap"><table>
    <thead><tr><th>Run</th><th>Server</th><th>Package</th><th>Findings</th><th>Started</th></tr></thead>
    <tbody>{analysis_rows}</tbody>
  </table></div>
</section>
<section class="notice"><strong>Interpretation boundary:</strong> a completed static analysis records observable package properties. It does not execute the MCP server and does not establish that the server is safe or malicious.</section>
"""
    return layout("Dashboard", body)


def servers_page(result: dict[str, Any]) -> str:
    query = result["query"]
    rows = "".join(_browser_server_row(row) for row in result["rows"])
    if not rows:
        rows = '<tr><td colspan="6" class="empty">No matching servers.</td></tr>'

    total_pages = max(1, ceil(result["total"] / result["page_size"]))
    pagination = _pagination("/servers", query, result["page"], total_pages)
    body = f"""
<section class="page-heading">
  <p class="eyebrow">Official registry catalog</p>
  <h1>Server browser</h1>
  <p>One row per server identifier, showing its most recently imported metadata variant.</p>
</section>
<form class="search" method="get" action="/servers">
  <label for="q">Search identifiers, descriptions, packages, repositories, and remote URLs</label>
  <div><input id="q" name="q" value="{escape(query)}" maxlength="200" autocomplete="off"><button type="submit">Search</button></div>
</form>
<div class="result-summary">{result['total']:,} server identifiers · page {result['page']} of {total_pages}</div>
<section class="panel compact">
  <div class="table-wrap"><table>
    <thead><tr><th>Server</th><th>Latest version</th><th>Versions</th><th>Package</th><th>Repository</th><th>Updated</th></tr></thead>
    <tbody>{rows}</tbody>
  </table></div>
</section>
{pagination}
"""
    return layout("Servers", body)


def server_detail_page(data: dict[str, Any]) -> str:
    identifier = data["server_identifier"]
    version_sections = "".join(_version_section(version) for version in data["versions"])
    body = f"""
<section class="page-heading">
  <p class="eyebrow">Server record</p>
  <h1>{escape(identifier)}</h1>
  <p>{escape(_text(data.get('description'), 'No description supplied by the registry.'))}</p>
  <div class="meta">{len(data['versions'])} immutable metadata record(s)</div>
</section>
{version_sections}
"""
    return layout(identifier, body)


def analysis_detail_page(run: dict[str, Any]) -> str:
    findings = "".join(
        f"""<article class="finding severity-{escape(_text(item['severity']))}">
          <div class="finding-header"><span class="badge">{escape(_text(item['severity']))}</span><strong>{escape(_text(item['title']))}</strong></div>
          <div class="meta">{escape(_text(item['rule_id']))} · {escape(_text(item['disposition']))} · confidence {escape(_text(item['confidence']))}</div>
          <p><code>{escape(_text(item['subject_path']))}{':' + str(item['line_number']) if item['line_number'] else ''}</code></p>
          <p>{escape(_text(item['explanation']))}</p>
        </article>"""
        for item in run["findings"]
    ) or '<p class="empty">No findings were recorded for this run.</p>'

    evidence = "".join(
        f"<li><code>{escape(_text(item['relative_path']))}</code> · {item['byte_size']:,} bytes · {escape(_short_hash(item['sha256']))}</li>"
        for item in run["evidence_files"]
    ) or "<li>No finalized evidence rows.</li>"

    body = f"""
<section class="page-heading">
  <p class="eyebrow">Static analysis run #{run['id']}</p>
  <h1>{escape(_text(run['server_identifier']))} <span class="muted">{escape(_text(run['server_version']))}</span></h1>
  <p><span class="badge status-{escape(_text(run['status']))}">{escape(_text(run['status']))}</span> package <code>{escape(_text(run['package_identifier']))}</code></p>
</section>
<section class="cards">
  {_card('Artifact', _short_hash(run.get('artifact_sha256')), 'SHA-256 digest')}
  {_card('Ruleset', _text(run.get('ruleset_version')), 'Static-analysis policy')}
  {_card('Network', _text(run.get('network_mode')), 'Worker network profile')}
  {_card('Integrity', 'verified' if run.get('integrity_verified') else 'not verified', 'Published package integrity')}
</section>
<section class="panel"><h2>Findings</h2>{findings}</section>
<section class="panel"><h2>Evidence manifest</h2><ul class="evidence-list">{evidence}</ul></section>
<section class="notice"><strong>Limit:</strong> this is static package analysis. It does not execute package entry points, lifecycle scripts, or MCP tools.</section>
"""
    return layout(f"Analysis #{run['id']}", body)


def error_page(status: int, title: str, message: str) -> str:
    body = f"""<section class="page-heading"><p class="eyebrow">HTTP {status}</p><h1>{escape(title)}</h1><p>{escape(message)}</p><p><a href="/">Return to dashboard</a></p></section>"""
    return layout(title, body)


def _version_section(version: dict[str, Any]) -> str:
    repository = version.get("repository")
    repository_html = "Not declared"
    if repository and repository.get("url"):
        raw_url = _text(repository["url"])
        parsed = urlsplit(raw_url)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            url = escape(raw_url, quote=True)
            repository_html = f'<a href="{url}" rel="noreferrer">{escape(raw_url)}</a>'
        else:
            repository_html = escape(raw_url)

    packages = "".join(_package_block(package) for package in version["packages"])
    if not packages:
        packages = '<p class="empty">No package declaration.</p>'

    remotes = "".join(
        f"<li><code>{escape(_text(remote['transport']))}</code> {escape(_text(remote['url']))}</li>"
        for remote in version["remotes"]
    ) or "<li>No remote endpoint declaration.</li>"

    analyses = "".join(_analysis_card(run) for run in version["analyses"])
    if not analyses:
        analyses = '<p class="empty">No static analysis has been recorded for this metadata variant.</p>'

    return f"""
<section class="panel version-panel">
  <div class="panel-heading">
    <div><p class="eyebrow">Version</p><h2>{escape(_text(version['server_version']))}</h2></div>
    <span class="badge">{escape(_text(version['registry_status'], 'unknown'))}</span>
  </div>
  <dl class="facts">
    <div><dt>Canonical digest</dt><dd><code>{escape(_text(version['canonical_sha256']))}</code></dd></div>
    <div><dt>Published</dt><dd>{escape(_text(version['published_at'], 'Not supplied'))}</dd></div>
    <div><dt>Updated</dt><dd>{escape(_text(version['updated_at'], 'Not supplied'))}</dd></div>
    <div><dt>Repository</dt><dd>{repository_html}</dd></div>
  </dl>
  <h3>Packages</h3>{packages}
  <h3>Remote endpoints</h3><ul>{remotes}</ul>
  <h3>Static analysis history</h3><div class="analysis-grid">{analyses}</div>
</section>"""


def _package_block(package: dict[str, Any]) -> str:
    arguments = " ".join(
        f"<code>{escape(_text(argument['argument_value'], '<declared without literal value>'))}</code>"
        for argument in package["arguments"]
    ) or "None declared"
    environment = "".join(
        f"<li><code>{escape(_text(item['name']))}</code> {'required' if item['required'] else 'optional'} — {escape(_text(item['description'], 'No description'))}</li>"
        for item in package["environment"]
    ) or "<li>None declared</li>"
    return f"""<article class="package">
      <strong>{escape(_text(package['identifier']))}</strong>
      <div class="meta">{escape(_text(package['registry_type']))} · {escape(_text(package['transport']))} · declared version {escape(_text(package['version'], 'not supplied'))}</div>
      <p><span class="label">Arguments:</span> {arguments}</p>
      <p class="label">Environment declarations:</p><ul>{environment}</ul>
    </article>"""


def _analysis_card(run: dict[str, Any]) -> str:
    findings = int(run.get("finding_count") or 0)
    summary = f"{findings} finding{'s' if findings != 1 else ''}"
    if run.get("critical_count") or run.get("high_count"):
        summary += f" · {int(run.get('critical_count') or 0)} critical · {int(run.get('high_count') or 0)} high"
    return f"""<a class="analysis-card" href="/analyses/{run['id']}">
      <span class="badge status-{escape(_text(run['status']))}">{escape(_text(run['status']))}</span>
      <strong>Run #{run['id']}</strong>
      <span>{escape(_text(run['package_identifier']))}</span>
      <span class="meta">{summary} · {escape(_text(run['started_at']))}</span>
    </a>"""


def _server_row(row: dict[str, Any]) -> str:
    href = "/servers/" + quote(_text(row["server_identifier"]), safe="")
    return f"""<tr>
      <td><a href="{href}">{escape(_text(row['server_identifier']))}</a><div class="truncate">{escape(_text(row['description']))}</div></td>
      <td><code>{escape(_text(row['server_version']))}</code></td>
      <td>{escape(_text(row['package_identifier'], '—'))}</td>
      <td><span class="badge">{escape(_text(row['registry_status'], 'unknown'))}</span></td>
      <td>{escape(_text(row['updated_at'] or row['published_at'], '—'))}</td>
    </tr>"""


def _browser_server_row(row: dict[str, Any]) -> str:
    href = "/servers/" + quote(_text(row["server_identifier"]), safe="")
    return f"""<tr>
      <td><a href="{href}">{escape(_text(row['server_identifier']))}</a><div class="truncate">{escape(_text(row['description']))}</div></td>
      <td><code>{escape(_text(row['server_version']))}</code></td>
      <td>{int(row['version_count']):,}</td>
      <td>{escape(_text(row['package_identifier'], '—'))}<div class="meta">{escape(_text(row['package_transport']))}</div></td>
      <td>{escape(_text(row['repository_host'], '—'))}</td>
      <td>{escape(_text(row['updated_at'] or row['published_at'], '—'))}</td>
    </tr>"""


def _analysis_row(row: dict[str, Any]) -> str:
    findings = f"{int(row.get('critical_count') or 0)} critical · {int(row.get('high_count') or 0)} high · {int(row.get('medium_count') or 0)} medium"
    return f"""<tr>
      <td><a href="/analyses/{row['id']}">#{row['id']}</a> <span class="badge status-{escape(_text(row['status']))}">{escape(_text(row['status']))}</span></td>
      <td>{escape(_text(row['server_identifier']))}<div class="meta">{escape(_text(row['server_version']))}</div></td>
      <td>{escape(_text(row['package_identifier']))}</td>
      <td>{findings}</td>
      <td>{escape(_text(row['started_at']))}</td>
    </tr>"""


def _card(label: str, value: Any, detail: str) -> str:
    return f"""<article class="card"><span>{escape(str(label))}</span><strong>{escape(str(value))}</strong><small>{escape(detail)}</small></article>"""


def _pagination(base: str, query: str, page: int, total_pages: int) -> str:
    if total_pages <= 1:
        return ""
    links: list[str] = []
    if page > 1:
        links.append(_page_link(base, query, page - 1, "← Previous"))
    links.append(f"<span>Page {page} of {total_pages}</span>")
    if page < total_pages:
        links.append(_page_link(base, query, page + 1, "Next →"))
    return '<nav class="pagination" aria-label="Server pages">' + "".join(links) + "</nav>"


def _page_link(base: str, query: str, page: int, label: str) -> str:
    params = {"page": page}
    if query:
        params["q"] = query
    return f'<a href="{base}?{urlencode(params)}">{escape(label)}</a>'


def _short_hash(value: Any) -> str:
    text = _text(value, "—")
    return text if len(text) <= 16 else text[:12] + "…"


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = str(value)
    return text if text else fallback
