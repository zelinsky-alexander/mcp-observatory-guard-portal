from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
import unittest

from mcp_portal.runtime_coverage_v1 import (
    _runtime_drift_detail,
    _runtime_drift_list,
    _runtime_metrics,
)


class RuntimeCoverageV1Tests(unittest.TestCase):
    def make_database(self) -> tuple[tempfile.TemporaryDirectory[str], sqlite3.Connection]:
        temporary = tempfile.TemporaryDirectory(prefix="portal-runtime-coverage-")
        path = Path(temporary.name) / "catalog.sqlite"
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
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
              registry_type TEXT,
              identifier TEXT,
              version TEXT,
              transport TEXT
            );
            CREATE TABLE runtime_observation_runs(
              id INTEGER PRIMARY KEY,
              server_version_id INTEGER NOT NULL,
              package_id INTEGER NOT NULL,
              status TEXT NOT NULL,
              artifact_sha256 TEXT,
              launch_profile_sha256 TEXT,
              sandbox_image TEXT NOT NULL,
              guard_version TEXT NOT NULL,
              inventory_sha256 TEXT,
              inventory_json TEXT,
              started_at TEXT DEFAULT CURRENT_TIMESTAMP,
              completed_at TEXT,
              error_stage TEXT,
              error_message TEXT
            );
            CREATE TABLE runtime_observation_tools(
              run_id INTEGER NOT NULL,
              name TEXT NOT NULL,
              definition_json TEXT NOT NULL,
              definition_sha256 TEXT NOT NULL,
              PRIMARY KEY(run_id,name)
            );
            CREATE TABLE runtime_discovery_schedule_profiles(
              profile_key TEXT PRIMARY KEY,
              scheduler_version TEXT NOT NULL,
              guard_sha256 TEXT NOT NULL,
              runtime_image TEXT NOT NULL,
              probe_profile_sha256 TEXT NOT NULL,
              runner_sha256 TEXT NOT NULL,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              selected_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE runtime_discovery_schedule_current(
              singleton INTEGER PRIMARY KEY,
              profile_key TEXT NOT NULL,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE runtime_discovery_schedule_state(
              profile_key TEXT NOT NULL,
              package_id INTEGER NOT NULL,
              state TEXT NOT NULL,
              reason_code TEXT,
              reason_message TEXT,
              attempt_count INTEGER NOT NULL DEFAULT 0,
              runtime_observation_run_id INTEGER,
              artifact_sha256 TEXT,
              launch_profile_sha256 TEXT,
              previous_compatible_run_id INTEGER,
              added_tools INTEGER,
              removed_tools INTEGER,
              modified_tools INTEGER,
              unchanged_tools INTEGER,
              discovered_at TEXT DEFAULT CURRENT_TIMESTAMP,
              last_attempt_at TEXT,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(profile_key,package_id)
            );

            INSERT INTO server_versions VALUES(1,'io.example/server','1.0.0');
            INSERT INTO server_versions VALUES(2,'io.example/server','2.0.0');
            INSERT INTO server_versions VALUES(3,'io.example/pending','1.0.0');
            INSERT INTO packages VALUES(10,1,'npm','example-mcp','1.0.0','stdio');
            INSERT INTO packages VALUES(11,2,'npm','example-mcp','2.0.0','stdio');
            INSERT INTO packages VALUES(12,3,'npm','pending-mcp','1.0.0','stdio');
            INSERT INTO packages VALUES(13,3,'pypi','other','1.0.0','stdio');

            INSERT INTO runtime_discovery_schedule_profiles(
              profile_key,scheduler_version,guard_sha256,runtime_image,
              probe_profile_sha256,runner_sha256
            ) VALUES(
              'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
              '1.0.0',
              'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
              'node:test',
              'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
              'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd'
            );
            INSERT INTO runtime_discovery_schedule_current VALUES(
              1,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',CURRENT_TIMESTAMP
            );

            INSERT INTO runtime_observation_runs(
              id,server_version_id,package_id,status,artifact_sha256,
              launch_profile_sha256,sandbox_image,guard_version,completed_at
            ) VALUES(
              101,1,10,'completed',
              '1111111111111111111111111111111111111111111111111111111111111111',
              '3333333333333333333333333333333333333333333333333333333333333333',
              'node:test','sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
              '2026-08-16T10:00:00Z'
            );
            INSERT INTO runtime_observation_runs(
              id,server_version_id,package_id,status,artifact_sha256,
              launch_profile_sha256,sandbox_image,guard_version,completed_at
            ) VALUES(
              102,2,11,'completed',
              '2222222222222222222222222222222222222222222222222222222222222222',
              '4444444444444444444444444444444444444444444444444444444444444444',
              'node:test','sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
              '2026-08-17T10:00:00Z'
            );

            INSERT INTO runtime_observation_tools VALUES(
              101,'read','{"name":"read","inputSchema":{"type":"object"}}',
              'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1'
            );
            INSERT INTO runtime_observation_tools VALUES(
              101,'old','{"name":"old"}',
              'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2'
            );
            INSERT INTO runtime_observation_tools VALUES(
              102,'read','{"name":"read","inputSchema":{"required":["path"],"type":"object"}}',
              'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb1'
            );
            INSERT INTO runtime_observation_tools VALUES(
              102,'new','{"name":"new"}',
              'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2'
            );

            INSERT INTO runtime_discovery_schedule_state(
              profile_key,package_id,state,attempt_count,runtime_observation_run_id,
              artifact_sha256,launch_profile_sha256
            ) VALUES(
              'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
              10,'completed',1,101,
              '1111111111111111111111111111111111111111111111111111111111111111',
              '3333333333333333333333333333333333333333333333333333333333333333'
            );
            INSERT INTO runtime_discovery_schedule_state(
              profile_key,package_id,state,attempt_count,runtime_observation_run_id,
              artifact_sha256,launch_profile_sha256,previous_compatible_run_id,
              added_tools,removed_tools,modified_tools,unchanged_tools
            ) VALUES(
              'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
              11,'completed',1,102,
              '2222222222222222222222222222222222222222222222222222222222222222',
              '4444444444444444444444444444444444444444444444444444444444444444',
              101,1,1,1,0
            );
            INSERT INTO runtime_discovery_schedule_state(
              profile_key,package_id,state,attempt_count
            ) VALUES(
              'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
              12,'eligible',0
            );
            INSERT INTO runtime_discovery_schedule_state(
              profile_key,package_id,state,reason_code,attempt_count
            ) VALUES(
              'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
              13,'unsupported','unsupported_ecosystem',0
            );
            """
        )
        db.commit()
        return temporary, db

    def test_runtime_metrics_use_current_scheduler_profile(self) -> None:
        temporary, db = self.make_database()
        try:
            metrics = _runtime_metrics(db)
            self.assertTrue(metrics["available"])
            self.assertTrue(metrics["scheduled"])
            self.assertEqual(metrics["eligible"], 3)
            self.assertEqual(metrics["completed"], 2)
            self.assertEqual(metrics["unsupported_or_unresolvable"], 1)
            self.assertEqual(metrics["never_attempted"], 1)
            self.assertEqual(metrics["comparable"], 1)
            self.assertEqual(metrics["drifted"], 1)
        finally:
            db.close()
            temporary.cleanup()

    def test_runtime_drift_list_and_detail_are_bounded(self) -> None:
        temporary, db = self.make_database()
        try:
            listing = _runtime_drift_list(db, page=1, page_size=50)
            self.assertEqual(listing["total"], 1)
            self.assertEqual(listing["rows"][0]["newer_run_id"], 102)
            self.assertEqual(listing["rows"][0]["modified_tools"], 1)

            detail = _runtime_drift_detail(db, 102)
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertEqual([item["name"] for item in detail["added"]], ["new"])
            self.assertEqual([item["name"] for item in detail["removed"]], ["old"])
            self.assertEqual([item["name"] for item in detail["modified"]], ["read"])
            self.assertIsNone(_runtime_drift_detail(db, 999))
        finally:
            db.close()
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
