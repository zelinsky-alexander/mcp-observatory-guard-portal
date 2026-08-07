from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from fixture_catalog import create_fixture
from mcp_portal.catalog import Catalog
from mcp_portal.public_intelligence import PublicIntelligence


V2_SCHEMA = """
CREATE TABLE storage_v2_info(
  singleton INTEGER PRIMARY KEY,
  schema_version INTEGER NOT NULL,
  installed_at TEXT,
  updated_at TEXT
);
CREATE TABLE analysis_v2_rule_definitions(
  id INTEGER PRIMARY KEY,
  ruleset_version TEXT NOT NULL,
  rule_id TEXT NOT NULL,
  category TEXT NOT NULL,
  severity TEXT NOT NULL,
  title TEXT NOT NULL,
  explanation TEXT NOT NULL,
  UNIQUE(ruleset_version,rule_id)
);
CREATE TABLE analysis_v2_run_summaries(
  analysis_run_id INTEGER PRIMARY KEY,
  artifact_sha256 TEXT,
  analyzer_name TEXT NOT NULL,
  analyzer_version TEXT NOT NULL,
  ruleset_version TEXT NOT NULL,
  status TEXT NOT NULL,
  file_count INTEGER NOT NULL DEFAULT 0,
  total_file_bytes INTEGER NOT NULL DEFAULT 0,
  dependency_count INTEGER NOT NULL DEFAULT 0,
  finding_count INTEGER NOT NULL DEFAULT 0,
  info_count INTEGER NOT NULL DEFAULT 0,
  low_count INTEGER NOT NULL DEFAULT 0,
  medium_count INTEGER NOT NULL DEFAULT 0,
  high_count INTEGER NOT NULL DEFAULT 0,
  critical_count INTEGER NOT NULL DEFAULT 0,
  executable_count INTEGER NOT NULL DEFAULT 0,
  native_binary_count INTEGER NOT NULL DEFAULT 0,
  generated_count INTEGER NOT NULL DEFAULT 0,
  minified_count INTEGER NOT NULL DEFAULT 0,
  unreviewed_count INTEGER NOT NULL DEFAULT 0,
  unreviewed_high_count INTEGER NOT NULL DEFAULT 0,
  unreviewed_critical_count INTEGER NOT NULL DEFAULT 0,
  suspicious_count INTEGER NOT NULL DEFAULT 0,
  confirmed_risk_count INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);
CREATE TABLE analysis_v2_rule_summaries(
  analysis_run_id INTEGER NOT NULL,
  rule_definition_id INTEGER NOT NULL,
  occurrence_count INTEGER NOT NULL,
  PRIMARY KEY(analysis_run_id,rule_definition_id)
);
CREATE TABLE analysis_v2_coverage_summary(
  profile_key TEXT PRIMARY KEY,
  eligible_package_records INTEGER NOT NULL,
  completed_package_records INTEGER NOT NULL,
  failed_package_records INTEGER NOT NULL,
  unsupported_package_records INTEGER NOT NULL,
  unresolvable_package_records INTEGER NOT NULL,
  never_attempted_package_records INTEGER NOT NULL,
  running_package_records INTEGER NOT NULL,
  unique_artifacts_analyzed INTEGER NOT NULL,
  info_findings INTEGER NOT NULL,
  low_findings INTEGER NOT NULL,
  medium_findings INTEGER NOT NULL,
  high_findings INTEGER NOT NULL,
  critical_findings INTEGER NOT NULL,
  unreviewed_findings INTEGER NOT NULL,
  unreviewed_high_or_critical_findings INTEGER NOT NULL,
  suspicious_findings INTEGER NOT NULL,
  confirmed_risk_findings INTEGER NOT NULL,
  updated_at TEXT
);
CREATE TABLE analysis_v2_evidence_manifests(
  analysis_run_id INTEGER PRIMARY KEY,
  storage_kind TEXT NOT NULL,
  locator TEXT NOT NULL,
  bundle_sha256 TEXT,
  inventory_sha256 TEXT,
  retained_artifact INTEGER NOT NULL,
  updated_at TEXT
);
CREATE TABLE static_analysis_schedule_profiles(
  profile_key TEXT PRIMARY KEY,
  analysis_type TEXT,
  analyzer_name TEXT,
  analyzer_version TEXT,
  ruleset_version TEXT,
  rules_sha256 TEXT
);
CREATE TABLE static_analysis_schedule_current(
  singleton INTEGER PRIMARY KEY,
  profile_key TEXT NOT NULL
);
CREATE TABLE storage_v2_hot_catalog_info(
  singleton INTEGER PRIMARY KEY,
  built_at TEXT,
  detail_policy TEXT,
  source_database_bytes INTEGER,
  source_analysis_runs INTEGER,
  source_analysis_findings INTEGER,
  source_analysis_files INTEGER
);
"""


