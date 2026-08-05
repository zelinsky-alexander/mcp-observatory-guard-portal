from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from mcp_portal.coverage_v2 import coverage_page
from mcp_portal.public_intelligence import PublicIntelligence


class CoverageV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.database = Path(self._temporary.name) / "catalog.sqlite"
        connection = sqlite3.connect(self.database)
        connection.executescript(
            """
            CREATE TABLE packages(
              id INTEGER PRIMARY KEY,
              server_version_id INTEGER NOT NULL,
              registry_type TEXT NOT NULL,
              identifier TEXT,
              version TEXT,
              transport TEXT NOT NULL
            );
            CREATE TABLE static_analysis_schedule_profiles(
              profile_key TEXT PRIMARY KEY,
              analysis_type TEXT NOT NULL,
              analyzer_name TEXT NOT NULL,
              analyzer_version TEXT NOT NULL,
              ruleset_version TEXT NOT NULL,
              rules_sha256 TEXT NOT NULL
            );
            CREATE TABLE static_analysis_schedule_current(
              singleton INTEGER PRIMARY KEY,
              profile_key TEXT NOT NULL
            );
            CREATE TABLE static_analysis_schedule_state(
              profile_key TEXT NOT NULL,
              package_id INTEGER NOT NULL,
              state TEXT NOT NULL,
              attempt_count INTEGER NOT NULL,
              artifact_sha256 TEXT
            );
            CREATE TABLE runtime_observation_runs(
              package_id INTEGER NOT NULL,
              status TEXT NOT NULL
            );
            CREATE TABLE analysis_findings(disposition TEXT NOT NULL);

            INSERT INTO packages VALUES
              (1, 1, 'npm', 'one', '1.0.0', 'stdio'),
              (2, 2, 'pypi', 'two', '2.0.0', 'stdio'),
              (3, 3, 'docker', 'three', '3.0.0', 'stdio'),
              (4, 4, 'npm', 'four', NULL, 'stdio');
            INSERT INTO static_analysis_schedule_profiles VALUES
              ('profile', 'npm_package_static_v1', 'mcp-observatory-static',
               '1.1.0', '1.0.0', 'rules-digest');
            INSERT INTO static_analysis_schedule_current VALUES(1, 'profile');
            INSERT INTO static_analysis_schedule_state VALUES
              ('profile', 1, 'completed', 1,
               'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'),
              ('profile', 2, 'failed', 1, NULL),
              ('profile', 3, 'unsupported', 0, NULL),
              ('profile', 4, 'unresolvable', 0, NULL);
            INSERT INTO runtime_observation_runs VALUES(1, 'completed');
            INSERT INTO analysis_findings VALUES('unreviewed'), ('reviewed-benign');
            """
        )
        connection.commit()
        connection.close()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_reports_mutually_exclusive_static_scheduler_states(self) -> None:
        result = PublicIntelligence(self.database).analysis_coverage()
        self.assertEqual(result["package_records"], 4)
        self.assertEqual(result["eligible_package_records"], 2)
        self.assertEqual(result["analyzed_package_records"], 1)
        self.assertEqual(result["failed_package_records"], 1)
        self.assertEqual(
            result["unsupported_or_unresolvable_package_records"], 2
        )
        self.assertEqual(result["never_analyzed_package_records"], 0)
        self.assertEqual(result["unique_artifacts_analyzed"], 1)
        self.assertEqual(result["runtime_discovery"]["completed"], 1)
        self.assertEqual(result["human_review"]["reviewed"], 1)

    def test_page_separates_assurance_layers(self) -> None:
        result = PublicIntelligence(self.database).analysis_coverage()
        html = coverage_page(result, public_readonly=True)
        self.assertIn("Static artifact coverage", html)
        self.assertIn("Eligible package records", html)
        self.assertIn("Unsupported / unresolvable", html)
        self.assertIn("Unique artifacts analyzed", html)
        self.assertIn("Runtime discovery coverage", html)
        self.assertIn("Controlled behavioral coverage", html)
        self.assertIn("Human-review coverage", html)
        self.assertIn("50.0% static artifact coverage", html)


if __name__ == "__main__":
    unittest.main()
