"""Storage v2 read model for the public portal.

The hot catalog keeps registry state, analysis run identity and compact Storage
v2 summaries while bulky v1 detail rows may be absent. Common public routes must
therefore never aggregate the raw findings/files history. This module replaces
only the dashboard and coverage readers; server search continues to use the
latest-snapshot performance implementation.
"""

from __future__ import annotations

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

    original_schema_status = Catalog.schema_status

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
        return status

    Catalog.schema_status = schema_status
    Catalog.dashboard = dashboard
    PublicIntelligence.analysis_coverage = analysis_coverage


def _rows(rows: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _latest_snapshot(connection: Any) -> dict[str, Any] | None:
    return _row(
        connection.execute(
            """
            SELECT id, snapshot_sha256, completed_at, started_at,
                   registry_base_url, bundle_version, pages,
                   records_received, unique_server_versions, imported_at
            FROM snapshots
            ORDER BY completed_at COLLATE BINARY DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    )


def _coverage_row(connection: Any) -> Any:
    tables = {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table'"
        )
    }
    if "analysis_v2_coverage_summary" not in tables:
        return None
    if {
        "static_analysis_schedule_current",
        "static_analysis_schedule_profiles",
    }.issubset(tables):
        row = connection.execute(
            """SELECT c.*
               FROM analysis_v2_coverage_summary c
               JOIN static_analysis_schedule_current cur
                 ON cur.profile_key=c.profile_key
               WHERE cur.singleton=1"""
        ).fetchone()
        if row is not None:
            return row
    return connection.execute(
        """SELECT * FROM analysis_v2_coverage_summary
           ORDER BY updated_at COLLATE BINARY DESC, profile_key COLLATE BINARY
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
                """
                SELECT COUNT(DISTINCT sv.server_identifier) AS servers,
                       COUNT(*) AS immutable_versions,
                       COUNT(DISTINCT sv.canonical_sha256) AS canonical_artifacts
                FROM snapshot_server_versions link
                JOIN server_versions sv ON sv.id=link.server_version_id
                WHERE link.snapshot_id=?
                """,
                (snapshot_id,),
            ).fetchone()
            if counted is not None:
                totals = {key: int(counted[key] or 0) for key in totals}
            changes = _rows(
                connection.execute(
                    """
                    SELECT sv.id, sv.server_identifier, sv.server_version,
                           sv.description, sv.registry_status, sv.published_at,
                           sv.updated_at, sv.canonical_sha256,
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
                    LIMIT ?
                    """,
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
            coverage = _coverage_row(connection)
            if coverage is not None:
                analysis["completed"] = int(coverage["completed_package_records"] or 0)
                analysis["failed"] = int(coverage["failed_package_records"] or 0)
                analysis["running"] = int(coverage["running_package_records"] or 0)
                analysis["unreviewed_high_or_critical"] = int(
                    coverage["unreviewed_high_or_critical_findings"] or 0
                )
            analysis["recent"] = _rows(
                connection.execute(
                    """
                    WITH recent_runs AS (
                        SELECT id,status,started_at,completed_at,artifact_sha256,
                               ruleset_version,server_version_id,package_id
                        FROM analysis_runs
                        ORDER BY started_at COLLATE BINARY DESC,id DESC
                        LIMIT ?
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
                    ORDER BY rr.started_at COLLATE BINARY DESC,rr.id DESC
                    """,
                    (recent_limit,),
                ).fetchall()
            )
        elif status.get("analysis_available"):
            counts = connection.execute(
                """SELECT SUM(status='completed') completed,
                          SUM(status='failed') failed,
                          SUM(status='running') running
                   FROM analysis_runs"""
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
           JOIN static_analysis_schedule_profiles p
             ON p.profile_key=c.profile_key
           WHERE c.singleton=1"""
    ).fetchone()
    return dict(row) if row is not None else None


def analysis_coverage(self: Any) -> dict[str, Any]:
    with self._connect() as connection:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
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
                    "info_findings",
                    "low_findings",
                    "medium_findings",
                    "high_findings",
                    "critical_findings",
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
                    "running_package_records": int(
                        coverage["running_package_records"] or 0
                    ),
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
        }
        return result
