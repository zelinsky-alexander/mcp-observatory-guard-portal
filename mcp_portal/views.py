"""Small server-rendered HTML views with explicit escaping."""

from __future__ import annotations

from html import escape
from math import ceil
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

PORTAL_NAME = "Open MCP Behavioral Assurance Observatory"


def layout(title: str, body: str, *, public_readonly: bool = False) -> str:
    safe_title = escape(title)
    local_jobs = "" if public_readonly else '<a href="/jobs">Local jobs</a>'
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title} · {PORTAL_NAME}</title><link rel="stylesheet" href="/static/portal.css"></head>
<body><header class="site-header"><div><a class="brand" href="/">MCP Observatory</a><span class="tagline">Evidence, provenance, and change over time</span></div>
<nav aria-label="Primary navigation"><a href="/">Dashboard</a><a href="/servers">Servers</a><a href="/reports/ecosystems">Ecosystems</a>{local_jobs}<a href="/about">About</a><a href="/methodology">Methodology</a></nav></header>
<aside class="independence-notice"><strong>Independent security research project.</strong> Not affiliated with or endorsed by the Model Context Protocol project, the Official MCP Registry, package registries, or listed publishers.</aside>
<main>{body}</main><footer><p>Results describe exact artifacts under documented analysis profiles. They do not prove safety or author intent.</p><nav aria-label="Research information"><a href="/about">About</a><a href="/methodology">Methodology</a><a href="/data-sources">Data Sources</a><a href="/disclaimer">Disclaimer</a><a href="/privacy">Privacy</a><a href="/corrections">Corrections</a></nav></footer></body></html>"""


def dashboard_page(data: dict[str, Any], *, public_readonly: bool = False) -> str:
    latest = data["latest_snapshot"]
    totals = data["totals"]
    analysis = data["analysis"]
    snapshot_text = "No imported snapshot"
    if latest:
        snapshot_text = f"{escape(_text(latest['completed_at']))} · {escape(_short_hash(latest['snapshot_sha256']))}"
    cards = "".join(
        _card(label, value, detail, detail_href)
        for label, value, detail, detail_href in (
            ("Servers", totals["servers"], "Distinct registry identifiers", None),
            (
                "Immutable records",
                totals["immutable_versions"],
                "Version and metadata variants",
                None,
            ),
            (
                "Completed analyses",
                analysis["completed"],
                "Static package analysis runs",
                None,
            ),
            (
                "Review queue",
                analysis["unreviewed_high_or_critical"],
                "Unreviewed high or critical findings",
                "/findings/unreviewed-high-or-critical",
            ),
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
    return layout("Dashboard", body, public_readonly=public_readonly)


def servers_page(result: dict[str, Any], *, public_readonly: bool = False) -> str:
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
<div class="result-summary">{result['total']:,} server identifiers · page {result['page']} of {total_pages}{filter_summary}</div><section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Server</th><th>Latest version</th><th>Versions</th><th>Package</th><th>Repository</th><th>Updated</th></tr></thead><tbody>{rows}</tbody></table></div></section>{pagination}""", public_readonly=public_readonly)


def ecosystem_report_page(rows: list[dict[str, Any]], *, public_readonly: bool = False) -> str:
    table_rows = "".join(_ecosystem_row(row) for row in rows) or (
        '<tr><td colspan="4" class="empty">No package declarations are available.</td></tr>'
    )
    return layout(
        "Package ecosystems",
        f"""<section class="page-heading"><p class="eyebrow">Catalog report</p><h1>Package ecosystems</h1><p>Package declarations grouped by their Registry ecosystem.</p></section>
<section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Ecosystem</th><th>Package records</th><th>Unique package identifiers</th><th>Server versions</th></tr></thead><tbody>{table_rows}</tbody></table></div></section>
<section class="notice"><strong>Counting boundary:</strong> package records include repeated declarations across immutable server-version records. A server version that declares packages from multiple ecosystems is counted once in each relevant ecosystem.</section>""",
        public_readonly=public_readonly,
    )


