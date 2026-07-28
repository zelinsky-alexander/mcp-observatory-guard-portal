"""Single-process worker for constrained on-demand Observatory analysis jobs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
from typing import Any

from .analysis_catalog import AnalysisSelectionError, resolve_candidate
from .config import AnalysisConfig, Config, ConfigurationError
from .jobs import JobStore, JobStoreError


class WorkerError(RuntimeError):
    """Raised when a child analysis cannot be completed safely."""


def process_next(config: Config, store: JobStore) -> bool:
    analysis = config.analysis
    if analysis is None:
        raise WorkerError("on-demand analysis is not enabled")
    job = store.claim_next()
    if job is None:
        return False

    try:
        candidate = resolve_candidate(
            config.database_path, int(job["server_version_id"]), int(job["package_id"])
        )
        for field in (
            "server_identifier",
            "server_version",
            "package_identifier",
            "package_version",
        ):
            if getattr(candidate, field) != job[field]:
                raise WorkerError(f"catalog selection changed before execution: {field}")

        argv = build_argv(config.database_path, analysis, candidate)
        result = run_bounded(
            argv,
            timeout_seconds=analysis.timeout_seconds,
            maximum_output_bytes=analysis.maximum_output_bytes,
        )
        stdout_text = result["stdout"].decode("utf-8", errors="replace")
        stderr_text = result["stderr"].decode("utf-8", errors="replace")
        if result["timed_out"]:
            store.fail(
                int(job["id"]),
                error_message=f"analysis exceeded {analysis.timeout_seconds} seconds",
                return_code=result["return_code"],
                stdout_excerpt=stdout_text,
                stderr_excerpt=stderr_text,
                output_truncated=result["truncated"],
            )
            return True
        if result["return_code"] != 0:
            store.fail(
                int(job["id"]),
                error_message="mcp-observatory analysis exited non-zero",
                return_code=result["return_code"],
                stdout_excerpt=stdout_text,
                stderr_excerpt=stderr_text,
                output_truncated=result["truncated"],
            )
            return True

        payload = _parse_result(stdout_text)
        store.complete(
            int(job["id"]),
            analysis_run_id=int(payload["analysis_run_id"]),
            artifact_sha256=_optional_string(payload.get("artifact_sha256")),
            reused_existing=bool(payload.get("reused_existing", False)),
            return_code=int(result["return_code"]),
            stdout_excerpt=stdout_text,
            stderr_excerpt=stderr_text,
            output_truncated=result["truncated"],
        )
    except (AnalysisSelectionError, WorkerError, OSError, ValueError, json.JSONDecodeError) as exc:
        store.fail(int(job["id"]), error_message=str(exc))
    return True


def build_argv(database_path: Path, analysis: AnalysisConfig, candidate: Any) -> list[str]:
    return [
        str(analysis.observatory_binary),
        "analyze",
        "package",
        "--database",
        str(database_path),
        "--server",
        candidate.server_identifier,
        "--version",
        candidate.server_version,
        "--package",
        candidate.package_identifier,
        "--rules",
        str(analysis.rules_path),
        "--evidence-root",
        str(analysis.evidence_root),
        "--format",
        "json",
    ]


def run_bounded(
    argv: list[str], *, timeout_seconds: int, maximum_output_bytes: int
) -> dict[str, Any]:
    environment = _minimal_environment()
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        close_fds=True,
        start_new_session=True,
        env=environment,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = False
    deadline = time.monotonic() + timeout_seconds
    timed_out = False

    try:
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _terminate_group(process)
                break
            events = selector.select(timeout=min(0.25, remaining))
            for key, _ in events:
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                target = buffers[key.data]
                available = maximum_output_bytes - len(target)
                if available > 0:
                    target.extend(chunk[:available])
                if len(chunk) > available:
                    truncated = True
        if process.poll() is None:
            process.wait(timeout=3)
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if not stream.closed:
                stream.close()

    return {
        "return_code": process.returncode if process.returncode is not None else -1,
        "stdout": bytes(buffers["stdout"]),
        "stderr": bytes(buffers["stderr"]),
        "truncated": truncated,
        "timed_out": timed_out,
    }


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=3)


def _minimal_environment() -> dict[str, str]:
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
    }
    for name in (
        "DOCKER_HOST",
        "XDG_RUNTIME_DIR",
        "TMPDIR",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    ):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _parse_result(stdout_text: str) -> dict[str, Any]:
    payload = json.loads(stdout_text)
    if not isinstance(payload, dict):
        raise WorkerError("mcp-observatory JSON output is not an object")
    if payload.get("status") != "completed":
        raise WorkerError("mcp-observatory JSON output did not report completed status")
    run_id = payload.get("analysis_run_id")
    if not isinstance(run_id, int) or run_id <= 0:
        raise WorkerError("mcp-observatory JSON output has no valid analysis_run_id")
    return payload


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process portal static-analysis jobs")
    parser.add_argument("--once", action="store_true", help="process at most one queued job")
    options = parser.parse_args(argv)
    try:
        config = Config.from_env()
        if config.analysis is None:
            raise ConfigurationError("MCP_PORTAL_ENABLE_ANALYSIS must be enabled for the worker")
        store = JobStore(config.analysis.jobs_database_path)
    except (ConfigurationError, JobStoreError) as exc:
        print(f"worker startup failed: {exc}", file=sys.stderr)
        return 2

    while True:
        try:
            processed = process_next(config, store)
        except (WorkerError, JobStoreError) as exc:
            print(f"worker failed: {exc}", file=sys.stderr)
            return 3
        if options.once:
            return 0
        if not processed:
            time.sleep(config.analysis.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
