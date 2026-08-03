"""Bounded read-only queries for public catalog intelligence pages."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from urllib.parse import quote


class PublicIntelligenceError(RuntimeError):
    """Raised when public intelligence data cannot be read safely."""


class PublicIntelligence:
    """Query snapshot changes, refresh health, and analysis coverage.

    The adapter opens SQLite in read-only/query-only mode, accepts only bounded
    integer pagination parameters, and never reads runtime, evidence, or worker
    filesystem state.
    """

    def __init__(self, database_path: Path):
        self._database_path = database_path.resolve()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        encoded = quote(self._database_path.as_posix(), safe="/")
        try:
            connection = sqlite3.connect(
                f"file:{encoded}?mode=ro", uri=True, timeout=5.0
            )
        except sqlite3.Error as exc:
            raise PublicIntelligenceError(
                f"cannot open Observatory catalog read-only: {exc}"
            ) from exc
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            yield connection
        except sqlite3.Error as exc:
            raise PublicIntelligenceError(
                f"public catalog intelligence query failed: {exc}"
            ) from exc
        finally:
            connection.close()

    def snapshot_history(self, *, page: int, page_size: int) -> dict[str, Any]:
        page, page_size = _bounded_page(page, page_size)
        offset = (page - 1) * page_size
        with self._connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])
            rows = _rows(
                connection.execute(
                    """
                    SELECT id, started_at, completed_at, pages, records_received,
                           unique_server_versions,
                           substr(snapshot_sha256, 1, 16) AS sha256_prefix
                    FROM snapshots
                    ORDER BY completed_at COLLATE BINARY DESC, id DESC
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

    def refresh_status(self) -> dict[str, Any]:
        """Return sanitized status derived only from published snapshots."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, started_at, completed_at, pages, records_received,
                       unique_server_versions,
                       substr(snapshot_sha256, 1, 16) AS sha256_prefix
                FROM snapshots
                ORDER BY completed_at COLLATE BINARY DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        return {"latest_snapshot": dict(row) if row is not None else None}

    def latest_changes(
        self, *, kind: str, page: int, page_size: int
    ) -> dict[str, Any]:
        """Compare exact immutable server-version membership across snapshots."""
        if kind not in {"added", "removed"}:
            raise ValueError("kind must be 'added' or 'removed'")
        page, page_size = _bounded_page(page, page_size)
        offset = (page - 1) * page_size

        with self._connect() as connection:
            pair = connection.execute(
                """
                SELECT
                    (SELECT id FROM snapshots
                     ORDER BY completed_at COLLATE BINARY DESC, id DESC
                     LIMIT 1 OFFSET 0) AS latest_id,
                    (SELECT id FROM snapshots
                     ORDER BY completed_at COLLATE BINARY DESC, id DESC
                     LIMIT 1 OFFSET 1) AS previous_id
                """
            ).fetchone()
            latest_id = pair["latest_id"] if pair else None
            previous_id = pair["previous_id"] if pair else None
            if latest_id is None or previous_id is None:
                return {
                    "kind": kind,
                    "latest_snapshot_id": latest_id,
                    "previous_snapshot_id": previous_id,
                    "page": page,
                    "page_size": page_size,
                    "total": 0,
                    "rows": [],
                }

            if kind == "added":
                source_id, comparison_id = latest_id, previous_id
            else:
                source_id, comparison_id = previous_id, latest_id

            base = """
                FROM snapshot_server_versions source_link
                JOIN server_versions sv ON sv.id=source_link.server_version_id
                LEFT JOIN snapshot_server_versions comparison_link
                  ON comparison_link.snapshot_id=?
                 AND comparison_link.server_version_id=source_link.server_version_id
                WHERE source_link.snapshot_id=?
                  AND comparison_link.server_version_id IS NULL
            """
            total = int(
                connection.execute(
                    "SELECT COUNT(*) " + base,
                    (comparison_id, source_id),
                ).fetchone()[0]
            )
            rows = _rows(
                connection.execute(
                    """
                    SELECT sv.id, sv.server_identifier, sv.server_version,
                           sv.registry_status, sv.published_at, sv.updated_at,
                           substr(sv.canonical_sha256, 1, 16) AS sha256_prefix
                    """
                    + base
                    + """
                    ORDER BY sv.server_identifier COLLATE BINARY,
                             sv.server_version COLLATE BINARY
                    LIMIT ? OFFSET ?
                    """,
                    (comparison_id, source_id, page_size, offset),
                ).fetchall()
            )

        return {
            "kind": kind,
            "latest_snapshot_id": latest_id,
            "previous_snapshot_id": previous_id,
            "page": page,
            "page_size": page_size,
            "total": total,
            "rows": rows,
        }

    def analysis_coverage(self) -> dict[str, int]:
        """Return exact-package-record coverage without exposing evidence."""
        with self._connect() as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table'"
                )
            }
            package_records = int(
                connection.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
            )
            if "analysis_runs" not in tables:
                return {
                    "package_records": package_records,
                    "analyzed_package_records": 0,
                    "failed_package_records": 0,
                    "never_analyzed_package_records": package_records,
                }
            row = connection.execute(
                """
                SELECT COUNT(*) AS package_records,
                       SUM(EXISTS(
                           SELECT 1 FROM analysis_runs ar
                           WHERE ar.package_id=p.id AND ar.status='completed'
                       )) AS analyzed_package_records,
                       SUM(EXISTS(
                           SELECT 1 FROM analysis_runs ar
                           WHERE ar.package_id=p.id AND ar.status='failed'
                       )) AS failed_package_records,
                       SUM(NOT EXISTS(
                           SELECT 1 FROM analysis_runs ar
                           WHERE ar.package_id=p.id
                       )) AS never_analyzed_package_records
                FROM packages p
                """
            ).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}


def _bounded_page(page: int, page_size: int) -> tuple[int, int]:
    if page < 1:
        page = 1
    if not 1 <= page_size <= 100:
        raise ValueError("page_size must be between 1 and 100")
    return page, page_size


def _rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]
