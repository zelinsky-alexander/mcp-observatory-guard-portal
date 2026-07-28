from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from mcp_portal.catalog import Catalog
from fixture_catalog import create_fixture


class CatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "catalog.sqlite"
        create_fixture(self.database)
        self.catalog = Catalog(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_dashboard_reports_latest_snapshot_and_analysis(self) -> None:
        result = self.catalog.dashboard()
        self.assertEqual(result["totals"]["servers"], 2)
        self.assertEqual(result["latest_snapshot"]["records_received"], 2)
        self.assertEqual(result["analysis"]["completed"], 1)
        self.assertEqual(result["analysis"]["unreviewed_high_or_critical"], 1)

    def test_server_search_matches_package_identifier(self) -> None:
        result = self.catalog.search_servers("@example/search", page=1, page_size=20)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["server_identifier"], "io.example/search")

    def test_server_detail_includes_packages_and_analysis(self) -> None:
        detail = self.catalog.server_detail("io.example/filesystem")
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail["versions"][0]["packages"][0]["identifier"], "@example/filesystem")
        self.assertEqual(detail["versions"][0]["analyses"][0]["id"], 100)

    def test_analysis_detail_includes_findings_and_evidence(self) -> None:
        detail = self.catalog.analysis_detail(100)
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail["findings"][0]["severity"], "high")
        self.assertEqual(detail["evidence_files"][0]["relative_path"], "analysis-summary.json")


if __name__ == "__main__":
    unittest.main()
