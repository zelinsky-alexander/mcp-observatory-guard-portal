#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import unittest

from mcp_portal import runtime_coverage_v1 as runtime


class RuntimeOutcomesV2Tests(unittest.TestCase):
    def make_db(self) -> sqlite3.Connection:
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript(
            """
            CREATE TABLE runtime_discovery_schedule_profiles(
              profile_key TEXT PRIMARY KEY,
              scheduler_version TEXT,guard_sha256 TEXT,runtime_image TEXT,
              probe_profile_sha256 TEXT,runner_sha256 TEXT
            );
            CREATE TABLE runtime_discovery_schedule_current(
              singleton INTEGER PRIMARY KEY,profile_key TEXT
            );
            CREATE TABLE runtime_discovery_schedule_state(
              profile_key TEXT,package_id INTEGER,state TEXT,attempt_count INTEGER,
              artifact_sha256 TEXT,previous_compatible_run_id INTEGER,
              added_tools INTEGER,removed_tools INTEGER,modified_tools INTEGER
            );
            INSERT INTO runtime_discovery_schedule_profiles VALUES(
              'p','1','g','auto-node-v1','probe','runner'
            );
            INSERT INTO runtime_discovery_schedule_current VALUES(1,'p');
            INSERT INTO runtime_discovery_schedule_state VALUES('p',1,'completed',1,'a',NULL,NULL,NULL,NULL);
            INSERT INTO runtime_discovery_schedule_state VALUES('p',2,'failed',1,NULL,NULL,NULL,NULL,NULL);
            INSERT INTO runtime_discovery_schedule_state VALUES('p',3,'blocked',0,NULL,NULL,NULL,NULL,NULL);
            INSERT INTO runtime_discovery_schedule_state VALUES('p',4,'inconclusive',1,NULL,NULL,NULL,NULL,NULL);
            INSERT INTO runtime_discovery_schedule_state VALUES('p',5,'eligible',0,NULL,NULL,NULL,NULL,NULL);
            """
        )
        return db

    def test_metrics_keep_outcome_classes_separate(self) -> None:
        db = self.make_db()
        data = runtime._runtime_metrics(db)
        self.assertEqual(data["eligible"], 5)
        self.assertEqual(data["completed"], 1)
        self.assertEqual(data["failed"], 1)
        self.assertEqual(data["blocked"], 1)
        self.assertEqual(data["inconclusive"], 1)
        self.assertEqual(data["never_attempted"], 1)
        db.close()

    def test_panel_explains_blocked_and_inconclusive(self) -> None:
        db = self.make_db()
        html = runtime._runtime_coverage_panel(runtime._runtime_metrics(db))
        self.assertIn("Blocked", html)
        self.assertIn("Inconclusive", html)
        self.assertIn("unavailable launch prerequisite", html)
        self.assertIn("did not progress far enough for a protocol verdict", html)
        db.close()


if __name__ == "__main__":
    unittest.main()
