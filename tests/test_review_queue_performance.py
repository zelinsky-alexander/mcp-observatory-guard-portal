from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fixture_catalog import create_fixture
from mcp_portal.catalog import Catalog


class ReviewQueuePerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.hot = Path(self.temporary.name) / "hot.sqlite"
        self.history = Path(self.temporary.name) / "history.sqlite"
        create_fixture(self.hot)
        create_fixture(self.history)

        db = sqlite3.connect(self.hot)
        try:
            db.execute("DELETE FROM analysis_findings")
            db.execute(
                """CREATE TABLE analysis_v2_coverage_summary(
                       profile_key TEXT PRIMARY KEY,
                       unreviewed_high_or_critical_findings INTEGER NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            db.execute(
                "INSERT INTO analysis_v2_coverage_summary VALUES('fixture',123,'2026-08-16T00:00:00Z')"
            )
            db.commit()
        finally:
            db.close()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_total_comes_from_hot_summary_while_rows_come_from_history(self) -> None:
        with patch.dict(
            os.environ,
            {"MCP_PORTAL_HISTORY_DATABASE": str(self.history)},
            clear=False,
        ):
            result = Catalog(self.hot).unreviewed_high_or_critical_findings(
                page=1, page_size=50
            )

        self.assertEqual(result["total"], 123)
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["title"], "Process execution API")

    def test_page_size_is_bounded(self) -> None:
        with patch.dict(
            os.environ,
            {"MCP_PORTAL_HISTORY_DATABASE": str(self.history)},
            clear=False,
        ):
            with self.assertRaises(ValueError):
                Catalog(self.hot).unreviewed_high_or_critical_findings(
                    page=1, page_size=101
                )


if __name__ == "__main__":
    unittest.main()
