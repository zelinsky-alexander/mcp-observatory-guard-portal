from __future__ import annotations

from pathlib import Path
import sqlite3
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

    def test_server_search_filters_by_ecosystem(self) -> None:
        result = self.catalog.search_servers(
            "", page=1, page_size=20, ecosystem="pypi"
        )
        self.assertEqual(result["ecosystem"], "pypi")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["server_identifier"], "io.example/filesystem")
        self.assertEqual(result["rows"][0]["package_identifier"], "example-filesystem")

    def test_ecosystem_summary_counts_records_identifiers_and_versions(self) -> None:
        result = self.catalog.ecosystem_summary()
        self.assertEqual(
            result,
            [
                {
                    "ecosystem": "npm",
                    "package_records": 3,
                    "unique_packages": 2,
                    "server_versions": 2,
                },
                {
                    "ecosystem": "pypi",
                    "package_records": 1,
                    "unique_packages": 1,
                    "server_versions": 1,
                },
            ],
        )

    def test_unreviewed_high_or_critical_findings(self) -> None:
        result = self.catalog.unreviewed_high_or_critical_findings(
            page=1, page_size=20
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["id"], 1)
        self.assertEqual(result["rows"][0]["analysis_run_id"], 100)
        self.assertEqual(result["rows"][0]["server_identifier"], "io.example/filesystem")
        self.assertEqual(result["rows"][0]["package_identifier"], "@example/filesystem")

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
        self.assertEqual(detail["findings"][0]["public_excerpt"], "")
        self.assertEqual(detail["findings"][0]["public_excerpt_eligible"], 0)
        self.assertEqual(detail["findings"][0]["subject_sha256"], "f" * 64)
        self.assertEqual(detail["evidence_files"][0]["relative_path"], "analysis-summary.json")

    def test_analysis_detail_uses_only_explicitly_eligible_public_excerpt(self) -> None:
        connection = sqlite3.connect(self.database)
        connection.execute(
            """UPDATE analysis_findings
                  SET evidence='private evidence', public_excerpt='approved context',
                      public_excerpt_eligible=0,
                      public_excerpt_reason='awaiting legal review'
                WHERE id=1"""
        )
        connection.commit()
        connection.close()
        finding = self.catalog.analysis_detail(100)["findings"][0]
        self.assertEqual(finding["public_excerpt"], "")
        self.assertEqual(finding["public_excerpt_eligible"], 0)
        self.assertNotIn("private evidence", finding.values())

        connection = sqlite3.connect(self.database)
        connection.execute(
            "UPDATE analysis_findings SET public_excerpt_eligible=1 WHERE id=1"
        )
        connection.commit()
        connection.close()
        finding = self.catalog.analysis_detail(100)["findings"][0]
        self.assertEqual(finding["public_excerpt"], "approved context")
        self.assertEqual(finding["public_excerpt_eligible"], 1)

    def test_analysis_detail_fails_closed_without_complete_excerpt_contract(self) -> None:
        connection = sqlite3.connect(self.database)
        connection.execute(
            """UPDATE analysis_findings
                  SET public_excerpt='must remain private',
                      public_excerpt_eligible=1
                WHERE id=1"""
        )
        connection.execute(
            "ALTER TABLE analysis_findings DROP COLUMN public_excerpt_reason"
        )
        connection.commit()
        connection.close()

        finding = self.catalog.analysis_detail(100)["findings"][0]
        self.assertEqual(finding["public_excerpt"], "")
        self.assertEqual(finding["public_excerpt_eligible"], 0)
        self.assertNotIn("must remain private", finding.values())

    def test_analysis_detail_includes_review_history(self) -> None:
        connection = sqlite3.connect(self.database)
        connection.execute(
            """
            INSERT INTO analysis_finding_reviews
            VALUES(1, 1, 'unreviewed', 'expected', 'catalog-test',
                   '2026-07-30T12:00:00Z')
            """
        )
        connection.commit()
        connection.close()
        detail = self.catalog.analysis_detail(100)
        self.assertEqual(
            detail["findings"][0]["reviews"][0]["disposition"], "expected"
        )

    def test_runtime_observation_is_optional_and_bounded(self) -> None:
        self.assertFalse(self.catalog.schema_status()["runtime_available"])
        self.assertIsNone(self.catalog.runtime_observation(1))
        connection = sqlite3.connect(self.database)
        connection.executescript(
            """CREATE TABLE runtime_observation_runs(
            id INTEGER PRIMARY KEY,server_version_id INTEGER,package_id INTEGER,
            status TEXT,artifact_sha256 TEXT,launch_profile_sha256 TEXT,
            sandbox_image TEXT,guard_version TEXT,inventory_sha256 TEXT,
            inventory_json TEXT,started_at TEXT,completed_at TEXT,error_stage TEXT,
            error_message TEXT);
            CREATE TABLE runtime_observation_tools(
            run_id INTEGER,name TEXT,definition_json TEXT,definition_sha256 TEXT);
            """
        )
        connection.execute(
            "INSERT INTO runtime_observation_runs VALUES(1,1,10,'completed',?,?,?,?,?,'{}','start','done',NULL,NULL)",
            ("a" * 64, "b" * 64, "node:test", "sha256:" + "c" * 64, "d" * 64),
        )
        connection.execute(
            "INSERT INTO runtime_observation_tools VALUES(1,?,?,?)",
            ("<tool>", "x" * 5000, "e" * 64),
        )
        connection.commit()
        connection.close()
        observation = self.catalog.runtime_observation(1)
        self.assertEqual(observation["package_identifier"], "@example/filesystem")
        self.assertEqual(observation["tools"][0]["name"], "<tool>")
        self.assertEqual(len(observation["tools"][0]["definition_json"]), 4096)
        self.assertEqual(observation["tools"][0]["definition_truncated"], 1)


if __name__ == "__main__":
    unittest.main()
