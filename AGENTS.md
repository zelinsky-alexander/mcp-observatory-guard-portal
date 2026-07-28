# Repository guidance

## Scope

This repository is the presentation and orchestration layer for MCP Observatory research data.

- Do not reimplement Registry collection or package analysis from `mcp-observatory`.
- Do not reimplement MCP enforcement or runtime inspection from `mcp-native-guard`.
- Treat the Observatory SQLite schema and future versioned job/observation documents as explicit integration contracts.

## Current milestone

The application is read-only.

- Only `GET` and `HEAD` routes are allowed.
- Open the Observatory catalog with SQLite read-only and query-only settings.
- Never execute shell commands, packages, MCP servers, or analysis jobs from an HTTP request.
- Escape all text derived from the catalog before inserting it into HTML.
- Preserve the terminology boundary between observation, finding, review disposition and safety verdict.

## Engineering

- Python 3.12 or newer.
- Prefer the standard library until a dependency has a documented purpose, compatible licence, maintenance assessment and security review.
- Keep tests offline except for loopback traffic.
- Bound query text, page sizes, identifiers and response work.
- Use parameterized SQL only.
- Keep changes small and independently reviewable.

## Validation

Run:

```bash
python3 -m unittest discover -s tests -v
```
