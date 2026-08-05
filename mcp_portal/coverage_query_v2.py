"""Schema-tolerant coverage query used during catalog migrations."""

from __future__ import annotations

from typing import Any


def apply_coverage_query_v2() -> None:
    from .public_intelligence import PublicIntelligence

    PublicIntelligence.analysis_coverage = analysis_coverage


def _columns(connection: Any, table: str) -> set[str]:
    return {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})")}


def analysis_coverage(self: Any) -> dict[str, Any]:
    with self._connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
        }
        package_columns = _columns(connection, "packages")
        total = int(connection.execute("SELECT COUNT(*) FROM packages").fetchone()[0])
        result: dict[str, Any] = {
            "package_records": total,
            "eligible_package_records": total,
            "analyzed_package_records": 0,
            "failed_package_records": 0,
            "unsupported_or_unresolvable_package_records": 0,
            "never_analyzed_package_records": total,
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
                       FROM static_analysis_schedule_state WHERE profile_key=?""",
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
            run_columns = _columns(connection, "analysis_runs")
            completed = int(
                connection.execute(
                    """SELECT COUNT(*) FROM packages p WHERE EXISTS(
                         SELECT 1 FROM analysis_runs ar
                         WHERE ar.package_id=p.id AND ar.status='completed')"""
                ).fetchone()[0]
            )
            failed = int(
                connection.execute(
                    """SELECT COUNT(*) FROM packages p WHERE EXISTS(
                         SELECT 1 FROM analysis_runs ar
                         WHERE ar.package_id=p.id AND ar.status='failed')
                       AND NOT EXISTS(
                         SELECT 1 FROM analysis_runs ar
                         WHERE ar.package_id=p.id AND ar.status='completed')"""
                ).fetchone()[0]
            )
            never = int(
                connection.execute(
                    """SELECT COUNT(*) FROM packages p WHERE NOT EXISTS(
                         SELECT 1 FROM analysis_runs ar WHERE ar.package_id=p.id)"""
                ).fetchone()[0]
            )
            result.update(
                {
                    "analyzed_package_records": completed,
                    "failed_package_records": failed,
                    "never_analyzed_package_records": never,
                }
            )
            if {"registry_type", "version"}.issubset(package_columns):
                eligible = int(
                    connection.execute(
                        """SELECT COUNT(*) FROM packages
                           WHERE registry_type IN('npm','pypi')
                             AND version IS NOT NULL AND trim(version)<>''"""
                    ).fetchone()[0]
                )
                result["eligible_package_records"] = eligible
                result["unsupported_or_unresolvable_package_records"] = total - eligible
                result["never_analyzed_package_records"] = int(
                    connection.execute(
                        """SELECT COUNT(*) FROM packages p
                           WHERE p.registry_type IN('npm','pypi')
                             AND p.version IS NOT NULL AND trim(p.version)<>''
                             AND NOT EXISTS(SELECT 1 FROM analysis_runs ar
                               WHERE ar.package_id=p.id)"""
                    ).fetchone()[0]
                )
            if "artifact_sha256" in run_columns:
                result["unique_artifacts_analyzed"] = int(
                    connection.execute(
                        """SELECT COUNT(DISTINCT artifact_sha256)
                           FROM analysis_runs
                           WHERE status='completed' AND artifact_sha256 IS NOT NULL"""
                    ).fetchone()[0]
                )
        elif {"registry_type", "version"}.issubset(package_columns):
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
