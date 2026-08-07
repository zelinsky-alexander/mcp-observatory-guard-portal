# Storage v2 portal sidecar

The `storage-v2-mvp` branch supports an isolated read-only portal on a separate loopback port.

Common pages (`/`, `/servers`, `/coverage`, snapshots, ecosystem reports) read the compact Storage v2 hot catalog. When `MCP_PORTAL_HISTORY_DATABASE` is set, bounded server and analysis detail reads use the full history/control database instead of forcing millions of file/finding rows into the hot read model.

Install on the production host only after the Observatory Storage v2 sidecar has been prepared:

```bash
sudo MCP_PORTAL_V2_PROJECT_DIR="$PWD" \
  ./scripts/install_storage_v2_sidecar_portal.sh --start
```

Defaults:

```text
service:  mcp-portal-storage-v2.service
listen:   127.0.0.1:8081
hot DB:   /var/lib/mcp-observatory-v2/catalog/local-registry.sqlite
history:  /var/lib/mcp-observatory-v2/history/assurance-history.sqlite
```

The installer does not modify the existing public portal service, Nginx, Cloudflare, or production timers. The sidecar is installed disabled and is started only when `--start` is supplied.
