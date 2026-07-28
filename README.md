# MCP Observatory Guard Portal

A small research portal for the future **Open MCP and Agent Behavioral Assurance Observatory**.

The portal browses the SQLite catalog and static-analysis records produced by [`mcp-observatory`](https://github.com/zelinsky-alexander/mcp-observatory). Its optional local orchestration mode can queue an exact npm-backed server record for the existing `mcp-observatory analyze package` workflow. It does not implement package analysis itself and does not execute MCP servers or tools.

## Current pages

- Dashboard with the latest imported snapshot, catalog totals, latest refresh records, recent static-analysis runs, and optional portal jobs.
- Searchable server browser with one current row per Registry server identifier.
- Server detail with immutable metadata variants, packages, declared environment names, remotes, snapshot history, analysis history, and eligible analysis actions.
- Static-analysis detail with findings and finalized evidence metadata.
- Portal-owned analysis queue and job detail pages.
- `/healthz` schema check.

## Default read-only mode

```bash
export MCP_PORTAL_DATABASE=/home/alex/source/mcp-observatory/db/local-registry.sqlite
export MCP_PORTAL_HOST=127.0.0.1
export MCP_PORTAL_PORT=8080
python3 -m mcp_portal
```

Open `http://127.0.0.1:8080`.

## Enable on-demand static analysis

This milestone deliberately restricts analysis-enabled mode to a loopback host. Configure exact local paths:

```bash
export MCP_PORTAL_DATABASE=/home/alex/source/mcp-observatory/db/local-registry.sqlite
export MCP_PORTAL_HOST=127.0.0.1
export MCP_PORTAL_PORT=8080

export MCP_PORTAL_ENABLE_ANALYSIS=1
export MCP_PORTAL_JOBS_DATABASE=/home/alex/source/mcp-observatory-guard-portal/runtime/portal-jobs.sqlite
export MCP_PORTAL_OBSERVATORY_BINARY=/home/alex/source/mcp-observatory/build/release/mcp-observatory
export MCP_PORTAL_ANALYSIS_RULES=/home/alex/source/mcp-observatory/rules/artifact-static-analysis-v1.json
export MCP_PORTAL_EVIDENCE_ROOT=/home/alex/source/mcp-observatory/evidence

python3 -m mcp_portal
```

Run the single-job worker in a second terminal:

```bash
python3 -m mcp_portal.worker
```

For scheduled or diagnostic use, process at most one queued job:

```bash
python3 -m mcp_portal.worker --once
```

Optional limits:

- `MCP_PORTAL_PAGE_SIZE`: 1–100, default 50.
- `MCP_PORTAL_ANALYSIS_TIMEOUT_SECONDS`: 30–7200, default 900.
- `MCP_PORTAL_MAXIMUM_OUTPUT_BYTES`: 4096–1048576 per stdout/stderr stream, default 65536.
- `MCP_PORTAL_WORKER_POLL_SECONDS`: 1–60, default 2.

Create the `runtime` directory before startup. The portal creates only its own jobs database; Observatory remains the sole writer of Registry and analysis records.

## Security boundary

The portal:

- opens the Observatory SQLite catalog with `mode=ro` and `query_only`;
- stores queue state in a separate portal-owned SQLite database;
- accepts analysis requests only for exact existing `server_version_id` and `package_id` pairs;
- re-resolves those IDs from the catalog both at submission and immediately before execution;
- supports npm packages with an exact declared package version only;
- protects browser submissions with an HMAC CSRF token and same-origin checks;
- starts a separate worker with a fixed executable and argument vector using `shell=False`;
- never exposes `--force`, arbitrary package names, URLs, commands, paths, or extra CLI flags through HTTP;
- deduplicates queued and running jobs for the same exact package record;
- bounds stdout and stderr capture and terminates the process group on timeout;
- passes a minimal environment to the child process;
- escapes database and worker text before inserting it into HTML;
- has no third-party Python runtime dependencies.

The worker invokes the already implemented Observatory static analyzer. That analyzer downloads the package and inspects the archive in its restricted Docker workflow. The portal itself does not add a new sandbox.

A completed static-analysis result does **not** prove that an MCP server is safe or malicious. It describes one exact package artifact under the recorded analyzer and ruleset versions.

## Requirements

- Python 3.12 or newer.
- An existing `mcp-observatory` SQLite catalog using schema version 1 or 2.
- Schema version 2 to display or create static-analysis results.
- For on-demand analysis: a working release build of `mcp-observatory`, Docker access required by its analyzer, the versioned analysis rules file, and an existing evidence directory.

## Tests

```bash
python3 -m compileall -q mcp_portal tests
python3 -m unittest discover -s tests -v
```

Tests use a compact temporary Observatory schema, a temporary portal queue, a fake Observatory executable, and loopback HTTP only.

## Repository responsibilities

| Repository | Responsibility |
|---|---|
| `mcp-observatory` | Registry collection, immutable history, package acquisition, static analysis, evidence, and authoritative SQLite writes |
| `mcp-native-guard` | Future bounded MCP runtime inspection, policy enforcement, and runtime observations |
| `mcp-observatory-guard-portal` | Browsing and constrained orchestration through a portal-owned job contract |

## Planned next milestones

1. Consistent database publishing with SQLite backup and atomic replacement.
2. Authentication and rate limits before any non-loopback deployment.
3. Explicit daily-change views and analysis filters.
4. Evidence file serving through a strict allowlisted manifest.
5. Runtime-observation pages after the sandbox and `mcp-native-guard` integration contract exists.

## Licence

Apache License 2.0. The implementation is original project code based on documented Python, SQLite, HTTP, subprocess, and `mcp-observatory` interfaces. No third-party source code is included.

Before public deployment, perform manual security, privacy, licence, similarity, accessibility, and legal review.
