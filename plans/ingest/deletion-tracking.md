# Deletion Tracking & Verification

Define robust delete handling with tombstones, false-positive protection, and reconciliation between webhook and full-sync signals.

## Goals

- Prevent false tombstones from pagination/snapshot drift.
- Keep global entity active while at least one source still has it.
- Make webhook-vs-sync conflict resolution deterministic.

## Deletion Sources

- Explicit delete events/webhooks (`*.delete`).
- Snapshot-diff inferred deletes (`previous_keys - current_keys`).
- Soft-delete signals (`archived`, `active=false`, equivalent flags).

## Verification Flow (Required)

1. Mark candidate as `delete_pending`.
2. Verify with lookup-by-id (`GET /contact/{id}`, `GET /customer/{id}`, provider equivalent).
3. Tombstone only when lookup confirms not-found/archived semantics.
4. If lookup succeeds, clear pending delete and increment `false_delete_candidate` metric.

Safety knobs:
- Require two consecutive misses before verification in high-volume syncs.
- Add jittered finalization delay (5–15 min) to absorb transient API inconsistencies.

## Canonical Policy

- Entity remains active globally while at least one authoritative source has it.
- Global tombstone only when all tracked sources indicate deletion/not-found.

## Webhook vs Full Sync Rule

- Webhooks are low-latency hints.
- Full sync is canonical reconciliation.
- Webhook-applied state is marked `provisional=true` until next reconciliation.
- Conflict comparator: `(source_version, updated_at, ingest_time)`.

## Suggested Tables

- `delete_candidates(entity_type, source_system, source_id, first_missing_at, last_checked_at, verification_status, attempts)`
- `entity_presence(entity_id, source_system, source_entity_id, exists_in_source, last_seen_at, deleted_at)`

## Orchestration Hooks

- `Delete verification` step after inferred deletes.
- `Deletion/tombstone processor` after ingest/event.
- Nightly reconciliation to clear provisional state and correct drift.

## DLT Handoff Plan (Execution Model)

DLT is responsible for reliable extraction/loading; verification is responsible for delete correctness.

### Responsibilities

- **dlt (ingestion layer):**
	- Read source APIs with cursor/pagination/state handling.
	- Load latest records into staging tables.
	- Provide stable run boundaries (what was ingested this run).
- **verification job (control layer):**
	- Compute delete candidates from snapshot-diff/webhook hints.
	- Perform lookup-by-id confirmation before final tombstone.
	- Update `delete_candidates` and `entity_presence` deterministically.

### Per-Run Sequence

1. Run dlt ingestion for source + entity (contacts/companies/associations).
2. Derive `current_keys` from staging for this run scope.
3. Compute inferred candidates as `previous_keys - current_keys` and write `delete_pending` rows.
4. Apply safety gates (two consecutive misses / jitter delay where configured).
5. Verify candidates with provider lookup-by-id.
6. Confirmed missing/archived → mark source presence as deleted.
7. Verification says still present → clear candidate and count false-positive metric.
8. Run canonical tombstone evaluation (all-sources-deleted rule).

### Why dlt Still Helps Here

- Removes custom code for pagination/cursor/state/retry/loading.
- Gives consistent ingestion semantics across providers.
- Produces reliable staging snapshots used by `previous_keys - current_keys`.
- Lets custom logic focus on correctness policy (verification + reconciliation), not plumbing.

### Practical Integration Notes

- Keep verification as a separate step/job after dlt runs; do not embed it in dlt resources.
- Scope verification to entities touched in the latest run to reduce API calls.
- If lookup APIs are rate-limited, process candidates in batches with retry/backoff.
- Keep webhook deletes `provisional` until verified and/or superseded by full-sync reconciliation.

## dlt Extension vs Upstream vs Fork

For delete verification features, compare the implementation paths:

| Path | What we build | Benefits | Costs/Risks | Phase 1 Fit |
|------|----------------|----------|-------------|-------------|
| Internal extension around dlt | Separate verification module + SQL state tables + post-dlt orchestration step | Fastest delivery, keeps dlt upgrades easy, preserves policy flexibility | Some custom code to maintain | **Best** |
| Upstream contribution to dlt | Generic hooks/helpers (post-run metadata, reusable delete-candidate primitives) | Reduces long-term local glue, helps community | Contributor overhead, slower timeline, must stay generic | **Selective** |
| Fork dlt | Maintain custom dlt branch with verification logic inside framework | Maximum control over internals | High maintenance burden, upgrade friction, divergence risk | **Avoid** |

### Decision (Phase 1)

- Implement verification as an **internal extension around dlt**.
- Keep dlt unmodified for extraction/loading.
- Only upstream small, generic pieces if they prove reusable.
- Do not fork dlt in Phase 1.

### Revisit Triggers

Re-evaluate upstream/fork choice only if one or more occurs:

- The internal extension requires repeated monkey-patching of dlt internals.
- More than one source needs the exact same generic post-run primitive that dlt lacks.
- dlt upgrade churn repeatedly breaks our integration boundary.

## Related Plans

- [Data Ingestion](data-ingestion.md)
- [Webhooks](webhooks.md)
- [Orchestration](../ops/orchestration.md)
- [Deletion Conflict Policies](../sync/deletion-conflict-policies.md)

