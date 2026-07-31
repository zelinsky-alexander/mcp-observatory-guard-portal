from __future__ import annotations

import unittest

from mcp_portal.views import finding_source_page, runtime_observation_page


class ViewTests(unittest.TestCase):
    def test_large_minified_source_renders_wrapped_bounded_window(self) -> None:
        html = finding_source_page(
            {
                "finding_id": 819,
                "analysis_run_id": 28,
                "subject_path": "package/static/css.worker.js",
                "line_number": 7,
                "sha256": "f" * 64,
                "byte_size": 500_000,
                "displayed_byte_size": 20,
                "start_line": 7,
                "truncated_before": True,
                "truncated_after": True,
                "starts_mid_line": True,
                "ends_mid_line": True,
                "content": "minified&lt;payload>",
            }
        )
        self.assertIn("showing 20 verified bytes around the finding", html)
        self.assertIn("Earlier verified source omitted", html)
        self.assertIn("Later verified source omitted", html)
        self.assertIn("source-line-target", html)
        self.assertIn("source-line-content", html)
        self.assertIn(">7</span>", html)
        self.assertIn("minified&amp;lt;payload&gt;", html)
        self.assertIn('href="/findings/819/source/download"', html)
        self.assertIn("Download complete verified file (500,000 bytes)", html)

    def test_complete_source_does_not_offer_download(self) -> None:
        html = finding_source_page(
            {
                "finding_id": 1,
                "analysis_run_id": 1,
                "subject_path": "package/index.js",
                "line_number": 1,
                "sha256": "f" * 64,
                "byte_size": 8,
                "displayed_byte_size": 8,
                "start_line": 1,
                "truncated_before": False,
                "truncated_after": False,
                "starts_mid_line": False,
                "ends_mid_line": False,
                "content": "safe();\n",
            }
        )
        self.assertNotIn("/source/download", html)

    def test_runtime_observation_escapes_inventory_text(self) -> None:
        html = runtime_observation_page(
            {
                "id": 1,
                "server_identifier": "<server>",
                "server_version": "1.0.0",
                "status": "completed",
                "package_identifier": "<package>",
                "artifact_sha256": "a" * 64,
                "inventory_sha256": "b" * 64,
                "sandbox_image": "node:test",
                "tools": [
                    {
                        "name": "<script>",
                        "definition_sha256": "c" * 64,
                        "definition_json": "<img src=x>",
                        "definition_truncated": 1,
                    }
                ],
            }
        )
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("bounded display", html)


if __name__ == "__main__":
    unittest.main()
