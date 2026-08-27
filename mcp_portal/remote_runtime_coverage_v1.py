"""Read-only coverage panel for registry-declared remote MCP observations."""

from __future__ import annotations

from html import escape
from typing import Any, Callable


def apply_remote_runtime_coverage_v1() -> None:
    from . import public_intelligence, public_ui

    original_analysis_coverage = public_intelligence.PublicIntelligence.analysis_coverage
    if getattr(original_analysis_coverage, "_remote_runtime_coverage_v1", False):
        return

    def analysis_coverage(self: Any) -> dict[str, Any]:
        result = original_analysis_coverage(self)
        with self._connect() as connection:
            result["remote_runtime_discovery"] = _metrics(connection)
        return result

    setattr(analysis_coverage, "_remote_runtime_coverage_v1", True)
    public_intelligence.PublicIntelligence.analysis_coverage = analysis_coverage

    original_page: Callable[..., str] = public_ui.coverage_page

    def coverage_page(data: dict[str, Any], *, public_readonly: bool = False) -> str:
        html = original_page(data, public_readonly=public_readonly)
        return html.replace(
            "</main>",
            _panel(data.get("remote_runtime_discovery") or {}) + "</main>",
            1,
        )

    public_ui.coverage_page = coverage_page


def _tables(connection: Any) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")
    }


def _metrics(connection: Any) -> dict[str, Any]:
    required = {
        "runtime_remote_schedule_profiles",
        "runtime_remote_schedule_current",
        "runtime_remote_schedule_state",
    }
    if not required.issubset(_tables(connection)):
        return {"available": False}
    profile = connection.execute(
        """SELECT p.profile_key,p.scheduler_version,p.probe_profile_sha256,p.runner_sha256
           FROM runtime_remote_schedule_current c
           JOIN runtime_remote_schedule_profiles p ON p.profile_key=c.profile_key
           WHERE c.singleton=1"""
    ).fetchone()
    if profile is None:
        return {"available": False}
    row = connection.execute(
        """SELECT COUNT(*) total,
                  SUM(state IN('eligible','running','completed','failed','blocked','inconclusive')) eligible,
                  SUM(state='completed') completed,SUM(state='failed') failed,
                  SUM(state='blocked') blocked,SUM(state='inconclusive') inconclusive,
                  SUM(state IN('unsupported','unresolvable')) unsupported,
                  SUM(state='eligible' AND attempt_count=0) never_attempted,
                  SUM(state='completed' AND previous_compatible_run_id IS NOT NULL) comparable,
                  SUM(state='completed' AND previous_compatible_run_id IS NOT NULL AND
                    (COALESCE(added_tools,0)+COALESCE(removed_tools,0)+COALESCE(modified_tools,0))>0) drifted
           FROM runtime_remote_schedule_state WHERE profile_key=?""",
        (profile["profile_key"],),
    ).fetchone()
    return {
        "available": True,
        "total": int(row["total"] or 0),
        "eligible": int(row["eligible"] or 0),
        "completed": int(row["completed"] or 0),
        "failed": int(row["failed"] or 0),
        "blocked": int(row["blocked"] or 0),
        "inconclusive": int(row["inconclusive"] or 0),
        "unsupported": int(row["unsupported"] or 0),
        "never_attempted": int(row["never_attempted"] or 0),
        "comparable": int(row["comparable"] or 0),
        "drifted": int(row["drifted"] or 0),
        "profile": dict(profile),
    }


def _panel(data: dict[str, Any]) -> str:
    if not data.get("available"):
        return ""
    cells = [
        ("Declared remotes", data.get("total", 0)),
        ("Probe-eligible", data.get("eligible", 0)),
        ("Completed", data.get("completed", 0)),
        ("Blocked/auth", data.get("blocked", 0)),
        ("Inconclusive", data.get("inconclusive", 0)),
        ("Protocol failures", data.get("failed", 0)),
        ("Comparable", data.get("comparable", 0)),
        ("Drifted", data.get("drifted", 0)),
    ]
    cards = "".join(
        f'<div class="metric"><strong>{escape(str(value))}</strong><span>{escape(label)}</span></div>'
        for label, value in cells
    )
    return (
        '<section class="panel runtime-remote-coverage">'
        '<h2>Declared remote runtime coverage</h2>'
        '<p>Exact registry-declared HTTP(S) endpoints are checked with initialize and tools/list only. '
        'No tool invocation, URL guessing, redirects, or address-space scanning is performed.</p>'
        f'<div class="metric-grid">{cards}</div>'
        '</section>'
    )
