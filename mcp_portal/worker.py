"""Single-process worker for constrained on-demand Observatory analysis jobs."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
from typing import Any

from .analysis_catalog import (
    AnalysisSelectionError,
    resolve_candidate,
    resolve_review_candidate,
    resolve_runtime_candidate,
    resolve_runtime_result,
)
from .config import (
    AnalysisConfig,
    Config,
    ConfigurationError,
    ReviewConfig,
    RuntimeDiscoveryConfig,
)
from .jobs import JobStore, JobStoreError


class WorkerError(RuntimeError):
    """Raised when a child analysis cannot be completed safely."""


def process_next(config: Config, store: JobStore) -> bool:
    analysis = config.analysis
    if analysis is None:
        return False
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


def process_next_review(config: Config, store: JobStore) -> bool:
    review = config.review
    if review is None:
        return False
    job = store.claim_next_review()
    if job is None:
        return False

    try:
        candidate = resolve_review_candidate(
            config.database_path,
            int(job["finding_id"]),
            str(job["expected_disposition"]),
        )
        for field in ("analysis_run_id", "title", "subject_path"):
            if getattr(candidate, field) != job[field]:
                raise WorkerError(
                    f"catalog review selection changed before execution: {field}"
                )
        result = run_bounded(
            build_review_argv(config.database_path, review, job),
            timeout_seconds=review.timeout_seconds,
            maximum_output_bytes=review.maximum_output_bytes,
        )
        stdout_text = result["stdout"].decode("utf-8", errors="replace")
        stderr_text = result["stderr"].decode("utf-8", errors="replace")
        if result["timed_out"]:
            store.fail_review(
                int(job["id"]),
                error_message=f"review exceeded {review.timeout_seconds} seconds",
                return_code=result["return_code"],
                stdout_excerpt=stdout_text,
                stderr_excerpt=stderr_text,
                output_truncated=result["truncated"],
            )
            return True
        if result["return_code"] != 0:
            store.fail_review(
                int(job["id"]),
                error_message="mcp-observatory review exited non-zero",
                return_code=result["return_code"],
                stdout_excerpt=stdout_text,
                stderr_excerpt=stderr_text,
                output_truncated=result["truncated"],
            )
            return True
        if result["truncated"]:
            store.fail_review(
                int(job["id"]),
                error_message="mcp-observatory review output exceeded the portal limit",
                return_code=result["return_code"],
                stdout_excerpt=stdout_text,
                stderr_excerpt=stderr_text,
                output_truncated=True,
            )
            return True
        payload = _parse_review_result(stdout_text, job)
        store.complete_review(
            int(job["id"]),
            review_id=int(payload["review_id"]),
            return_code=int(result["return_code"]),
            stdout_excerpt=stdout_text,
            stderr_excerpt=stderr_text,
            output_truncated=result["truncated"],
        )
    except (
        AnalysisSelectionError,
        WorkerError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        store.fail_review(int(job["id"]), error_message=str(exc))
    return True


def process_next_runtime(config: Config, store: JobStore) -> bool:
    runtime = config.runtime_discovery
    if runtime is None:
        return False
    job = store.claim_next_runtime()
    if job is None:
        return False
    try:
        candidate = resolve_runtime_candidate(
            config.database_path,
            int(job["server_version_id"]),
            int(job["package_id"]),
        )
        for field in (
            "server_identifier",
            "server_version",
            "package_identifier",
            "package_version",
        ):
            if getattr(candidate, field) != job[field]:
                raise WorkerError(
                    f"catalog runtime selection changed before execution: {field}"
                )
        with _writer_lock(runtime.writer_lock_path):
            result = run_bounded(
                build_runtime_argv(config.database_path, runtime, candidate),
                timeout_seconds=runtime.timeout_seconds * 3 + 240,
                maximum_output_bytes=runtime.maximum_output_bytes,
            )
        stdout_text = result["stdout"].decode("utf-8", errors="replace")
        stderr_text = result["stderr"].decode("utf-8", errors="replace")
        if result["timed_out"]:
            store.fail_runtime(
                int(job["id"]),
                error_message="runtime discovery exceeded its total execution limit",
                return_code=result["return_code"],
                stdout_excerpt=stdout_text,
                stderr_excerpt=stderr_text,
                output_truncated=result["truncated"],
            )
            return True
        if result["return_code"] != 0 or result["truncated"]:
            message = (
                "runtime discovery output exceeded the portal limit"
                if result["truncated"]
                else "runtime discovery exited non-zero"
            )
            store.fail_runtime(
                int(job["id"]),
                error_message=message,
                return_code=result["return_code"],
                stdout_excerpt=stdout_text,
                stderr_excerpt=stderr_text,
                output_truncated=result["truncated"],
            )
            return True
        payload = _parse_runtime_result(stdout_text)
        observation = resolve_runtime_result(
            config.database_path,
            int(payload["runtime_observation_run_id"]),
            candidate.server_version_id,
            candidate.package_id,
        )
        if payload["artifact_sha256"] != observation.artifact_sha256:
            raise WorkerError("runtime result artifact digest does not match the catalog row")
        if payload["launch_profile_sha256"] != observation.launch_profile_sha256:
            raise WorkerError("runtime result profile digest does not match the catalog row")
        if payload["tool_count"] != observation.tool_count:
            raise WorkerError("runtime result tool count does not match the catalog row")
        if "sha256:" + payload["guard_sha256"] != observation.guard_version:
            raise WorkerError("runtime result guard digest does not match the catalog row")
        store.complete_runtime(
            int(job["id"]),
            runtime_observation_run_id=observation.runtime_observation_run_id,
            artifact_sha256=observation.artifact_sha256,
            launch_profile_sha256=observation.launch_profile_sha256,
            inventory_sha256=observation.inventory_sha256,
            guard_sha256=payload["guard_sha256"],
            tool_count=observation.tool_count,
            return_code=int(result["return_code"]),
            stdout_excerpt=stdout_text,
            stderr_excerpt=stderr_text,
            output_truncated=0,
        )
    except (
        AnalysisSelectionError,
        WorkerError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        store.fail_runtime(int(job["id"]), error_message=str(exc))
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


def build_review_argv(
    database_path: Path, review: ReviewConfig, job: dict[str, Any]
) -> list[str]:
    return [
        str(review.observatory_binary),
        "review",
        "finding",
        "--database",
        str(database_path),
        "--finding-id",
        str(int(job["finding_id"])),
        "--expected-disposition",
        str(job["expected_disposition"]),
        "--disposition",
        str(job["disposition"]),
        "--reviewer",
        review.reviewer,
        "--format",
        "json",
    ]


def build_runtime_argv(
    database_path: Path, runtime: RuntimeDiscoveryConfig, candidate: Any
) -> list[str]:
    return [
        sys.executable,
        str(runtime.runner_path),
        "observe",
        "--database",
        str(database_path),
        "--server",
        candidate.server_identifier,
        "--version",
        candidate.server_version,
        "--package",
        candidate.package_identifier,
        "--guard-binary",
        str(runtime.guard_binary),
        "--evidence-root",
        str(runtime.evidence_root),
        "--runtime-image",
        runtime.runtime_image,
        "--timeout",
        str(runtime.timeout_seconds),
    ]


@contextmanager
def _writer_lock(path: Path):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o640)
    try:
        with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
    except Exception:
        raise


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


def _parse_review_result(
    stdout_text: str, job: dict[str, Any]
) -> dict[str, Any]:
    payload = json.loads(stdout_text)
    if not isinstance(payload, dict) or payload.get("status") != "completed":
        raise WorkerError(
            "mcp-observatory review JSON did not report completed status"
        )
    if payload.get("finding_id") != int(job["finding_id"]):
        raise WorkerError(
            "mcp-observatory review JSON returned a different finding identifier"
        )
    if payload.get("disposition") != job["disposition"]:
        raise WorkerError(
            "mcp-observatory review JSON returned a different disposition"
        )
    review_id = payload.get("review_id")
    if not isinstance(review_id, int) or review_id <= 0:
        raise WorkerError("mcp-observatory review JSON has no valid review_id")
    return payload


def _parse_runtime_result(stdout_text: str) -> dict[str, Any]:
    payload = json.loads(stdout_text)
    if not isinstance(payload, dict) or payload.get("status") != "completed":
        raise WorkerError("runtime discovery JSON did not report completed status")
    run_id = payload.get("runtime_observation_run_id")
    if not isinstance(run_id, int) or run_id <= 0:
        raise WorkerError("runtime discovery JSON has no valid observation identifier")
    for name in (
        "artifact_sha256",
        "launch_profile_sha256",
        "guard_sha256",
    ):
        value = payload.get(name)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise WorkerError(f"runtime discovery JSON has no valid {name}")
    tool_count = payload.get("tool_count")
    if not isinstance(tool_count, int) or not 0 <= tool_count <= 256:
        raise WorkerError("runtime discovery JSON has no valid tool count")
    return payload


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Process portal static-analysis jobs")
    parser.add_argument("--once", action="store_true", help="process at most one queued job")
    options = parser.parse_args(argv)
    try:
        config = Config.from_env()
        if (
            config.analysis is None
            and config.review is None
            and config.runtime_discovery is None
        ):
            raise ConfigurationError(
                "analysis, review, or runtime discovery must be enabled for the worker"
            )
        configured_paths = {
            item.jobs_database_path
            for item in (config.analysis, config.review, config.runtime_discovery)
            if item is not None
        }
        if len(configured_paths) != 1:
            raise ConfigurationError(
                "analysis, review, and runtime discovery must use the same portal job database"
            )
        jobs_path = configured_paths.pop()
        store = JobStore(jobs_path)
    except (ConfigurationError, JobStoreError) as exc:
        print(f"worker startup failed: {exc}", file=sys.stderr)
        return 2

    while True:
        try:
            processed = process_next(config, store)
            if not processed:
                processed = process_next_runtime(config, store)
            if not processed:
                processed = process_next_review(config, store)
        except (WorkerError, JobStoreError) as exc:
            print(f"worker failed: {exc}", file=sys.stderr)
            return 3
        if options.once:
            return 0
        if not processed:
            enabled = next(
                item
                for item in (
                    config.analysis,
                    config.runtime_discovery,
                    config.review,
                )
                if item is not None
            )
            poll_seconds = enabled.poll_seconds
            time.sleep(poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
