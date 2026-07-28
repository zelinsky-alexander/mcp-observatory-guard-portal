# Architecture

## Trust boundaries

```text
Browser
  |
  | GET / HEAD only
  v
Portal HTTP process
  |
  | parameterized read-only SQL
  v
Published Observatory SQLite catalog

mcp-observatory writer ---> authoritative catalog and evidence
mcp-native-guard -------> future versioned runtime observations
```

The portal does not own Registry records, package-analysis records or runtime observations. It renders authoritative data produced by the other repositories.

## Read model

This scaffold can read the active Observatory database directly for local development. A public or concurrent deployment should not do that. The planned publisher should use SQLite's backup API to create a consistent temporary copy, validate the schema, and atomically replace the portal's published read model.

## Routes

- `/` dashboard.
- `/servers` searchable, paginated server identifiers.
- `/servers/{identifier}` immutable versions and related records.
- `/analyses/{id}` static-analysis findings and evidence manifest.
- `/healthz` read-only schema check.
- `/static/portal.css` fixed stylesheet.

## Future write path

On-demand analysis will be implemented later through a separate portal-owned queue database and worker. Browser input will select existing internal IDs only. The worker will resolve trusted values from the Observatory catalog and invoke a fixed executable with an argument array and `shell=false`.
