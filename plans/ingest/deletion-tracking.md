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

## Related Plans

- [Data Ingestion](data-ingestion.md)
- [Webhooks](webhooks.md)
- [Orchestration](../ops/orchestration.md)
- [Deletion Conflict Policies](../sync/deletion-conflict-policies.md)

