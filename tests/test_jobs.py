from pathlib import Path
import sqlite3
import tempfile
import unittest

from fixture_catalog import create_fixture
from mcp_portal.analysis_catalog import (
    AnalysisSelectionError,
    resolve_candidate,
    resolve_review_candidate,
    resolve_runtime_candidate,
)
from mcp_portal.jobs import JobStore


class JobTests(unittest.TestCase):
    def test_schema_two_migrates_to_runtime_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "jobs.sqlite"
            connection = sqlite3.connect(database)
            connection.executescript(
                "CREATE TABLE schema_info(singleton INTEGER PRIMARY KEY,"
                "schema_version INTEGER NOT NULL);"
                "INSERT INTO schema_info VALUES(1,2);"
            )
            connection.close()
            JobStore(database)
            connection = sqlite3.connect(database)
            self.assertEqual(
                connection.execute(
                    "SELECT schema_version FROM schema_info WHERE singleton=1"
                ).fetchone()[0],
                3,
            )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_schema WHERE type='table' "
                    "AND name='runtime_discovery_jobs'"
                ).fetchone()
            )
            connection.close()

    def test_enqueue_deduplicate_claim_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = root / "catalog.sqlite"
            create_fixture(catalog)
            store = JobStore(root / "jobs.sqlite")
            candidate = resolve_candidate(catalog, 1, 10)

            first, created = store.enqueue(candidate)
            self.assertTrue(created)
            second, created = store.enqueue(candidate)
            self.assertFalse(created)
            self.assertEqual(first["id"], second["id"])

            claimed = store.claim_next()
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed["status"], "running")
            store.complete(
                claimed["id"],
                analysis_run_id=101,
                artifact_sha256="f" * 64,
                reused_existing=False,
                return_code=0,
                stdout_excerpt="{}",
                stderr_excerpt="",
                output_truncated=False,
            )
            self.assertEqual(store.get(claimed["id"])["status"], "completed")

    def test_enqueue_claim_and_complete_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = root / "catalog.sqlite"
            create_fixture(catalog)
            store = JobStore(root / "jobs.sqlite")
            candidate = resolve_review_candidate(catalog, 1, "unreviewed")
            first, created = store.enqueue_review(
                candidate, disposition="expected", reviewer="job-test"
            )
            self.assertTrue(created)
            second, created = store.enqueue_review(
                candidate, disposition="expected", reviewer="job-test"
            )
            self.assertFalse(created)
            self.assertEqual(first["id"], second["id"])
            claimed = store.claim_next_review()
            self.assertEqual(claimed["status"], "running")
            store.complete_review(
                claimed["id"],
                review_id=7,
                return_code=0,
                stdout_excerpt="{}",
                stderr_excerpt="",
                output_truncated=False,
            )
            self.assertEqual(store.get_review(claimed["id"])["status"], "completed")

    def test_enqueue_claim_and_complete_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = root / "catalog.sqlite"
            create_fixture(catalog)
            store = JobStore(root / "jobs.sqlite")
            candidate = resolve_runtime_candidate(catalog, 1, 10)
            first, created = store.enqueue_runtime(candidate)
            self.assertTrue(created)
            second, created = store.enqueue_runtime(candidate)
            self.assertFalse(created)
            self.assertEqual(first["id"], second["id"])
            claimed = store.claim_next_runtime()
            self.assertEqual(claimed["status"], "running")
            store.complete_runtime(
                claimed["id"],
                runtime_observation_run_id=5,
                artifact_sha256="a" * 64,
                launch_profile_sha256="b" * 64,
                inventory_sha256="c" * 64,
                guard_sha256="d" * 64,
                tool_count=2,
                return_code=0,
                stdout_excerpt="{}",
                stderr_excerpt="",
                output_truncated=0,
            )
            self.assertEqual(store.get_runtime(claimed["id"])["status"], "completed")

    def test_runtime_candidate_requires_stdio(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            catalog = Path(temp) / "catalog.sqlite"
            create_fixture(catalog)
            connection = sqlite3.connect(catalog)
            connection.execute("UPDATE packages SET transport='sse' WHERE id=10")
            connection.commit()
            connection.close()
            with self.assertRaises(AnalysisSelectionError):
                resolve_runtime_candidate(catalog, 1, 10)


if __name__ == "__main__":
    unittest.main()
