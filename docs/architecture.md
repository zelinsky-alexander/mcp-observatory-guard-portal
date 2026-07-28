# Architecture

## Trust boundaries

```text
Browser on loopback
  |
  | GET/HEAD; optional CSRF-protected POST of internal IDs only
  v
Portal HTTP process
  |                         |
  | read-only SQL           | queue rows only
  v                         v
Observatory catalog      portal-jobs.sqlite
                              |
                              | separate worker claims one job
                              v
                    fixed mcp-observatory argv
                              |
                              v
                   Observatory static analyzer
                              |
                              +--> authoritative analysis rows
                              +--> immutable evidence directory
```

`mcp-observatory` remains the sole owner of Registry and package-analysis data. The portal owns only request lifecycle state. `mcp-native-guard` remains the future runtime sensor.

## Analysis request contract

The browser never supplies package names, versions, URLs, paths, commands, or CLI flags. It submits:

- `server_version_id`;
- `package_id`;
- an HMAC CSRF token bound to those two IDs.

The portal resolves the pair from the read-only Observatory catalog, verifies that the package belongs to the server record, requires npm plus an exact declared version, and copies the resolved identity into the portal-owned queue. A partial unique index prevents a second queued or running job for the same pair.

The worker re-resolves the IDs and compares the stored identity before execution. It then constructs a fixed argument vector for `mcp-observatory analyze package`. `shell=True`, arbitrary flags, and `--force` are not supported.

## Process model

The HTTP server does not run analysis inline. A separate worker:

1. atomically claims the oldest queued job;
2. validates the catalog identity again;
3. launches `mcp-observatory` in a new process group;
4. drains stdout and stderr while retaining only configured bounded excerpts;
5. terminates the process group after the configured timeout;
6. validates the JSON result and records `analysis_run_id`;
7. marks the portal job completed or failed.

The Observatory CLI remains responsible for package download, restricted Docker static inspection, evidence validation, and authoritative catalog writes.

## Databases

- Observatory SQLite database: opened using URI `mode=ro` and `PRAGMA query_only=ON` by the portal and selection resolver.
- Portal jobs SQLite database: writable only by the portal and worker. It contains no authoritative security findings.

A public or concurrent deployment should browse a consistently published SQLite backup rather than the active Observatory database.

## Routes

- `/` dashboard.
- `/servers` searchable, paginated server identifiers.
- `/servers/{identifier}` immutable versions, packages, and eligible analysis forms.
- `POST /analysis-requests` constrained queue submission when enabled.
- `/jobs` portal queue.
- `/jobs/{id}` job status and bounded output.
- `/analyses/{id}` authoritative Observatory analysis.
- `/healthz` catalog schema check.
- `/static/portal.css` fixed stylesheet.

## Current deployment boundary

Analysis-enabled mode is rejected unless the portal binds to loopback. This is not yet an Internet-facing job API. Authentication, authorization, rate limiting, reverse-proxy hardening, and published read-model rotation are later milestones.