class StorageV2ReadModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "catalog.sqlite"
        create_fixture(self.database)
        db = sqlite3.connect(self.database)
        db.executescript(V2_SCHEMA)
        db.execute("INSERT INTO storage_v2_info VALUES(1,2,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        db.execute(
            """INSERT INTO analysis_v2_run_summaries(
                 analysis_run_id,artifact_sha256,analyzer_name,analyzer_version,
                 ruleset_version,status,file_count,total_file_bytes,dependency_count,
                 finding_count,info_count,low_count,medium_count,high_count,critical_count,
                 executable_count,native_binary_count,generated_count,minified_count,
                 unreviewed_count,unreviewed_high_count,unreviewed_critical_count,
                 suspicious_count,confirmed_risk_count,updated_at)
               VALUES(100,?,'mcp-observatory','0.1.0','artifact-static-v1','completed',
                      123,456789,12,250,10,20,150,70,0,1,0,0,3,200,70,0,0,0,CURRENT_TIMESTAMP)""",
            ("d" * 64,),
        )
        db.execute(
            """INSERT INTO analysis_v2_coverage_summary VALUES(
                 'profile',4,3,1,0,0,0,0,3,
                 10,20,150,70,0,200,70,0,0,CURRENT_TIMESTAMP)"""
        )
        db.execute(
            """INSERT INTO static_analysis_schedule_profiles VALUES(
                 'profile','npm_package_static_v1','mcp-observatory-static','1.1.0',
                 'artifact-static-v1',?)""",
            ("1" * 64,),
        )
        db.execute("INSERT INTO static_analysis_schedule_current VALUES(1,'profile')")
        db.execute(
            "INSERT INTO storage_v2_hot_catalog_info VALUES(1,CURRENT_TIMESTAMP,'summaries-only',1,1,1,1)"
        )
        # Simulate the compact hot catalog: the raw detail tables are empty.
        db.execute("DELETE FROM analysis_findings")
        db.execute("DELETE FROM analysis_files")
        db.execute("DELETE FROM analysis_evidence")
        db.commit()
        db.close()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_dashboard_uses_v2_counts_when_raw_findings_are_absent(self) -> None:
        result = Catalog(self.database).dashboard()
        self.assertTrue(result["schema"]["storage_v2_available"])
        self.assertTrue(result["schema"]["storage_v2_hot_catalog"])
        self.assertEqual(result["analysis"]["completed"], 3)
        self.assertEqual(result["analysis"]["failed"], 1)
        self.assertEqual(result["analysis"]["unreviewed_high_or_critical"], 70)
        self.assertEqual(result["analysis"]["recent"][0]["high_count"], 70)
        self.assertEqual(result["analysis"]["recent"][0]["medium_count"], 150)

    def test_coverage_uses_materialized_v2_summary(self) -> None:
        result = PublicIntelligence(self.database).analysis_coverage()
        self.assertEqual(result["eligible_package_records"], 4)
        self.assertEqual(result["analyzed_package_records"], 3)
        self.assertEqual(result["failed_package_records"], 1)
        self.assertEqual(result["unique_artifacts_analyzed"], 3)
        self.assertEqual(result["human_review"]["total"], 250)
        self.assertEqual(result["human_review"]["reviewed"], 50)
        self.assertTrue(result["storage_v2"]["available"])
        self.assertTrue(result["storage_v2"]["hot_catalog"])

    def test_latest_snapshot_totals_remain_bounded(self) -> None:
        result = Catalog(self.database).dashboard()
        self.assertEqual(result["totals"]["servers"], 2)
        self.assertEqual(result["totals"]["immutable_versions"], 2)


if __name__ == "__main__":
    unittest.main()
