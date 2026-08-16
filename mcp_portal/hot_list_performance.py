"""Keep bounded public list routes on the compact Storage v2 hot catalog.

Longitudinal server metadata and analysis-run summaries are already retained in
hot storage.  History is reserved for detail/evidence reads after a user opens
an individual record.  This avoids paying the 2+ GB history database cost for
simple paginated navigation.
"""

from __future__ import annotations

from typing import Any, Callable


class _HotCatalogView:
    """Delegate to a catalog while suppressing history redirection."""

    _storage_v2_history_path = None

    def __init__(self, catalog: Any) -> None:
        self._catalog = catalog

    def __getattr__(self, name: str) -> Any:
        return getattr(self._catalog, name)


def apply_hot_list_performance() -> None:
    """Route public list/browse queries to hot Storage v2 state exactly once."""
    from . import post_v2_bugfixes as bugfixes

    original_search: Callable[..., dict[str, Any]] = bugfixes._search_servers
    if getattr(original_search, "_hot_list_performance", False):
        return

    original_records = bugfixes._immutable_records
    original_analyses = bugfixes._analysis_runs

    def search_servers(catalog: Any, **kwargs: Any) -> dict[str, Any]:
        result = original_search(_HotCatalogView(catalog), **kwargs)
        return result

    def immutable_records(catalog: Any, **kwargs: Any) -> dict[str, Any]:
        return original_records(_HotCatalogView(catalog), **kwargs)

    def analysis_runs(catalog: Any, **kwargs: Any) -> dict[str, Any]:
        return original_analyses(_HotCatalogView(catalog), **kwargs)

    setattr(search_servers, "_hot_list_performance", True)
    bugfixes._search_servers = search_servers
    bugfixes._immutable_records = immutable_records
    bugfixes._analysis_runs = analysis_runs
