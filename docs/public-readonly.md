# Public read-only mode

`public-readonly` is the only application mode intended for an unauthenticated
public endpoint. Enable it explicitly:

```bash
export MCP_PORTAL_MODE=public-readonly
export MCP_PORTAL_DATABASE=/srv/mcp-portal-public/catalog.sqlite
export MCP_PORTAL_HOST=127.0.0.1
export MCP_PORTAL_PORT=8080
python3 -m mcp_portal
```

The example binds to loopback for publication through Nginx. A direct
non-loopback bind is accepted only in `public-readonly` mode, but a hardened
reverse proxy remains recommended.

## Capability boundary

Public mode permits GET and HEAD browsing of:

- catalog servers, versions, package declarations, remotes, and repositories;
- imported snapshot and analysis history;
- findings, dispositions, paths, hashes, evidence-manifest metadata, and
  provenance recorded in the catalog;
- dedicated public finding excerpts explicitly marked eligible during analysis
  or review, escaped and bounded to 2,048 characters; and
- the About, Methodology, Data Sources, Disclaimer, Privacy, and Corrections
  pages.

Public mode does not initialize the portal jobs database. It returns `405` for
POST, PUT, PATCH, and DELETE, and makes local job pages, runtime observations,
finding source views, and complete source downloads unavailable. Analysis,
evidence viewing, review, or runtime-discovery feature flags conflict with this
mode and abort startup.

The public-excerpt catalog contract consists of `public_excerpt`,
`public_excerpt_eligible`, and `public_excerpt_reason` on `analysis_findings`.
The portal displays the bounded `public_excerpt` only when eligibility is
exactly `1`. If any contract column is absent, or eligibility is not approved,
the portal fails closed and displays no excerpt. It never derives an excerpt or
eligibility from the private `evidence` field.

The Observatory database is still opened with SQLite `mode=ro` and
`PRAGMA query_only=ON`. Publish a dedicated catalog copy owned by the data
publisher and readable, but not writable, by the portal identity. Do not grant
that identity access to the jobs database, evidence root, analyzer executables,
container runtime, writer lock, or refresh credentials.

## Deployment examples

- [`deploy/systemd/mcp-portal-public.service`](../deploy/systemd/mcp-portal-public.service)
  runs the application as an unprivileged user with a read-only filesystem and
  makes the Observatory working data and portal queues inaccessible.
- [`deploy/systemd/public-readonly.env.example`](../deploy/systemd/public-readonly.env.example)
  contains the complete intended public environment surface.
- [`deploy/nginx/mcp-portal-public.conf`](../deploy/nginx/mcp-portal-public.conf)
  terminates TLS, accepts GET/HEAD only, strips client-address forwarding, bounds
  request bodies and timeouts, and applies a per-address request rate.

Review paths, service users, TLS settings, logging, retention, and distribution
permissions for the target host before installation. Validate with
`systemd-analyze security mcp-portal-public.service` and `nginx -t`. The example
does not replace host patching, firewalling, monitoring, backups, or legal and
privacy review.
