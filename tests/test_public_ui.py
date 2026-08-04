from __future__ import annotations

import unittest

from mcp_portal import views
from mcp_portal.public_ui import (
    changes_page,
    coverage_page,
    install_public_intelligence_ui,
    snapshots_page,
    status_page,
)


class PublicIntelligenceViewTests(unittest.TestCase):
    def test_installation_is_idempotent_and_adds_navigation(self) -> None:
        install_public_intelligence_ui()
        first = views.layout
        install_public_intelligence_ui()
        self.assertIs(views.layout, first)
        html = views.layout("Test", "<p>body</p>", public_readonly=True)
        self.assertIn('href="/changes"', html)
        self.assertIn('href="/snapshots"', html)
        self.assertIn('href="/coverage"', html)
        self.assertIn("Open MCP Longitudinal Assurance Project", html)
        self.assertNotIn(">MCP Observatory<", html)

    def test_status_page_renders_published_snapshot_without_worker_claims(self) -> None:
        html = status_page(
            {
                "latest_snapshot": {
                    "id": 42,
                    "started_at": "2026-08-04T06:00:00Z",
                    "completed_at": "2026-08-04T06:02:05Z",
                    "pages": 11,
                    "records_received": 120,
                    "unique_server_versions": 118,
                    "sha256_prefix": "abcdef0123456789",
                }
            },
            public_readonly=True,
        )
        self.assertIn("Latest successfully published snapshot", html)
        self.assertIn("2m 5s", html)
        self.assertIn("abcdef0123456789", html)
        self.assertIn("does not claim that a refresh worker", html)
        self.assertNotIn("worker is healthy", html)

    def test_snapshot_history_escapes_values_and_paginates(self) -> None:
        html = snapshots_page(
            {
                "page": 1,
                "page_size": 1,
                "total": 2,
                "rows": [
                    {
                        "id": 2,
                        "started_at": "<start>",
                        "completed_at": "<completed>",
                        "pages": 3,
                        "records_received": 4,
                        "unique_server_versions": 5,
                        "sha256_prefix": "<digest>",
                    }
                ],
            },
            public_readonly=True,
        )
        self.assertIn("&lt;completed&gt;", html)
        self.assertIn("&lt;digest&gt;", html)
        self.assertNotIn("<completed>", html)
        self.assertIn("page=2", html)

    def test_changes_page_links_to_server_and_describes_membership_boundary(self) -> None:
        html = changes_page(
            {
                "kind": "added",
                "latest_snapshot_id": 2,
                "previous_snapshot_id": 1,
                "page": 1,
                "page_size": 50,
                "total": 1,
                "rows": [
                    {
                        "server_identifier": "scope/server name",
                        "server_version": "1.0.0",
                        "registry_status": "active",
                        "published_at": "2026-08-04",
                        "updated_at": "2026-08-04",
                        "sha256_prefix": "1234567890abcdef",
                    }
                ],
            },
            public_readonly=True,
        )
        self.assertIn("/servers/scope%2Fserver%20name", html)
        self.assertIn("exact immutable server-version records", html.lower())
        self.assertIn("do not by themselves prove", html)

    def test_coverage_does_not_present_overlapping_counts_as_a_partition(self) -> None:
        html = coverage_page(
            {
                "package_records": 100,
                "analyzed_package_records": 25,
                "failed_package_records": 10,
                "never_analyzed_package_records": 70,
            },
            public_readonly=True,
        )
        self.assertIn("25.0%", html)
        self.assertIn("Failed at least once", html)
        self.assertIn("not mutually exclusive", html)
        self.assertIn("not a safety certification", html)


if __name__ == "__main__":
    unittest.main()
