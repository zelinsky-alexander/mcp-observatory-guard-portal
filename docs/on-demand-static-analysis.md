# On-demand static analysis

## Supported selection

The portal can queue only an exact package row already present in the Observatory catalog. Version 1 supports:

- Registry type `npm`;
- an exact non-empty package version;
- a package row that belongs to the selected immutable server-version row.

Unsupported or ambiguous packages remain visible but do not receive an analysis button.

## Request lifecycle

```text
queued -> running -> completed
                  -> failed
```

Submitting the same package while a job is queued or running returns the existing job. A completed compatible Observatory run may also be reused by the Observatory CLI because the portal intentionally does not pass `--force`.

The portal job status is orchestration metadata. The authoritative static-analysis status, findings, artifact digest, and evidence remain in the Observatory database and evidence root.

## Failure handling

A job fails when:

- the catalog selection no longer resolves to the stored identity;
- the Observatory process cannot be started;
- the configured timeout is exceeded;
- the CLI exits non-zero;
- stdout does not contain a valid completed JSON result with a positive `analysis_run_id`.

Stdout and stderr excerpts are bounded and HTML-escaped. Truncation is recorded. The job page must not be treated as a substitute for Observatory evidence.

## Operational model

Run one worker for this milestone. Multiple workers are not a supported deployment profile even though queue claiming is transactional. A crashed worker can leave a job in `running`; manual recovery and stale-job administration are future work.

Do not expose analysis-enabled mode beyond loopback. Before remote deployment add authentication, authorization, rate limiting, durable audit identity, and a consistently published read model.
