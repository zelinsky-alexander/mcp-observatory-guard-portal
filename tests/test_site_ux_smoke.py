from __future__ import annotations

import http.server
import threading
import unittest

from scripts import site_ux_smoke


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        pages = {
            "/": (
                200,
                "text/html; charset=utf-8",
                """
                <html><body>
                  <a href="/good">Good</a>
                  <a href="/good">Duplicate good</a>
                  <a href="/missing">Missing</a>
                  <a href="#">Dead</a>
                  <a href="https://example.org/reference">External</a>
                  <div class="card" data-href="/card">Card destination</div>
                  <div role="link" data-href="/role-link" aria-label="Role link"></div>
                </body></html>
                """,
            ),
            "/good": (200, "text/html; charset=utf-8", '<a href="/leaf">Leaf</a>'),
            "/card": (200, "text/html; charset=utf-8", "<p>Card page</p>"),
            "/role-link": (200, "text/html; charset=utf-8", "<p>Role page</p>"),
            "/leaf": (200, "text/plain; charset=utf-8", "leaf"),
            "/missing": (404, "text/html; charset=utf-8", "missing"),
        }
        status, content_type, body = pages.get(
            self.path, (404, "text/plain; charset=utf-8", "unknown")
        )
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


class SiteUxSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.root = f"http://{host}:{port}/"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.thread.join(timeout=5)
        cls.server.server_close()

    def test_crawl_records_navigation_and_broken_destinations(self) -> None:
        pages, controls = site_ux_smoke.crawl(self.root, timeout=2, max_pages=20)
        summary = site_ux_smoke.build_summary(pages, controls)

        page_statuses = {page.url: page.status for page in pages}
        self.assertEqual(page_statuses[self.root], 200)
        self.assertEqual(page_statuses[self.root + "missing"], 404)
        self.assertEqual(page_statuses[self.root + "card"], 200)
        self.assertEqual(page_statuses[self.root + "role-link"], 200)

        self.assertEqual(len(summary["broken_pages"]), 1)
        self.assertTrue(
            any(item.classification == "placeholder-fragment" for item in summary["dead_controls"])
        )
        self.assertTrue(
            any(item.normalized_destination == "https://example.org/reference" for item in summary["external_links"])
        )
        self.assertIn(self.root + "good", summary["duplicate_internal_destinations"])

    def test_normalization_flags_empty_and_javascript_targets(self) -> None:
        self.assertEqual(site_ux_smoke.normalize_http_destination(self.root, ""), (None, "empty"))
        self.assertEqual(
            site_ux_smoke.normalize_http_destination(self.root, "javascript:void(0)"),
            (None, "javascript-placeholder"),
        )

    def test_root_requires_http_url(self) -> None:
        self.assertEqual(site_ux_smoke.normalize_site_root("example.test"), "https://example.test/")
        with self.assertRaises(ValueError):
            site_ux_smoke.normalize_site_root("file:///tmp/index.html")


if __name__ == "__main__":
    unittest.main()
