# Deletion Conflict Policies

Define how to resolve conflicts when one source deletes and the other updates or still contains a record.

## Options

### Option A: Delete wins
- Once deletion observed, entity is tombstoned until explicit restore.
- Simple semantics, can hide valid updates from surviving source.

### Option B: Master-system-driven delete wins
- Deletion only wins if deleting source is configured master for entity/field.
- Aligns with MDM governance, requires clear master config.

### Option C: Quarantine conflicts (Recommended)
- Conflicting delete/update enters `conflict_queue`.
- Keep global entity active (current Phase 1 baseline), require review/rule evaluation.

## Proposed Policy Ladder

1. Single-source delete + other source active: keep entity active, mark conflict.
2. All sources deleted/not-found: global tombstone.
3. Restore event after tombstone: reopen entity and replay latest surviving source state.

## Operational Requirements

- Conflict telemetry and alerting.
- Manual resolution path and SLA.
- Replay-safe resolution commands.

## Open Questions

- What is acceptable conflict-resolution SLA in production-like runs?
- Which conflicts can be auto-resolved by deterministic rules?
