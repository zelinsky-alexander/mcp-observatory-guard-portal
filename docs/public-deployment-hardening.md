# Public deployment hardening

This branch starts the transition from loopback-only testing to a Cloudflare Tunnel plus AWS Lightsail deployment while preserving local WSL operation.

## Implemented in this slice

- Explicit application configuration for queue capacity, anonymous request windows, worker leases, retry attempts, trusted proxy handling, and the Observatory writer lock.
- A fixed-argument `scripts/observatory-locked` wrapper so refresh and analysis can share one advisory writer lock.
- SQLite-native, integrity-checked backups with a configurable target and atomic publication.
- A health command covering `/healthz`, catalog availability, stale analysis jobs, and free disk space.

## WSL backup target on Windows

A Windows directory is visible from WSL below `/mnt/<drive-letter>`. For example:

```bash
mkdir -p /mnt/c/Users/alex/McpObservatoryBackups
python3 scripts/backup_observatory.py \
  --catalog /home/alex/source/mcp-observatory/db/local-registry.sqlite \
  --jobs /home/alex/source/mcp-observatory-guard-portal/runtime/portal-jobs.sqlite \
  --evidence /home/alex/source/mcp-observatory/evidence \
  --lock /home/alex/source/mcp-observatory-guard-portal/runtime/observatory-writer.lock \
  --target /mnt/c/Users/alex/McpObservatoryBackups
```

The target is intentionally configurable. On Lightsail it should point to a mounted off-instance or synchronized backup location rather than the VM's root filesystem.

## Shared writer lock

Make the wrapper executable and configure both the portal worker and refresh task to invoke it:

```bash
chmod 0755 scripts/observatory-locked
export MCP_OBSERVATORY_REAL_BINARY=/home/alex/source/mcp-observatory/build/release/mcp-observatory
export MCP_OBSERVATORY_WRITER_LOCK=/home/alex/source/mcp-observatory-guard-portal/runtime/observatory-writer.lock
export MCP_PORTAL_OBSERVATORY_BINARY=/home/alex/source/mcp-observatory-guard-portal/scripts/observatory-locked
```

Then run refresh through the same wrapper:

```bash
scripts/observatory-locked refresh --database /path/to/local-registry.sqlite ...
```

The lock serializes Observatory database writers. It does not prevent the portal's read-only catalog queries.

## New environment controls

```bash
MCP_PORTAL_MAXIMUM_QUEUED_JOBS=100
MCP_PORTAL_REQUESTS_PER_CLIENT_WINDOW=2
MCP_PORTAL_REQUEST_WINDOW_SECONDS=3600
MCP_PORTAL_RUNNING_LEASE_SECONDS=1200
MCP_PORTAL_MAXIMUM_ATTEMPTS=2
MCP_PORTAL_TRUST_PROXY_HEADERS=0
MCP_PORTAL_OBSERVATORY_WRITER_LOCK=/path/to/observatory-writer.lock
```

`MCP_PORTAL_TRUST_PROXY_HEADERS` must remain disabled locally. It should only be enabled after the origin is reachable exclusively through a trusted Cloudflare proxy or tunnel and the application validates the expected forwarding header.

## Health check

```bash
python3 scripts/check_deployment_health.py \
  --catalog /path/to/local-registry.sqlite \
  --jobs /path/to/portal-jobs.sqlite \
  --storage /var/lib/mcp-observatory \
  --minimum-free-gib 5
```

Run it from a systemd timer. A non-zero exit should be surfaced through journald monitoring or an external uptime/alerting service.

## Service identities

Production should use separate operating-system identities:

- `mcp-portal`: portal code, read-only catalog access, queue write access, no Docker access.
- `mcp-worker`: queue access, fixed Observatory wrapper execution, evidence/catalog write access, restricted Docker access.
- `mcp-refresh`: scheduled refresh through the same writer-lock wrapper, no portal privileges.
- `mcp-backup`: read access to databases/evidence and write access only to the configured backup destination.

Do not add the public portal identity to the Docker group. Docker socket access is effectively host-root access.

## Still to implement on this branch

- Enforce the configured queue maximum and per-client request window in `JobStore.enqueue`.
- Record a normalized client key without retaining unnecessary raw address data.
- Add lease owner, lease expiry, heartbeat, and attempt columns with schema migration and expired-job recovery.
- Add portal responses for quota exhaustion (`429`) and queue saturation (`503`).
- Add systemd unit templates with hardening directives and failure hooks.
- Add tests for quota boundaries, crash recovery, lock serialization, backup restoration, and proxy-header trust.

The configuration fields are added first so subsequent queue and worker changes have a stable explicit contract.
