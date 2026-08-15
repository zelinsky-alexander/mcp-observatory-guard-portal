# Post-Storage-v2 Portal Fix Plan

Branch: `fix/post-v2-portal-drilldowns`

This branch starts from current `main` after Storage v2 cutover. The public portal remains read-only. Fixes must preserve the compact hot catalog and use bounded history reads where detail is not present in the hot database.

## Issues in scope

- #14 Review queue count/detail mismatch.
- #15 Server browser latest-snapshot-only UX.
- #16 Snapshot history has no drill-down.
- #17 Static coverage metrics lack drill-down.
- #18 Coverage page mixes operational, planned, and private capabilities.
- #19 Dashboard KPI cards lack drill-down.
- #20 Official MCP Registry provenance is not explicit enough.

## Implementation order

### Batch A — correctness and status communication

1. Fix #14 by routing the bounded unreviewed high/critical listing to the configured Storage v2 history catalog, while keeping the aggregate count in the hot summary.
2. Fix #18 by presenting runtime discovery as planned/not yet enabled until compatible completed observations exist, controlled behavioral analysis as planned later, and hiding human-review coverage from public mode while review is private.
3. Fix #20 with a consistent provenance notice: catalog source records come from the Official MCP Registry through its REST API; analysis, coverage, comparisons, runtime observations, and review state are independently derived by MCPLA.

### Batch B — longitudinal navigation

4. Fix #15 with explicit server-browser scopes: `Current snapshot` and `All observed servers`. Default stays current snapshot for fast common browsing; all-observed uses bounded historical identity reads.
5. Fix #16 with a stable snapshot detail route, whole-row tap targets, snapshot identity/collection metadata, browse-this-snapshot, and compare-with-previous navigation.

### Batch C — metric drill-down

6. Fix #17 with filtered paginated static-coverage detail routes sharing the same predicates as the aggregate counts.
7. Fix #19 with dashboard drill-down routes for distinct servers, immutable records, and completed analyses. Whole KPI cards should be tappable on mobile.

## Storage v2 rules

- Do not restore bulk v1 finding/file/evidence rows to the hot DB.
- Use `MCP_PORTAL_HISTORY_DATABASE` for bounded detail where appropriate.
- Keep aggregate counts on compact v2 summaries.
- Add count/list parity tests for every KPI drill-down.
- Keep page size bounded by existing portal limits.
- No browser-supplied commands, package URLs, Docker images, or runtime flags.

## Repository boundary

Portal owns public routes, rendering, query orchestration, filters, pagination, and provenance/status wording.

Observatory changes are required only when the authoritative Storage v2 publication model lacks a compact projection needed for fast, semantically stable public reads. Such schema/publication changes belong on `mcp-observatory` branch `fix/post-v2-portal-support`.

## Acceptance gate before merge

- Existing portal tests pass.
- New parity/navigation tests pass.
- `/`, `/servers`, `/coverage`, `/snapshots` stay fast with the production Storage v2 shape.
- Public mode stays read-only.
- History queries are bounded and paginated.
- No safety claim is introduced by analysis or runtime wording.
