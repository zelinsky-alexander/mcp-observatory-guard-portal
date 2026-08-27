from __future__ import annotations

import sqlite3

from mcp_portal.remote_runtime_coverage_v1 import _metrics, _panel


def test_remote_metrics_and_panel():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE runtime_remote_schedule_profiles(
          profile_key TEXT PRIMARY KEY,scheduler_version TEXT,probe_profile_sha256 TEXT,
          runner_sha256 TEXT,created_at TEXT,selected_at TEXT);
        CREATE TABLE runtime_remote_schedule_current(singleton INTEGER PRIMARY KEY,profile_key TEXT,updated_at TEXT);
        CREATE TABLE runtime_remote_schedule_state(
          profile_key TEXT,remote_id INTEGER,state TEXT,reason_code TEXT,reason_message TEXT,
          attempt_count INTEGER,runtime_remote_observation_run_id INTEGER,inventory_sha256 TEXT,
          previous_compatible_run_id INTEGER,added_tools INTEGER,removed_tools INTEGER,
          modified_tools INTEGER,unchanged_tools INTEGER,discovered_at TEXT,last_attempt_at TEXT,updated_at TEXT);
        INSERT INTO runtime_remote_schedule_profiles VALUES('p','1','probe','runner','now','now');
        INSERT INTO runtime_remote_schedule_current VALUES(1,'p','now');
        INSERT INTO runtime_remote_schedule_state VALUES(
          'p',1,'completed',NULL,NULL,1,10,'sha',NULL,NULL,NULL,NULL,NULL,'now','now','now');
        INSERT INTO runtime_remote_schedule_state VALUES(
          'p',2,'blocked','authentication','HTTP 401',1,11,NULL,NULL,NULL,NULL,NULL,NULL,'now','now','now');
        """
    )
    data = _metrics(connection)
    assert data["available"] is True
    assert data["completed"] == 1
    assert data["blocked"] == 1
    assert "Declared remote runtime coverage" in _panel(data)
    connection.close()
