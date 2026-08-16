from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fixture_catalog import create_fixture
from mcp_portal.catalog import Catalog
from mcp_portal.post_v2_bugfixes import _analysis_runs, _search_servers


class HotListPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.hot = root / "hot.sqlite"
        self.history = root / "history.sqlite"
        create_fixture(self.hot)
        create_fixture(self.history)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_longitudinal_server_list_uses_hot_catalog(self) -> None:
        db = sqlite3.connect(self.hot)
        try:
            db.execute(
                "INSERT INTO server_versions VALUES(3,?,?,?,?,?,?,?,?)",
                (
                    "io.example/hot-only",
                    "3.0.0",
                    "Present only in newer hot catalog",
                    "active",
                    "2026-08-16T10:00:00Z",
                    "2026-08-16T10:00:00Z",
                    "9" * 64,
                    '{"name":"hot-only"}',
                ),
            )
            db.execute(
                "INSERT INTO packages VALUES(30,3,0,'npm','@example/hot-only','3.0.0','stdio')"
            )
            db.commit()
        finally:
            db.close()

        with patch.dict(
            os.environ,
            {"MCP_PORTAL_HISTORY_DATABASE": str(self.history)},
            clear=False,
        ):
            result = _search_servers(
                Catalog(self.hot),
                scope="all",
                snapshot_id=0,
                query="",
                ecosystem="",
                page=1,
                page_size=50,
            )

        self.assertEqual(result["total"], 3)
        self.assertIn(
            "io.example/hot-only",
            {row["server_identifier"] for row in result["rows"]},
        )

    def test_analysis_list_uses_newer_hot_runs(self) -> None:
        db = sqlite3.connect(self.hot)
        try:
            db.execute(
                """INSERT INTO analysis_runs VALUES(
                    101,1,10,'npm_package_static_v1','completed','mcp-observatory','0.1.0','artifact-static-v1',
                    '2026-08-16T10:00:00Z','2026-08-16T10:01:00Z',?,'sha512-test',1,
                    'node:22-bookworm-slim',?,'none',1,'65532:65532','{}',NULL,NULL)""",
                ("8" * 64, "sha256:fixture"),
            )
            db.commit()
        finally:
            db.close()

        with patch.dict(
            os.environ,
            {"MCP_PORTAL_HISTORY_DATABASE": str(self.history)},
            clear=False,
        ):
            result = _analysis_runs(
                Catalog(self.hot), status="completed", page=1, page_size=50
            )

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["rows"][0]["id"], 101)


if __name__ == "__main__":
    unittest.main()
