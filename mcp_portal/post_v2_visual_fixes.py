"""Final visual/correctness fixes found during production-shaped browser review."""

from __future__ import annotations

from typing import Any


FULL_PROJECT_NAME = "MCP Longitudinal Assurance Project"


def apply_post_v2_visual_fixes() -> None:
    """Apply browser-review fixes after every other post-v2 patch."""
    from . import post_v2_bugfixes as bugfixes
    from . import views

    if getattr(views.layout, "_post_v2_visual_fixes", False):
        return

    # Avoid the internal acronym in public-facing prose.
    bugfixes.PROVENANCE_HTML = (
        '<section class="provenance-notice">'
        '<strong>Catalog source:</strong> Official MCP Registry, collected through '
        'the official Registry REST API. MCP Longitudinal Assurance Project '
        'independently preserves publication history and derives analysis, '
        'coverage, drift, and assurance observations from those records and '
        'referenced artifacts.'
        '</section>'
    )

    original_layout = views.layout

    def layout(
        title: str,
        body: str,
        *,
        public_readonly: bool = False,
    ) -> str:
        html = original_layout(title, body, public_readonly=public_readonly)
        return html.replace("MCPLA", FULL_PROJECT_NAME)

    setattr(layout, "_post_v2_visual_fixes", True)
    views.layout = layout

    # Make server scope visually behave like tabs: separated controls and a
    # clearly selected current scope.
    original_servers_scope_page = bugfixes.servers_scope_page

    def servers_scope_page(
        result: dict[str, Any], *, public_readonly: bool = False
    ) -> str:
        html = original_servers_scope_page(
            result, public_readonly=public_readonly
        )
        scope = result.get("scope")
        current_class = "scope-tab active" if scope == "current" else "scope-tab"
        all_class = "scope-tab active" if scope == "all" else "scope-tab"
        current_aria = ' aria-current="page"' if scope == "current" else ""
        all_aria = ' aria-current="page"' if scope == "all" else ""
        old = (
            '<nav class="scope-tabs" aria-label="Server browser scope">'
            '<a href="/servers?scope=current">Current snapshot</a>'
            '<a href="/servers?scope=all">All observed servers</a>'
            '</nav>'
        )
        new = (
            '<nav class="scope-tabs" aria-label="Server browser scope">'
            f'<a class="{current_class}"{current_aria} '
            'href="/servers?scope=current">Current snapshot</a>'
            f'<a class="{all_class}"{all_aria} '
            'href="/servers?scope=all">All observed servers</a>'
            '</nav>'
        )
        return html.replace(old, new, 1)

    bugfixes.servers_scope_page = servers_scope_page

    # Storage v2 may publish the compact coverage summary without retaining a
    # row in static_analysis_schedule_current. Resolve the drill-down profile
    # using exactly the same fallback as the aggregate read model.
    def coverage_records(
        catalog: Any, *, state: str, page: int, page_size: int
    ) -> dict[str, Any]:
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
                "SELECT profile_key FROM static_analysis_schedule_current "
                "WHERE singleton=1"
            ).fetchone()
            if profile is None:
                profile = connection.execute(
                    """SELECT profile_key
                       FROM analysis_v2_coverage_summary
                       ORDER BY updated_at COLLATE BINARY DESC,
                                profile_key COLLATE BINARY
                       LIMIT 1"""
                ).fetchone()
            if profile is None:
                return {
                    "state": state,
                    "page": 1,
                    "page_size": page_size,
                    "total": 0,
                    "rows": [],
                }

            profile_key = profile["profile_key"]
            predicate = predicates[state]
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM static_analysis_schedule_state s "
                    f"WHERE s.profile_key=? AND {predicate}",
                    (profile_key,),
                ).fetchone()[0]
            )
            offset = (max(page, 1) - 1) * page_size
            rows = [
                dict(row)
                for row in connection.execute(
                    f"""SELECT s.package_id,s.state,s.reason_code,
                               s.reason_message,s.attempt_count,
                               s.analysis_run_id,s.artifact_sha256,s.updated_at,
                               p.identifier AS package_identifier,
                               p.version AS package_version,
                               p.registry_type,p.transport,
                               sv.server_identifier,sv.server_version
                        FROM static_analysis_schedule_state s
                        JOIN packages p ON p.id=s.package_id
                        JOIN server_versions sv ON sv.id=p.server_version_id
                        WHERE s.profile_key=? AND {predicate}
                        ORDER BY s.updated_at COLLATE BINARY DESC,
                                 s.package_id DESC
                        LIMIT ? OFFSET ?""",
                    (profile_key, page_size, offset),
                ).fetchall()
            ]

        return {
            "state": state,
            "page": max(page, 1),
            "page_size": page_size,
            "total": total,
            "rows": rows,
        }

    bugfixes._coverage_records = coverage_records
