"""Portal configuration loaded from a small, explicit environment surface."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when portal configuration is invalid."""


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    jobs_database_path: Path
    observatory_binary: Path
    rules_path: Path
    evidence_root: Path
    writer_lock_path: Path
    timeout_seconds: int = 900
    maximum_output_bytes: int = 65536
    poll_seconds: int = 2
    maximum_queued_jobs: int = 100
    requests_per_client_window: int = 2
    request_window_seconds: int = 3600
    running_lease_seconds: int = 1200
    maximum_attempts: int = 2
    trusted_proxy: bool = False


@dataclass(frozen=True, slots=True)
class Config:
    database_path: Path
    host: str = "127.0.0.1"
    port: int = 8080
    page_size: int = 50
    analysis: AnalysisConfig | None = None

    @classmethod
    def from_env(cls) -> "Config":
        database_path = _existing_file("MCP_PORTAL_DATABASE", required=True)
        assert database_path is not None

        host = os.environ.get("MCP_PORTAL_HOST", "127.0.0.1").strip()
        if not host:
            raise ConfigurationError("MCP_PORTAL_HOST must not be empty")

        port = _bounded_integer("MCP_PORTAL_PORT", 8080, minimum=1, maximum=65535)
        page_size = _bounded_integer("MCP_PORTAL_PAGE_SIZE", 50, minimum=1, maximum=100)
        analysis = None
        if _enabled("MCP_PORTAL_ENABLE_ANALYSIS"):
            if host not in {"127.0.0.1", "localhost", "::1"}:
                raise ConfigurationError(
                    "analysis-enabled portal must bind to loopback and be published through a reverse proxy or tunnel"
                )
            jobs_database_path = _writable_database_path("MCP_PORTAL_JOBS_DATABASE")
            observatory_binary = _existing_file("MCP_PORTAL_OBSERVATORY_BINARY", required=True)
            rules_path = _existing_file("MCP_PORTAL_ANALYSIS_RULES", required=True)
            evidence_root = _directory_path("MCP_PORTAL_EVIDENCE_ROOT")
            writer_lock_path = _writable_path(
                "MCP_PORTAL_OBSERVATORY_WRITER_LOCK",
                default=jobs_database_path.parent / "observatory-writer.lock",
            )
            assert observatory_binary is not None and rules_path is not None
            if not os.access(observatory_binary, os.X_OK):
                raise ConfigurationError(
                    f"MCP_PORTAL_OBSERVATORY_BINARY is not executable: {observatory_binary}"
                )
            analysis = AnalysisConfig(
                jobs_database_path=jobs_database_path,
                observatory_binary=observatory_binary,
                rules_path=rules_path,
                evidence_root=evidence_root,
                writer_lock_path=writer_lock_path,
                timeout_seconds=_bounded_integer(
                    "MCP_PORTAL_ANALYSIS_TIMEOUT_SECONDS", 900, minimum=30, maximum=7200
                ),
                maximum_output_bytes=_bounded_integer(
                    "MCP_PORTAL_MAXIMUM_OUTPUT_BYTES", 65536, minimum=4096, maximum=1048576
                ),
                poll_seconds=_bounded_integer(
                    "MCP_PORTAL_WORKER_POLL_SECONDS", 2, minimum=1, maximum=60
                ),
                maximum_queued_jobs=_bounded_integer(
                    "MCP_PORTAL_MAXIMUM_QUEUED_JOBS", 100, minimum=1, maximum=10000
                ),
                requests_per_client_window=_bounded_integer(
                    "MCP_PORTAL_REQUESTS_PER_CLIENT_WINDOW", 2, minimum=1, maximum=1000
                ),
                request_window_seconds=_bounded_integer(
                    "MCP_PORTAL_REQUEST_WINDOW_SECONDS", 3600, minimum=60, maximum=86400
                ),
                running_lease_seconds=_bounded_integer(
                    "MCP_PORTAL_RUNNING_LEASE_SECONDS", 1200, minimum=60, maximum=14400
                ),
                maximum_attempts=_bounded_integer(
                    "MCP_PORTAL_MAXIMUM_ATTEMPTS", 2, minimum=1, maximum=10
                ),
                trusted_proxy=_enabled("MCP_PORTAL_TRUST_PROXY_HEADERS"),
            )

        return cls(
            database_path=database_path,
            host=host,
            port=port,
            page_size=page_size,
            analysis=analysis,
        )


def _enabled(name: str) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in {"", "0", "false", "no"}:
        return False
    if raw in {"1", "true", "yes"}:
        return True
    raise ConfigurationError(f"{name} must be one of 0, 1, false, true, no, or yes")


def _existing_file(name: str, *, required: bool) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        if required:
            raise ConfigurationError(f"{name} must name an existing file")
        return None
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        raise ConfigurationError(f"{name} does not name a file: {path}")
    return path


def _writable_database_path(name: str) -> Path:
    raw = os.environ.get(name, "").strip()
    if not raw:
        raise ConfigurationError(f"{name} must name the portal-owned queue database")
    path = Path(raw).expanduser().resolve()
    if path.exists() and not path.is_file():
        raise ConfigurationError(f"{name} is not a regular file: {path}")
    if not path.parent.is_dir():
        raise ConfigurationError(f"parent directory does not exist for {name}: {path.parent}")
    return path


def _writable_path(name: str, *, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    path = Path(raw).expanduser().resolve() if raw else default.resolve()
    if not path.parent.is_dir():
        raise ConfigurationError(f"parent directory does not exist for {name}: {path.parent}")
    if path.exists() and not path.is_file():
        raise ConfigurationError(f"{name} is not a regular file: {path}")
    return path


def _directory_path(name: str) -> Path:
    raw = os.environ.get(name, "").strip()
    if not raw:
        raise ConfigurationError(f"{name} must name an existing evidence directory")
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise ConfigurationError(f"{name} does not name a directory: {path}")
    return path


def _bounded_integer(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw, 10)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a decimal integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value
