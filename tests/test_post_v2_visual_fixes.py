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

    def test_public_header_has_compact_identity_source_and_quiet_affiliation(self) -> None:
        html = views.layout("Test", "<p>body</p>", public_readonly=True)
        header = html.split("</header>", 1)[0]

        self.assertIn(
            '<header class="site-header compact-header">',
            header,
        )
        self.assertIn('class="header-main"', header)
        self.assertIn('class="header-identity"', header)
        self.assertIn('class="header-title-row"', header)

        self.assertIn(
            '<a class="header-brand" href="/">MCP Longitudinal Assurance</a>',
            header,
        )
        self.assertIn('class="header-tagline"', header)
        self.assertIn(
            "Independent research into MCP server provenance, artifact identity, "
            "capability drift, and observed behavior over time.",
            header,
        )

        self.assertIn(
            'class="header-nav" aria-label="Primary navigation"',
            header,
        )

        self.assertIn('class="header-meta"', header)
        self.assertIn('class="catalog-source"', header)
        self.assertIn(
            'class="catalog-label">Catalog source</span>',
            header,
        )
        self.assertIn(
            "Official MCP Registry · Registry REST API",
            header,
        )

        self.assertIn('class="header-affiliation"', header)
        self.assertIn(
            "Not affiliated with or endorsed by the Model Context Protocol project",
            header,
        )

        self.assertNotIn("assurance-header-card", header)
        self.assertNotIn("assurance-title-marker", header)
        self.assertNotIn("assurance-source-section", header)
        self.assertNotIn("assurance-source-label", header)
        self.assertNotIn("assurance-source-text", header)

        self.assertNotIn(
            "Not affiliated with or endorsed by the Model Context Protocol project, "
            "the Official MCP Registry",
            header,
        )
        self.assertNotIn("Independent security research project", header)
        self.assertNotIn("provenance-notice", html)
        self.assertNotIn("independence-notice", html)

    def test_public_header_keeps_primary_navigation_inside_main_header_row(self) -> None:
        html = views.layout("Test", "<p>body</p>", public_readonly=True)
        header = html.split("</header>", 1)[0]

        main_start = header.index('<div class="header-main">')
        nav_start = header.index(
            '<nav class="header-nav" aria-label="Primary navigation">'
        )
        meta_start = header.index('<div class="header-meta">')

        self.assertLess(main_start, nav_start)
        self.assertLess(nav_start, meta_start)

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
                    INSERT INTO static_analysis_schedule_current
                    VALUES(1, 'profile-v2');
                    INSERT INTO analysis_v2_coverage_summary
                    VALUES('profile-v2', '2026-08-18T10:00:00Z');
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
                    INSERT INTO server_versions
                    VALUES(2, 'io.example/history-server', '1.0.0');
                    INSERT INTO packages
                    VALUES(20, 2, '@example/history-server', '1.0.0', 'npm', 'stdio');
                    INSERT INTO static_analysis_schedule_state
                    VALUES(
                        'profile-v2', 20, 'completed', NULL, NULL, 1, 200,
                        'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                        '2026-08-18T10:00:00Z'
                    );
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
            self.assertEqual(
                result["rows"][0]["package_identifier"],
                "@example/history-server",
            )


if __name__ == "__main__":
    unittest.main()
