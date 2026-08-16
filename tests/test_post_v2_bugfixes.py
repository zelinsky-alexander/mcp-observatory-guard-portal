from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fixture_catalog import create_fixture
from mcp_portal import views
from mcp_portal.catalog import Catalog
from mcp_portal.post_v2_bugfixes import (
    _analysis_runs,
    _coverage_records,
    _immutable_records,
    _search_servers,
    _snapshot_detail,
    coverage_page,
    servers_scope_page,
    snapshots_page,
)
from mcp_portal.public_intelligence import PublicIntelligence


SCHEDULER_SCHEMA = """
CREATE TABLE static_analysis_schedule_profiles(
  profile_key TEXT PRIMARY KEY,
  analysis_type TEXT,
  analyzer_name TEXT,
  analyzer_version TEXT,
  ruleset_version TEXT,
  rules_sha256 TEXT
);
CREATE TABLE static_analysis_schedule_current(
  singleton INTEGER PRIMARY KEY,
  profile_key TEXT NOT NULL
);
CREATE TABLE static_analysis_schedule_state(
  profile_key TEXT NOT NULL,
  package_id INTEGER NOT NULL,
  state TEXT NOT NULL,
  reason_code TEXT,
  reason_message TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  analysis_run_id INTEGER,
  artifact_sha256 TEXT,
  reused_existing INTEGER NOT NULL DEFAULT 0,
  discovered_at TEXT,
  last_attempt_at TEXT,
  updated_at TEXT,
  PRIMARY KEY(profile_key, package_id)
);
"""


