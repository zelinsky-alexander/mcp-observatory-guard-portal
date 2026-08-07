"""Scale public catalog reads to large longitudinal SQLite catalogs.

The public list and dashboard views should read primarily from the latest
published snapshot. Historical versions remain available through server detail
pages, but they must not make the common landing/list queries scan all history.
"""

from __future__ import annotations

from typing import Any


def apply_performance_hotfix() -> None:
    from .catalog import Catalog

    Catalog.dashboard = dashboard
    Catalog.search_servers = search_servers


def _rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def dashboard(self: Any, *, recent_limit: int = 12) -> dict[str, Any]:
    status = self.schema_status()
    with self._connect() as connection:
        latest_snapshot = _row(
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

        totals = {"servers": 0, "immutable_versions": 0, "canonical_artifacts": 0}
        changes: list[dict[str, Any]] = []
        if latest_snapshot is not None:
            snapshot_id = latest_snapshot["id"]
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
                    ORDER BY COALESCE(sv.updated_at, sv.published_at, '') COLLATE BINARY DESC,
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
        if status["analysis_available"]:
            counts = connection.execute(
                """
                SELECT SUM(status='completed') AS completed,
                       SUM(status='failed') AS failed,
                       SUM(status='running') AS running
                FROM analysis_runs
                """
            ).fetchone()
            if counts is not None:
                for key in ("completed", "failed", "running"):
                    analysis[key] = int(counts[key] or 0)

            analysis["unreviewed_high_or_critical"] = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM analysis_findings
                    WHERE disposition='unreviewed'
                      AND severity IN ('high','critical')
                    """
                ).fetchone()[0]
            )

            # Limit analysis_runs before joining findings. The old query grouped
            # the complete findings history and only then applied LIMIT, which
            # becomes increasingly expensive as static-analysis coverage grows.
            analysis["recent"] = _rows(
                connection.execute(
                    """
                    WITH recent_runs AS (
                        SELECT id, status, started_at, completed_at,
                               artifact_sha256, ruleset_version,
                               server_version_id, package_id
                        FROM analysis_runs
                        ORDER BY started_at COLLATE BINARY DESC, id DESC
                        LIMIT ?
                    )
                    SELECT rr.id, rr.status, rr.started_at, rr.completed_at,
                           rr.artifact_sha256, rr.ruleset_version,
                           sv.server_identifier, sv.server_version,
                           p.identifier AS package_identifier,
                           SUM(af.severity='critical') AS critical_count,
                           SUM(af.severity='high') AS high_count,
                           SUM(af.severity='medium') AS medium_count
                    FROM recent_runs rr
                    JOIN server_versions sv ON sv.id=rr.server_version_id
                    JOIN packages p ON p.id=rr.package_id
                    LEFT JOIN analysis_findings af ON af.analysis_run_id=rr.id
                    GROUP BY rr.id
                    ORDER BY rr.started_at COLLATE BINARY DESC, rr.id DESC
                    """,
                    (recent_limit,),
                ).fetchall()
            )

        return {
            "schema": status,
            "latest_snapshot": latest_snapshot,
            "totals": totals,
            "changes": changes,
            "analysis": analysis,
        }


def search_servers(
    self: Any,
    query: str,
    *,
    page: int,
    page_size: int,
    ecosystem: str = "",
) -> dict[str, Any]:
    if page < 1:
        page = 1
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")

    normalized = query.strip()[:200]
    normalized_ecosystem = ecosystem.strip()[:200]
    pattern = "%" + _escape_like(normalized) + "%"
    offset = (page - 1) * page_size

    with self._connect() as connection:
        latest = connection.execute(
            """
            SELECT id FROM snapshots
            ORDER BY completed_at COLLATE BINARY DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        if latest is None:
            return {
                "query": normalized,
                "ecosystem": normalized_ecosystem,
                "page": page,
                "page_size": page_size,
                "total": 0,
                "rows": [],
            }

        parameters: dict[str, Any] = {
            "snapshot_id": int(latest["id"]),
            "query": normalized,
            "pattern": pattern,
            "ecosystem": normalized_ecosystem,
            "page_size": page_size,
            "offset": offset,
        }
        where_sql = """
            link.snapshot_id=:snapshot_id
            AND (
                :query='' OR
                sv.server_identifier LIKE :pattern ESCAPE '\\' OR
                COALESCE(sv.description, '') LIKE :pattern ESCAPE '\\' OR
                EXISTS(SELECT 1 FROM packages sp
                       WHERE sp.server_version_id=sv.id
                         AND sp.identifier LIKE :pattern ESCAPE '\\') OR
                EXISTS(SELECT 1 FROM repositories sr
                       WHERE sr.server_version_id=sv.id
                         AND COALESCE(sr.url, '') LIKE :pattern ESCAPE '\\') OR
                EXISTS(SELECT 1 FROM remotes sm
                       WHERE sm.server_version_id=sv.id
                         AND sm.url LIKE :pattern ESCAPE '\\')
            )
            AND (
                :ecosystem='' OR EXISTS(
                    SELECT 1 FROM packages ep
                    WHERE ep.server_version_id=sv.id
                      AND ep.registry_type=:ecosystem COLLATE BINARY
                )
            )
        """

        total = int(
            connection.execute(
                f"""
                SELECT COUNT(DISTINCT sv.server_identifier)
                FROM snapshot_server_versions link
                JOIN server_versions sv ON sv.id=link.server_version_id
                WHERE {where_sql}
                """,
                parameters,
            ).fetchone()[0]
        )

        rows = _rows(
            connection.execute(
                f"""
                WITH matching AS (
                    SELECT sv.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY sv.server_identifier
                               ORDER BY COALESCE(sv.updated_at, sv.published_at, '') COLLATE BINARY DESC,
                                        sv.id DESC
                           ) AS row_number
                    FROM snapshot_server_versions link
                    JOIN server_versions sv ON sv.id=link.server_version_id
                    WHERE {where_sql}
                )
                SELECT m.id, m.server_identifier, m.server_version,
                       m.description, m.registry_status, m.published_at,
                       m.updated_at, m.canonical_sha256,
                       (SELECT COUNT(DISTINCT allv.server_version)
                        FROM server_versions allv
                        WHERE allv.server_identifier=m.server_identifier) AS version_count,
                       (SELECT p.identifier FROM packages p
                        WHERE p.server_version_id=m.id
                          AND (:ecosystem='' OR p.registry_type=:ecosystem COLLATE BINARY)
                        ORDER BY p.position LIMIT 1) AS package_identifier,
                       (SELECT p.transport FROM packages p
                        WHERE p.server_version_id=m.id
                          AND (:ecosystem='' OR p.registry_type=:ecosystem COLLATE BINARY)
                        ORDER BY p.position LIMIT 1) AS package_transport,
                       (SELECT r.host FROM repositories r
                        WHERE r.server_version_id=m.id) AS repository_host
                FROM matching m
                WHERE m.row_number=1
                ORDER BY COALESCE(m.updated_at, m.published_at, '') COLLATE BINARY DESC,
                         m.server_identifier COLLATE BINARY
                LIMIT :page_size OFFSET :offset
                """,
                parameters,
            ).fetchall()
        )

    return {
        "query": normalized,
        "ecosystem": normalized_ecosystem,
        "page": page,
        "page_size": page_size,
        "total": total,
        "rows": rows,
    }
