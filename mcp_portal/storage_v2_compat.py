"""Compatibility routing between legacy and Storage v2 portal read models."""

from __future__ import annotations

from typing import Any


def apply_storage_v2_compat() -> None:
    """Use Storage v2 readers only when the compact v2 schema is present.

    Storage v2 is applied after the legacy coverage and performance adapters.
    Keep those legacy adapters authoritative for non-v2 catalogs so existing
    fixtures and older catalogs retain their established semantics.
    """
    from .catalog import Catalog
    from .coverage_query_v2 import analysis_coverage as legacy_analysis_coverage
    from .performance_hotfix import dashboard as legacy_dashboard
    from .public_intelligence import PublicIntelligence
    from .storage_v2_read_model import (
        V2_TABLES,
        analysis_coverage as storage_v2_analysis_coverage,
        dashboard as storage_v2_dashboard,
    )

    def has_storage_v2(subject: Any) -> bool:
        with subject._connect() as connection:
            tables = {
                str(row["name"])
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table'"
                )
            }
        return V2_TABLES.issubset(tables)

    def dashboard(self: Any, *, recent_limit: int = 12) -> dict[str, Any]:
        if has_storage_v2(self):
            return storage_v2_dashboard(self, recent_limit=recent_limit)
        return legacy_dashboard(self, recent_limit=recent_limit)

    def analysis_coverage(self: Any) -> dict[str, Any]:
        if has_storage_v2(self):
            return storage_v2_analysis_coverage(self)
        return legacy_analysis_coverage(self)

    Catalog.dashboard = dashboard
    PublicIntelligence.analysis_coverage = analysis_coverage
