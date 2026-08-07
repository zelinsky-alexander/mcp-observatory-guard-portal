"""Storage v2 read model for the public portal.

Common pages read only the compact hot catalog. When
``MCP_PORTAL_HISTORY_DATABASE`` is configured, bounded server/analysis detail
reads are delegated to the full history database so the hot catalog does not
need millions of file/finding rows.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

V2_TABLES = {
    "storage_v2_info",
    "analysis_v2_run_summaries",
    "analysis_v2_rule_definitions",
    "analysis_v2_rule_summaries",
    "analysis_v2_coverage_summary",
}


def apply_storage_v2_read_model() -> None:
    from .catalog import Catalog
    from .public_intelligence import PublicIntelligence

    original_init = Catalog.__init__
    original_schema_status = Catalog.schema_status
    original_server_detail = Catalog.server_detail
    original_analysis_detail = Catalog.analysis_detail
    original_finding_source_metadata = Catalog.finding_source_metadata

    def init(self: Any, database_path: Path) -> None:
        original_init(self, database_path)
        configured = os.environ.get("MCP_PORTAL_HISTORY_DATABASE", "").strip()
        history = Path(configured).resolve() if configured else None
        self._storage_v2_history_path = (
            history
            if history is not None
            and history.is_file()
            and history != self._database_path
            else None
        )

    def history_catalog(self: Any) -> Any | None:
        path = getattr(self, "_storage_v2_history_path", None)
        if path is None:
            return None
        detail = Catalog(path)
        # Avoid another redirection layer when invoking the captured original
        # detail readers against history.
        detail._storage_v2_history_path = None
        return detail

    def schema_status(self: Any) -> dict[str, Any]:
        status = original_schema_status(self)
        with self._connect() as connection:
            tables = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table'"
                )
            }
        status["storage_v2_available"] = V2_TABLES.issubset(tables)
        status["storage_v2_hot_catalog"] = "storage_v2_hot_catalog_info" in tables
        status["storage_v2_history_available"] = (
            getattr(self, "_storage_v2_history_path", None) is not None
        )
        return status

    def server_detail(self: Any, server_identifier: str) -> dict[str, Any] | None:
        detail = history_catalog(self)
        if detail is not None:
            return original_server_detail(detail, server_identifier)
        result = original_server_detail(self, server_identifier)
        if result is None or not self.schema_status().get("storage_v2_available"):
            return result
        # A summaries-only hot catalog has empty v1 findings. Repair cards from
        # the compact run summary without consulting historical detail rows.
        with self._connect() as connection:
            for version in result["versions"]:
                for run in version.get("analyses", []):
                    summary = connection.execute(
                        """SELECT finding_count,critical_count,high_count,medium_count
                           FROM analysis_v2_run_summaries WHERE analysis_run_id=?""",
                        (run["id"],),
                    ).fetchone()
                    if summary is not None:
                        for name in (
                            "finding_count", "critical_count", "high_count", "medium_count"
                        ):
                            run[name] = int(summary[name] or 0)
        return result

    def analysis_detail(self: Any, analysis_run_id: int) -> dict[str, Any] | None:
        detail = history_catalog(self)
        if detail is not None:
            return original_analysis_detail(detail, analysis_run_id)
        return original_analysis_detail(self, analysis_run_id)

    def finding_source_metadata(self: Any, finding_id: int) -> dict[str, Any] | None:
        detail = history_catalog(self)
        if detail is not None:
            return original_finding_source_metadata(detail, finding_id)
        return original_finding_source_metadata(self, finding_id)

    Catalog.__init__ = init
    Catalog.schema_status = schema_status
    Catalog.dashboard = dashboard
    Catalog.server_detail = server_detail
    Catalog.analysis_detail = analysis_detail
    Catalog.finding_source_metadata = finding_source_metadata
    PublicIntelligence.analysis_coverage = analysis_coverage


def _rows(rows: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _latest_snapshot(connection: Any) -> dict[str, Any] | None:
    return _row(
        connection.execute(
            """SELECT id,snapshot_sha256,completed_at,started_at,
                      registry_base_url,bundle_version,pages,records_received,
                      unique_server_versions,imported_at
               FROM snapshots
               ORDER BY completed_at COLLATE BINARY DESC,id DESC LIMIT 1"""
        ).fetchone()
    )


def _coverage_row(connection: Any) -> Any:
    tables = {
        str(row["name"])
        for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")
    }
    if "analysis_v2_coverage_summary" not in tables:
        return None
    if {
        "static_analysis_schedule_current",
        "static_analysis_schedule_profiles",
    }.issubset(tables):
        row = connection.execute(
            """SELECT c.* FROM analysis_v2_coverage_summary c
               JOIN static_analysis_schedule_current cur
                 ON cur.profile_key=c.profile_key
               WHERE cur.singleton=1"""
        ).fetchone()
        if row is not None:
            return row
    return connection.execute(
        """SELECT * FROM analysis_v2_coverage_summary
           ORDER BY updated_at COLLATE BINARY DESC,profile_key COLLATE BINARY
           LIMIT 1"""
    ).fetchone()


def dashboard(self: Any, *, recent_limit: int = 12) -> dict[str, Any]:
    status = self.schema_status()
    with self._connect() as connection:
        latest_snapshot = _latest_snapshot(connection)
        totals = {"servers": 0, "immutable_versions": 0, "canonical_artifacts": 0}
        changes: list[dict[str, Any]] = []
        if latest_snapshot is not None:
            snapshot_id = int(latest_snapshot["id"])
            counted = connection.execute(
                """SELECT COUNT(DISTINCT sv.server_identifier) AS servers,
                          COUNT(*) AS immutable_versions,
                          COUNT(DISTINCT sv.canonical_sha256) AS canonical_artifacts
                   FROM snapshot_server_versions link
                   JOIN server_versions sv ON sv.id=link.server_version_id
                   WHERE link.snapshot_id=?""",
                (snapshot_id,),
            ).fetchone()
            if counted is not None:
                totals = {key: int(counted[key] or 0) for key in totals}
            changes = _rows(
                connection.execute(
                    """SELECT sv.id,sv.server_identifier,sv.server_version,
                              sv.description,sv.registry_status,sv.published_at,
                              sv.updated_at,sv.canonical_sha256,
                              (SELECT p.identifier FROM packages p
                               WHERE p.server_version_id=sv.id
                               ORDER BY p.position LIMIT 1) AS package_identifier,
                              (SELECT p.transport FROM packages p
                               WHERE p.server_version_id=sv.id
                               ORDER BY p.position LIMIT 1) AS package_transport
                       FROM snapshot_server_versions link
                       JOIN server_versions sv ON sv.id=link.server_version_id
                       WHERE link.snapshot_id=?
                       ORDER BY COALESCE(sv.updated_at,sv.published_at,'') COLLATE BINARY DESC,
                                sv.server_identifier COLLATE BINARY,
                                sv.server_version COLLATE BINARY
                       LIMIT ?""",
                    (snapshot_id, recent_limit),
                ).fetchall()
            )

        analysis: dict[str, Any] = {
            "completed": 0,
            "failed": 0,
            "running": 0,
            "unreviewed_high_or_critical": 0,
            "recent": [],
        }
        if status.get("storage_v2_available"):
            # Preserve the dashboard's historical semantics: these are run
            # counts, not package-schedule counts. The v2 summary table is small.
            counts = connection.execute(
                """SELECT SUM(status='completed') completed,
                          SUM(status='failed') failed,
                          SUM(status='running') running
                   FROM analysis_v2_run_summaries"""
            ).fetchone()
            if counts is not None:
                for key in ("completed", "failed", "running"):
                    analysis[key] = int(counts[key] or 0)
            coverage = _coverage_row(connection)
            if coverage is not None:
                analysis["unreviewed_high_or_critical"] = int(
                    coverage["unreviewed_high_or_critical_findings"] or 0
                )
            analysis["recent"] = _rows(
                connection.execute(
                    """WITH recent_runs AS (
                           SELECT id,status,started_at,completed_at,artifact_sha256,
                                  ruleset_version,server_version_id,package_id
                           FROM analysis_runs
                           ORDER BY started_at COLLATE BINARY DESC,id DESC LIMIT ?
                       )
                       SELECT rr.id,rr.status,rr.started_at,rr.completed_at,
                              rr.artifact_sha256,rr.ruleset_version,
                              sv.server_identifier,sv.server_version,
                              p.identifier AS package_identifier,
                              COALESCE(s.critical_count,0) AS critical_count,
                              COALESCE(s.high_count,0) AS high_count,
                              COALESCE(s.medium_count,0) AS medium_count
                       FROM recent_runs rr
                       JOIN server_versions sv ON sv.id=rr.server_version_id
                       JOIN packages p ON p.id=rr.package_id
                       LEFT JOIN analysis_v2_run_summaries s
                         ON s.analysis_run_id=rr.id
                       ORDER BY rr.started_at COLLATE BINARY DESC,rr.id DESC""",
                    (recent_limit,),
                ).fetchall()
            )
        elif status.get("analysis_available"):
            counts = connection.execute(
                """SELECT SUM(status='completed') completed,SUM(status='failed') failed,
                          SUM(status='running') running FROM analysis_runs"""
            ).fetchone()
            if counts is not None:
                for key in ("completed", "failed", "running"):
                    analysis[key] = int(counts[key] or 0)
            analysis["unreviewed_high_or_critical"] = int(
                connection.execute(
                    """SELECT COUNT(*) FROM analysis_findings
                       WHERE disposition='unreviewed'
                         AND severity IN('high','critical')"""
                ).fetchone()[0]
            )

        return {
            "schema": status,
            "latest_snapshot": latest_snapshot,
            "totals": totals,
            "changes": changes,
            "analysis": analysis,
        }


def _static_profile(connection: Any, tables: set[str]) -> dict[str, Any] | None:
    required = {
        "static_analysis_schedule_current",
        "static_analysis_schedule_profiles",
    }
    if not required.issubset(tables):
        return None
    row = connection.execute(
        """SELECT p.profile_key,p.analysis_type,p.analyzer_name,
                  p.analyzer_version,p.ruleset_version,p.rules_sha256
           FROM static_analysis_schedule_current c
           JOIN static_analysis_schedule_profiles p ON p.profile_key=c.profile_key
           WHERE c.singleton=1"""
    ).fetchone()
    return dict(row) if row is not None else None


def analysis_coverage(self: Any) -> dict[str, Any]:
    with self._connect() as connection:
        tables = {
            str(row["name"])
            for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")
        }
        package_records = int(connection.execute("SELECT COUNT(*) FROM packages").fetchone()[0])
        profile = _static_profile(connection, tables)
        coverage = _coverage_row(connection) if "analysis_v2_coverage_summary" in tables else None
        result: dict[str, Any] = {
            "package_records": package_records,
            "eligible_package_records": package_records,
            "analyzed_package_records": 0,
            "failed_package_records": 0,
            "unsupported_or_unresolvable_package_records": 0,
            "never_analyzed_package_records": package_records,
            "unique_artifacts_analyzed": 0,
            "running_package_records": 0,
            "static_profile": profile,
        }
        total_findings = 0
        unreviewed = 0
        if coverage is not None:
            total_findings = sum(
                int(coverage[name] or 0)
                for name in (
                    "info_findings", "low_findings", "medium_findings",
                    "high_findings", "critical_findings",
                )
            )
            unreviewed = int(coverage["unreviewed_findings"] or 0)
            result.update(
                {
                    "eligible_package_records": int(coverage["eligible_package_records"] or 0),
                    "analyzed_package_records": int(coverage["completed_package_records"] or 0),
                    "failed_package_records": int(coverage["failed_package_records"] or 0),
                    "unsupported_or_unresolvable_package_records": int(
                        (coverage["unsupported_package_records"] or 0)
                        + (coverage["unresolvable_package_records"] or 0)
                    ),
                    "never_analyzed_package_records": int(
                        coverage["never_attempted_package_records"] or 0
                    ),
                    "unique_artifacts_analyzed": int(
                        coverage["unique_artifacts_analyzed"] or 0
                    ),
                    "running_package_records": int(coverage["running_package_records"] or 0),
                }
            )

        package_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(packages)")
        }
        runtime_eligible = 0
        if {"registry_type", "transport", "version"}.issubset(package_columns):
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
        result["human_review"] = {
            "total": total_findings,
            "reviewed": max(0, total_findings - unreviewed),
            "available": coverage is not None,
        }
        result["controlled_behavioral"] = {
            "available": False,
            "completed": 0,
            "eligible": 0,
        }
        result["storage_v2"] = {
            "available": coverage is not None,
            "hot_catalog": "storage_v2_hot_catalog_info" in tables,
            "history_available": getattr(self, "_storage_v2_history_path", None) is not None,
        }
        return result
