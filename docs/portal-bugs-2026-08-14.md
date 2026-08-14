# Portal bugs found during public review — 2026-08-14

This document records user-visible issues observed on the public MCPLA portal during manual mobile review on 2026-08-14.

The issues are tracked separately in GitHub so implementation and validation can proceed independently.

## 1. Review-queue count contradicts the result list

**GitHub:** #14 — Storage v2 review queue shows 0 items while dashboard reports 233,085 unreviewed high/critical findings

### Observed behavior

The dashboard reports:

```text
Review queue
233085
Unreviewed high or critical findings
```

Selecting that link opens the review page, which reports:

```text
0 findings · page 1 of 1
```

and displays an empty table with the message that there are no unreviewed high or critical findings.

### Why this matters

The portal exposes two incompatible answers for the same review scope. A public research interface must not show an aggregate count that cannot be reconciled with the linked detail view.

### Likely cause to verify

The most likely Storage v2 failure mode is that the dashboard uses a compact aggregate/coverage summary while the review page still expects detailed legacy finding rows that are not present in the hot published database.

The aggregate count should not be assumed wrong until both query paths are traced.

### Preferred fix

Do not restore the previous multi-million-row detail model to the hot database and do not simply change the aggregate value to zero.

Publish a compact review index sufficient for pagination and retain full evidence/detail in the history store. The portal path should become:

```text
Dashboard aggregate summary
        |
        +-> count

Review page
        |
        +-> compact review index
                |
                +-> paginated items
                        |
                        +-> history/evidence detail when opened
```

Add a publication/parity check so the aggregate number and the number of review-index rows for the same filter cannot silently diverge.

Also suppress `page 1 of 1` when there are zero results.

## 2. Server browser exposes only the latest snapshot

**GitHub:** #15 — Server browser only exposes latest snapshot; add all-observed longitudinal view

### Observed behavior

The Server browser explicitly says that it shows one row per server identifier in the latest published snapshot and currently reports 516 identifiers in that scope.

This answers the current-state question, but there is no equivalent browser for every server identifier retained in historical Registry snapshots.

### Why this matters

MCPLA is longitudinal. Users should be able to answer both:

1. Which servers are present in the current published snapshot?
2. Which servers have ever appeared in the collected history?

The current page hides historical-only identifiers, removals, reappearances, and first/last-seen chronology.

### Preferred fix

Keep the current behavior but expose it as one explicit scope:

```text
Server browser

[ Current snapshot ] [ All observed servers ]
```

For the all-observed scope, show compact longitudinal metadata where available:

- server identifier;
- current/historical status;
- first seen;
- last seen;
- latest-snapshot membership;
- observed version count.

Search should respect the selected scope.

If Storage v2 does not retain enough history in the hot database for this to be fast, publish a compact server-history summary rather than querying the full history store on every request or restoring large legacy tables.

## Validation principles

Both fixes should preserve the Storage v2 objective: the public hot catalog remains compact and fast while authoritative historical detail remains available through bounded projections or history-backed drill-down.

Tests should verify semantic parity between public aggregate values and their linked detail pages, and should cover both current-snapshot and all-observed server browsing.