def unreviewed_findings_page(result: dict[str, Any], *, public_readonly: bool = False) -> str:
    rows = "".join(_unreviewed_finding_row(row) for row in result["rows"]) or (
        '<tr><td colspan="6" class="empty">'
        "No unreviewed high or critical findings.</td></tr>"
    )
    total_pages = max(1, ceil(result["total"] / result["page_size"]))
    pagination = _pagination(
        "/findings/unreviewed-high-or-critical",
        "",
        result["page"],
        total_pages,
        aria_label="Finding pages",
    )
    return layout(
        "Unreviewed high or critical findings",
        f"""<section class="page-heading"><p class="eyebrow">Review queue</p><h1>Unreviewed high or critical findings</h1><p>Static-analysis findings awaiting a review disposition.</p></section>
<div class="result-summary">{result['total']:,} finding{'s' if result['total'] != 1 else ''} · page {result['page']} of {total_pages}</div>
<section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Severity</th><th>Finding</th><th>Server</th><th>Package</th><th>Location</th><th>Analyzed</th></tr></thead><tbody>{rows}</tbody></table></div></section>{pagination}
<section class="notice"><strong>Review boundary:</strong> an unreviewed finding is an observation awaiting disposition, not a safety verdict.</section>""",
        public_readonly=public_readonly,
    )


def server_detail_page(data: dict[str, Any], *, public_readonly: bool = False) -> str:
    identifier = data["server_identifier"]
    sections = "".join(_version_section(version) for version in data["versions"])
    return layout(identifier, f"""<section class="page-heading"><p class="eyebrow">Server record</p><h1>{escape(identifier)}</h1><p>{escape(_text(data.get('description'), 'No description supplied by the registry.'))}</p><div class="meta">{len(data['versions'])} immutable metadata record(s)</div></section>{sections}""", public_readonly=public_readonly)


def analysis_detail_page(run: dict[str, Any], *, public_readonly: bool = False) -> str:
    findings = "".join(
        _finding_article(item) for item in run["findings"]
    ) or '<p class="empty">No findings were recorded for this run.</p>'
    evidence = "".join(f"<li><code>{escape(_text(item['relative_path']))}</code> · {item['byte_size']:,} bytes · SHA-256 <code>{escape(_text(item['sha256']))}</code></li>" for item in run["evidence_files"]) or "<li>No finalized evidence rows.</li>"
    body = f"""<section class="page-heading"><p class="eyebrow">Static analysis run #{run['id']}</p><h1>{escape(_text(run['server_identifier']))} <span class="muted">{escape(_text(run['server_version']))}</span></h1><p><span class="badge status-{escape(_text(run['status']))}">{escape(_text(run['status']))}</span> package <code>{escape(_text(run['package_identifier']))}</code></p></section>
<section class="cards">{_card('Artifact', _text(run.get('artifact_sha256')), 'SHA-256 digest')}{_card('Ruleset', _text(run.get('ruleset_version')), 'Static-analysis policy')}{_card('Network', _text(run.get('network_mode')), 'Worker network profile')}{_card('Integrity', 'verified' if run.get('integrity_verified') else 'not verified', 'Published package integrity')}</section><section class="panel"><h2>Analysis provenance</h2><dl class="facts"><div><dt>Analyzer</dt><dd>{escape(_text(run.get('analyzer_name'), 'Not recorded'))} {escape(_text(run.get('analyzer_version')))}</dd></div><div><dt>Analysis type</dt><dd><code>{escape(_text(run.get('analysis_type'), 'Not recorded'))}</code></dd></div><div><dt>Started</dt><dd>{escape(_text(run.get('started_at'), 'Not recorded'))}</dd></div><div><dt>Completed</dt><dd>{escape(_text(run.get('completed_at'), 'Not recorded'))}</dd></div><div><dt>Base image</dt><dd><code>{escape(_text(run.get('base_image_ref'), 'Not recorded'))}</code></dd></div><div><dt>Base image digest</dt><dd><code>{escape(_text(run.get('base_image_digest'), 'Not recorded'))}</code></dd></div><div><dt>Published integrity</dt><dd><code>{escape(_text(run.get('published_integrity'), 'Not recorded'))}</code></dd></div><div><dt>Container identity</dt><dd><code>{escape(_text(run.get('container_user'), 'Not recorded'))}</code></dd></div></dl></section><section class="panel"><h2>Findings</h2>{findings}</section><section class="panel"><h2>Evidence manifest</h2><ul class="evidence-list">{evidence}</ul></section><section class="notice"><strong>Limit:</strong> this is static package analysis. It does not execute package entry points, lifecycle scripts, or MCP tools.</section>"""
    return layout(f"Analysis #{run['id']}", body, public_readonly=public_readonly)


