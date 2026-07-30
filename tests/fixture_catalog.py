"""Create a compact Observatory schema-v2 fixture for offline tests."""

from __future__ import annotations

from pathlib import Path
import sqlite3


SCHEMA = """
CREATE TABLE schema_info(singleton INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL, creating_program_version TEXT, search_mode TEXT NOT NULL);
CREATE TABLE snapshots(id INTEGER PRIMARY KEY, snapshot_sha256 TEXT NOT NULL UNIQUE, completed_at TEXT NOT NULL, started_at TEXT, registry_base_url TEXT NOT NULL, collector_name TEXT, collector_version TEXT, collector_git_commit TEXT, bundle_version INTEGER NOT NULL, source_bundle_path TEXT NOT NULL, pages INTEGER NOT NULL, records_received INTEGER NOT NULL, unique_server_versions INTEGER NOT NULL, imported_at TEXT NOT NULL);
CREATE TABLE server_versions(id INTEGER PRIMARY KEY, server_identifier TEXT NOT NULL, server_version TEXT NOT NULL, description TEXT, registry_status TEXT, published_at TEXT, updated_at TEXT, canonical_sha256 TEXT NOT NULL, canonical_json TEXT NOT NULL, UNIQUE(server_identifier, server_version, canonical_sha256));
CREATE TABLE snapshot_server_versions(snapshot_id INTEGER NOT NULL, server_version_id INTEGER NOT NULL, PRIMARY KEY(snapshot_id, server_version_id));
CREATE TABLE repositories(server_version_id INTEGER PRIMARY KEY, source TEXT, url TEXT, scheme TEXT, host TEXT, owner TEXT, repository_name TEXT);
CREATE TABLE packages(id INTEGER PRIMARY KEY, server_version_id INTEGER NOT NULL, position INTEGER NOT NULL, registry_type TEXT NOT NULL, identifier TEXT NOT NULL, version TEXT, transport TEXT NOT NULL, UNIQUE(server_version_id, position));
CREATE TABLE package_arguments(package_id INTEGER NOT NULL, position INTEGER NOT NULL, argument_value TEXT, PRIMARY KEY(package_id, position));
CREATE TABLE package_environment(package_id INTEGER NOT NULL, position INTEGER NOT NULL, name TEXT NOT NULL, required INTEGER NOT NULL, description TEXT, PRIMARY KEY(package_id, position));
CREATE TABLE remotes(id INTEGER PRIMARY KEY, server_version_id INTEGER NOT NULL, position INTEGER NOT NULL, url TEXT NOT NULL, scheme TEXT, host TEXT, port INTEGER, transport TEXT NOT NULL, UNIQUE(server_version_id, position));
CREATE TABLE analysis_runs(id INTEGER PRIMARY KEY, server_version_id INTEGER NOT NULL, package_id INTEGER NOT NULL, analysis_type TEXT NOT NULL, status TEXT NOT NULL, analyzer_name TEXT NOT NULL, analyzer_version TEXT NOT NULL, ruleset_version TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, artifact_sha256 TEXT, published_integrity TEXT, integrity_verified INTEGER, base_image_ref TEXT, base_image_digest TEXT, network_mode TEXT, container_read_only INTEGER, container_user TEXT, summary_json TEXT, error_stage TEXT, error_message TEXT);
CREATE TABLE analysis_artifacts(id INTEGER PRIMARY KEY, analysis_run_id INTEGER NOT NULL UNIQUE, registry_type TEXT NOT NULL, package_identifier TEXT NOT NULL, package_version TEXT NOT NULL, source_url TEXT NOT NULL, local_relative_path TEXT NOT NULL, byte_size INTEGER NOT NULL, sha256 TEXT NOT NULL, published_integrity TEXT NOT NULL, integrity_verified INTEGER NOT NULL, downloaded_at TEXT NOT NULL);
CREATE TABLE analysis_findings(id INTEGER PRIMARY KEY, analysis_run_id INTEGER NOT NULL, rule_id TEXT NOT NULL, category TEXT NOT NULL, severity TEXT NOT NULL, confidence TEXT NOT NULL, disposition TEXT NOT NULL, subject_path TEXT NOT NULL, line_number INTEGER, symbol TEXT, title TEXT NOT NULL, evidence TEXT, explanation TEXT NOT NULL);
CREATE TABLE analysis_files(id INTEGER PRIMARY KEY, analysis_run_id INTEGER NOT NULL, archive_path TEXT NOT NULL, file_type TEXT NOT NULL, byte_size INTEGER NOT NULL, sha256 TEXT NOT NULL, executable INTEGER NOT NULL, native_binary INTEGER NOT NULL, generated INTEGER NOT NULL, minified INTEGER NOT NULL, UNIQUE(analysis_run_id, archive_path));
CREATE TABLE analysis_dependencies(id INTEGER PRIMARY KEY, analysis_run_id INTEGER NOT NULL, dependency_type TEXT NOT NULL, dependency_name TEXT NOT NULL, declared_version TEXT NOT NULL, resolved_version TEXT, direct INTEGER NOT NULL, development INTEGER NOT NULL);
CREATE TABLE analysis_evidence(id INTEGER PRIMARY KEY, analysis_run_id INTEGER NOT NULL, evidence_type TEXT NOT NULL, relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL, media_type TEXT NOT NULL, UNIQUE(analysis_run_id, relative_path));
CREATE TABLE analysis_finding_reviews(id INTEGER PRIMARY KEY, finding_id INTEGER NOT NULL, previous_disposition TEXT NOT NULL, disposition TEXT NOT NULL, reviewer TEXT NOT NULL, reviewed_at TEXT NOT NULL);
"""


