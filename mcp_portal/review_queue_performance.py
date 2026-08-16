"""Fast Storage-v2 review-queue reads.

The compact hot catalog owns aggregate coverage counts while the history catalog
owns finding detail.  Keep the public queue paginated without rescanning the
multi-gigabyte history database just to compute its total.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def apply_review_queue_performance() -> None:
    """Install the Storage-v2 review queue reader exactly once."""
    from .catalog import Catalog
    from .storage_v2_read_model import _coverage_row

    original = Catalog.unreviewed_high_or_critical_findings
    if getattr(original, "_review_queue_performance", False):
        return

    def unreviewed_high_or_critical_findings(
        self: Any, *, page: int, page_size: int
    ) -> dict[str, Any]:
        if page < 1:
            page = 1
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")

        history_path = getattr(self, "_storage_v2_history_path", None)
        if history_path is None:
            return original(self, page=page, page_size=page_size)

        # The total is already materialized in the hot Storage-v2 coverage
        # summary.  Never COUNT the multi-million-row history findings table for
        # a public page request.
        with self._connect() as hot:
            coverage = _coverage_row(hot)
        if coverage is None:
            return original(self, page=page, page_size=page_size)
        total = int(coverage["unreviewed_high_or_critical_findings"] or 0)

        detail = Catalog(Path(history_path))
        detail._storage_v2_history_path = None
        offset = (page - 1) * page_size

        # The queue-support index added by Observatory is ordered by severity,
        # analysis_run_id DESC, finding id DESC.  Selecting the bounded page of
        # finding ids first prevents the joins from expanding the candidate set.
        # For this queue, analysis_run_id is the deterministic recency key; the
        # run timestamp is still returned for display.
        with detail._connect() as history:
            rows = [
                dict(row)
                for row in history.execute(
                    """
                    WITH page_findings AS (
                        SELECT id
                        FROM analysis_findings
                        WHERE disposition='unreviewed'
                          AND severity IN ('high','critical')
                        ORDER BY severity COLLATE BINARY ASC,
                                 analysis_run_id DESC,
                                 id DESC
                        LIMIT ? OFFSET ?
                    )
                    SELECT af.id, af.analysis_run_id, af.rule_id, af.category,
                           af.severity, af.confidence, af.disposition,
                           af.subject_path, af.line_number, af.symbol,
                           af.title, af.explanation,
                           ar.started_at,
                           sv.server_identifier, sv.server_version,
                           p.identifier AS package_identifier
                    FROM page_findings q
                    JOIN analysis_findings af ON af.id=q.id
                    JOIN analysis_runs ar ON ar.id=af.analysis_run_id
                    JOIN server_versions sv ON sv.id=ar.server_version_id
                    JOIN packages p ON p.id=ar.package_id
                    ORDER BY af.severity COLLATE BINARY ASC,
                             af.analysis_run_id DESC,
                             af.id DESC
                    """,
                    (page_size, offset),
                ).fetchall()
            ]

        return {
            "page": page,
            "page_size": page_size,
            "total": total,
            "rows": rows,
        }

    setattr(
        unreviewed_high_or_critical_findings,
        "_review_queue_performance",
        True,
    )
    Catalog.unreviewed_high_or_critical_findings = (
        unreviewed_high_or_critical_findings
    )
