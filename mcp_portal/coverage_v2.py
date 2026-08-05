"""Static artifact and assurance-layer coverage for the public portal."""

from __future__ import annotations

from html import escape
from typing import Any


def apply_coverage_v2() -> None:
    """Install coverage queries and views before public routes are wrapped."""
    from . import public_intelligence, public_ui

    public_intelligence.PublicIntelligence.analysis_coverage = _analysis_coverage
    public_ui.coverage_page = coverage_page
    public_ui._dashboard_intelligence = _dashboard_intelligence


def _analysis_coverage(self: Any) -> dict[str, Any]:
    with self._connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
        }
        total = int(connection.execute("SELECT COUNT(*) FROM packages").fetchone()[0])
        result: dict[str, Any] = {
            "package_records": total,
            "eligible_package_records": 0,
            "analyzed_package_records": 0,
            "failed_package_records": 0,
            "unsupported_or_unresolvable_package_records": 0,
            "never_analyzed_package_records": 0,
            "unique_artifacts_analyzed": 0,
            "running_package_records": 0,
            "static_profile": None,
        }

        schedule_tables = {
            "static_analysis_schedule_current",
            "static_analysis_schedule_profiles",
            "static_analysis_schedule_state",
        }
        if schedule_tables.issubset(tables):
            profile = connection.execute(
                """SELECT p.profile_key,p.analysis_type,p.analyzer_name,
                          p.analyzer_version,p.ruleset_version,p.rules_sha256
                   FROM static_analysis_schedule_current c
                   JOIN static_analysis_schedule_profiles p
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
                           COUNT(DISTINCT CASE WHEN state='completed'
                             THEN artifact_sha256 END) unique_artifacts,
                           SUM(state='running') running
                       FROM static_analysis_schedule_state
                       WHERE profile_key=?""",
                    (profile["profile_key"],),
                ).fetchone()
                result.update(
                    {
                        "eligible_package_records": int(row["eligible"] or 0),
                        "analyzed_package_records": int(row["completed"] or 0),
                        "failed_package_records": int(row["failed"] or 0),
                        "unsupported_or_unresolvable_package_records": int(
                            row["unsupported"] or 0
                        ),
                        "never_analyzed_package_records": int(
                            row["never_attempted"] or 0
                        ),
                        "unique_artifacts_analyzed": int(
                            row["unique_artifacts"] or 0
                        ),
                        "running_package_records": int(row["running"] or 0),
                        "static_profile": dict(profile),
                    }
                )
        elif "analysis_runs" in tables:
            row = connection.execute(
                """SELECT
                       SUM(p.registry_type IN('npm','pypi')
                           AND p.version IS NOT NULL AND trim(p.version)<>'') eligible,
                       SUM(EXISTS(SELECT 1 FROM analysis_runs ar
                           WHERE ar.package_id=p.id AND ar.status='completed')) completed,
                       SUM(EXISTS(SELECT 1 FROM analysis_runs ar
                           WHERE ar.package_id=p.id AND ar.status='failed')
                           AND NOT EXISTS(SELECT 1 FROM analysis_runs ar
                           WHERE ar.package_id=p.id AND ar.status='completed')) failed,
                       SUM(p.registry_type NOT IN('npm','pypi')
                           OR p.version IS NULL OR trim(p.version)='') unsupported,
                       SUM(p.registry_type IN('npm','pypi')
                           AND p.version IS NOT NULL AND trim(p.version)<>''
                           AND NOT EXISTS(SELECT 1 FROM analysis_runs ar
                             WHERE ar.package_id=p.id)) never_attempted,
                       COUNT(DISTINCT CASE WHEN ar.status='completed'
                         THEN ar.artifact_sha256 END) unique_artifacts
                   FROM packages p LEFT JOIN analysis_runs ar ON ar.package_id=p.id"""
            ).fetchone()
            result.update(
                {
                    "eligible_package_records": int(row["eligible"] or 0),
                    "analyzed_package_records": int(row["completed"] or 0),
                    "failed_package_records": int(row["failed"] or 0),
                    "unsupported_or_unresolvable_package_records": int(
                        row["unsupported"] or 0
                    ),
                    "never_analyzed_package_records": int(
                        row["never_attempted"] or 0
                    ),
                    "unique_artifacts_analyzed": int(
                        row["unique_artifacts"] or 0
                    ),
                }
            )
        else:
            eligible = int(
                connection.execute(
                    """SELECT COUNT(*) FROM packages
                       WHERE registry_type IN('npm','pypi')
                         AND version IS NOT NULL AND trim(version)<>''"""
                ).fetchone()[0]
            )
            result["eligible_package_records"] = eligible
            result["never_analyzed_package_records"] = eligible
            result["unsupported_or_unresolvable_package_records"] = total - eligible

        runtime_eligible = int(
            connection.execute(
                """SELECT COUNT(*) FROM packages
                   WHERE registry_type='npm' AND transport='stdio'
                     AND version IS NOT NULL AND trim(version)<>''"""
            ).fetchone()[0]
        )
        runtime_completed = 0
        if "runtime_observation_runs" in tables:
            runtime_completed = int(
                connection.execute(
                    """SELECT COUNT(DISTINCT package_id)
                       FROM runtime_observation_runs WHERE status='completed'"""
                ).fetchone()[0]
            )
        result["runtime_discovery"] = {
            "eligible": runtime_eligible,
            "completed": runtime_completed,
            "available": "runtime_observation_runs" in tables,
        }

        total_findings = reviewed_findings = 0
        if "analysis_findings" in tables:
            total_findings = int(
                connection.execute("SELECT COUNT(*) FROM analysis_findings").fetchone()[0]
            )
            reviewed_findings = int(
                connection.execute(
                    "SELECT COUNT(*) FROM analysis_findings WHERE disposition<>'unreviewed'"
                ).fetchone()[0]
            )
        result["human_review"] = {
            "total": total_findings,
            "reviewed": reviewed_findings,
            "available": "analysis_findings" in tables,
        }
        result["controlled_behavioral"] = {
            "available": False,
            "completed": 0,
            "eligible": 0,
        }
        return result


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
    runtime_value = (
        f"{_percent(runtime_completed, runtime_eligible):.1f}%"
        if runtime.get("available")
        else "Not started"
    )
    runtime_detail = (
        f"{runtime_completed:,} of {runtime_eligible:,} eligible npm stdio records"
        if runtime.get("available")
        else "Future metric; discovery observations are not yet published"
    )

    review = data.get("human_review") or {}
    reviewed = int(review.get("reviewed", 0))
    findings = int(review.get("total", 0))
    review_value = (
        f"{_percent(reviewed, findings):.1f}%"
        if review.get("available")
        else "Not started"
    )
    review_detail = (
        f"{reviewed:,} of {findings:,} findings have a disposition"
        if review.get("available")
        else "Future metric; no finding-review data is available"
    )

    body = f"""<section class="page-heading"><p class="eyebrow">Assurance reach</p><h1>Coverage</h1><p>Coverage layers are reported separately so static inspection is not confused with runtime or behavioral observation.</p></section>
<section class="panel"><div class="panel-heading"><div><p class="eyebrow">Current baseline</p><h2>Static artifact coverage</h2></div></div><section class="cards">{_card('Eligible package records', eligible, 'Supported registry with an exact version')}{_card('Successfully analyzed', analyzed, f'{static_percent:.1f}% of eligible records')}{_card('Failed attempts', failed, 'Current profile; no compatible completion')}{_card('Unsupported / unresolvable', unsupported, 'Not currently schedulable')}{_card('Never attempted', never, 'Eligible and not yet selected')}{_card('Unique artifacts analyzed', unique_artifacts, 'Distinct completed artifact SHA-256 values')}</section><p><strong>{analyzed:,}</strong> of <strong>{eligible:,}</strong> eligible package records are covered by the current static-analysis profile.</p><progress value="{analyzed}" max="{max(eligible, 1)}">{static_percent:.1f}%</progress><p class="meta">{static_percent:.1f}% static artifact coverage · {total:,} total package records</p></section>
<section class="panel"><div class="panel-heading"><div><p class="eyebrow">Separate assurance layers</p><h2>Coverage metrics</h2></div></div><section class="cards">{_card('Static artifact coverage', f'{static_percent:.1f}%', f'{analyzed:,} of {eligible:,} eligible records')}{_card('Runtime discovery coverage', runtime_value, runtime_detail)}{_card('Controlled behavioral coverage', 'Future metric', 'No MCP tools are invoked by the current pipeline')}{_card('Human-review coverage', review_value, review_detail)}</section></section>
<section class="notice"><strong>Counting boundary:</strong> scheduler states are mutually exclusive for the selected analyzer and ruleset profile. Completion records observed properties of an exact artifact and is not a safety certification.</section>"""
    return views.layout("Coverage", body, public_readonly=public_readonly)


