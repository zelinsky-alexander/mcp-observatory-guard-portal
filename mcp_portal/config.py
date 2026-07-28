"""Portal configuration loaded from a small, explicit environment surface."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when portal configuration is invalid."""


@dataclass(frozen=True, slots=True)
class Config:
    database_path: Path
    host: str = "127.0.0.1"
    port: int = 8080
    page_size: int = 50

    @classmethod
    def from_env(cls) -> "Config":
        raw_database = os.environ.get("MCP_PORTAL_DATABASE", "").strip()
        if not raw_database:
            raise ConfigurationError("MCP_PORTAL_DATABASE must name an Observatory SQLite database")

        database_path = Path(raw_database).expanduser().resolve()
        if not database_path.is_file():
            raise ConfigurationError(f"database does not exist or is not a file: {database_path}")

        host = os.environ.get("MCP_PORTAL_HOST", "127.0.0.1").strip()
        if not host:
            raise ConfigurationError("MCP_PORTAL_HOST must not be empty")

        port = _bounded_integer("MCP_PORTAL_PORT", 8080, minimum=1, maximum=65535)
        page_size = _bounded_integer("MCP_PORTAL_PAGE_SIZE", 50, minimum=1, maximum=100)
        return cls(database_path=database_path, host=host, port=port, page_size=page_size)


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
