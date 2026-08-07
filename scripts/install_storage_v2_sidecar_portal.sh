#!/usr/bin/env bash
set -euo pipefail

# Install an isolated local-only portal service for Storage v2 validation.
# It deliberately does not modify the existing mcp-portal-public service,
# Nginx, Cloudflare, or any production timer.

portal_dir="${MCP_PORTAL_V2_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
hot_db="${MCP_PORTAL_V2_DATABASE:-/var/lib/mcp-observatory-v2/catalog/local-registry.sqlite}"
port="${MCP_PORTAL_V2_PORT:-8081}"
unit="${MCP_PORTAL_V2_UNIT:-mcp-portal-storage-v2.service}"
unit_path="/etc/systemd/system/$unit"

usage() {
  cat <<'EOF'
Usage: sudo ./scripts/install_storage_v2_sidecar_portal.sh [--start]

Environment overrides:
  MCP_PORTAL_V2_PROJECT_DIR   portal checkout on the storage-v2-mvp branch
  MCP_PORTAL_V2_DATABASE      compact hot catalog
  MCP_PORTAL_V2_PORT          loopback test port (default 8081)
  MCP_PORTAL_V2_UNIT          systemd unit name

The service is installed disabled by default. Pass --start to start it now.
EOF
}

start=0
case "${1:-}" in
  "") ;;
  --start) start=1 ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 2; }
[[ -d "$portal_dir/mcp_portal" ]] || { echo "portal package not found: $portal_dir" >&2; exit 2; }
[[ -f "$hot_db" ]] || { echo "Storage v2 hot catalog not found: $hot_db" >&2; exit 2; }
[[ "$port" =~ ^[0-9]+$ ]] && (( port >= 1024 && port <= 65535 )) || {
  echo "invalid sidecar port: $port" >&2; exit 2;
}
id mcp-portal >/dev/null 2>&1 || { echo "mcp-portal user does not exist" >&2; exit 2; }
getent group mcp-catalog >/dev/null 2>&1 || { echo "mcp-catalog group does not exist" >&2; exit 2; }

cat >"$unit_path" <<EOF
[Unit]
Description=Open MCP Longitudinal Assurance Storage v2 sidecar portal
After=network.target

[Service]
Type=simple
User=mcp-portal
Group=mcp-portal
SupplementaryGroups=mcp-catalog
WorkingDirectory=$portal_dir
Environment=MCP_PORTAL_DATABASE=$hot_db
Environment=MCP_PORTAL_HOST=127.0.0.1
Environment=MCP_PORTAL_PORT=$port
Environment=MCP_PORTAL_PAGE_SIZE=50
Environment=MCP_PORTAL_MODE=public-readonly
Environment=MCP_PORTAL_ENABLE_ANALYSIS=0
Environment=MCP_PORTAL_ENABLE_EVIDENCE_VIEW=0
Environment=MCP_PORTAL_ENABLE_REVIEW=0
Environment=MCP_PORTAL_ENABLE_RUNTIME_DISCOVERY=0
ExecStart=/usr/bin/python3 -u -m mcp_portal
Restart=on-failure
RestartSec=2s
UMask=0027

NoNewPrivileges=yes
PrivateDevices=yes
PrivateTmp=yes
ProtectClock=yes
ProtectControlGroups=yes
ProtectHome=yes
ProtectHostname=yes
ProtectKernelLogs=yes
ProtectKernelModules=yes
ProtectKernelTunables=yes
ProtectSystem=strict
ReadOnlyPaths=$hot_db
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=multi-user.target
EOF

systemd-analyze verify "$unit_path"
systemctl daemon-reload
systemctl disable "$unit" >/dev/null 2>&1 || true

if (( start )); then
  systemctl restart "$unit"
  systemctl --no-pager --full status "$unit"
fi

cat <<EOF
Storage v2 sidecar portal unit installed: $unit
URL: http://127.0.0.1:$port
Database: $hot_db

The existing public portal and reverse proxy were not modified.
EOF
