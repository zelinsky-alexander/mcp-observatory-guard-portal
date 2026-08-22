# Runtime discovery UI v1

Runtime discovery has two deliberately separate presentation paths:

1. private/local on-demand orchestration through the existing durable portal queue;
2. public read-only coverage and tool-definition drift derived only from observations
   already published in the hot catalog.

Neither path runs analysis inline in an HTTP request.

## Private/local on-demand discovery

Runtime discovery is exposed through the existing loopback-only portal and its
durable worker queue. It does not run a second HTTP process.

```bash
export MCP_PORTAL_DATABASE=/home/alex/source/mcp-observatory/db/local-registry.sqlite
export MCP_PORTAL_ENABLE_RUNTIME_DISCOVERY=1
export MCP_PORTAL_JOBS_DATABASE=/home/alex/source/mcp-observatory-guard-portal/runtime/portal-jobs.sqlite
export MCP_PORTAL_RUNTIME_DISCOVERY_RUNNER=/home/alex/source/mcp-observatory/tools/runtime_discovery.py
export MCP_PORTAL_NATIVE_GUARD_BINARY=/home/alex/source/mcp-native-guard/build/release/mcp-native-guard
export MCP_PORTAL_EVIDENCE_ROOT=/home/alex/source/mcp-observatory/evidence
export MCP_PORTAL_RUNTIME_IMAGE=node:22-bookworm-slim
export MCP_PORTAL_RUNTIME_TIMEOUT_SECONDS=240
python3 -m mcp_portal.worker
```

Run the portal normally and open the server detail page at
`http://127.0.0.1:8080`.

The page lists eligible exact npm stdio package records, submits only internal
`server_version_id` and `package_id` pairs protected by an HMAC CSRF token,
re-resolves the selection before queueing and again in the worker, and constructs a
fixed argument vector for the Observatory runtime-discovery runner. HTTP clients
cannot supply commands, package URLs, image names, paths, timeouts, or Docker flags.

The HTTP request returns after queueing. The worker bounds child output, applies a
total timeout, terminates the process group, and verifies the resulting authoritative
observation row before completing the portal-owned job. Non-loopback deployment still
requires authentication, enforced rate limits, leases, and worker-host isolation.

## Public automatic coverage

When Storage v2 publishes `runtime_discovery_schedule_*`,
`runtime_observation_runs`, and `runtime_observation_tools` into the hot read model,
public mode exposes only bounded read-only summaries:

- `/coverage` shows eligible, completed, failed, unsupported/unresolvable,
  never-attempted, running, comparable, and drifted observation counts for the
  current scheduler profile;
- `/runtime-drift` lists completed observations whose canonical interface changed
  from the previous compatible server version;
- `/runtime-drift/<newer-run-id>` shows added, removed, and modified tool names plus
  bounded definition-hash identities and observation provenance.

These routes never open the portal jobs database, never read runtime evidence files,
and never enable a public execution or review endpoint. The existing raw
`/runtime-observations/<id>` route remains unavailable in public-readonly mode.

A modified tool means the complete canonical tool object differs. Runtime discovery
uses `initialize`, `notifications/initialized`, and `tools/list` only; it does not
invoke tools and does not establish that a server is safe or malicious.
