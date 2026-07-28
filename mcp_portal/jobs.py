"""Portal-owned durable queue for on-demand static-analysis requests."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from .analysis_catalog import AnalysisCandidate


class JobStoreError(RuntimeError):
    """Raised when the portal queue cannot be read or updated safely."""


class JobStore:
    SCHEMA_VERSION = 1

    def __init__(self, path: Path):
        self.path = path.resolve()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        try:
            connection = sqlite3.connect(self.path, timeout=5.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
        except sqlite3.Error as exc:
            raise JobStoreError(f"cannot open portal job database: {exc}") from exc
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS schema_info(
                        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                        schema_version INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS analysis_jobs(
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        server_version_id INTEGER NOT NULL,
                        package_id INTEGER NOT NULL,
                        server_identifier TEXT NOT NULL,
                        server_version TEXT NOT NULL,
                        package_identifier TEXT NOT NULL,
                        package_version TEXT NOT NULL,
                        requested_at TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed')),
                        started_at TEXT,
                        completed_at TEXT,
                        analysis_run_id INTEGER,
                        artifact_sha256 TEXT,
                        reused_existing INTEGER CHECK(reused_existing IN (0,1)),
                        return_code INTEGER,
                        stdout_excerpt TEXT NOT NULL DEFAULT '',
                        stderr_excerpt TEXT NOT NULL DEFAULT '',
                        output_truncated INTEGER NOT NULL DEFAULT 0 CHECK(output_truncated IN (0,1)),
                        error_message TEXT
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS active_analysis_job
                    ON analysis_jobs(server_version_id, package_id)
                    WHERE status IN ('queued','running');
                    CREATE INDEX IF NOT EXISTS analysis_jobs_status
                    ON analysis_jobs(status, requested_at, id);
                    """
                )
                row = connection.execute(
                    "SELECT schema_version FROM schema_info WHERE singleton=1"
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO schema_info(singleton, schema_version) VALUES(1, ?)",
                        (self.SCHEMA_VERSION,),
                    )
                elif int(row[0]) != self.SCHEMA_VERSION:
                    raise JobStoreError(
                        f"unsupported portal job schema version: {int(row[0])}"
                    )
        except sqlite3.Error as exc:
            raise JobStoreError(f"cannot initialize portal job database: {exc}") from exc

    def enqueue(self, candidate: AnalysisCandidate) -> tuple[dict[str, Any], bool]:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT * FROM analysis_jobs
                    WHERE server_version_id=? AND package_id=?
                      AND status IN ('queued','running')
                    ORDER BY id DESC LIMIT 1
                    """,
                    (candidate.server_version_id, candidate.package_id),
                ).fetchone()
                if existing is not None:
                    return dict(existing), False
                values = asdict(candidate)
                cursor = connection.execute(
                    """
                    INSERT INTO analysis_jobs(
                        server_version_id, package_id, server_identifier, server_version,
                        package_identifier, package_version, requested_at, status)
                    VALUES(:server_version_id, :package_id, :server_identifier, :server_version,
                           :package_identifier, :package_version, :requested_at, 'queued')
                    """,
                    {**values, "requested_at": _utc_now()},
                )
                row = connection.execute(
                    "SELECT * FROM analysis_jobs WHERE id=?", (cursor.lastrowid,)
                ).fetchone()
                assert row is not None
                return dict(row), True
        except sqlite3.IntegrityError:
            current = self.find_active(candidate.server_version_id, candidate.package_id)
            if current is None:
                raise JobStoreError("analysis request conflicted but no active job was found")
            return current, False
        except sqlite3.Error as exc:
            raise JobStoreError(f"cannot enqueue analysis request: {exc}") from exc

    def find_active(self, server_version_id: int, package_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM analysis_jobs WHERE server_version_id=? AND package_id=?
                   AND status IN ('queued','running') ORDER BY id DESC LIMIT 1""",
                (server_version_id, package_id),
            ).fetchone()
            return None if row is None else dict(row)

    def claim_next(self) -> dict[str, Any] | None:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM analysis_jobs WHERE status='queued' ORDER BY requested_at, id LIMIT 1"
                ).fetchone()
                if row is None:
                    return None
                now = _utc_now()
                cursor = connection.execute(
                    "UPDATE analysis_jobs SET status='running', started_at=? WHERE id=? AND status='queued'",
                    (now, row["id"]),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return None
                claimed = connection.execute(
                    "SELECT * FROM analysis_jobs WHERE id=?", (row["id"],)
                ).fetchone()
                assert claimed is not None
                return dict(claimed)
        except sqlite3.Error as exc:
            raise JobStoreError(f"cannot claim analysis job: {exc}") from exc

    def complete(
        self,
        job_id: int,
        *,
        analysis_run_id: int,
        artifact_sha256: str | None,
        reused_existing: bool,
        return_code: int,
        stdout_excerpt: str,
        stderr_excerpt: str,
        output_truncated: bool,
    ) -> None:
        self._finish(
            job_id,
            status="completed",
            analysis_run_id=analysis_run_id,
            artifact_sha256=artifact_sha256,
            reused_existing=int(reused_existing),
            return_code=return_code,
            stdout_excerpt=stdout_excerpt,
            stderr_excerpt=stderr_excerpt,
            output_truncated=int(output_truncated),
            error_message=None,
        )

    def fail(
        self,
        job_id: int,
        *,
        error_message: str,
        return_code: int | None = None,
        stdout_excerpt: str = "",
        stderr_excerpt: str = "",
        output_truncated: bool = False,
    ) -> None:
        self._finish(
            job_id,
            status="failed",
            analysis_run_id=None,
            artifact_sha256=None,
            reused_existing=None,
            return_code=return_code,
            stdout_excerpt=stdout_excerpt,
            stderr_excerpt=stderr_excerpt,
            output_truncated=int(output_truncated),
            error_message=error_message[:2000],
        )

    def _finish(self, job_id: int, *, status: str, **values: Any) -> None:
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE analysis_jobs
                    SET status=:status, completed_at=:completed_at,
                        analysis_run_id=:analysis_run_id, artifact_sha256=:artifact_sha256,
                        reused_existing=:reused_existing, return_code=:return_code,
                        stdout_excerpt=:stdout_excerpt, stderr_excerpt=:stderr_excerpt,
                        output_truncated=:output_truncated, error_message=:error_message
                    WHERE id=:job_id AND status='running'
                    """,
                    {
                        "job_id": job_id,
                        "status": status,
                        "completed_at": _utc_now(),
                        **values,
                    },
                )
                if cursor.rowcount != 1:
                    raise JobStoreError(f"analysis job {job_id} is not running")
        except sqlite3.Error as exc:
            raise JobStoreError(f"cannot finish analysis job: {exc}") from exc

    def get(self, job_id: int) -> dict[str, Any] | None:
        if job_id <= 0:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM analysis_jobs WHERE id=?", (job_id,)
            ).fetchone()
            return None if row is None else dict(row)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 200))
        with self._connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM analysis_jobs ORDER BY requested_at DESC, id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            ]

    def summary(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT SUM(status='queued') AS queued, SUM(status='running') AS running,
                          SUM(status='completed') AS completed, SUM(status='failed') AS failed
                   FROM analysis_jobs"""
            ).fetchone()
        return {
            "queued": int(row["queued"] or 0),
            "running": int(row["running"] or 0),
            "completed": int(row["completed"] or 0),
            "failed": int(row["failed"] or 0),
            "recent": self.recent(12),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