def finding_source_page(source: dict[str, Any]) -> str:
    target_line = source.get("line_number")
    rendered_lines = []
    source_lines = source["content"].splitlines()
    for number, line in enumerate(
        source_lines, start=source["start_line"]
    ):
        classes = "source-line"
        if number == target_line:
            classes += " source-line-target"
        fragment = ""
        if number == source["start_line"] and source["starts_mid_line"]:
            fragment = '<span class="source-fragment">…</span>'
        trailing_fragment = ""
        if (
            number == source["start_line"] + len(source_lines) - 1
            and source["ends_mid_line"]
        ):
            trailing_fragment = '<span class="source-fragment">…</span>'
        rendered_lines.append(
            f'<span class="{classes}"><span class="source-line-number">'
            f'{number}</span><span class="source-line-content">'
            f"{fragment}{escape(line)}{trailing_fragment}</span></span>"
        )
    if not rendered_lines:
        rendered_lines.append(
            f'<span class="source-line"><span class="source-line-number">'
            f'{source["start_line"]}</span><span class="source-line-content">'
            "</span></span>"
        )
    before = (
        '<div class="source-truncation">Earlier verified source omitted</div>'
        if source["truncated_before"]
        else ""
    )
    after = (
        '<div class="source-truncation">Later verified source omitted</div>'
        if source["truncated_after"]
        else ""
    )
    window = ""
    if source["displayed_byte_size"] != source["byte_size"]:
        window = (
            f" · showing {source['displayed_byte_size']:,} verified bytes "
            f"around the finding"
        )
    download = ""
    if source["truncated_before"] or source["truncated_after"]:
        download = (
            '<p class="source-download-row"><a class="source-download" '
            f'href="/findings/{source["finding_id"]}/source/download">'
            f'Download complete verified file ({source["byte_size"]:,} bytes)'
            "</a></p>"
        )
    body = f"""<section class="page-heading"><p class="eyebrow">Finding #{source['finding_id']} source evidence</p><h1>{escape(_text(source['subject_path']))}</h1><p>Analysis run <a href="/analyses/{source['analysis_run_id']}#finding-{source['finding_id']}">#{source['analysis_run_id']}</a> · SHA-256 <code>{escape(_short_hash(source['sha256']))}</code> · {source['byte_size']:,} bytes{window}</p></section>
{download}<section class="panel source-panel">{before}<pre class="source-code">{"".join(rendered_lines)}</pre>{after}</section>
<section class="notice"><strong>Evidence boundary:</strong> this is verified text from the exact analyzed package artifact. It is not executed by the portal.</section>"""
    return layout(f"Source for finding #{source['finding_id']}", body)


