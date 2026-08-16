from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from mcp_portal import post_v2_bugfixes, views
from mcp_portal.catalog import Catalog


class PostV2VisualFixTests(unittest.TestCase):
    def test_public_html_expands_mcpla_name(self) -> None:
        html = views.layout("Test", "<p>MCPLA preserves history.</p>", public_readonly=True)
        self.assertIn("MCP Longitudinal Assurance Project preserves history", html)
        self.assertNotIn(">MCPLA ", html)

    def test_public_header_has_clear_title_context_and_no_bullet_separator(self) -> None:
        html = views.layout("Test", "<p>body</p>", public_readonly=True)
        header = html.split("</header>", 1)[0]
        self.assertIn(
            '<a class="brand assurance-brand" href="/">MCP Longitudinal Assurance</a>',
            header,
        )
        self.assertIn('class="tagline assurance-subtitle"', header)
        self.assertIn('class="assurance-affiliation"', header)
        self.assertIn(
            "Not affiliated with or endorsed by the Model Context Protocol project",
            header,
        )
        self.assertIn('class="assurance-source"', header)
        self.assertIn("<strong>Catalog source:</strong>", header)
        self.assertNotIn("Independent security research project", header)
        self.assertNotIn("header-disclaimer", header)
        self.assertNotIn("provenance-notice", html)
        self.assertNotIn("independence-notice", html)

    def test_server_scope_tabs_are_separated_and_selected(self) -> None:
        result = {
            "scope": "all",
            "snapshot_id": None,
            "query": "",
            "ecosystem": "",
            "page": 1,
            "page_size": 50,
            "total": 0,
            "rows": [],
        }
        html = post_v2_bugfixes.servers_scope_page(result, public_readonly=True)
        self.assertIn('class="scope-tab" href="/servers?scope=current"', html)
        self.assertIn(
            'class="scope-tab active" aria-current="page" href="/servers?scope=all"',
            html,
        )

    def test_coverage_drilldown_uses_hot_profile_and_history_state_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hot = root / "hot.sqlite"
            history = root / "history.sqlite"

            db = sqlite3.connect(hot)
            try:
                db.executescript(
                    """
                    CREATE TABLE static_analysis_schedule_current(
                        singleton INTEGER PRIMARY KEY,
                        profile_key TEXT NOT NULL
                    );
                    CREATE TABLE analysis_v2_coverage_summary(
                        profile_key TEXT PRIMARY KEY,
                        updated_at TEXT NOT NULL
                    );
                    INSERT INTO analysis_v2_coverage_summary
                    VALUES('profile-v2', '2026-08-16T10:00:00Z');
                    """
                )
                db.commit()
            finally:
                db.close()

            db = sqlite3.connect(history)
            try:
                db.executescript(
                    """
                    CREATE TABLE server_versions(
                        id INTEGER PRIMARY KEY,
                        server_identifier TEXT NOT NULL,
                        server_version TEXT NOT NULL
                    );
                    CREATE TABLE packages(
                        id INTEGER PRIMARY KEY,
                        server_version_id INTEGER NOT NULL,
                        identifier TEXT NOT NULL,
                        version TEXT,
                        registry_type TEXT NOT NULL,
                        transport TEXT NOT NULL
                    );
                    CREATE TABLE static_analysis_schedule_state(
                        profile_key TEXT NOT NULL,
                        package_id INTEGER NOT NULL,
                        state TEXT NOT NULL,
                        reason_code TEXT,
                        reason_message TEXT,
                        attempt_count INTEGER NOT NULL,
                        analysis_run_id INTEGER,
                        artifact_sha256 TEXT,
                        updated_at TEXT NOT NULL
                    );
                    INSERT INTO server_versions VALUES(1, 'io.example/server', '1.0.0');
                    INSERT INTO packages VALUES(10, 1, '@example/server', '1.0.0', 'npm', 'stdio');
                    INSERT INTO static_analysis_schedule_state
                    VALUES('profile-v2', 10, 'completed', NULL, NULL, 1, 100,
                           'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                           '2026-08-16T10:00:00Z');
                    """
                )
                db.commit()
            finally:
                db.close()

            catalog = Catalog(hot)
            catalog._storage_v2_history_path = history
            result = post_v2_bugfixes._coverage_records(
                catalog, state="completed", page=1, page_size=50
            )
            self.assertEqual(result["total"], 1)
            self.assertEqual(len(result["rows"]), 1)
            self.assertEqual(result["rows"][0]["package_identifier"], "@example/server")


if __name__ == "__main__":
    unittest.main()