class PostV2BugfixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "catalog.sqlite"
        create_fixture(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _add_historical_only_server(self) -> None:
        db = sqlite3.connect(self.database)
        try:
            db.execute(
                "INSERT INTO server_versions VALUES(3, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "io.example/historical",
                    "0.9.0",
                    "Historical-only fixture",
                    "inactive",
                    "2026-06-01T00:00:00Z",
                    "2026-06-15T00:00:00Z",
                    "9" * 64,
                    '{"name":"historical"}',
                ),
            )
            db.execute(
                "INSERT INTO packages VALUES(30, 3, 0, 'npm', '@example/historical', '0.9.0', 'stdio')"
            )
            db.commit()
        finally:
            db.close()

    def test_current_and_all_server_scopes_are_distinct_and_counted(self) -> None:
        self._add_historical_only_server()
        catalog = Catalog(self.database)

        current = _search_servers(
            catalog,
            scope="current",
            snapshot_id=0,
            query="",
            ecosystem="",
            page=1,
            page_size=50,
        )
        all_observed = _search_servers(
            catalog,
            scope="all",
            snapshot_id=0,
            query="",
            ecosystem="",
            page=1,
            page_size=50,
        )

        self.assertEqual(current["total"], 2)
        self.assertEqual(all_observed["total"], 3)
        self.assertNotIn(
            "io.example/historical",
            {row["server_identifier"] for row in current["rows"]},
        )
        self.assertIn(
            "io.example/historical",
            {row["server_identifier"] for row in all_observed["rows"]},
        )

    def test_snapshot_membership_count_matches_snapshot_server_browse(self) -> None:
        catalog = Catalog(self.database)
        detail = _snapshot_detail(catalog, 1)
        self.assertIsNotNone(detail)
        assert detail is not None

        scoped = _search_servers(
            catalog,
            scope="snapshot",
            snapshot_id=1,
            query="",
            ecosystem="",
            page=1,
            page_size=50,
        )
        self.assertEqual(detail["server_count"], scoped["total"])
        self.assertEqual(scoped["total"], 2)

    def test_dashboard_inventory_counts_match_drill_down_totals(self) -> None:
        self._add_historical_only_server()
        catalog = Catalog(self.database)
        dashboard = catalog.dashboard()

        all_servers = _search_servers(
            catalog,
            scope="all",
            snapshot_id=0,
            query="",
            ecosystem="",
            page=1,
            page_size=50,
        )
        records = _immutable_records(catalog, page=1, page_size=50)
        analyses = _analysis_runs(
            catalog, status="completed", page=1, page_size=50
        )

        self.assertEqual(dashboard["totals"]["servers"], all_servers["total"])
        self.assertEqual(
            dashboard["totals"]["immutable_versions"], records["total"]
        )
        self.assertEqual(dashboard["analysis"]["completed"], analyses["total"])

    def test_static_coverage_aggregate_and_drill_down_predicates_match(self) -> None:
        db = sqlite3.connect(self.database)
        try:
            db.executescript(SCHEDULER_SCHEMA)
            db.execute(
                "INSERT INTO static_analysis_schedule_profiles VALUES('profile','npm_package_static_v1','mcp-observatory-static','1.1.0','artifact-static-v1',?)",
                ("1" * 64,),
            )
            db.execute(
                "INSERT INTO static_analysis_schedule_current VALUES(1,'profile')"
            )
            rows = [
                ("profile", 10, "completed", None, None, 1, 100, "d" * 64),
                ("profile", 11, "failed", "analysis_failed", "fixture", 2, None, None),
                ("profile", 20, "unsupported", "unsupported_registry", "fixture", 0, None, None),
                ("profile", 21, "eligible", None, None, 0, None, None),
            ]
            db.executemany(
                """INSERT INTO static_analysis_schedule_state(
                     profile_key,package_id,state,reason_code,reason_message,
                     attempt_count,analysis_run_id,artifact_sha256,reused_existing,
                     discovered_at,last_attempt_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,0,CURRENT_TIMESTAMP,NULL,CURRENT_TIMESTAMP)""",
                rows,
            )
            db.commit()
        finally:
            db.close()

        coverage = PublicIntelligence(self.database).analysis_coverage()
        catalog = Catalog(self.database)

        expected = {
            "eligible": coverage["eligible_package_records"],
            "completed": coverage["analyzed_package_records"],
            "failed": coverage["failed_package_records"],
            "unsupported": coverage["unsupported_or_unresolvable_package_records"],
            "never": coverage["never_analyzed_package_records"],
        }
        for state, aggregate in expected.items():
            with self.subTest(state=state):
                drilldown = _coverage_records(
                    catalog, state=state, page=1, page_size=50
                )
                self.assertEqual(aggregate, drilldown["total"])

    def test_public_coverage_hides_review_and_labels_future_layers(self) -> None:
        html = coverage_page(
            {
                "package_records": 100,
                "eligible_package_records": 80,
                "analyzed_package_records": 40,
                "failed_package_records": 4,
                "unsupported_or_unresolvable_package_records": 20,
                "never_analyzed_package_records": 36,
                "unique_artifacts_analyzed": 39,
                "runtime_discovery": {
                    "available": False,
                    "eligible": 50,
                    "completed": 0,
                },
                "human_review": {
                    "available": True,
                    "total": 2000,
                    "reviewed": 2,
                },
            },
            public_readonly=True,
        )
        self.assertIn("50.0%", html)
        self.assertIn("Planned next", html)
        self.assertIn("Planned later", html)
        self.assertNotIn("Human-review coverage", html)
        self.assertIn('href="/coverage/records?state=completed"', html)

    def test_filtered_server_view_keeps_clear_filter_affordance(self) -> None:
        result = _search_servers(
            Catalog(self.database),
            scope="current",
            snapshot_id=0,
            query="",
            ecosystem="pypi",
            page=1,
            page_size=50,
        )
        html = servers_scope_page(result, public_readonly=True)
        self.assertIn("Clear ecosystem filter", html)
        self.assertIn('href="/servers?scope=current"', html)
        self.assertIn('name="ecosystem" value="pypi"', html)

    def test_snapshot_rows_and_dashboard_cards_have_valid_drill_down_links(self) -> None:
        snapshot_html = snapshots_page(
            {
                "page": 1,
                "page_size": 50,
                "total": 1,
                "rows": [
                    {
                        "id": 1,
                        "started_at": "2026-07-28T03:59:00Z",
                        "completed_at": "2026-07-28T04:00:00Z",
                        "pages": 1,
                        "records_received": 2,
                        "unique_server_versions": 2,
                        "sha256_prefix": "a" * 16,
                    }
                ],
            },
            public_readonly=True,
        )
        self.assertIn('href="/snapshots/1"', snapshot_html)
        self.assertIn("View snapshot →", snapshot_html)

        dashboard_html = views.dashboard_page(
            Catalog(self.database).dashboard(), public_readonly=True
        )
        self.assertIn('href="/servers?scope=all"', dashboard_html)
        self.assertIn('href="/records"', dashboard_html)
        self.assertIn('href="/analyses?status=completed"', dashboard_html)
        self.assertNotIn('<a href="/servers?scope=all">Distinct registry identifiers</a>', dashboard_html)

    def test_storage_v2_review_queue_reads_history_detail(self) -> None:
        hot = Path(self.temporary.name) / "hot.sqlite"
        history = Path(self.temporary.name) / "history.sqlite"
        create_fixture(hot)
        create_fixture(history)
        db = sqlite3.connect(hot)
        try:
            db.execute("DELETE FROM analysis_findings")
            db.commit()
        finally:
            db.close()

        with patch.dict(
            os.environ,
            {"MCP_PORTAL_HISTORY_DATABASE": str(history)},
            clear=False,
        ):
            result = Catalog(hot).unreviewed_high_or_critical_findings(
                page=1, page_size=50
            )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["title"], "Process execution API")


if __name__ == "__main__":
    unittest.main()
