from __future__ import annotations

import unittest

from mcp_portal import app, views
from mcp_portal.about_methodology import (
    ABOUT_PATH,
    LEGACY_METHODOLOGY_PATH,
    about_methodology_page,
    apply_about_methodology,
)


class AboutMethodologyTests(unittest.TestCase):
    def test_installation_is_idempotent_and_navigation_has_one_page(self) -> None:
        apply_about_methodology()
        first_layout = views.layout
        apply_about_methodology()
        self.assertIs(views.layout, first_layout)

        html = views.layout("Test", "<p>body</p>", public_readonly=True)
        self.assertIn('href="/about">About</a>', html)
        self.assertNotIn('href="/methodology"', html)
        self.assertEqual(html.count('href="/about"'), 2)

    def test_methodology_is_highlighted_on_about_page(self) -> None:
        html = about_methodology_page(public_readonly=True)
        self.assertIn("<h1>About</h1>", html)
        self.assertIn('class="notice methodology-highlight"', html)
        self.assertIn("<h2>How observations are produced</h2>", html)
        self.assertIn("Immutable catalog history", html)
        self.assertIn("Exact artifact analysis", html)
        self.assertLess(
            html.index("How observations are produced"),
            html.index("Independent MCP ecosystem research"),
        )

    def test_about_information_path_uses_combined_page(self) -> None:
        html = app.information_page(ABOUT_PATH, public_readonly=True)
        self.assertIsNotNone(html)
        assert html is not None
        self.assertIn("<h1>About</h1>", html)
        self.assertIn("Methodology", html)
        self.assertIsNone(
            app.information_page(
                LEGACY_METHODOLOGY_PATH,
                public_readonly=True,
            )
        )

    def test_legacy_methodology_route_redirects_permanently(self) -> None:
        handler = object.__new__(app.PortalHandler)
        handler.path = LEGACY_METHODOLOGY_PATH
        response: dict[str, object] = {"headers": []}

        handler.send_response = lambda status: response.update(status=status)
        handler._security_headers = lambda: response.update(security=True)
        handler.send_header = lambda name, value: response["headers"].append(
            (name, value)
        )
        handler.end_headers = lambda: response.update(ended=True)

        handler._dispatch(include_body=True)

        self.assertEqual(response["status"], 308)
        self.assertIn(("Location", ABOUT_PATH), response["headers"])
        self.assertIn(("Content-Length", "0"), response["headers"])
        self.assertTrue(response["security"])
        self.assertTrue(response["ended"])


if __name__ == "__main__":
    unittest.main()