def jobs_page(summary: dict[str, Any] | None) -> str:
    if summary is None:
        return layout("Analysis queue", '<section class="page-heading"><p class="eyebrow">On-demand analysis</p><h1>Analysis queue disabled</h1><p>Enable the local analysis configuration to submit static package jobs.</p></section>')
    rows = "".join(_job_row(job) for job in summary["recent"]) or '<tr><td colspan="5" class="empty">No analysis jobs have been submitted.</td></tr>'
    cards = "".join(_card(name.title(), summary[name], "Portal-owned job state") for name in ("queued", "running", "completed", "failed"))
    review = summary["review"]
    review_rows = "".join(
        _review_job_row(job) for job in review["recent"]
    ) or '<tr><td colspan="5" class="empty">No review jobs have been submitted.</td></tr>'
    review_cards = "".join(
        _card(
            name.title(),
            review[name],
            "Portal-owned review job state",
        )
        for name in ("queued", "running", "completed", "failed")
    )
    runtime = summary["runtime"]
    runtime_rows = "".join(
        _runtime_job_row(job) for job in runtime["recent"]
    ) or '<tr><td colspan="5" class="empty">No runtime-discovery jobs have been submitted.</td></tr>'
    runtime_cards = "".join(
        _card(name.title(), runtime[name], "Portal-owned runtime job state")
        for name in ("queued", "running", "completed", "failed")
    )
    return layout("Local jobs", f"""<section class="page-heading"><p class="eyebrow">Constrained local orchestration</p><h1>Analysis, runtime, and review queues</h1><p>Only existing internal catalog identifiers and fixed operations can be submitted.</p></section><h2>Static-analysis jobs</h2><section class="cards">{cards}</section><section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Job</th><th>Server</th><th>Package</th><th>Status</th><th>Requested</th></tr></thead><tbody>{rows}</tbody></table></div></section><h2>Runtime-discovery jobs</h2><section class="cards">{runtime_cards}</section><section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Job</th><th>Server</th><th>Package</th><th>Status</th><th>Requested</th></tr></thead><tbody>{runtime_rows}</tbody></table></div></section><h2>Finding-review jobs</h2><section class="cards">{review_cards}</section><section class="panel compact"><div class="table-wrap"><table><thead><tr><th>Job</th><th>Finding</th><th>Transition</th><th>Status</th><th>Requested</th></tr></thead><tbody>{review_rows}</tbody></table></div></section>""")


def job_detail_page(job: dict[str, Any]) -> str:
    analysis_link = f'<a href="/analyses/{job["analysis_run_id"]}">Open Observatory analysis #{job["analysis_run_id"]}</a>' if job.get("analysis_run_id") else "No Observatory analysis run recorded."
    logs = ""
    for title, value in (("Standard output", job.get("stdout_excerpt")), ("Standard error", job.get("stderr_excerpt"))):
        if value:
            logs += f"<h3>{title}</h3><pre>{escape(_text(value))}</pre>"
    error = f'<section class="notice danger"><strong>Failure:</strong> {escape(_text(job.get("error_message")))}</section>' if job.get("error_message") else ""
    body = f"""<section class="page-heading"><p class="eyebrow">Analysis job #{job['id']}</p><h1>{escape(_text(job['server_identifier']))} <span class="muted">{escape(_text(job['server_version']))}</span></h1><p><span class="badge status-{escape(_text(job['status']))}">{escape(_text(job['status']))}</span> package <code>{escape(_text(job['package_identifier']))}</code></p></section>{error}<section class="panel"><dl class="facts"><div><dt>Requested</dt><dd>{escape(_text(job['requested_at']))}</dd></div><div><dt>Started</dt><dd>{escape(_text(job.get('started_at'), 'Not started'))}</dd></div><div><dt>Completed</dt><dd>{escape(_text(job.get('completed_at'), 'Not completed'))}</dd></div><div><dt>Artifact</dt><dd><code>{escape(_text(job.get('artifact_sha256'), 'Not available'))}</code></dd></div><div><dt>Result</dt><dd>{analysis_link}</dd></div><div><dt>Reused existing</dt><dd>{'yes' if job.get('reused_existing') else 'no'}</dd></div></dl></section><section class="panel"><h2>Bounded worker output</h2>{logs or '<p class="empty">No worker output recorded.</p>'}</section>"""
    return layout(f"Job #{job['id']}", body)


