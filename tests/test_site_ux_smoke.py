from __future__ import annotations

import contextlib
import http.server
import io
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
            "/good": (
                200,
                "text/html; charset=utf-8",
                '<a href="/leaf">Leaf</a><a href="/good">Self</a>',
            ),
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
        pages, controls, crawl_limit_reached = site_ux_smoke.crawl(
            self.root, timeout=2, max_pages=20
        )
        summary = site_ux_smoke.build_summary(
            pages, controls, crawl_limit_reached=crawl_limit_reached
        )

        page_statuses = {page.url: page.status for page in pages}
        self.assertEqual(page_statuses[self.root], 200)
        self.assertEqual(page_statuses[self.root + "missing"], 404)
        self.assertEqual(page_statuses[self.root + "card"], 200)
        self.assertEqual(page_statuses[self.root + "role-link"], 200)

        self.assertFalse(crawl_limit_reached)
        self.assertEqual(len(summary["broken_pages"]), 1)
        self.assertTrue(
            any(
                item.classification == "placeholder-fragment"
                for item in summary["dead_controls"]
            )
        )
        self.assertTrue(
            any(
                item.normalized_destination == "https://example.org/reference"
                for item in summary["external_links"]
            )
        )
        duplicate_key = (self.root, self.root + "good")
        self.assertIn(duplicate_key, summary["same_page_duplicate_destinations"])
        self.assertEqual(len(summary["same_page_duplicate_destinations"]), 1)
        self.assertEqual(summary["unique_external_destinations"], ["https://example.org/reference"])

    def test_repeated_destination_on_different_pages_is_not_a_duplicate(self) -> None:
        controls = [
            site_ux_smoke.DiscoveredControl(
                "https://example.test/a",
                "a",
                "Shared",
                "/shared",
                "https://example.test/shared",
                "internal",
            ),
            site_ux_smoke.DiscoveredControl(
                "https://example.test/b",
                "a",
                "Shared",
                "/shared",
                "https://example.test/shared",
                "internal",
            ),
        ]
        self.assertEqual(site_ux_smoke.find_same_page_duplicates(controls), {})

    def test_compact_report_does_not_list_every_success_or_external_link(self) -> None:
        pages, controls, crawl_limit_reached = site_ux_smoke.crawl(
            self.root, timeout=2, max_pages=20
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            site_ux_smoke.print_report(
                self.root,
                pages,
                controls,
                crawl_limit_reached=crawl_limit_reached,
                verbose=False,
            )
        report = output.getvalue()
        self.assertIn("Broken internal destinations: 1", report)
        self.assertIn("Same-page duplicate destinations: 1", report)
        self.assertNotIn("\nInternal pages\n", report)
        self.assertNotIn("External links (recorded, not requested)", report)

    def test_crawl_reports_when_page_limit_is_reached(self) -> None:
        pages, _, crawl_limit_reached = site_ux_smoke.crawl(
            self.root, timeout=2, max_pages=1
        )
        self.assertEqual(len(pages), 1)
        self.assertTrue(crawl_limit_reached)

    def test_normalization_flags_empty_and_javascript_targets(self) -> None:
        self.assertEqual(
            site_ux_smoke.normalize_http_destination(self.root, ""), (None, "empty")
        )
        self.assertEqual(
            site_ux_smoke.normalize_http_destination(self.root, "javascript:void(0)"),
            (None, "javascript-placeholder"),
        )

    def test_root_requires_http_url(self) -> None:
        self.assertEqual(
            site_ux_smoke.normalize_site_root("example.test"), "https://example.test/"
        )
        with self.assertRaises(ValueError):
            site_ux_smoke.normalize_site_root("file:///tmp/index.html")


if __name__ == "__main__":
    unittest.main()
