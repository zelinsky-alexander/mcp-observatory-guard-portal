"""Resolve an exact, portal-selected Observatory package without trusting HTTP text."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from urllib.parse import quote


class AnalysisSelectionError(ValueError):
    """Raised when a requested catalog package is not eligible for static analysis."""


@dataclass(frozen=True, slots=True)
class AnalysisCandidate:
    server_version_id: int
    package_id: int
    server_identifier: str
    server_version: str
    package_identifier: str
    package_version: str
    registry_type: str
    transport: str


@dataclass(frozen=True, slots=True)
class ReviewCandidate:
    finding_id: int
    analysis_run_id: int
    title: str
    subject_path: str
    expected_disposition: str


def resolve_candidate(
    database_path: Path, server_version_id: int, package_id: int
) -> AnalysisCandidate:
    if server_version_id <= 0 or package_id <= 0:
        raise AnalysisSelectionError("server and package identifiers must be positive")
    encoded = quote(database_path.resolve().as_posix(), safe="/")
    uri = f"file:{encoded}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=5.0)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            row = connection.execute(
                """
                SELECT sv.id AS server_version_id, p.id AS package_id,
                       sv.server_identifier, sv.server_version,
                       p.identifier AS package_identifier,
                       p.version AS package_version,
                       p.registry_type, p.transport
                FROM server_versions sv
                JOIN packages p ON p.server_version_id=sv.id
                WHERE sv.id=? AND p.id=?
                """,
                (server_version_id, package_id),
            ).fetchone()
    except sqlite3.Error as exc:
        raise AnalysisSelectionError(f"cannot resolve analysis selection: {exc}") from exc
    if row is None:
        raise AnalysisSelectionError("selected package does not belong to the selected server record")
    if row["registry_type"] != "npm":
        raise AnalysisSelectionError("static analysis currently supports npm packages only")
    if not row["package_version"]:
        raise AnalysisSelectionError("selected package has no exact declared version")
    return AnalysisCandidate(**dict(row))


def resolve_review_candidate(
    database_path: Path, finding_id: int, expected_disposition: str
) -> ReviewCandidate:
    if finding_id <= 0:
        raise AnalysisSelectionError("finding identifier must be positive")
    encoded = quote(database_path.resolve().as_posix(), safe="/")
    uri = f"file:{encoded}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=5.0)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            schema = connection.execute(
                "SELECT schema_version FROM schema_info WHERE singleton=1"
            ).fetchone()
            if schema is None or int(schema[0]) not in (2, 3):
                raise AnalysisSelectionError(
                    "finding review requires Observatory schema version 2 or 3"
                )
            row = connection.execute(
                """
                SELECT id AS finding_id, analysis_run_id, title, subject_path,
                       disposition AS expected_disposition
                FROM analysis_findings WHERE id=?
                """,
                (finding_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise AnalysisSelectionError(f"cannot resolve review selection: {exc}") from exc
    if row is None:
        raise AnalysisSelectionError("selected finding does not exist")
    if row["expected_disposition"] != expected_disposition:
        raise AnalysisSelectionError(
            "finding disposition changed before review submission"
        )
    return ReviewCandidate(**dict(row))
