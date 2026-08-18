#!/usr/bin/env python3
"""Bounded UX smoke crawler for public portal navigation.

The crawler starts at the supplied site root, follows each same-origin HTTP(S)
destination once, reports broken/dead/same-page duplicate destinations, and
records external links without requesting them.

This implementation intentionally uses only Python's standard library.
"""

from __future__ import annotations

import argparse
import collections
import html.parser
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass


DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_PAGES = 500
USER_AGENT = "MCP-Assurance-Site-UX-Smoke/1.1"
NAV_ROLES = {"link", "menuitem"}
PLACEHOLDER_SCHEMES = {"javascript"}
NON_HTTP_SCHEMES = {"mailto", "tel", "sms"}


@dataclass(frozen=True)
class DiscoveredControl:
    page: str
    kind: str
    text: str
    raw_destination: str
    normalized_destination: str | None
    classification: str


@dataclass(frozen=True)
class PageResult:
    url: str
    status: int | None
    final_url: str | None
    error: str | None
    content_type: str | None

    @property
    def broken(self) -> bool:
        return self.status is None or self.status >= 400


class NavigationParser(html.parser.HTMLParser):
    """Extract navigation-like controls from server-rendered HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.controls: list[tuple[str, str, str]] = []
        self._stack: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key.lower(): (value or "") for key, value in attrs}
        tag = tag.lower()

        kind: str | None = None
        destination: str | None = None

        if tag in {"a", "area"}:
            kind = tag
            destination = attr_map.get("href", "")
        elif tag == "form":
            kind = "form"
            destination = attr_map.get("action", "")
        elif tag in {"button", "input"} and "formaction" in attr_map:
            kind = tag
            destination = attr_map.get("formaction", "")
        elif attr_map.get("role", "").lower() in NAV_ROLES:
            kind = f"role:{attr_map.get('role', '').lower()}"
            destination = attr_map.get("href") or attr_map.get("data-href") or ""
        elif "data-href" in attr_map:
            kind = "clickable-card"
            destination = attr_map.get("data-href", "")

        node = {
            "tag": tag,
            "kind": kind,
            "destination": destination,
            "text": [],
            "aria": attr_map.get("aria-label", ""),
            "title": attr_map.get("title", ""),
            "value": attr_map.get("value", ""),
        }
        self._stack.append(node)

        if tag in {"area", "input"}:
            self._finish_node()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self._stack and self._stack[-1]["tag"] == tag.lower():
            self._finish_node()

    def handle_data(self, data: str) -> None:
        for node in self._stack:
            text = node["text"]
            assert isinstance(text, list)
            text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index]["tag"] == tag:
                while len(self._stack) > index:
                    self._finish_node()
                return

    def close(self) -> None:
        super().close()
        while self._stack:
            self._finish_node()

    def _finish_node(self) -> None:
        node = self._stack.pop()
        kind = node["kind"]
        if not kind:
            return
        destination = str(node["destination"] or "")
        text_parts = node["text"]
        assert isinstance(text_parts, list)
        visible_text = " ".join("".join(text_parts).split())
        label = visible_text or str(node["aria"] or node["title"] or node["value"] or "")
        self.controls.append((str(kind), label[:160], destination.strip()))


def normalize_site_root(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if not parsed.scheme:
        value = f"https://{value}"
        parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("site URL must be an absolute http:// or https:// URL")
    return urllib.parse.urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), "/", "", ""))


def normalize_http_destination(base_url: str, raw: str) -> tuple[str | None, str]:
    raw = raw.strip()
    if not raw:
        return None, "empty"
    if raw == "#":
        return None, "placeholder-fragment"

    absolute = urllib.parse.urljoin(base_url, raw)
    parsed = urllib.parse.urlsplit(absolute)
    scheme = parsed.scheme.lower()

    if scheme in PLACEHOLDER_SCHEMES:
        return None, "javascript-placeholder"
    if scheme in NON_HTTP_SCHEMES:
        return None, "non-http"
    if scheme not in {"http", "https"}:
        return None, "unsupported-scheme"

    normalized = urllib.parse.urlunsplit(
        (scheme, parsed.netloc.lower(), parsed.path or "/", parsed.query, "")
    )

    base_parsed = urllib.parse.urlsplit(base_url)
    base_without_fragment = urllib.parse.urlunsplit(
        (
            base_parsed.scheme.lower(),
            base_parsed.netloc.lower(),
            base_parsed.path or "/",
            base_parsed.query,
            "",
        )
    )
    if parsed.fragment and normalized == base_without_fragment:
        return normalized, "same-page-fragment"
    return normalized, "http"


def same_origin(url: str, root: str) -> bool:
    target = urllib.parse.urlsplit(url)
    origin = urllib.parse.urlsplit(root)
    return target.scheme.lower() == origin.scheme.lower() and target.netloc.lower() == origin.netloc.lower()


def request_page(url: str, timeout: float) -> tuple[PageResult, str | None]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            final_url = response.geturl()
            content_type = response.headers.get_content_type()
            body = response.read(4 * 1024 * 1024)
            text = None
            if content_type in {"text/html", "application/xhtml+xml"}:
                charset = response.headers.get_content_charset() or "utf-8"
                text = body.decode(charset, errors="replace")
            return PageResult(url, status, final_url, None, content_type), text
    except urllib.error.HTTPError as exc:
        return PageResult(url, exc.code, exc.geturl(), str(exc), exc.headers.get_content_type()), None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return PageResult(url, None, None, str(exc), None), None


def crawl(
    root: str, *, timeout: float, max_pages: int
) -> tuple[list[PageResult], list[DiscoveredControl], bool]:
    queue = collections.deque([root])
    queued = {root}
    visited: set[str] = set()
    pages: list[PageResult] = []
    controls: list[DiscoveredControl] = []

    while queue and len(visited) < max_pages:
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)

        page_result, html_text = request_page(url, timeout)
        pages.append(page_result)
        if html_text is None or page_result.broken:
            continue

        parser = NavigationParser()
        try:
            parser.feed(html_text)
            parser.close()
        except Exception as exc:
            controls.append(
                DiscoveredControl(url, "parser", str(exc), "", None, "html-parse-error")
            )
            continue

        for kind, text, raw_destination in parser.controls:
            normalized, classification = normalize_http_destination(url, raw_destination)
            if normalized and classification == "http":
                classification = "internal" if same_origin(normalized, root) else "external"
            controls.append(
                DiscoveredControl(url, kind, text, raw_destination, normalized, classification)
            )
            if classification == "internal" and normalized not in visited and normalized not in queued:
                queue.append(normalized)
                queued.add(normalized)

    return pages, controls, bool(queue)


def find_same_page_duplicates(
    controls: list[DiscoveredControl],
) -> dict[tuple[str, str], list[DiscoveredControl]]:
    by_page_destination: dict[tuple[str, str], list[DiscoveredControl]] = collections.defaultdict(list)
    for control in controls:
        if control.classification == "internal" and control.normalized_destination:
            key = (control.page, control.normalized_destination)
            by_page_destination[key].append(control)
    return {key: items for key, items in by_page_destination.items() if len(items) > 1}


def build_summary(
    pages: list[PageResult], controls: list[DiscoveredControl], *, crawl_limit_reached: bool = False
) -> dict[str, object]:
    broken_pages = [page for page in pages if page.broken]
    dead_controls = [
        control
        for control in controls
        if control.classification
        in {
            "empty",
            "placeholder-fragment",
            "javascript-placeholder",
            "unsupported-scheme",
            "html-parse-error",
        }
    ]
    external = [control for control in controls if control.classification == "external"]
    unique_external = sorted(
        {
            control.normalized_destination
            for control in external
            if control.normalized_destination is not None
        }
    )
    same_page_fragments = [
        control for control in controls if control.classification == "same-page-fragment"
    ]
    duplicates = find_same_page_duplicates(controls)

    return {
        "pages_checked": len(pages),
        "controls_found": len(controls),
        "broken_pages": broken_pages,
        "dead_controls": dead_controls,
        "same_page_fragments": same_page_fragments,
        "external_links": external,
        "unique_external_destinations": unique_external,
        "same_page_duplicate_destinations": duplicates,
        "crawl_limit_reached": crawl_limit_reached,
    }


def print_report(
    root: str,
    pages: list[PageResult],
    controls: list[DiscoveredControl],
    *,
    crawl_limit_reached: bool,
    verbose: bool,
) -> None:
    summary = build_summary(pages, controls, crawl_limit_reached=crawl_limit_reached)
    broken_pages: list[PageResult] = summary["broken_pages"]  # type: ignore[assignment]
    dead_controls: list[DiscoveredControl] = summary["dead_controls"]  # type: ignore[assignment]
    same_page: list[DiscoveredControl] = summary["same_page_fragments"]  # type: ignore[assignment]
    external: list[DiscoveredControl] = summary["external_links"]  # type: ignore[assignment]
    unique_external: list[str] = summary["unique_external_destinations"]  # type: ignore[assignment]
    duplicates: dict[tuple[str, str], list[DiscoveredControl]] = summary[
        "same_page_duplicate_destinations"
    ]  # type: ignore[assignment]

    print(f"Site UX smoke report: {root}")
    print("=" * 72)
    print(f"Pages checked: {len(pages)}")
    print(f"Clickable/navigation controls found: {len(controls)}")
    print(f"Broken internal destinations: {len(broken_pages)}")
    print(f"Dead/placeholder controls: {len(dead_controls)}")
    print(f"Same-page fragments: {len(same_page)}")
    print(f"External link occurrences: {len(external)}")
    print(f"Unique external destinations: {len(unique_external)}")
    print(f"Same-page duplicate destinations: {len(duplicates)}")
    print(f"Crawl limit reached: {'yes' if crawl_limit_reached else 'no'}")

    if crawl_limit_reached:
        print("WARNING: crawl stopped at --max-pages; not all discovered internal pages were checked.")

    if broken_pages:
        print("\nBroken internal destinations")
        for page in broken_pages:
            status = "ERR" if page.status is None else str(page.status)
            detail = f" ({page.error})" if page.error else ""
            print(f"  {status:>3} {page.url}{detail}")

    if dead_controls:
        print("\nDead / placeholder controls")
        for item in dead_controls:
            label = item.text or "<no label>"
            print(f"  {item.classification:22} {item.page} :: {label!r} -> {item.raw_destination!r}")

    if same_page:
        print("\nSame-page fragment controls")
        for item in same_page:
            print(f"  {item.page} :: {(item.text or '<no label>')!r} -> {item.raw_destination!r}")

    if duplicates:
        print("\nSame-page duplicate destinations")
        for (page, destination), items in sorted(duplicates.items()):
            print(f"  {page} -> {destination} ({len(items)} controls)")
            for item in items[:8]:
                print(f"    - {(item.text or '<no label>')!r} [{item.kind}]")
            if len(items) > 8:
                print(f"    - ... {len(items) - 8} more")

    if verbose:
        print("\nInternal pages")
        for page in pages:
            marker = "BROKEN" if page.broken else "OK"
            status = "ERR" if page.status is None else str(page.status)
            redirect = f" -> {page.final_url}" if page.final_url and page.final_url != page.url else ""
            detail = f" ({page.error})" if page.error else ""
            print(f"  {marker:6} {status:>3} {page.url}{redirect}{detail}")

        if external:
            print("\nExternal links (recorded, not requested)")
            for item in external:
                print(
                    f"  {item.page} :: {(item.text or '<no label>')!r} -> "
                    f"{item.normalized_destination}"
                )


def json_payload(
    root: str,
    pages: list[PageResult],
    controls: list[DiscoveredControl],
    *,
    crawl_limit_reached: bool,
) -> dict[str, object]:
    summary = build_summary(pages, controls, crawl_limit_reached=crawl_limit_reached)
    duplicates = summary["same_page_duplicate_destinations"]
    assert isinstance(duplicates, dict)
    return {
        "root": root,
        "summary": {
            "pages_checked": summary["pages_checked"],
            "controls_found": summary["controls_found"],
            "broken_internal_destinations": len(summary["broken_pages"]),  # type: ignore[arg-type]
            "dead_placeholder_controls": len(summary["dead_controls"]),  # type: ignore[arg-type]
            "same_page_fragments": len(summary["same_page_fragments"]),  # type: ignore[arg-type]
            "external_link_occurrences": len(summary["external_links"]),  # type: ignore[arg-type]
            "unique_external_destinations": len(summary["unique_external_destinations"]),  # type: ignore[arg-type]
            "same_page_duplicate_destinations": len(duplicates),
            "crawl_limit_reached": crawl_limit_reached,
        },
        "pages": [asdict(page) | {"broken": page.broken} for page in pages],
        "controls": [asdict(control) for control in controls],
        "same_page_duplicate_destinations": [
            {
                "page": page,
                "destination": destination,
                "controls": [asdict(item) for item in items],
            }
            for (page, destination), items in sorted(duplicates.items())
        ],
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", help="Site root, for example https://example.test")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--json", dest="json_path", help="Also write the complete report as JSON")
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every checked internal page and every external-link occurrence",
    )
    parser.add_argument(
        "--fail-on-broken",
        action="store_true",
        help="Exit non-zero for broken internal destinations or dead/placeholder controls",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    if args.max_pages <= 0:
        raise SystemExit("--max-pages must be positive")

    try:
        root = normalize_site_root(args.site)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    pages, controls, crawl_limit_reached = crawl(
        root, timeout=args.timeout, max_pages=args.max_pages
    )
    print_report(
        root,
        pages,
        controls,
        crawl_limit_reached=crawl_limit_reached,
        verbose=args.verbose,
    )

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump(
                json_payload(
                    root,
                    pages,
                    controls,
                    crawl_limit_reached=crawl_limit_reached,
                ),
                handle,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")

    summary = build_summary(pages, controls, crawl_limit_reached=crawl_limit_reached)
    if args.fail_on_broken and (summary["broken_pages"] or summary["dead_controls"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
