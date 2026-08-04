"""Public-facing project branding for server-rendered portal pages."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

PROJECT_TITLE = "Open MCP Longitudinal Assurance Project"
PROJECT_SUBTITLE = (
    "Independent, evidence-based research into MCP server provenance, "
    "artifact identity, capability drift, and observed behavior over time."
)

_REPLACEMENTS = (
    ("Open MCP Behavioral Assurance Observatory", PROJECT_TITLE),
    ("MCP Observatory", "MCP Longitudinal Assurance"),
    ("Evidence, provenance, and change over time", PROJECT_SUBTITLE),
    (
        "Browse Registry history and static package-analysis evidence produced by "
        "<code>mcp-observatory</code>.",
        "Browse Registry history, artifact evidence, capability drift, and observed "
        "behavior recorded by this independent research project.",
    ),
    ("Open Observatory analysis", "Open analysis"),
    ("No Observatory analysis run recorded.", "No analysis run recorded."),
    ("mcp-observatory analysis runs", "project analysis runs"),
)


def apply_branding() -> None:
    """Apply the public project name to all HTML rendered by the portal views."""
    from . import views

    if getattr(views.layout, "_longitudinal_assurance_branding", False):
        return

    original_layout: Callable[..., str] = views.layout

    def branded_layout(
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
        for old_text, new_text in _REPLACEMENTS:
            html = html.replace(old_text, new_text)
        return html

    setattr(branded_layout, "_longitudinal_assurance_branding", True)
    views.PORTAL_NAME = PROJECT_TITLE
    views.layout = branded_layout