def create_fixture(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA)
        connection.execute("INSERT INTO schema_info VALUES(1, 3, 'test', 'like')")
        connection.execute(
            "INSERT INTO snapshots VALUES(1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "a" * 64,
                "2026-07-28T04:00:00Z",
                "2026-07-28T03:59:00Z",
                "https://registry.modelcontextprotocol.io",
                "mcp-observatory",
                "0.1.0",
                "abc123",
                2,
                "/evidence/refresh",
                1,
                2,
                2,
                "2026-07-28T04:01:00Z",
            ),
        )
        connection.executemany(
            "INSERT INTO server_versions VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "io.example/filesystem", "1.0.0", "Fixture filesystem server", "active", "2026-07-01T00:00:00Z", "2026-07-28T03:00:00Z", "b" * 64, '{"name":"filesystem"}'),
                (2, "io.example/search", "2.1.0", "Fixture search server", "active", "2026-07-02T00:00:00Z", "2026-07-27T03:00:00Z", "c" * 64, '{"name":"search"}'),
            ],
        )
        connection.executemany("INSERT INTO snapshot_server_versions VALUES(1, ?)", [(1,), (2,)])
        connection.execute("INSERT INTO repositories VALUES(1, 'github', 'https://github.com/example/filesystem', 'https', 'github.com', 'example', 'filesystem')")
        connection.execute("INSERT INTO repositories VALUES(2, 'github', 'https://github.com/example/search', 'https', 'github.com', 'example', 'search')")
        connection.executemany(
            "INSERT INTO packages VALUES(?, ?, 0, 'npm', ?, ?, 'stdio')",
            [(10, 1, '@example/filesystem', '1.0.0'), (20, 2, '@example/search', '2.1.0')],
        )
        connection.executemany(
            "INSERT INTO packages VALUES(?, ?, 1, ?, ?, ?, 'stdio')",
            [
                (11, 1, "pypi", "example-filesystem", "1.0.0"),
                (21, 2, "npm", "@example/filesystem", "1.0.0"),
            ],
        )
        connection.execute("INSERT INTO package_arguments VALUES(10, 0, '/sandbox')")
        connection.execute("INSERT INTO package_environment VALUES(10, 0, 'TEST_TOKEN', 0, 'Synthetic test credential')")
        connection.execute("INSERT INTO remotes VALUES(1, 2, 0, 'https://mcp.example.test', 'https', 'mcp.example.test', 443, 'streamable-http')")
        connection.execute(
            """INSERT INTO analysis_runs VALUES(
                100, 1, 10, 'npm_package_static_v1', 'completed', 'mcp-observatory', '0.1.0', 'artifact-static-v1',
                '2026-07-28T05:00:00Z', '2026-07-28T05:01:00Z', ?, 'sha512-test', 1,
                'node:22-bookworm-slim', ?, 'none', 1, '65532:65532', '{}', NULL, NULL)""",
            ("d" * 64, "sha256:fixture"),
        )
        connection.execute(
            "INSERT INTO analysis_findings VALUES(1, 100, 'node-child-process', 'process-api', 'high', 'high', 'unreviewed', 'package/index.js', 12, 'spawn', 'Process execution API', 'spawn(', 'The package references a process execution API.')"
        )
        connection.execute(
            "INSERT INTO analysis_evidence VALUES(1, 100, 'summary', 'analysis-summary.json', ?, 128, 'application/json')",
            ("e" * 64,),
        )
        connection.commit()
    finally:
        connection.close()
