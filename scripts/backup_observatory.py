#!/usr/bin/env python3
"""Create verified SQLite backups and copy evidence to a configurable target."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sqlite_backup(source: Path, destination: Path) -> None:
    source_uri = f"file:{source}?mode=ro"
    with sqlite3.connect(source_uri, uri=True) as source_db:
        integrity = source_db.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise RuntimeError(f"integrity check failed for {source}: {integrity}")
        with sqlite3.connect(destination) as target_db:
            source_db.backup(target_db)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--keep", type=int, default=14)
    args = parser.parse_args()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target_root = args.target.expanduser().resolve()
    target_root.mkdir(parents=True, exist_ok=True)
    staging = target_root / f".{timestamp}.partial"
    final = target_root / timestamp
    staging.mkdir()

    lock_fd = os.open(args.lock.expanduser().resolve(), os.O_CREAT | os.O_RDWR, 0o640)
    try:
        with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            catalog_copy = staging / "local-registry.sqlite"
            jobs_copy = staging / "portal-jobs.sqlite"
            sqlite_backup(args.catalog.expanduser().resolve(), catalog_copy)
            sqlite_backup(args.jobs.expanduser().resolve(), jobs_copy)
            shutil.copytree(
                args.evidence.expanduser().resolve(), staging / "evidence", symlinks=False
            )

        manifest = {
            "created_at": timestamp,
            "catalog_sha256": sha256(catalog_copy),
            "jobs_sha256": sha256(jobs_copy),
            "evidence_files": sum(1 for path in (staging / "evidence").rglob("*") if path.is_file()),
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        staging.rename(final)

        backups = sorted(
            (path for path in target_root.iterdir() if path.is_dir() and not path.name.startswith(".")),
            reverse=True,
        )
        for old in backups[max(1, args.keep) :]:
            shutil.rmtree(old)
        print(final)
        return 0
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
