from pathlib import Path
import tempfile
import unittest

from fixture_catalog import create_fixture
from mcp_portal.analysis_catalog import resolve_candidate
from mcp_portal.config import AnalysisConfig, Config
from mcp_portal.jobs import JobStore
from mcp_portal.worker import process_next


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


if __name__ == "__main__":
    unittest.main()
