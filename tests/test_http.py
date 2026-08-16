from __future__ import annotations

from pathlib import Path
import http.client
import hashlib
import re
import sqlite3
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from mcp_portal.app import create_server
from mcp_portal.config import (
    AnalysisConfig,
    Config,
    EvidenceConfig,
    ReviewConfig,
    RuntimeDiscoveryConfig,
)
from fixture_catalog import create_fixture


class HttpTests(unittest.TestCase):
    def _start(
        self,
        analysis: bool,
        *,
        evidence: bool = False,
        review: bool = False,
        runtime: bool = False,
        public_readonly: bool = False,
    ) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        database = root / "catalog.sqlite"
        self.database_path = database
        create_fixture(database)
        config = Config(database_path=database, host="127.0.0.1", port=0, page_size=20)
        if analysis or evidence or review:
            binary = root / "fake-observatory"
            binary.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "if sys.argv[1:3] == ['evidence', 'finding-source']:\n"
                " if sys.argv[sys.argv.index('--format') + 1] == 'raw':\n"
                "  sys.stdout.write('complete verified source\\n')\n"
                " else:\n"
                "  print(json.dumps({'status':'completed','finding_id':1,"
                "'analysis_run_id':100,'subject_path':'package/index.js',"
                "'line_number':2,'sha256':'f'*64,'byte_size':200000,"
                "'displayed_byte_size':19,'start_line':1,"
                "'truncated_before':False,'truncated_after':True,"
                "'starts_mid_line':False,'ends_mid_line':True,"
                "'content':'first\\n<script>\\nlast'}))\n"
                "elif sys.argv[1:3] == ['review', 'finding']:\n"
                " print(json.dumps({'status':'completed','review_id':9,"
                "'finding_id':1,'disposition':'expected'}))\n"
                "else:\n"
                " print(json.dumps({'status':'completed','analysis_run_id':321,"
                "'artifact_sha256':'a'*64,'reused_existing':False}))\n",
                encoding="utf-8",
            )
            binary.chmod(0o755)
        analysis_config = None
        evidence_config = None
        review_config = None
        runtime_config = None
        if analysis:
            rules = root / "rules.json"
            rules.write_text("{}", encoding="utf-8")
            evidence_root = root / "evidence"
            evidence_root.mkdir()
            analysis_config = AnalysisConfig(
                root / "jobs.sqlite", binary, rules, evidence_root
            )
        if evidence:
            evidence_root = root / "evidence"
            evidence_root.mkdir(exist_ok=True)
            evidence_config = EvidenceConfig(binary, evidence_root)
        if review:
            review_config = ReviewConfig(
                root / "jobs.sqlite", binary, "http-test-reviewer"
            )
        if runtime:
            runner = root / "runtime-runner.py"
            runner.write_text("print('{}')\n", encoding="utf-8")
            guard = root / "guard"
            guard.write_text("guard", encoding="utf-8")
            guard.chmod(0o755)
            evidence_root = root / "evidence"
            evidence_root.mkdir(exist_ok=True)
            runtime_config = RuntimeDiscoveryConfig(
                root / "jobs.sqlite",
                runner,
                guard,
                evidence_root,
                root / "writer.lock",
            )
        config = Config(
            database_path=database,
            host="127.0.0.1",
            port=0,
            page_size=20,
            mode="public-readonly" if public_readonly else "local",
            analysis=analysis_config,
            evidence=evidence_config,
            review=review_config,
            runtime_discovery=runtime_config,
        )
        self.server = create_server(config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host_header = f"127.0.0.1:{self.server.server_port}"
        self.base_url = "http://" + self.host_header

    def _request_status(self, method: str, path: str, body: bytes = b"") -> int:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=2
        )
        connection.request(method, path, body=body)
        response = connection.getresponse()
        response.read()
        connection.close()
        return response.status

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
            self.assertIn(
                'href="/findings/unreviewed-high-or-critical">'
                "Unreviewed high or critical findings</a>",
                body,
            )
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

        with urlopen(self.base_url + "/servers/io.example%2Ffilesystem", timeout=2) as response:
            body = response.read().decode("utf-8")
            self.assertIn("@example/filesystem", body)
            self.assertIn("Static analysis history", body)
            self.assertIn("on-demand analysis is disabled", body)

    def test_unreviewed_high_or_critical_findings_report(self) -> None:
        self._start(False)
        with urlopen(
            self.base_url + "/findings/unreviewed-high-or-critical", timeout=2
        ) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Unreviewed high or critical findings", body)
            self.assertIn("Process execution API", body)
            self.assertIn("io.example/filesystem", body)
            self.assertIn("@example/filesystem", body)
            self.assertIn(
                'href="/analyses/100#finding-1"',
                body,
            )
            self.assertIn("not a safety verdict", body)

    def test_finding_source_and_review_submission(self) -> None:
        self._start(False, evidence=True, review=True)
        with urlopen(self.base_url + "/analyses/100", timeout=2) as response:
            body = response.read().decode("utf-8")
            self.assertIn('href="/findings/1/source"', body)
            self.assertIn('action="/review-requests"', body)
        token_match = re.search(
            r'action="/review-requests".*?name="csrf_token" value="([0-9a-f]+)"',
            body,
        )
        self.assertIsNotNone(token_match)

        with urlopen(self.base_url + "/findings/1/source", timeout=2) as response:
            source_body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("package/index.js", source_body)
            self.assertIn("&lt;script&gt;", source_body)
            self.assertNotIn("<script>", source_body)
            self.assertIn("source-line-target", source_body)
            self.assertIn(
                'href="/findings/1/source/download"', source_body
            )
            self.assertIn("Download complete verified file", source_body)

        with urlopen(
            self.base_url + "/findings/1/source/download", timeout=2
        ) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"complete verified source\n")
            self.assertEqual(
                response.headers["Content-Type"], "application/octet-stream"
            )
            self.assertEqual(
                response.headers["Content-Disposition"],
                'attachment; filename="index.js"',
            )

        payload = urlencode(
            {
                "finding_id": "1",
                "expected_disposition": "unreviewed",
                "disposition": "expected",
                "csrf_token": token_match.group(1),
            }
        )
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=2
        )
        connection.request(
            "POST",
            "/review-requests",
            body=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Host": self.host_header,
                "Sec-Fetch-Site": "same-origin",
            },
        )
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 303)
        self.assertEqual(response.getheader("Location"), "/review-jobs/1")
        job = self.server.jobs.get_review(1)
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["disposition"], "expected")
        self.assertEqual(job["reviewer"], "http-test-reviewer")

    def test_search_escapes_database_text(self) -> None:
        self._start(False)
        with urlopen(self.base_url + "/servers?q=filesystem", timeout=2) as response:
            body = response.read().decode("utf-8")
            self.assertIn("io.example/filesystem", body)

    def test_ecosystem_report(self) -> None:
        self._start(False)
        with urlopen(self.base_url + "/reports/ecosystems", timeout=2) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Package ecosystems", body)
            self.assertIn(
                '<a href="/servers?ecosystem=npm"><code>npm</code></a>', body
            )
            self.assertIn("<td>3</td>", body)
            self.assertIn("Counting boundary", body)

        with urlopen(self.base_url + "/servers?ecosystem=pypi", timeout=2) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("io.example/filesystem", body)
            self.assertNotIn("io.example/search", body)
            self.assertIn("example-filesystem", body)
            self.assertIn('name="ecosystem" value="pypi"', body)
            self.assertIn("Clear ecosystem filter", body)

    def test_disabled_post_is_rejected(self) -> None:
        self._start(False)
        request = Request(self.base_url + "/", method="POST", data=b"")
        with self.assertRaises(HTTPError) as captured:
            urlopen(request, timeout=2)
        self.assertEqual(captured.exception.code, 405)
        self.assertEqual(captured.exception.headers["Allow"], "GET, HEAD")

    def test_public_readonly_mode_exposes_only_bounded_public_records(self) -> None:
        self._start(False, public_readonly=True)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                """UPDATE analysis_findings
                      SET evidence='private-token-value', public_excerpt=?,
                          public_excerpt_eligible=1,
                          public_excerpt_reason='approved by fixture review'
                    WHERE id=1""",
                ("<script>" + ("x" * 2200),),
            )
            connection.commit()
        finally:
            connection.close()
        catalog_before = hashlib.sha256(self.database_path.read_bytes()).digest()

        with urlopen(self.base_url + "/analyses/100", timeout=2) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Approved public excerpt", body)
            self.assertIn("&lt;script&gt;", body)
            self.assertNotIn("<script>", body)
            self.assertNotIn("private-token-value", body)
            self.assertIn("excerpt bounded", body)
            self.assertIn("f" * 64, body)
            self.assertNotIn("/findings/1/source", body)
            self.assertNotIn('action="/review-requests"', body)
            self.assertIn(
                "Not affiliated with or endorsed by the Model Context Protocol project",
                body,
            )
            self.assertNotIn('href="/jobs"', body)

        for path, heading in (
            ("/about", "About"),
            ("/data-sources", "Data Sources"),
            ("/disclaimer", "Disclaimer"),
            ("/privacy", "Privacy"),
            ("/corrections", "Corrections"),
        ):
            with self.subTest(path=path):
                with urlopen(self.base_url + path, timeout=2) as response:
                    page = response.read().decode("utf-8")
                    self.assertEqual(response.status, 200)
                    self.assertIn(f"<h1>{heading}</h1>", page)

        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=2
        )
        connection.request("GET", "/methodology")
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 308)
        self.assertEqual(response.getheader("Location"), "/about")
        connection.close()

        for path in (
            "/findings/1/source",
            "/findings/1/source/download",
            "/runtime-observations/1",
            "/jobs",
            "/jobs/1",
            "/review-jobs/1",
            "/runtime-jobs/1",
        ):
            with self.subTest(path=path):
                self.assertEqual(self._request_status("GET", path), 404)

        for method, path in (
            ("POST", "/analysis-requests"),
            ("POST", "/review-requests"),
            ("POST", "/runtime-discovery-requests"),
            ("PUT", "/analyses/100"),
            ("PATCH", "/analyses/100"),
            ("DELETE", "/analyses/100"),
        ):
            with self.subTest(method=method, path=path):
                self.assertEqual(self._request_status(method, path), 405)

        self.assertIsNone(self.server.jobs)
        self.assertEqual(
            hashlib.sha256(self.database_path.read_bytes()).digest(),
            catalog_before,
        )
        connection = sqlite3.connect(self.database_path)
        try:
            disposition = connection.execute(
                "SELECT disposition FROM analysis_findings WHERE id=1"
            ).fetchone()[0]
            review_count = connection.execute(
                "SELECT COUNT(*) FROM analysis_finding_reviews"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(disposition, "unreviewed")
        self.assertEqual(review_count, 0)

    def test_public_readonly_hides_ineligible_excerpt_and_evidence(self) -> None:
        self._start(False, public_readonly=True)
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                """UPDATE analysis_findings
                      SET evidence='private-token-value',
                          public_excerpt='not-yet-approved',
                          public_excerpt_eligible=0,
                          public_excerpt_reason='awaiting review'
                    WHERE id=1"""
            )
            connection.commit()
        finally:
            connection.close()

        with urlopen(self.base_url + "/analyses/100", timeout=2) as response:
            body = response.read().decode("utf-8")
        self.assertNotIn("Approved public excerpt", body)
        self.assertNotIn("private-token-value", body)
        self.assertNotIn("not-yet-approved", body)
        self.assertNotIn("awaiting review", body)

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

    def test_runtime_form_queues_job_with_csrf_and_same_origin(self) -> None:
        self._start(False, runtime=True)
        with urlopen(
            self.base_url + "/servers/io.example%2Ffilesystem", timeout=2
        ) as response:
            body = response.read().decode("utf-8")
        match = re.search(
            r'action="/runtime-discovery-requests".*?'
            r'name="csrf_token" value="([0-9a-f]+)"',
            body,
        )
        self.assertIsNotNone(match)
        payload = urlencode(
            {
                "server_version_id": "1",
                "package_id": "10",
                "csrf_token": match.group(1),
            }
        )
        rejected = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=2
        )
        rejected.request(
            "POST",
            "/runtime-discovery-requests",
            body=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Host": self.host_header,
                "Sec-Fetch-Site": "cross-site",
            },
        )
        response = rejected.getresponse()
        response.read()
        self.assertEqual(response.status, 400)
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=2
        )
        connection.request(
            "POST",
            "/runtime-discovery-requests",
            body=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Host": self.host_header,
                "Sec-Fetch-Site": "same-origin",
            },
        )
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 303)
        self.assertEqual(response.getheader("Location"), "/runtime-jobs/1")
        self.assertEqual(self.server.jobs.get_runtime(1)["status"], "queued")


if __name__ == "__main__":
    unittest.main()
