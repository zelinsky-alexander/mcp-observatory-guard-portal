# MCP Observatory Guard Portal

A small, read-only web portal for the future **Open MCP and Agent Behavioral Assurance Observatory**.

This first milestone browses the SQLite catalog and static-analysis records produced by [`mcp-observatory`](https://github.com/zelinsky-alexander/mcp-observatory). It does not collect registry data, analyze packages, execute MCP servers, invoke tools, or call `mcp-native-guard`.

## Current pages

- Dashboard with the latest imported snapshot, catalog totals, latest refresh records, and recent static-analysis runs.
- Searchable server browser with one current row per Registry server identifier.
- Server detail with immutable metadata variants, packages, declared environment names, remotes, snapshot history, and analysis history.
- Static-analysis detail with findings and finalized evidence metadata.
- Read-only `/healthz` endpoint.

## Security boundary

The portal:

- opens the configured SQLite database with `mode=ro`;
- enables SQLite `query_only` mode;
- implements only `GET` and `HEAD` routes;
- never accepts package names, filesystem paths, commands, or analysis arguments for execution;
- escapes all database-controlled HTML text;
- sends a restrictive Content Security Policy and related browser security headers;
- has no third-party Python runtime dependencies.

A completed static-analysis result does **not** prove that an MCP server is safe or malicious. It describes one exact package artifact under the recorded analyzer and ruleset versions.

## Requirements

- Python 3.12 or newer.
- An existing `mcp-observatory` SQLite catalog using schema version 1 or 2.
- Schema version 2 is required to display static-analysis results.

## Run locally

```bash
cd ~/source/mcp-observatory-guard-portal

export MCP_PORTAL_DATABASE=/home/alex/source/mcp-observatory/db/local-registry.sqlite
export MCP_PORTAL_HOST=127.0.0.1
export MCP_PORTAL_PORT=8080

python3 -m mcp_portal
```

Open `http://127.0.0.1:8080`.

Optional configuration:

- `MCP_PORTAL_PAGE_SIZE`: server rows per page, from 1 to 100; default 50.

The development server is appropriate for local research use. Do not expose it directly to the public Internet. A later deployment milestone should place it behind an authenticated, hardened reverse proxy and use a consistently published read-only database snapshot.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

Tests build a compact temporary Observatory schema and do not use the network except for loopback HTTP requests to the local test server.

## Repository responsibilities

| Repository | Responsibility |
|---|---|
| `mcp-observatory` | Registry collection, immutable history, package acquisition, static analysis, evidence and authoritative SQLite writes |
| `mcp-native-guard` | Future bounded MCP runtime inspection, policy enforcement and runtime observations |
| `mcp-observatory-guard-portal` | Read-only browsing and, in later milestones, constrained orchestration through explicit job contracts |

## Planned next milestones

1. Consistent database publishing with SQLite backup and atomic replacement.
2. Dashboard filters and explicit daily-change views.
3. Authenticated, rate-limited analysis request queue owned by a separate portal database.
4. Evidence file serving through a strict allowlisted manifest.
5. Runtime-observation pages after the sandbox and `mcp-native-guard` integration contract exists.

## Licence

Apache License 2.0. The portal implementation is original project code based on Python and SQLite documented interfaces and the published `mcp-observatory` schema.

Before public deployment, perform manual security, privacy, licence, similarity, accessibility and legal review.