def review_job_detail_page(job: dict[str, Any]) -> str:
    error = (
        f'<section class="notice danger"><strong>Failure:</strong> '
        f"{escape(_text(job.get('error_message')))}</section>"
        if job.get("error_message")
        else ""
    )
    result = (
        f"Recorded as review #{job['review_id']}."
        if job.get("review_id")
        else "No authoritative review has been recorded yet."
    )
    body = f"""<section class="page-heading"><p class="eyebrow">Review job #{job['id']}</p><h1>{escape(_text(job['title']))}</h1><p><span class="badge status-{escape(_text(job['status']))}">{escape(_text(job['status']))}</span> finding <a href="/analyses/{job['analysis_run_id']}#finding-{job['finding_id']}">#{job['finding_id']}</a></p></section>{error}
<section class="panel"><dl class="facts"><div><dt>Source</dt><dd><code>{escape(_text(job['subject_path']))}</code></dd></div><div><dt>Transition</dt><dd>{escape(_text(job['expected_disposition']))} → {escape(_text(job['disposition']))}</dd></div><div><dt>Reviewer</dt><dd>{escape(_text(job['reviewer']))}</dd></div><div><dt>Requested</dt><dd>{escape(_text(job['requested_at']))}</dd></div><div><dt>Completed</dt><dd>{escape(_text(job.get('completed_at'), 'Not completed'))}</dd></div><div><dt>Result</dt><dd>{result}</dd></div></dl></section>"""
    return layout(f"Review job #{job['id']}", body)


def runtime_job_detail_page(job: dict[str, Any]) -> str:
    error = f'<section class="notice danger"><strong>Failure:</strong> {escape(_text(job.get("error_message")))}</section>' if job.get("error_message") else ""
    result = f'<a href="/runtime-observations/{job["runtime_observation_run_id"]}">Open runtime observation #{job["runtime_observation_run_id"]}</a>' if job.get("runtime_observation_run_id") else "No authoritative runtime observation recorded."
    logs = ""
    for title, value in (("Standard output", job.get("stdout_excerpt")), ("Standard error", job.get("stderr_excerpt"))):
        if value:
            logs += f"<h3>{title}</h3><pre>{escape(_text(value))}</pre>"
    body = f"""<section class="page-heading"><p class="eyebrow">Runtime-discovery job #{job['id']}</p><h1>{escape(_text(job['server_identifier']))} <span class="muted">{escape(_text(job['server_version']))}</span></h1><p><span class="badge status-{escape(_text(job['status']))}">{escape(_text(job['status']))}</span> package <code>{escape(_text(job['package_identifier']))}</code></p></section>{error}<section class="panel"><dl class="facts"><div><dt>Requested</dt><dd>{escape(_text(job['requested_at']))}</dd></div><div><dt>Completed</dt><dd>{escape(_text(job.get('completed_at'), 'Not completed'))}</dd></div><div><dt>Artifact</dt><dd><code>{escape(_text(job.get('artifact_sha256'), 'Not available'))}</code></dd></div><div><dt>Inventory</dt><dd><code>{escape(_text(job.get('inventory_sha256'), 'Not available'))}</code></dd></div><div><dt>Tools observed</dt><dd>{escape(_text(job.get('tool_count'), 'Not available'))}</dd></div><div><dt>Result</dt><dd>{result}</dd></div></dl></section><section class="panel"><h2>Bounded worker output</h2>{logs or '<p class="empty">No worker output recorded.</p>'}</section><section class="notice"><strong>Interpretation boundary:</strong> runtime discovery records an MCP tool inventory. It does not invoke tools or establish a safety verdict.</section>"""
    return layout(f"Runtime job #{job['id']}", body)


