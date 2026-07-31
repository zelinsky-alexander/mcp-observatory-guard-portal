"""Read-only query adapter for the MCP Observatory SQLite catalog."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from urllib.parse import quote


class CatalogError(RuntimeError):
    """Raised when the configured catalog is incompatible or unavailable."""


class Catalog:
    """Queries an Observatory catalog without modifying it."""

    REQUIRED_TABLES = {
        "schema_info",
        "snapshots",
        "server_versions",
        "snapshot_server_versions",
        "repositories",
        "packages",
        "package_arguments",
        "package_environment",
        "remotes",
    }

    ANALYSIS_TABLES = {
        "analysis_runs",
        "analysis_artifacts",
        "analysis_findings",
        "analysis_files",
        "analysis_dependencies",
        "analysis_evidence",
    }
    REVIEW_TABLE = "analysis_finding_reviews"
    RUNTIME_TABLES = {
        "runtime_observation_runs",
        "runtime_observation_tools",
    }

    def __init__(self, database_path: Path):
        self._database_path = database_path.resolve()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        encoded_path = quote(self._database_path.as_posix(), safe="/")
        uri = f"file:{encoded_path}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        except sqlite3.Error as exc:
            raise CatalogError(f"cannot open Observatory catalog read-only: {exc}") from exc
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            yield connection
        except sqlite3.Error as exc:
            raise CatalogError(f"Observatory catalog query failed: {exc}") from exc
        finally:
            connection.close()

    def schema_status(self) -> dict[str, Any]:
        with self._connect() as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table'"
                )
            }
            missing = sorted(self.REQUIRED_TABLES - tables)
            if missing:
                raise CatalogError(
                    "incompatible Observatory catalog; missing tables: " + ", ".join(missing)
                )
            row = connection.execute(
                "SELECT schema_version, search_mode FROM schema_info WHERE singleton=1"
            ).fetchone()
            if row is None:
                raise CatalogError("schema_info does not contain the singleton schema row")
            version = int(row["schema_version"])
            if version not in (1, 2, 3):
                raise CatalogError(f"unsupported Observatory schema version: {version}")
            if version == 3 and self.REVIEW_TABLE not in tables:
                raise CatalogError(
                    "incompatible Observatory catalog; missing table: "
                    + self.REVIEW_TABLE
                )
            return {
                "schema_version": version,
                "search_mode": row["search_mode"],
                "analysis_available": self.ANALYSIS_TABLES.issubset(tables),
                "review_available": self.REVIEW_TABLE in tables,
                "runtime_available": self.RUNTIME_TABLES.issubset(tables),
            }

    def runtime_observation(self, run_id: int) -> dict[str, Any] | None:
        if run_id <= 0:
            return None
        if not self.schema_status()["runtime_available"]:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """SELECT r.*,sv.server_identifier,sv.server_version,
                          p.identifier AS package_identifier,
                          p.version AS package_version
                   FROM runtime_observation_runs r
                   JOIN server_versions sv ON sv.id=r.server_version_id
                   JOIN packages p ON p.id=r.package_id
                   WHERE r.id=?""",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["tools"] = [
                dict(tool)
                for tool in connection.execute(
                    """SELECT name,substr(definition_json,1,4096) AS definition_json,
                              length(definition_json) > 4096 AS definition_truncated,
                              definition_sha256
                       FROM runtime_observation_tools WHERE run_id=?
                       ORDER BY name COLLATE BINARY LIMIT 256""",
                    (run_id,),
                ).fetchall()
            ]
            return result

    def dashboard(self, *, recent_limit: int = 12) -> dict[str, Any]:
        status = self.schema_status()
        with self._connect() as connection:
            latest_snapshot = _row_to_dict(
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
            totals = _row_to_dict(
                connection.execute(
                    """
                    SELECT COUNT(DISTINCT server_identifier) AS servers,
                           COUNT(*) AS immutable_versions,
                           COUNT(DISTINCT canonical_sha256) AS canonical_artifacts
                    FROM server_versions
                    """
                ).fetchone()
            ) or {"servers": 0, "immutable_versions": 0, "canonical_artifacts": 0}

            changes: list[dict[str, Any]] = []
            if latest_snapshot is not None:
                changes = _rows_to_dicts(
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
                        (latest_snapshot["id"], recent_limit),
                    ).fetchall()
                )

            analysis = {
                "completed": 0,
                "failed": 0,
                "running": 0,
                "unreviewed_high_or_critical": 0,
                "recent": [],
            }
            if status["analysis_available"]:
                counts = _row_to_dict(
                    connection.execute(
                        """
                        SELECT SUM(status='completed') AS completed,
                               SUM(status='failed') AS failed,
                               SUM(status='running') AS running
                        FROM analysis_runs
                        """
                    ).fetchone()
                )
                if counts:
                    analysis.update({key: int(counts[key] or 0) for key in counts})
                analysis["unreviewed_high_or_critical"] = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM analysis_findings
                        WHERE disposition='unreviewed' AND severity IN ('high','critical')
                        """
                    ).fetchone()[0]
                )
                analysis["recent"] = _rows_to_dicts(
                    connection.execute(
                        """
                        SELECT ar.id, ar.status, ar.started_at, ar.completed_at,
                               ar.artifact_sha256, ar.ruleset_version,
                               sv.server_identifier, sv.server_version,
                               p.identifier AS package_identifier,
                               SUM(af.severity='critical') AS critical_count,
                               SUM(af.severity='high') AS high_count,
                               SUM(af.severity='medium') AS medium_count
                        FROM analysis_runs ar
                        JOIN server_versions sv ON sv.id=ar.server_version_id
                        JOIN packages p ON p.id=ar.package_id
                        LEFT JOIN analysis_findings af ON af.analysis_run_id=ar.id
                        GROUP BY ar.id
                        ORDER BY ar.started_at COLLATE BINARY DESC, ar.id DESC
                        LIMIT ?
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

    def ecosystem_summary(self) -> list[dict[str, Any]]:
        """Summarize package declarations by Registry ecosystem."""
        self.schema_status()
        with self._connect() as connection:
            return _rows_to_dicts(
                connection.execute(
                    """
                    SELECT registry_type AS ecosystem,
                           COUNT(*) AS package_records,
                           COUNT(DISTINCT identifier) AS unique_packages,
                           COUNT(DISTINCT server_version_id) AS server_versions
                    FROM packages
                    GROUP BY registry_type
                    ORDER BY package_records DESC, registry_type COLLATE BINARY
                    """
                ).fetchall()
            )

    def unreviewed_high_or_critical_findings(
        self, *, page: int, page_size: int
    ) -> dict[str, Any]:
        """List the findings counted by the dashboard review-queue card."""
        if page < 1:
            page = 1
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

        status = self.schema_status()
        if not status["analysis_available"]:
            return {
                "page": page,
                "page_size": page_size,
                "total": 0,
                "rows": [],
            }

        offset = (page - 1) * page_size
        with self._connect() as connection:
            total = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM analysis_findings
                    WHERE disposition='unreviewed'
                      AND severity IN ('high','critical')
                    """
                ).fetchone()[0]
            )
            rows = _rows_to_dicts(
                connection.execute(
                    """
                    SELECT af.id, af.analysis_run_id, af.rule_id, af.category,
                           af.severity, af.confidence, af.disposition,
                           af.subject_path, af.line_number, af.symbol,
                           af.title, af.explanation,
                           ar.started_at,
                           sv.server_identifier, sv.server_version,
                           p.identifier AS package_identifier
                    FROM analysis_findings af
                    JOIN analysis_runs ar ON ar.id=af.analysis_run_id
                    JOIN server_versions sv ON sv.id=ar.server_version_id
                    JOIN packages p ON p.id=ar.package_id
                    WHERE af.disposition='unreviewed'
                      AND af.severity IN ('high','critical')
                    ORDER BY CASE af.severity
                                 WHEN 'critical' THEN 0 ELSE 1 END,
                             ar.started_at COLLATE BINARY DESC,
                             af.id DESC
                    LIMIT ? OFFSET ?
                    """,
                    (page_size, offset),
                ).fetchall()
            )
        return {
            "page": page,
            "page_size": page_size,
            "total": total,
            "rows": rows,
        }

    def search_servers(
        self,
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
        where_sql = """
            (:query = '' OR
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
                      AND sm.url LIKE :pattern ESCAPE '\\'))
            AND (:ecosystem = '' OR EXISTS(
                SELECT 1 FROM packages ep
                WHERE ep.server_version_id=sv.id
                  AND ep.registry_type = :ecosystem COLLATE BINARY
            ))
        """
        offset = (page - 1) * page_size
        parameters = {
            "query": normalized,
            "pattern": pattern,
            "ecosystem": normalized_ecosystem,
        }

        with self._connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(DISTINCT sv.server_identifier) "
                    f"FROM server_versions sv WHERE {where_sql}",
                    parameters,
                ).fetchone()[0]
            )
            rows = _rows_to_dicts(
                connection.execute(
                    f"""
                    WITH matching AS (
                        SELECT sv.*,
                               ROW_NUMBER() OVER (
                                   PARTITION BY sv.server_identifier
                                   ORDER BY COALESCE(sv.updated_at, sv.published_at, '') COLLATE BINARY DESC,
                                            sv.id DESC
                               ) AS row_number
                        FROM server_versions sv
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
                              AND (:ecosystem = '' OR
                                   p.registry_type = :ecosystem COLLATE BINARY)
                            ORDER BY p.position LIMIT 1) AS package_identifier,
                           (SELECT p.transport FROM packages p
                            WHERE p.server_version_id=m.id
                              AND (:ecosystem = '' OR
                                   p.registry_type = :ecosystem COLLATE BINARY)
                            ORDER BY p.position LIMIT 1) AS package_transport,
                           (SELECT r.host FROM repositories r
                            WHERE r.server_version_id=m.id) AS repository_host
                    FROM matching m
                    WHERE m.row_number=1
                    ORDER BY COALESCE(m.updated_at, m.published_at, '') COLLATE BINARY DESC,
                             m.server_identifier COLLATE BINARY
                    LIMIT :page_size OFFSET :offset
                    """,
                    parameters | {"page_size": page_size, "offset": offset},
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

    def server_detail(self, server_identifier: str) -> dict[str, Any] | None:
        identifier = server_identifier.strip()
        if not identifier or len(identifier) > 512:
            return None
        analysis_available = self.schema_status()["analysis_available"]
        with self._connect() as connection:
            versions = _rows_to_dicts(
                connection.execute(
                    """
                    SELECT id, server_identifier, server_version, description,
                           registry_status, published_at, updated_at,
                           canonical_sha256, canonical_json
                    FROM server_versions
                    WHERE server_identifier=?
                    ORDER BY COALESCE(updated_at, published_at, '') COLLATE BINARY DESC,
                             server_version COLLATE BINARY DESC,
                             id DESC
                    """,
                    (identifier,),
                ).fetchall()
            )
            if not versions:
                return None

            for version in versions:
                version_id = version["id"]
                version["repository"] = _row_to_dict(
                    connection.execute(
                        """
                        SELECT source, url, scheme, host, owner, repository_name
                        FROM repositories WHERE server_version_id=?
                        """,
                        (version_id,),
                    ).fetchone()
                )
                version["packages"] = _rows_to_dicts(
                    connection.execute(
                        """
                        SELECT id, position, registry_type, identifier, version, transport
                        FROM packages WHERE server_version_id=? ORDER BY position
                        """,
                        (version_id,),
                    ).fetchall()
                )
                for package in version["packages"]:
                    package["arguments"] = _rows_to_dicts(
                        connection.execute(
                            """
                            SELECT position, argument_value
                            FROM package_arguments WHERE package_id=? ORDER BY position
                            """,
                            (package["id"],),
                        ).fetchall()
                    )
                    package["environment"] = _rows_to_dicts(
                        connection.execute(
                            """
                            SELECT position, name, required, description
                            FROM package_environment WHERE package_id=? ORDER BY position
                            """,
                            (package["id"],),
                        ).fetchall()
                    )
                version["remotes"] = _rows_to_dicts(
                    connection.execute(
                        """
                        SELECT position, url, scheme, host, port, transport
                        FROM remotes WHERE server_version_id=? ORDER BY position
                        """,
                        (version_id,),
                    ).fetchall()
                )
                version["snapshots"] = _rows_to_dicts(
                    connection.execute(
                        """
                        SELECT s.snapshot_sha256, s.completed_at, s.imported_at
                        FROM snapshot_server_versions link
                        JOIN snapshots s ON s.id=link.snapshot_id
                        WHERE link.server_version_id=?
                        ORDER BY s.completed_at COLLATE BINARY DESC, s.id DESC
                        LIMIT 20
                        """,
                        (version_id,),
                    ).fetchall()
                )
                version["analyses"] = []
                if analysis_available:
                    version["analyses"] = _rows_to_dicts(
                        connection.execute(
                            """
                            SELECT ar.id, ar.status, ar.started_at, ar.completed_at,
                                   ar.artifact_sha256, ar.ruleset_version,
                                   ar.integrity_verified, ar.error_stage, ar.error_message,
                                   p.identifier AS package_identifier,
                                   SUM(af.severity='critical') AS critical_count,
                                   SUM(af.severity='high') AS high_count,
                                   SUM(af.severity='medium') AS medium_count,
                                   COUNT(af.id) AS finding_count
                            FROM analysis_runs ar
                            JOIN packages p ON p.id=ar.package_id
                            LEFT JOIN analysis_findings af ON af.analysis_run_id=ar.id
                            WHERE ar.server_version_id=?
                            GROUP BY ar.id
                            ORDER BY ar.started_at COLLATE BINARY DESC, ar.id DESC
                            """,
                            (version_id,),
                        ).fetchall()
                    )

            return {
                "server_identifier": identifier,
                "description": versions[0]["description"],
                "versions": versions,
                "analysis_available": analysis_available,
            }

    def analysis_detail(self, analysis_run_id: int) -> dict[str, Any] | None:
        if analysis_run_id <= 0 or not self.schema_status()["analysis_available"]:
            return None
        with self._connect() as connection:
            run = _row_to_dict(
                connection.execute(
                    """
                    SELECT ar.*, sv.server_identifier, sv.server_version,
                           p.identifier AS package_identifier,
                           p.registry_type, p.transport
                    FROM analysis_runs ar
                    JOIN server_versions sv ON sv.id=ar.server_version_id
                    JOIN packages p ON p.id=ar.package_id
                    WHERE ar.id=?
                    """,
                    (analysis_run_id,),
                ).fetchone()
            )
            if run is None:
                return None
            run["findings"] = _rows_to_dicts(
                connection.execute(
                    """
                    SELECT id, rule_id, category, severity, confidence, disposition,
                           subject_path, line_number, symbol, title, evidence, explanation
                    FROM analysis_findings WHERE analysis_run_id=?
                    ORDER BY CASE severity
                                 WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                                 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END,
                             rule_id, id
                    """,
                    (analysis_run_id,),
                ).fetchall()
            )
            reviews_by_finding: dict[int, list[dict[str, Any]]] = {}
            if self.schema_status()["review_available"]:
                for review in _rows_to_dicts(
                    connection.execute(
                        """
                        SELECT r.id, r.finding_id, r.previous_disposition,
                               r.disposition, r.reviewer, r.reviewed_at
                        FROM analysis_finding_reviews r
                        JOIN analysis_findings af ON af.id=r.finding_id
                        WHERE af.analysis_run_id=?
                        ORDER BY r.id DESC
                        """,
                        (analysis_run_id,),
                    ).fetchall()
                ):
                    reviews_by_finding.setdefault(
                        int(review["finding_id"]), []
                    ).append(review)
            for finding in run["findings"]:
                finding["reviews"] = reviews_by_finding.get(
                    int(finding["id"]), []
                )
            run["evidence_files"] = _rows_to_dicts(
                connection.execute(
                    """
                    SELECT evidence_type, relative_path, sha256, byte_size, media_type
                    FROM analysis_evidence WHERE analysis_run_id=?
                    ORDER BY relative_path
                    """,
                    (analysis_run_id,),
                ).fetchall()
            )
            return run

    def finding_source_metadata(
        self, finding_id: int
    ) -> dict[str, Any] | None:
        if finding_id <= 0 or not self.schema_status()["analysis_available"]:
            return None
        with self._connect() as connection:
            return _row_to_dict(
                connection.execute(
                    """
                    SELECT id, analysis_run_id, subject_path, line_number
                    FROM analysis_findings WHERE id=?
                    """,
                    (finding_id,),
                ).fetchone()
            )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return None if row is None else dict(row)


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]
