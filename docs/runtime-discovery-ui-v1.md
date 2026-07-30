# Runtime discovery UI v1

The experimental runtime-discovery interface runs as a separate loopback-only
HTTP process while the runtime schema and queue contract stabilize.

```bash
export MCP_PORTAL_DATABASE=/home/alex/source/mcp-observatory/db/local-registry.sqlite
export MCP_PORTAL_RUNTIME_DISCOVERY_RUNNER=/home/alex/source/mcp-observatory/tools/runtime_discovery.py
export MCP_PORTAL_NATIVE_GUARD_BINARY=/home/alex/source/mcp-native-guard/build/release/mcp-native-guard
export MCP_PORTAL_EVIDENCE_ROOT=/home/alex/source/mcp-observatory/evidence
export MCP_PORTAL_RUNTIME_IMAGE=node:22-bookworm-slim
export MCP_PORTAL_RUNTIME_HOST=127.0.0.1
export MCP_PORTAL_RUNTIME_PORT=8081
export MCP_PORTAL_RUNTIME_TIMEOUT_SECONDS=240
python3 -m mcp_portal.runtime_dashboard
```

Open `http://127.0.0.1:8081`.

The page lists eligible exact npm stdio package records, submits only internal
`server_version_id` and `package_id` pairs protected by an HMAC CSRF token,
re-resolves the selection before execution, and constructs a fixed argument vector
for the Observatory runtime-discovery runner. HTTP clients cannot supply commands,
package URLs, image names, paths, or Docker flags.

This first UI intentionally executes one request synchronously and is restricted to
loopback. Before non-loopback deployment it must be moved into the existing portal
job queue with authentication, rate limits, leases, and worker-host isolation.
