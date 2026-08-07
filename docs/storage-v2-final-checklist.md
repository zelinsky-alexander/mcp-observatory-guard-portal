# Storage v2 portal pre-PR checklist

- Common routes use the compact hot catalog.
- Coverage is read from materialized v2 counters.
- Dashboard recent-analysis counts come from v2 run summaries.
- Optional bounded detail reads use the separate full history DB.
- Sidecar binds only to loopback port 8081 by default.
- No Nginx, Cloudflare, or existing portal service change is included.