def _dashboard_intelligence(
    status: dict[str, Any],
    added: dict[str, Any],
    removed: dict[str, Any],
    coverage: dict[str, Any],
) -> str:
    snapshot = status.get("latest_snapshot") or {}
    eligible = int(coverage.get("eligible_package_records", 0))
    analyzed = int(coverage.get("analyzed_package_records", 0))
    percent = _percent(analyzed, eligible)
    return f"""<section class="panel"><div class="panel-heading"><div><p class="eyebrow">Catalog intelligence</p><h2>Latest published change summary</h2></div><a href="/status">View publication status →</a></div><section class="cards">{_card('Snapshot', f"#{_text(snapshot.get('id'), '—')}", _text(snapshot.get('completed_at'), 'No published snapshot'), '/snapshots')}{_card('Added', f"{int(added.get('total') or 0):,}", 'Exact server-version records', '/changes?kind=added')}{_card('Removed', f"{int(removed.get('total') or 0):,}", 'Exact server-version records', '/changes?kind=removed')}{_card('Static artifact coverage', f'{percent:.1f}%', f'{analyzed:,} of {eligible:,} eligible records', '/coverage')}</section></section>"""


def _card(label: str, value: str | int, detail: str, href: str | None = None) -> str:
    detail_html = escape(str(detail))
    if href is not None:
        detail_html = f'<a href="{escape(href, quote=True)}">{detail_html}</a>'
    return f'<article class="card"><span>{escape(label)}</span><strong>{escape(str(value))}</strong><small>{detail_html}</small></article>'


def _percent(part: int, whole: int) -> float:
    return 0.0 if whole <= 0 else part * 100.0 / whole


def _text(value: Any, fallback: str = "") -> str:
    return fallback if value is None else str(value)
