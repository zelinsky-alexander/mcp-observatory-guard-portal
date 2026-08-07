from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from fixture_catalog import create_fixture
from mcp_portal.catalog import Catalog


class LargeCatalogReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "catalog.sqlite"
        create_fixture(self.database)

        # Add longitudinal history that is deliberately not a member of the
        # latest published snapshot. Common public list/dashboard queries must
        # not grow with this historical-only data.
        connection = sqlite3.connect(self.database)
        connection.execute(
            """INSERT INTO server_versions VALUES(
                   3, 'io.example/retired', '0.9.0', 'Historical only',
                   'inactive', '2026-06-01T00:00:00Z', '2026-06-02T00:00:00Z',
                   ?, '{"name":"retired"}')""",
            ("9" * 64,),
        )
        connection.execute(
            """INSERT INTO packages VALUES(
                   30, 3, 0, 'npm', '@example/retired', '0.9.0', 'stdio')"""
        )
        connection.commit()
        connection.close()
        self.catalog = Catalog(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_dashboard_totals_are_latest_snapshot_totals(self) -> None:
        result = self.catalog.dashboard()
        self.assertEqual(result["totals"]["servers"], 2)
        self.assertEqual(result["totals"]["immutable_versions"], 2)

    def test_server_list_excludes_historical_only_versions(self) -> None:
        result = self.catalog.search_servers("", page=1, page_size=20)
        self.assertEqual(result["total"], 2)
        self.assertNotIn(
            "io.example/retired",
            {row["server_identifier"] for row in result["rows"]},
        )

    def test_search_does_not_match_historical_only_package(self) -> None:
        result = self.catalog.search_servers(
            "@example/retired", page=1, page_size=20
        )
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["rows"], [])


if __name__ == "__main__":
    unittest.main()
