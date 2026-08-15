# Production v2 release layout

Production deploys all three repositories from `main` into immutable release directories and exposes exactly one active release through `current` symlinks:

- `/opt/mcp-observatory/releases/<release>` → `/opt/mcp-observatory/current`
- `/opt/mcp-native-guard/releases/<release>` → `/opt/mcp-native-guard/current`
- `/opt/mcp-observatory-guard-portal/releases/<release>` → `/opt/mcp-observatory-guard-portal/current`

Persistent Storage v2 state remains under `/var/lib/mcp-observatory-v2` and is never stored inside a Git checkout.

The production services remain the Storage v2 units:

- `mcp-observatory-v2-refresh.service` / `.timer`
- `mcp-observatory-v2-static-analysis.service` / `.timer`
- `mcp-portal-storage-v2.service`

The upgrade script clones exact `main` commits into new release directories, builds/tests before downtime, switches `current` symlinks atomically, normalizes existing v2 systemd service paths to the canonical `current` locations, restarts the v2 stack, performs loopback smoke tests, and retains a bounded number of old releases for rollback.

Old migration working trees such as `/opt/mcp-storage-v2-test` and duplicate source clones under `/home/ubuntu` are not part of the canonical production layout and may be removed after a successful migration and verification.