def runtime_observation_page(observation: dict[str, Any]) -> str:
    tools = "".join(
        f'<article class="package"><strong>{escape(_text(tool["name"]))}</strong><div class="meta">Definition SHA-256 <code>{escape(_text(tool["definition_sha256"]))}</code></div><pre>{escape(_text(tool["definition_json"]))}{"\n… bounded display" if tool.get("definition_truncated") else ""}</pre></article>'
        for tool in observation["tools"]
    ) or '<p class="empty">No tools were observed.</p>'
    body = f"""<section class="page-heading"><p class="eyebrow">Runtime observation #{observation['id']}</p><h1>{escape(_text(observation['server_identifier']))} <span class="muted">{escape(_text(observation['server_version']))}</span></h1><p><span class="badge status-{escape(_text(observation['status']))}">{escape(_text(observation['status']))}</span> package <code>{escape(_text(observation['package_identifier']))}</code></p></section><section class="cards">{_card('Tools', len(observation['tools']), 'Observed MCP tool definitions')}{_card('Artifact', _short_hash(observation.get('artifact_sha256')), 'Exact package digest')}{_card('Inventory', _short_hash(observation.get('inventory_sha256')), 'Canonical inventory digest')}{_card('Sandbox image', _text(observation.get('sandbox_image')), 'Configured runtime image')}</section><section class="panel"><h2>Observed tool inventory</h2>{tools}</section><section class="notice"><strong>Observation boundary:</strong> this records tool definitions exposed during constrained discovery. No MCP tool was invoked, and the result is not a safety verdict.</section>"""
    return layout(f"Runtime observation #{observation['id']}", body)


def error_page(
    status: int,
    title: str,
    message: str,
    *,
    public_readonly: bool = False,
) -> str:
    return layout(
        title,
        f'<section class="page-heading"><p class="eyebrow">HTTP {status}</p>'
        f'<h1>{escape(title)}</h1><p>{escape(message)}</p>'
        '<p><a href="/">Return to dashboard</a></p></section>',
        public_readonly=public_readonly,
    )


INFORMATION_PAGES = {
    "/about": (
        "About",
        "Independent MCP ecosystem research",
        "This portal publishes reproducible observations about exact registry records and package artifacts. Independent security research project. Not affiliated with or endorsed by the Model Context Protocol project, the Official MCP Registry, package registries, or listed publishers.",
        "Records are presented to support inspection, comparison, and correction. A listing is not a recommendation, certification, accusation, or safety verdict.",
    ),
    "/methodology": (
        "Methodology",
        "How observations are produced",
        "Registry metadata is imported as immutable, content-addressed history. Static analysis evaluates an exact package artifact under the analyzer, ruleset, integrity, and network profile recorded with each run. Findings identify observable patterns and retain their confidence and review disposition.",
        "The public portal does not execute servers, invoke MCP tools, run analysis, perform runtime discovery, or expose complete source and evidence files. A finding excerpt is displayed only when a dedicated public excerpt was explicitly approved during analysis or review; displayed excerpts are escaped and bounded to 2,048 characters.",
    ),
    "/data-sources": (
        "Data Sources",
        "Source and provenance boundaries",
        "Catalog records originate from imported MCP Registry metadata. Package identifiers, repository declarations, remote endpoints, publication times, and canonical hashes reflect the recorded snapshot. Static findings and evidence manifests originate from mcp-observatory analysis runs.",
        "Publisher-supplied metadata may be incomplete, stale, or inaccurate. Provenance fields and hashes identify the exact records and artifacts observed; they do not establish publisher identity or intent.",
    ),
    "/disclaimer": (
        "Disclaimer",
        "Research results are not safety advice",
        "Findings are automated or reviewed observations about specific artifacts. They may contain false positives, false negatives, or incomplete context. Absence of a finding does not establish safety, and presence of a finding does not establish malicious intent.",
        "Do not rely on this portal as the sole basis for security, legal, procurement, deployment, or incident-response decisions. Verify relevant artifacts and context independently.",
    ),
    "/privacy": (
        "Privacy",
        "Public browsing and operational logs",
        "The application has no accounts, analytics, cookies, browser storage, or state-changing public submission forms in public-readonly mode. It does not intentionally collect visitor-provided content beyond bounded catalog search terms.",
        "The hosting system or reverse proxy may retain bounded operational request logs, including network address, time, path, status, and user agent, for security and reliability. Operators should minimize retention and restrict access according to their published deployment policy.",
    ),
    "/corrections": (
        "Corrections",
        "How to report a disputed or inaccurate record",
        "Report a correction through the project repository's published issue channel. Include the server identifier, version, analysis or finding ID, the specific disputed field, supporting provenance, and a safe way to follow up. Do not include secrets, personal data, or complete proprietary source files.",
        "Correction requests are reviewed against the immutable source record. Confirmed presentation errors can be fixed in the portal; upstream registry or analyzer errors must be corrected at their source and reflected in a later import or analysis run. Historical records are not silently rewritten.",
    ),
}


