"""Bounded access to Observatory-owned finding source evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import EvidenceConfig
from .worker import run_bounded


class EvidenceError(RuntimeError):
    """Raised when finalized source evidence cannot be displayed safely."""


def read_finding_source(
    database_path: Path, config: EvidenceConfig, finding_id: int
) -> dict[str, Any]:
    if finding_id <= 0:
        raise EvidenceError("finding identifier must be positive")
    argv = [
        str(config.observatory_binary),
        "evidence",
        "finding-source",
        "--database",
        str(database_path),
        "--evidence-root",
        str(config.evidence_root),
        "--finding-id",
        str(finding_id),
        "--format",
        "json",
    ]
    result = run_bounded(
        argv,
        timeout_seconds=config.timeout_seconds,
        maximum_output_bytes=config.maximum_output_bytes,
    )
    stderr = result["stderr"].decode("utf-8", errors="replace")
    if result["timed_out"]:
        raise EvidenceError("finding source lookup timed out")
    if result["truncated"]:
        raise EvidenceError("finding source output exceeded the portal limit")
    if result["return_code"] != 0:
        detail = stderr.strip()[:1000]
        raise EvidenceError(detail or "finding source lookup failed")
    try:
        payload = json.loads(result["stdout"].decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("finding source returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("status") != "completed":
        raise EvidenceError("finding source did not report completed status")
    if payload.get("finding_id") != finding_id:
        raise EvidenceError("finding source returned a different finding identifier")
    for name in ("analysis_run_id", "byte_size", "displayed_byte_size"):
        if not isinstance(payload.get(name), int) or payload[name] < 0:
            raise EvidenceError(f"finding source returned invalid {name}")
    if (
        not isinstance(payload.get("start_line"), int)
        or payload["start_line"] <= 0
    ):
        raise EvidenceError("finding source returned invalid start_line")
    for name in (
        "truncated_before",
        "truncated_after",
        "starts_mid_line",
        "ends_mid_line",
    ):
        if type(payload.get(name)) is not bool:
            raise EvidenceError(f"finding source returned invalid {name}")
    for name in ("subject_path", "sha256", "content"):
        if not isinstance(payload.get(name), str):
            raise EvidenceError(f"finding source returned invalid {name}")
    line_number = payload.get("line_number")
    if line_number is not None and (
        not isinstance(line_number, int) or line_number <= 0
    ):
        raise EvidenceError("finding source returned invalid line_number")
    content_bytes = payload["content"].encode("utf-8")
    if len(content_bytes) != payload["displayed_byte_size"]:
        raise EvidenceError("finding source content size does not match its metadata")
    if payload["displayed_byte_size"] > payload["byte_size"]:
        raise EvidenceError("finding source window exceeds the verified file size")
    if len(payload["sha256"]) != 64 or any(
        character not in "0123456789abcdef" for character in payload["sha256"]
    ):
        raise EvidenceError("finding source returned invalid SHA-256 metadata")
    return payload


def download_finding_source(
    database_path: Path, config: EvidenceConfig, finding_id: int
) -> bytes:
    if finding_id <= 0:
        raise EvidenceError("finding identifier must be positive")
    argv = [
        str(config.observatory_binary),
        "evidence",
        "finding-source",
        "--database",
        str(database_path),
        "--evidence-root",
        str(config.evidence_root),
        "--finding-id",
        str(finding_id),
        "--format",
        "raw",
    ]
    result = run_bounded(
        argv,
        timeout_seconds=config.timeout_seconds,
        maximum_output_bytes=config.maximum_download_bytes,
    )
    stderr = result["stderr"].decode("utf-8", errors="replace")
    if result["timed_out"]:
        raise EvidenceError("finding source download timed out")
    if result["truncated"]:
        raise EvidenceError("finding source exceeds the portal download limit")
    if result["return_code"] != 0:
        detail = stderr.strip()[:1000]
        raise EvidenceError(detail or "finding source download failed")
    return result["stdout"]
