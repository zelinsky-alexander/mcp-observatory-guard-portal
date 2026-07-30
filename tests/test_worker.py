from pathlib import Path
import tempfile
import unittest

from fixture_catalog import create_fixture
from mcp_portal.analysis_catalog import (
    resolve_candidate,
    resolve_review_candidate,
)
from mcp_portal.config import AnalysisConfig, Config, ReviewConfig
from mcp_portal.jobs import JobStore
from mcp_portal.worker import (
    build_review_argv,
    process_next,
    process_next_review,
)


class WorkerTests(unittest.TestCase):
    def test_worker_executes_fixed_argument_vector(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = root / "catalog.sqlite"
            create_fixture(catalog)

            binary = root / "fake-observatory"
            binary.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "print(json.dumps({'status':'completed','analysis_run_id':321,"
                "'artifact_sha256':'a'*64,'reused_existing':False}))\n",
                encoding="utf-8",
            )
            binary.chmod(0o755)
            rules = root / "rules.json"
            rules.write_text("{}", encoding="utf-8")
            evidence = root / "evidence"
            evidence.mkdir()

            analysis = AnalysisConfig(
                root / "jobs.sqlite",
                binary,
                rules,
                evidence,
                timeout_seconds=30,
                maximum_output_bytes=8192,
                poll_seconds=1,
            )
            config = Config(
                catalog,
                host="127.0.0.1",
                port=0,
                page_size=20,
                analysis=analysis,
            )
            store = JobStore(analysis.jobs_database_path)
            store.enqueue(resolve_candidate(catalog, 1, 10))

            self.assertTrue(process_next(config, store))
            job = store.recent(1)[0]
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["analysis_run_id"], 321)

    def test_review_worker_executes_fixed_argument_vector(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = root / "catalog.sqlite"
            create_fixture(catalog)
            binary = root / "fake-observatory"
            binary.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "print(json.dumps({'status':'completed','review_id':44,"
                "'finding_id':1,'disposition':'expected'}))\n",
                encoding="utf-8",
            )
            binary.chmod(0o755)
            review = ReviewConfig(
                root / "jobs.sqlite",
                binary,
                "worker-test-reviewer",
                timeout_seconds=30,
                maximum_output_bytes=8192,
                poll_seconds=1,
            )
            config = Config(
                catalog,
                host="127.0.0.1",
                port=0,
                page_size=20,
                review=review,
            )
            store = JobStore(review.jobs_database_path)
            candidate = resolve_review_candidate(catalog, 1, "unreviewed")
            job, _created = store.enqueue_review(
                candidate,
                disposition="expected",
                reviewer=review.reviewer,
            )
            self.assertEqual(
                build_review_argv(catalog, review, job),
                [
                    str(binary),
                    "review",
                    "finding",
                    "--database",
                    str(catalog),
                    "--finding-id",
                    "1",
                    "--expected-disposition",
                    "unreviewed",
                    "--disposition",
                    "expected",
                    "--reviewer",
                    "worker-test-reviewer",
                    "--format",
                    "json",
                ],
            )
            self.assertTrue(process_next_review(config, store))
            completed = store.get_review(1)
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["review_id"], 44)


if __name__ == "__main__":
    unittest.main()
