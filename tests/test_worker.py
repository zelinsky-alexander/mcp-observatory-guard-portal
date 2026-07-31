from pathlib import Path
import tempfile
import unittest

from fixture_catalog import create_fixture
from mcp_portal.analysis_catalog import (
    resolve_candidate,
    resolve_review_candidate,
    resolve_runtime_candidate,
)
from mcp_portal.config import (
    AnalysisConfig,
    Config,
    ReviewConfig,
    RuntimeDiscoveryConfig,
)
from mcp_portal.jobs import JobStore
from mcp_portal.worker import (
    build_review_argv,
    build_runtime_argv,
    process_next,
    process_next_review,
    process_next_runtime,
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

    def test_runtime_worker_executes_fixed_argument_vector(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = root / "catalog.sqlite"
            create_fixture(catalog)
            runner = root / "runtime-runner.py"
            runner.write_text(
                "import json, sqlite3, sys\n"
                "args=sys.argv\n"
                "db=sqlite3.connect(args[args.index('--database')+1])\n"
                "db.executescript(\"CREATE TABLE runtime_observation_runs("
                "id INTEGER PRIMARY KEY,server_version_id INTEGER,package_id INTEGER,"
                "status TEXT,artifact_sha256 TEXT,launch_profile_sha256 TEXT,"
                "sandbox_image TEXT,guard_version TEXT,inventory_sha256 TEXT,"
                "inventory_json TEXT,started_at TEXT,completed_at TEXT,error_stage TEXT,"
                "error_message TEXT);CREATE TABLE runtime_observation_tools("
                "run_id INTEGER,name TEXT,definition_json TEXT,definition_sha256 TEXT);\")\n"
                "db.execute(\"INSERT INTO runtime_observation_runs VALUES("
                "7,1,10,'completed',?,?,?,?,?,'{}','now','now',NULL,NULL)\","
                "('a'*64,'b'*64,'node:test','sha256:'+'d'*64,'c'*64))\n"
                "db.execute(\"INSERT INTO runtime_observation_tools VALUES(7,'read','{}',?)\",('e'*64,))\n"
                "db.commit()\n"
                "print(json.dumps({'status':'completed','runtime_observation_run_id':7,"
                "'artifact_sha256':'a'*64,'launch_profile_sha256':'b'*64,"
                "'guard_sha256':'d'*64,'tool_count':1}))\n",
                encoding="utf-8",
            )
            guard = root / "guard"
            guard.write_text("guard", encoding="utf-8")
            guard.chmod(0o755)
            evidence = root / "evidence"
            evidence.mkdir()
            runtime = RuntimeDiscoveryConfig(
                root / "jobs.sqlite",
                runner,
                guard,
                evidence,
                root / "writer.lock",
                runtime_image="node:test",
                timeout_seconds=30,
                maximum_output_bytes=8192,
                poll_seconds=1,
            )
            config = Config(catalog, port=0, runtime_discovery=runtime)
            candidate = resolve_runtime_candidate(catalog, 1, 10)
            argv = build_runtime_argv(catalog, runtime, candidate)
            self.assertEqual(argv[1:4], [str(runner), "observe", "--database"])
            self.assertNotIn("--force", argv)
            store = JobStore(runtime.jobs_database_path)
            store.enqueue_runtime(candidate)
            self.assertTrue(process_next_runtime(config, store))
            completed = store.get_runtime(1)
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(completed["runtime_observation_run_id"], 7)
            self.assertEqual(completed["tool_count"], 1)


if __name__ == "__main__":
    unittest.main()
