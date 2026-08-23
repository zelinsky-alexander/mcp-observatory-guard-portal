"""Public presentation for prerequisite-aware runtime discovery outcomes."""

from __future__ import annotations

from html import escape
import sqlite3
from typing import Any


def apply_runtime_outcomes_v2() -> None:
    """Augment the runtime-coverage layer with blocked/inconclusive semantics."""
    from . import runtime_coverage_v1 as runtime

    if getattr(runtime._runtime_metrics, "_runtime_outcomes_v2", False):
        return

    original_metrics = runtime._runtime_metrics
    original_panel = runtime._runtime_coverage_panel

    def metrics(
        connection: sqlite3.Connection,
        fallback: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = original_metrics(connection, fallback=fallback)
        result.setdefault("blocked", 0)
        result.setdefault("inconclusive", 0)
        if not result.get("scheduled"):
            return result

        tables = runtime._tables(connection)
        if not {
            "runtime_discovery_schedule_current",
            "runtime_discovery_schedule_state",
        }.issubset(tables):
            return result
        current = connection.execute(
            "SELECT profile_key FROM runtime_discovery_schedule_current WHERE singleton=1"
        ).fetchone()
        if current is None:
            return result
        row = connection.execute(
            """SELECT
                   SUM(state='blocked') AS blocked,
                   SUM(state='inconclusive') AS inconclusive
               FROM runtime_discovery_schedule_state
               WHERE profile_key=?""",
            (current["profile_key"],),
        ).fetchone()
        blocked = int(row["blocked"] or 0)
        inconclusive = int(row["inconclusive"] or 0)
        result["blocked"] = blocked
        result["inconclusive"] = inconclusive
        # runtime_coverage_v1 predates these states, so its eligible aggregate
        # intentionally needs extending rather than replacing.
        result["eligible"] = int(result.get("eligible", 0)) + blocked + inconclusive
        return result

    setattr(metrics, "_runtime_outcomes_v2", True)
    runtime._runtime_metrics = metrics

    def panel(data: dict[str, Any]) -> str:
        html = original_panel(data)
        failed = int(data.get("failed", 0))
        blocked = int(data.get("blocked", 0))
        inconclusive = int(data.get("inconclusive", 0))
        old = runtime._card("Failed attempts", failed, "Current runtime profile")
        new = (
            runtime._card(
                "Failed",
                failed,
                "Observed protocol/runtime failure after meaningful startup",
            )
            + runtime._card(
                "Blocked",
                blocked,
                "Declared or diagnosed prerequisite unavailable",
            )
            + runtime._card(
                "Inconclusive",
                inconclusive,
                "Server could not be meaningfully exercised",
            )
        )
        html = html.replace(old, new, 1)
        boundary = (
            "<strong>Boundary:</strong> runtime discovery sends <code>initialize</code>, "
            "<code>notifications/initialized</code>, and <code>tools/list</code> only. "
            "Tool-definition drift is observed interface change, not a vulnerability or safety verdict."
        )
        replacement = (
            boundary
            + " Blocked means the zero-secret probe could identify an unavailable launch prerequisite; "
            + "inconclusive means the server did not progress far enough for a protocol verdict."
        )
        return html.replace(boundary, replacement, 1)

    setattr(panel, "_runtime_outcomes_v2", True)
    runtime._runtime_coverage_panel = panel
