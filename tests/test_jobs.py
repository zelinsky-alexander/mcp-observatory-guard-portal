from pathlib import Path
import tempfile
import unittest

from fixture_catalog import create_fixture
from mcp_portal.analysis_catalog import resolve_candidate
from mcp_portal.jobs import JobStore


class JobTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
