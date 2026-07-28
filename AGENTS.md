# Repository guidance

## Scope

This repository is the presentation and constrained orchestration layer for MCP Observatory research data.

- Do not reimplement Registry collection or package analysis from `mcp-observatory`.
- Do not reimplement MCP enforcement or runtime inspection from `mcp-native-guard`.
- Treat the Observatory SQLite schema and portal job schema as explicit integration contracts.

## Current milestone

The default application remains read-only. Optional on-demand static analysis is local-only and queue-based.

- Open the Observatory catalog with SQLite read-only and query-only settings.
- Store job lifecycle state only in the separate portal-owned database.
- HTTP may submit existing internal IDs only; never accept arbitrary package names, paths, URLs, commands, or CLI flags.
- Require HMAC CSRF validation and same-origin checks for analysis submission.
- Never execute analysis inline in the HTTP request thread.
- The worker must use a fixed executable, an argument list, `shell=False`, bounded output, a timeout, and process-group termination.
- Do not expose `--force` through the portal.
- Escape all catalog, job, and child-process text before inserting it into HTML.
- Preserve the terminology boundary between observation, finding, review disposition, and safety verdict.

## Engineering

- Python 3.12 or newer.
- Prefer the standard library until a dependency has a documented purpose, compatible licence, maintenance assessment, and security review.
- Keep tests offline except for loopback traffic.
- Bound query text, page sizes, form bodies, identifiers, output capture, and execution time.
- Use parameterized SQL only.
- Keep changes small and independently reviewable.

## Validation

Run:

```bash
python3 -m compileall -q mcp_portal tests
python3 -m unittest discover -s tests -v
```