def information_page(path: str, *, public_readonly: bool = False) -> str | None:
    content = INFORMATION_PAGES.get(path)
    if content is None:
        return None
    title, eyebrow, first, second = content
    body = (
        f'<section class="page-heading"><p class="eyebrow">{escape(eyebrow)}</p>'
        f'<h1>{escape(title)}</h1><p>{escape(first)}</p></section>'
        f'<section class="panel"><p>{escape(second)}</p></section>'
    )
    return layout(title, body, public_readonly=public_readonly)


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
    runtime_request = package.get("runtime_request")
    runtime_action = ""
    if runtime_request:
        runtime_action = f"""<form class="analysis-form" method="post" action="/runtime-discovery-requests"><input type="hidden" name="server_version_id" value="{runtime_request['server_version_id']}"><input type="hidden" name="package_id" value="{runtime_request['package_id']}"><input type="hidden" name="csrf_token" value="{escape(runtime_request['csrf_token'], quote=True)}"><button type="submit">Queue runtime discovery</button><small>Discovery runs offline after cache population and does not invoke tools.</small></form>"""
    elif package.get("runtime_unavailable_reason"):
        runtime_action = f'<p class="meta">Runtime discovery unavailable: {escape(_text(package["runtime_unavailable_reason"]))}</p>'
    return f"""<article class="package"><strong>{escape(_text(package['identifier']))}</strong><div class="meta">{escape(_text(package['registry_type']))} · {escape(_text(package['transport']))} · declared version {escape(_text(package['version'], 'not supplied'))}</div><p><span class="label">Arguments:</span> {arguments}</p><p class="label">Environment declarations:</p><ul>{environment}</ul>{action}{runtime_action}</article>"""


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


def _finding_article(item: dict[str, Any]) -> str:
    location = escape(_text(item["subject_path"]))
    if item.get("line_number"):
        location += ":" + escape(str(item["line_number"]))
    if item.get("source_enabled"):
        location = (
            f'<a href="/findings/{item["id"]}/source"><code>{location}</code></a>'
        )
    else:
        location = f"<code>{location}</code>"
    if item.get("subject_sha256"):
        location += (
            " · SHA-256 "
            f'<code>{escape(_text(item["subject_sha256"]))}</code>'
        )

    reviews = "".join(
        f"<li>{escape(_text(review['reviewed_at']))} · "
        f"{escape(_text(review['previous_disposition']))} → "
        f"<strong>{escape(_text(review['disposition']))}</strong> · "
        f"{escape(_text(review['reviewer']))}</li>"
        for review in item.get("reviews", [])
    )
    history = (
        f'<details class="review-history"><summary>Review history '
        f"({len(item['reviews'])})</summary><ul>{reviews}</ul></details>"
        if reviews
        else ""
    )
    review_form = ""
    request = item.get("review_request")
    if request:
        options = "".join(
            f'<option value="{escape(value, quote=True)}">'
            f"{escape(value)}</option>"
            for value in request["dispositions"]
        )
        review_form = f"""<form class="review-form" method="post" action="/review-requests"><input type="hidden" name="finding_id" value="{item['id']}"><input type="hidden" name="expected_disposition" value="{escape(_text(item['disposition']), quote=True)}"><input type="hidden" name="csrf_token" value="{escape(request['csrf_token'], quote=True)}"><label for="disposition-{item['id']}">Review disposition</label><select id="disposition-{item['id']}" name="disposition">{options}</select><button type="submit">Submit review</button></form>"""

    excerpt = ""
    if item.get("public_excerpt_eligible") == 1 and item.get("public_excerpt"):
        truncation = "\n… excerpt bounded" if item.get("public_excerpt_truncated") else ""
        excerpt = (
            '<details class="finding-excerpt"><summary>Approved public excerpt</summary>'
            f'<pre>{escape(_text(item["public_excerpt"]))}{truncation}</pre></details>'
        )

    return f"""<article id="finding-{item['id']}" class="finding severity-{escape(_text(item['severity']))}"><div class="finding-header"><span class="badge">{escape(_text(item['severity']))}</span><strong>{escape(_text(item['title']))}</strong></div><div class="meta">{escape(_text(item['rule_id']))} · {escape(_text(item['disposition']))} · confidence {escape(_text(item['confidence']))}</div><p>{location}</p><p>{escape(_text(item['explanation']))}</p>{excerpt}{history}{review_form}</article>"""


