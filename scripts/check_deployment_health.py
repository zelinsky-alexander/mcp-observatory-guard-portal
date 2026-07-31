#!/usr/bin/env python3
"""Fail with a concise diagnostic when the local deployment needs attention."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3
import sys
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--health-url", default="http://127.0.0.1:8080/healthz")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--storage", type=Path, required=True)
    parser.add_argument("--minimum-free-gib", type=float, default=5.0)
    parser.add_argument("--maximum-running-minutes", type=int, default=30)
    args = parser.parse_args()

    errors: list[str] = []
    try:
        with urllib.request.urlopen(args.health_url, timeout=5) as response:
            if response.status != 200 or response.read(16).strip() != b"ok":
                errors.append(f"portal health returned HTTP {response.status}")
    except Exception as exc:
        errors.append(f"portal health request failed: {exc}")

    free = shutil.disk_usage(args.storage).free / (1024 ** 3)
    if free < args.minimum_free_gib:
        errors.append(f"low disk space: {free:.2f} GiB free")

    try:
        with sqlite3.connect(f"file:{args.catalog.resolve()}?mode=ro", uri=True) as db:
            result = db.execute("PRAGMA quick_check").fetchone()
            if result is None or result[0] != "ok":
                errors.append(f"catalog quick_check failed: {result}")
    except sqlite3.Error as exc:
        errors.append(f"catalog unavailable: {exc}")

    try:
        threshold = datetime.now(timezone.utc).timestamp() - args.maximum_running_minutes * 60
        with sqlite3.connect(f"file:{args.jobs.resolve()}?mode=ro", uri=True) as db:
            rows = db.execute(
                """SELECT 'analysis',COUNT(*) FROM analysis_jobs
                   WHERE status='running' AND started_at IS NOT NULL
                     AND CAST(strftime('%s',started_at) AS INTEGER) < ?
                   UNION ALL SELECT 'review',COUNT(*) FROM review_jobs
                   WHERE status='running' AND started_at IS NOT NULL
                     AND CAST(strftime('%s',started_at) AS INTEGER) < ?
                   UNION ALL SELECT 'runtime-discovery',COUNT(*)
                   FROM runtime_discovery_jobs
                   WHERE status='running' AND started_at IS NOT NULL
                     AND CAST(strftime('%s',started_at) AS INTEGER) < ?""",
                (int(threshold), int(threshold), int(threshold)),
            ).fetchall()
            for label, stale in rows:
                if stale:
                    errors.append(
                        f"{stale} {label} job(s) have been running too long"
                    )
    except sqlite3.Error as exc:
        errors.append(f"job queue unavailable: {exc}")

    if errors:
        for error in errors:
            print(f"CRITICAL: {error}", file=sys.stderr)
        return 1
    print(f"OK: portal, databases, queue and disk are healthy; free_gib={free:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
