from __future__ import annotations

from pathlib import Path
import http.client
import re
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from mcp_portal.app import create_server
from mcp_portal.config import AnalysisConfig, Config
from fixture_catalog import create_fixture


class HttpTests(unittest.TestCase):
    def _start(self, analysis: bool) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        database = root / "catalog.sqlite"
        create_fixture(database)
        config = Config(database_path=database, host="127.0.0.1", port=0, page_size=20)
        if analysis:
            binary = root / "fake-observatory"
            binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            binary.chmod(0o755)
            rules = root / "rules.json"
            rules.write_text("{}", encoding="utf-8")
            evidence = root / "evidence"
            evidence.mkdir()
            config = Config(
                database_path=database,
                host="127.0.0.1",
                port=0,
                page_size=20,
                analysis=AnalysisConfig(root / "jobs.sqlite", binary, rules, evidence),
            )
        self.server = create_server(config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host_header = f"127.0.0.1:{self.server.server_port}"
        self.base_url = "http://" + self.host_header

    def tearDown(self) -> None:
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            self.temporary.cleanup()

    def test_dashboard_and_server_detail(self) -> None:
        self._start(False)
        with urlopen(self.base_url + "/", timeout=2) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Recently imported registry records", body)
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

        with urlopen(self.base_url + "/servers/io.example%2Ffilesystem", timeout=2) as response:
            body = response.read().decode("utf-8")
            self.assertIn("@example/filesystem", body)
            self.assertIn("Static analysis history", body)
            self.assertIn("on-demand analysis is disabled", body)

    def test_search_escapes_database_text(self) -> None:
        self._start(False)
        with urlopen(self.base_url + "/servers?q=filesystem", timeout=2) as response:
            body = response.read().decode("utf-8")
            self.assertIn("io.example/filesystem", body)

    def test_disabled_post_is_rejected(self) -> None:
        self._start(False)
        request = Request(self.base_url + "/", method="POST", data=b"")
        with self.assertRaises(HTTPError) as captured:
            urlopen(request, timeout=2)
        self.assertEqual(captured.exception.code, 405)
        self.assertEqual(captured.exception.headers["Allow"], "GET, HEAD")

    def test_server_form_queues_job_with_csrf(self) -> None:
        self._start(True)
        with urlopen(self.base_url + "/servers/io.example%2Ffilesystem", timeout=2) as response:
            body = response.read().decode("utf-8")
        match = re.search(r'name="csrf_token" value="([0-9a-f]+)"', body)
        self.assertIsNotNone(match)
        payload = urlencode(
            {
                "server_version_id": "1",
                "package_id": "10",
                "csrf_token": match.group(1),
            }
        )
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=2
        )
        connection.request(
            "POST",
            "/analysis-requests",
            body=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": self.base_url,
                "Host": self.host_header,
            },
        )
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 303)
        self.assertEqual(response.getheader("Location"), "/jobs/1")
        self.assertEqual(self.server.jobs.get(1)["status"], "queued")


if __name__ == "__main__":
    unittest.main()