def _unreviewed_finding_row(row: dict[str, Any]) -> str:
    location = escape(_text(row["subject_path"], "—"))
    if row.get("line_number") is not None:
        location += ":" + escape(str(row["line_number"]))
    return (
        f'<tr><td><span class="badge severity-{escape(_text(row["severity"]))}">'
        f'{escape(_text(row["severity"]))}</span></td>'
        f'<td><a href="/analyses/{row["analysis_run_id"]}#finding-{row["id"]}">'
        f'{escape(_text(row["title"]))}</a>'
        f'<div class="meta">{escape(_text(row["rule_id"]))} · '
        f'confidence {escape(_text(row["confidence"]))}</div></td>'
        f'<td>{escape(_text(row["server_identifier"]))}'
        f'<div class="meta">{escape(_text(row["server_version"]))}</div></td>'
        f'<td>{escape(_text(row["package_identifier"]))}</td>'
        f"<td><code>{location}</code></td>"
        f'<td>{escape(_text(row["started_at"]))}</td></tr>'
    )


def _job_row(job: dict[str, Any]) -> str:
    return f'<tr><td><a href="/jobs/{job["id"]}">#{job["id"]}</a></td><td>{escape(_text(job["server_identifier"]))}<div class="meta">{escape(_text(job["server_version"]))}</div></td><td>{escape(_text(job["package_identifier"]))}</td><td><span class="badge status-{escape(_text(job["status"]))}">{escape(_text(job["status"]))}</span></td><td>{escape(_text(job["requested_at"]))}</td></tr>'


def _review_job_row(job: dict[str, Any]) -> str:
    return f'<tr><td><a href="/review-jobs/{job["id"]}">#{job["id"]}</a></td><td><a href="/analyses/{job["analysis_run_id"]}#finding-{job["finding_id"]}">#{job["finding_id"]}</a><div class="meta">{escape(_text(job["title"]))}</div></td><td>{escape(_text(job["expected_disposition"]))} → {escape(_text(job["disposition"]))}</td><td><span class="badge status-{escape(_text(job["status"]))}">{escape(_text(job["status"]))}</span></td><td>{escape(_text(job["requested_at"]))}</td></tr>'


def _runtime_job_row(job: dict[str, Any]) -> str:
    return f'<tr><td><a href="/runtime-jobs/{job["id"]}">#{job["id"]}</a></td><td>{escape(_text(job["server_identifier"]))}<div class="meta">{escape(_text(job["server_version"]))}</div></td><td>{escape(_text(job["package_identifier"]))}</td><td><span class="badge status-{escape(_text(job["status"]))}">{escape(_text(job["status"]))}</span></td><td>{escape(_text(job["requested_at"]))}</td></tr>'


def _card(label: str, value: Any, detail: str, detail_href: str | None = None) -> str:
    safe_detail = escape(detail)
    if detail_href is not None:
        safe_detail = (
            f'<a href="{escape(detail_href, quote=True)}">{safe_detail}</a>'
        )
    return f'<article class="card"><span>{escape(str(label))}</span><strong>{escape(str(value))}</strong><small>{safe_detail}</small></article>'


def _pagination(
    base: str,
    query: str,
    page: int,
    total_pages: int,
    *,
    ecosystem: str = "",
    aria_label: str = "Server pages",
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
    return (
        f'<nav class="pagination" aria-label="{escape(aria_label, quote=True)}">'
        + "".join(links)
        + "</nav>"
    )


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
