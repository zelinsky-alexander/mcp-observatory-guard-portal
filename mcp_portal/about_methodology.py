"""Combined public About page with highlighted methodology and legacy redirect."""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus
from typing import Any
from urllib.parse import urlsplit

ABOUT_PATH = "/about"
LEGACY_METHODOLOGY_PATH = "/methodology"


def apply_about_methodology() -> None:
    """Install one About page and remove duplicate Methodology navigation."""
    from . import app, views

    if getattr(app.PortalHandler._dispatch, "_combined_about_methodology", False):
        return

    original_layout: Callable[..., str] = views.layout

    def combined_layout(
        title: str,
        body: str,
        *,
        public_readonly: bool = False,
    ) -> str:
        html = original_layout(
            title,
            body,
            public_readonly=public_readonly,
        )
        separate_links = (
            '<a href="/about">About</a>'
            '<a href="/methodology">Methodology</a>'
        )
        return html.replace(separate_links, '<a href="/about">About</a>')

    setattr(combined_layout, "_combined_about_methodology", True)
    views.layout = combined_layout

    original_information_page = views.information_page

    def combined_information_page(
        path: str,
        *,
        public_readonly: bool = False,
    ) -> str | None:
        if path == ABOUT_PATH:
            return about_methodology_page(public_readonly=public_readonly)
        if path == LEGACY_METHODOLOGY_PATH:
            return None
        return original_information_page(
            path,
            public_readonly=public_readonly,
        )

    views.information_page = combined_information_page
    # app.py imports this function directly, so update that bound reference too.
    app.information_page = combined_information_page

    original_dispatch = app.PortalHandler._dispatch

    def combined_dispatch(self: Any, *, include_body: bool) -> None:
        if urlsplit(self.path).path == LEGACY_METHODOLOGY_PATH:
            self.send_response(HTTPStatus.PERMANENT_REDIRECT.value)
            self._security_headers()
            self.send_header("Location", ABOUT_PATH)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        original_dispatch(self, include_body=include_body)

    setattr(combined_dispatch, "_combined_about_methodology", True)
    app.PortalHandler._dispatch = combined_dispatch


def about_methodology_page(*, public_readonly: bool = False) -> str:
    """Render project context with methodology as the highlighted section."""
    from . import views

    body = """<section class="page-heading"><p class="eyebrow">Independent research project</p><h1>About</h1><p>This portal publishes reproducible observations about exact registry records and package artifacts to support inspection, comparison, and correction.</p></section>
<section class="notice methodology-highlight"><p class="eyebrow">Methodology</p><h2>How observations are produced</h2><ol><li><strong>Immutable catalog history.</strong> Registry metadata is imported as immutable, content-addressed history.</li><li><strong>Exact artifact analysis.</strong> Static analysis evaluates an exact package artifact under the analyzer, ruleset, integrity, and network profile recorded with each run.</li><li><strong>Recorded interpretation state.</strong> Findings identify observable patterns and retain their confidence and review disposition.</li></ol><p>The public portal does not execute servers, invoke MCP tools, run analysis, perform runtime discovery, or expose complete source and evidence files. A finding excerpt is displayed only when a dedicated public excerpt was explicitly approved during analysis or review; displayed excerpts are escaped and bounded to 2,048 characters.</p></section>
<section class="panel"><p class="eyebrow">Project context</p><h2>Independent MCP ecosystem research</h2><p>This is an independent security research project. It is not affiliated with or endorsed by the Model Context Protocol project, the Official MCP Registry, package registries, or listed publishers.</p><p>Records are presented to support inspection, comparison, and correction. A listing is not a recommendation, certification, accusation, or safety verdict.</p></section>
<section class="panel"><h2>Interpretation boundary</h2><p>Results describe exact records and artifacts under documented analysis profiles. They do not prove safety, malicious intent, publisher identity, or author intent. Absence of a finding does not establish safety, and presence of a finding does not establish malicious intent.</p></section>"""
    return views.layout(
        "About",
        body,
        public_readonly=public_readonly,
    )
