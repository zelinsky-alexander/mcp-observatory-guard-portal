from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from mcp_portal.public_intelligence import PublicIntelligence


class PublicIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.database = Path(self._temporary.name) / "catalog.sqlite"
        connection = sqlite3.connect(self.database)
        connection.executescript(
            """
            CREATE TABLE snapshots(
                id INTEGER PRIMARY KEY,
                started_at TEXT,
                completed_at TEXT,
                pages INTEGER,
                records_received INTEGER,
                unique_server_versions INTEGER,
                snapshot_sha256 TEXT
            );
            CREATE TABLE server_versions(
                id INTEGER PRIMARY KEY,
                server_identifier TEXT NOT NULL,
                server_version TEXT,
                registry_status TEXT,
                published_at TEXT,
                updated_at TEXT,
                canonical_sha256 TEXT
            );
            CREATE TABLE snapshot_server_versions(
                snapshot_id INTEGER NOT NULL,
                server_version_id INTEGER NOT NULL
            );
            CREATE TABLE packages(
                id INTEGER PRIMARY KEY,
                server_version_id INTEGER NOT NULL
            );
            CREATE TABLE analysis_runs(
                id INTEGER PRIMARY KEY,
                package_id INTEGER NOT NULL,
                status TEXT NOT NULL
            );

            INSERT INTO snapshots VALUES
                (1, '2026-08-01T01:00:00Z', '2026-08-01T01:10:00Z', 10, 100, 2, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'),
                (2, '2026-08-02T01:00:00Z', '2026-08-02T01:10:00Z', 11, 110, 2, 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb');
            INSERT INTO server_versions VALUES
                (10, 'alpha', '1.0.0', 'active', '2026-07-01', '2026-07-01', '11111111111111111111111111111111'),
                (11, 'removed', '1.0.0', 'active', '2026-07-01', '2026-07-01', '22222222222222222222222222222222'),
                (12, 'added', '1.0.0', 'active', '2026-08-02', '2026-08-02', '33333333333333333333333333333333');
            INSERT INTO snapshot_server_versions VALUES
                (1, 10), (1, 11),
                (2, 10), (2, 12);
            INSERT INTO packages VALUES (100, 10), (101, 11), (102, 12);
            INSERT INTO analysis_runs VALUES
                (1000, 100, 'completed'),
                (1001, 101, 'failed');
            """
        )
        connection.commit()
        connection.close()
        self.subject = PublicIntelligence(self.database)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_snapshot_history_is_newest_first_and_bounded(self) -> None:
        result = self.subject.snapshot_history(page=1, page_size=1)
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["rows"][0]["id"], 2)
        self.assertEqual(result["rows"][0]["sha256_prefix"], "bbbbbbbbbbbbbbbb")

    def test_latest_changes_reports_exact_membership_additions(self) -> None:
        result = self.subject.latest_changes(kind="added", page=1, page_size=50)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["server_identifier"], "added")

    def test_latest_changes_reports_exact_membership_removals(self) -> None:
        result = self.subject.latest_changes(kind="removed", page=1, page_size=50)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["server_identifier"], "removed")

    def test_analysis_coverage_counts_exact_package_records(self) -> None:
        result = self.subject.analysis_coverage()
        self.assertEqual(result["package_records"], 3)
        self.assertEqual(result["analyzed_package_records"], 1)
        self.assertEqual(result["failed_package_records"], 1)
        self.assertEqual(result["never_analyzed_package_records"], 1)

    def test_invalid_change_kind_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.subject.latest_changes(kind="modified", page=1, page_size=50)


if __name__ == "__main__":
    unittest.main()
