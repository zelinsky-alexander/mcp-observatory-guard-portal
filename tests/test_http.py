from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mcp_portal.app import create_server
from mcp_portal.config import Config
from fixture_catalog import create_fixture


class HttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        database = Path(self.temporary.name) / "catalog.sqlite"
        create_fixture(database)
        self.server = create_server(Config(database_path=database, host="127.0.0.1", port=0, page_size=20))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def test_dashboard_and_server_detail(self) -> None:
        with urlopen(self.base_url + "/", timeout=2) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Recently imported registry records", body)
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

        with urlopen(self.base_url + "/servers/io.example%2Ffilesystem", timeout=2) as response:
            body = response.read().decode("utf-8")
            self.assertIn("@example/filesystem", body)
            self.assertIn("Static analysis history", body)

    def test_search_escapes_database_text(self) -> None:
        with urlopen(self.base_url + "/servers?q=filesystem", timeout=2) as response:
            body = response.read().decode("utf-8")
            self.assertIn("io.example/filesystem", body)

    def test_post_is_rejected(self) -> None:
        request = Request(self.base_url + "/", method="POST", data=b"")
        with self.assertRaises(HTTPError) as captured:
            urlopen(request, timeout=2)
        self.assertEqual(captured.exception.code, 405)
        self.assertEqual(captured.exception.headers["Allow"], "GET, HEAD")


if __name__ == "__main__":
    unittest.main()
