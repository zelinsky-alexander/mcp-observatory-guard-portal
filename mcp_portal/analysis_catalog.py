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


RuntimeCandidate = AnalysisCandidate


@dataclass(frozen=True, slots=True)
class RuntimeObservationResult:
    runtime_observation_run_id: int
    artifact_sha256: str
    launch_profile_sha256: str
    inventory_sha256: str
    guard_version: str
    tool_count: int


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


def resolve_runtime_candidate(
    database_path: Path, server_version_id: int, package_id: int
) -> RuntimeCandidate:
    candidate = resolve_candidate(database_path, server_version_id, package_id)
    if candidate.transport != "stdio":
        raise AnalysisSelectionError(
            "runtime discovery currently supports stdio packages only"
        )
    return candidate


def resolve_runtime_result(
    database_path: Path,
    runtime_observation_run_id: int,
    server_version_id: int,
    package_id: int,
) -> RuntimeObservationResult:
    if runtime_observation_run_id <= 0:
        raise AnalysisSelectionError("runtime observation identifier must be positive")
    encoded = quote(database_path.resolve().as_posix(), safe="/")
    uri = f"file:{encoded}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=5.0)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            row = connection.execute(
                """SELECT r.id AS runtime_observation_run_id,
                          r.server_version_id,r.package_id,r.status,
                          r.artifact_sha256,r.launch_profile_sha256,
                          r.inventory_sha256,r.guard_version,
                          (SELECT COUNT(*) FROM runtime_observation_tools t
                           WHERE t.run_id=r.id) AS tool_count
                   FROM runtime_observation_runs r WHERE r.id=?""",
                (runtime_observation_run_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        raise AnalysisSelectionError(
            f"cannot resolve runtime observation result: {exc}"
        ) from exc
    if row is None or row["status"] != "completed":
        raise AnalysisSelectionError("runtime observation is not completed")
    if (
        int(row["server_version_id"]) != server_version_id
        or int(row["package_id"]) != package_id
    ):
        raise AnalysisSelectionError(
            "runtime observation belongs to a different catalog selection"
        )
    values = dict(row)
    values.pop("server_version_id")
    values.pop("package_id")
    values.pop("status")
    for name in (
        "artifact_sha256",
        "launch_profile_sha256",
        "inventory_sha256",
        "guard_version",
    ):
        if not isinstance(values[name], str) or not values[name]:
            raise AnalysisSelectionError(
                f"runtime observation has no valid {name}"
            )
    return RuntimeObservationResult(**values)


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
