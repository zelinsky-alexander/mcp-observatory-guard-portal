"""Compatibility adapter for callers using the pre-scheduler coverage shape."""

from __future__ import annotations

from typing import Any


def apply_coverage_view_compat() -> None:
    from . import public_ui

    layered_page = public_ui.coverage_page

    def coverage_page(
        data: dict[str, Any], *, public_readonly: bool = False
    ) -> str:
        if "eligible_package_records" in data:
            return layered_page(data, public_readonly=public_readonly)

        normalized = dict(data)
        total = int(normalized.get("package_records", 0))
        normalized.update(
            {
                "eligible_package_records": total,
                "unsupported_or_unresolvable_package_records": 0,
                "unique_artifacts_analyzed": 0,
                "runtime_discovery": {
                    "eligible": 0,
                    "completed": 0,
                    "available": False,
                },
                "human_review": {
                    "total": 0,
                    "reviewed": 0,
                    "available": False,
                },
                "controlled_behavioral": {
                    "eligible": 0,
                    "completed": 0,
                    "available": False,
                },
            }
        )
        html = layered_page(normalized, public_readonly=public_readonly)
        html = html.replace("Failed attempts", "Failed at least once", 1)
        html = html.replace(
            "Current profile; no compatible completion",
            "May overlap completed records",
            1,
        )
        html = html.replace(
            "scheduler states are mutually exclusive for the selected analyzer and ruleset profile. Completion records observed properties of an exact artifact and is not a safety certification.",
            "completed and failed counts are not mutually exclusive. A package record may have both a completed run and a failed run. Analysis completion is not a safety certification.",
            1,
        )
        return html

    public_ui.coverage_page = coverage_page
