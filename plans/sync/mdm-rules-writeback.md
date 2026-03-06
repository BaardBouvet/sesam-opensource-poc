# MDM Rules & Write-Back

Master data management rules per property, compare-and-swap writes, and syncing data back to HubSpot and Tripletex.

Includes write-back of contact→company associations and deletion/tombstone propagation.

## MDM Rule Options

### Option A: SQL-Based Rules — Recommended

- **What:** Express MDM rules as SQL CASE/COALESCE expressions in the golden record views.
- **Example:**
  ```sql
  -- HubSpot is master for name, Tripletex for address
  COALESCE(h.first_name, t.first_name) AS first_name,  -- HubSpot wins
  COALESCE(t.address, h.address) AS address,            -- Tripletex wins
  ```
- **Pros:**
  - Rules live next to the data, easy to reason about
  - No extra framework needed
  - Works naturally with pg-trickle IVM
- **Cons:**
  - Complex rules get hard to read in SQL
  - No built-in UI for rule management
- **Effort:** Low

### Option B: Python Rule Engine

- **What:** Define rules in Python (e.g. dataclasses or config dicts) that are applied during transformation.
- **Pros:**
  - More expressive for complex rules
  - Easier to test in isolation
  - Can support dynamic/conditional rules
- **Cons:**
  - Rules live outside the DB — need to keep in sync
  - Can't use IVM for rule application
- **Effort:** Medium

### Option C: Declarative Config (YAML/TOML) Rendering to SQL

- **What:** Define rules in a config file that gets compiled to SQL views. Mentioned in GOAL.md as a spin-off idea.
- **Example:**
  ```yaml
  person:
    first_name: { master: hubspot, fallback: tripletex }
    address: { master: tripletex, fallback: hubspot }
  ```
- **Pros:**
  - Best of both worlds: human-readable config, SQL execution
  - Could become a reusable product
- **Cons:**
  - Requires building a config→SQL compiler
  - Scope creep risk for a PoC
- **Effort:** High (but valuable if extracted later)

## Write-Back Options

### Option A: Diff-Based Write-Back — Recommended

- **What:** Compare golden record desired state with last-written state per target system. Write only changed fields.
- **Mechanism:** Per-target desired-state views + `sync_state` table + `sync_queue` diff view. See [Change Detection & Sync Queue](#change-detection--sync-queue) for full design.
- **Pros:**
  - Minimal API calls, respects rate limits
  - Natural compare-and-swap: include version/etag in sync_state
  - Clear audit trail of what was written and when
  - Idempotent & resumable: failed items reappear in the queue view
- **Effort:** Medium

### Option B: Full Record Write-Back

- **What:** Always push the full golden record to target systems.
- **Pros:** Simpler logic, no diff needed
- **Cons:** More API calls, risk of overwriting concurrent changes, wasteful
- **Effort:** Low

## Change Detection & Sync Queue

The "desired state" (what a target system *should* contain) is computable from golden records + MDM rules, but a desired-state view alone doesn't tell us **what changed** since the last sync. We need a mechanism to diff desired state against *last successfully written state* and produce actionable work items.

### Architecture: Desired State → Last-Written State → Sync Queue

```
┌──────────────────┐     ┌──────────────────┐
│ desired_state_*  │     │   sync_state     │
│  (SQL views —    │     │  (table — last   │
│   golden record  │     │   written payload │
│   projected per  │     │   per target)    │
│   target system) │     │                  │
└────────┬─────────┘     └────────┬─────────┘
         │                        │
         └──────┐     ┌───────────┘
                ▼     ▼
         ┌──────────────────┐
         │   sync_queue     │
         │  (view — rows    │
         │   where desired  │
         │   ≠ last-written │
         │   or new/deleted)│
         └────────┬─────────┘
                  │
                  ▼
         ┌──────────────────┐
         │  write-back      │
         │  worker          │
         │  (processes      │──► target API
         │   queue items,   │
         │   updates        │
         │   sync_state)    │
         └──────────────────┘
```

### 1. Per-Target Desired-State Views

Each target system gets a projection view that applies MDM rules and maps golden record fields to the target's schema. These views are the **single source of truth** for what the target should contain.

**Critical constraint:** The desired-state view must only include columns that are **managed by MDM rules** — i.e. fields where the golden record is authoritative. Fields that are owned locally by the target system (e.g. Tripletex-only accounting fields, HubSpot-only marketing fields) are intentionally excluded. This means:

- The view is a **narrow projection**, not a full mirror of the target record.
- We never overwrite target-local fields that the MDM system doesn't govern.
- Adding a field to MDM management requires adding it to the desired-state view; until then it is invisible to write-back.

```sql
-- Example: what each person should look like in Tripletex
-- Only includes fields governed by MDM rules — Tripletex-local fields are untouched.
CREATE VIEW person_tripletex_desired AS
SELECT
  g.golden_id,
  csl.target_id                      AS tripletex_id,  -- NULL if not yet created
  COALESCE(h.first_name, t.first_name) AS first_name,  -- MDM: HubSpot wins
  COALESCE(h.last_name, t.last_name)   AS last_name,
  COALESCE(t.address, h.address)        AS address,     -- MDM: Tripletex wins
  COALESCE(t.phone, h.phone)           AS phone,
  g.primary_company_tripletex_id        AS customer_id
  -- Note: Tripletex-local fields (e.g. ledger_account, vat_type) are NOT included.
  -- They remain under Tripletex's local control.
FROM golden_person g
LEFT JOIN hubspot_contacts h   ON ...
LEFT JOIN tripletex_contacts t ON ...
LEFT JOIN cross_system_link csl
       ON csl.entity_type = 'person'
      AND csl.target_system = 'tripletex'
      AND csl.source_id = g.golden_id::text;
```

These views can be maintained by IVM (pg-trickle) so they stay current as golden records change.

Because the view only contains managed fields, the JSONB payloads in `sync_state` and `sync_queue` also only contain managed fields — the `jsonb_diff` function will therefore only ever produce a patch for fields the MDM system is allowed to write.

### 2. Last-Written State Table (`sync_state`)

Stores the field values that were last successfully written (or confirmed to exist) in each target system.

```sql
CREATE TABLE sync_state (
  entity_type    TEXT NOT NULL,
  target_system  TEXT NOT NULL,
  golden_id      UUID NOT NULL,
  target_id      TEXT,                  -- NULL until first successful create
  last_payload   JSONB NOT NULL,        -- field values as last written
  last_synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  etag           TEXT,                  -- optional CAS/version token from target API
  sync_version   BIGINT NOT NULL DEFAULT 1,
  PRIMARY KEY (entity_type, target_system, golden_id)
);
```

**Key properties:**
- `last_payload` is a JSONB snapshot of the field values written, using the same keys as the desired-state view produces. This makes comparison straightforward.
- `target_id` is populated after the first successful create (and also captured in `cross_system_link`).
- `etag` supports compare-and-swap for systems that expose version tokens.
- `sync_version` is incremented on each successful write for debugging/audit.

### 3. Sync Queue View

A SQL view that diffs desired state against last-written state. Rows in this view are the **work items** for the write-back worker.

```sql
CREATE VIEW sync_queue AS
-- Creates & Updates
SELECT
  d.entity_type,
  d.target_system,
  d.golden_id,
  d.target_id,
  d.desired_payload,
  s.last_payload,
  s.etag,
  CASE
    WHEN s.golden_id IS NULL THEN 'create'
    ELSE 'update'
  END AS action
FROM desired_state_union d             -- UNION ALL of all per-target desired views
LEFT JOIN sync_state s
  USING (entity_type, target_system, golden_id)
WHERE s.golden_id IS NULL              -- new: never written
   OR d.desired_payload IS DISTINCT FROM s.last_payload  -- changed fields

UNION ALL

-- Deletes: sync_state entry exists but no desired state (tombstoned/removed golden record)
SELECT
  s.entity_type,
  s.target_system,
  s.golden_id,
  s.target_id,
  NULL AS desired_payload,
  s.last_payload,
  s.etag,
  'delete' AS action
FROM sync_state s
LEFT JOIN desired_state_union d
  USING (entity_type, target_system, golden_id)
WHERE d.golden_id IS NULL;
```

`desired_state_union` is a helper view that normalises all per-target desired views into a common shape:

```sql
CREATE VIEW desired_state_union AS
SELECT 'person'  AS entity_type, 'tripletex' AS target_system, golden_id, tripletex_id AS target_id,
       to_jsonb(d) - 'golden_id' - 'tripletex_id' AS desired_payload
FROM person_tripletex_desired d
UNION ALL
SELECT 'person', 'hubspot', golden_id, hubspot_id,
       to_jsonb(d) - 'golden_id' - 'hubspot_id'
FROM person_hubspot_desired d
-- ... extend per entity_type × target_system
;
```

### 4. Write-Back Worker Loop

```
for each row in sync_queue (ordered by entity_type, action priority):
    if action = 'create':
        response = target_api.create(desired_payload)
        INSERT INTO cross_system_link (source_id=golden_id, target_id=response.id, ...)
        INSERT INTO sync_state (target_id=response.id, last_payload=desired_payload, ...)
    elif action = 'update':
        if etag:  verify remote state matches etag (CAS)
        target_api.update(target_id, changed_fields(desired_payload, last_payload))
        UPDATE sync_state SET last_payload=desired_payload, etag=response.etag, ...
    elif action = 'delete':
        target_api.deactivate_or_delete(target_id)
        DELETE FROM sync_state WHERE ...
```

### Options: JSONB Payload vs Column-Level Comparison

#### Option A: JSONB Payload Comparison — Recommended

- **What:** Desired-state views produce a JSONB blob; `sync_state.last_payload` stores the same shape. Diff is `desired IS DISTINCT FROM last_payload`.
- **Pros:**
  - Schema-agnostic — adding a field to the desired view automatically participates in diff
  - One generic `sync_queue` view works for all entity types and targets
  - No need to enumerate columns in the diff logic
- **Cons:**
  - JSONB key ordering matters for equality (`IS DISTINCT FROM` does order-independent comparison in Postgres, but serialisation must be consistent)
  - Harder to extract *which* fields changed without a secondary `jsonb_each` diff
  - Slightly less transparent when debugging ("what changed?" requires JSONB comparison)
- **Mitigation:** Use `jsonb_diff(desired, last_payload)` helper function to produce a field-level changelog for audit/debugging.

#### Option B: Column-Level Comparison

- **What:** `sync_state` stores individual columns matching the desired-state view. Diff compares column by column.
- **Pros:**
  - Obvious which fields changed
  - Easier to build minimal PATCH payloads directly from the diff
  - No JSONB serialisation concerns
- **Cons:**
  - `sync_state` schema must mirror every desired-state view → schema coupling
  - Adding a field requires a migration to `sync_state` + updating the diff query
  - Need per-entity-type sync_state tables or a wide table with many nullable columns
- **Decision:** Not recommended for Phase 1. JSONB approach is more maintainable with two systems and evolving field sets.

### Properties of This Design

- **Idempotent:** `sync_queue` is a view — if a write fails and `sync_state` is not updated, the row reappears on next poll. No separate retry queue needed.
- **Backfill-friendly:** On first run, `sync_state` is empty → every golden record appears as `action = 'create'`.
- **Resumable:** Worker can crash and restart; the view recomputes the full diff.
- **Rate-limit aware:** Worker controls throughput by how fast it drains the queue.
- **Auditable:** `sync_state` + `sync_version` gives a history of what was written and when.

### Adding a New Managed Field

When a new field is added to a desired-state view (i.e. a previously unmanaged field is brought under MDM control), the JSONB payload from the view gains a new key that `sync_state.last_payload` does not have. This **automatically produces a diff** for every record where the new field's desired value is non-null — which is exactly the right behaviour:

- Every existing record enters `sync_queue` as an `update` with the new field in the delta.
- The write-back worker pushes the MDM-decided value to the target.
- After a successful write, `sync_state.last_payload` is updated to include the new key — subsequent cycles are quiet.

No migration of `sync_state` is needed; the JSONB column naturally tolerates schema evolution. The `jsonb_diff` function already handles the "key present in desired but absent in last_written" case (the `NOT last_written ? key` clause).

**Caveat:** If the new field is added for a large entity set, the first sync cycle after the change will produce a spike of updates. The worker should be rate-limit aware and may need to batch or prioritise.

### Optimising with Raw Ingested Data

The `sync_queue` view diffs desired state against last-written state, but many records may already be in a good state in the target — the value we'd write back is the same as what we just ingested from that system. We can use the raw ingested data to **short-circuit** the diff for these cases.

**Insight:** If the target system is the *winning source* for a field (per MDM rules) and we just ingested the current value from that system, then `desired_value == ingested_value` — writing it back would be a no-op.

#### Option A: Ingested-State Pre-Filter in the Sync Queue — Recommended

Add a filter to the `sync_queue` view (or a wrapper view) that excludes records where the desired payload matches the latest ingested data from the target system:

```sql
-- Optimised sync_queue: skip records where target already has the desired state
CREATE VIEW sync_queue_optimised AS
SELECT sq.*
FROM sync_queue sq
LEFT JOIN target_ingested_state tis
  USING (entity_type, target_system, golden_id)
WHERE sq.action = 'create'
   OR sq.action = 'delete'
   OR tis.golden_id IS NULL                          -- no ingested data to compare
   OR sq.desired_payload IS DISTINCT FROM tis.ingested_payload;  -- genuinely different
```

Where `target_ingested_state` is a view that projects the raw ingested data (from staging tables) into the same JSONB shape as the desired-state view, but only for the managed fields:

```sql
CREATE VIEW target_ingested_state AS
SELECT 'person' AS entity_type, 'tripletex' AS target_system,
       cm.golden_id,
       jsonb_build_object(
         'first_name', t.first_name,
         'last_name',  t.last_name,
         'address',    t.address,
         'phone',      t.phone,
         'customer_id', t.customer_id
       ) AS ingested_payload
FROM tripletex_contacts t
JOIN entity_cluster_member cm ON cm.source_system = 'tripletex' AND cm.source_id = t.tripletex_id
-- ... UNION ALL for other entity_type × target_system combinations
;
```

- **Pros:**
  - Avoids pointless API writes when the target already has the right value (common when the target is the winning source for most fields)
  - Reduces sync queue size significantly during steady state
  - Pure SQL — can be maintained by IVM alongside the other views
- **Cons:**
  - Adds view complexity; one more join in the queue
  - Only effective when ingested data is fresh — if ingestion is stale, the optimisation may be wrong (but the worst case is a redundant write, not data loss)
  - Requires the ingested-state view to project managed fields in the exact same JSONB shape as the desired-state view

#### Option B: Seed `sync_state` from Ingested Data on First Run

Instead of filtering the queue, pre-populate `sync_state` from the raw ingested data before the first sync cycle. This treats the current target state as "already written":

```sql
INSERT INTO sync_state (entity_type, target_system, golden_id, target_id, last_payload)
SELECT entity_type, target_system, golden_id, target_id, ingested_payload
FROM target_ingested_state
ON CONFLICT DO NOTHING;
```

- **Pros:**
  - One-time operation; no ongoing view complexity
  - After seeding, only genuine diffs appear in `sync_queue`
- **Cons:**
  - Assumes the current target state is correct — if there are existing drift issues, they won't be caught
  - Must be re-run if `sync_state` is cleared/rebuilt

#### Decision

Use **Option A (pre-filter)** as the primary mechanism — it works continuously and handles new ingestion cycles gracefully. Use **Option B (seed)** as a one-time bootstrap step to avoid a flood of no-op writes on initial deployment.

### Field-Level Diff for Minimal Payloads

Even with JSONB comparison, the worker should send only changed fields to the API (PATCH semantics). A helper function extracts the delta:

```sql
CREATE FUNCTION jsonb_diff(desired JSONB, last_written JSONB)
RETURNS JSONB LANGUAGE sql IMMUTABLE AS $$
  SELECT COALESCE(
    jsonb_object_agg(key, value),
    '{}'::jsonb
  )
  FROM jsonb_each(desired) AS d(key, value)
  WHERE NOT last_written ? key
     OR last_written -> key IS DISTINCT FROM value;
$$;
```

The worker calls `jsonb_diff(desired_payload, last_payload)` to build the minimal API payload for update actions.

## Compare-and-Swap

To avoid lost writes when MDM rules say "name from HubSpot, phone from Tripletex":

1. Read current value from target system before write
2. Verify it matches expected state (from `sync_state.etag` or `sync_state.last_payload`)
3. If changed externally, re-ingest the fresh data, let the pipeline recompute desired state, and re-diff
4. Write only if state matches expectations

Both HubSpot and Tripletex support conditional updates or at least return current state on read.

The `sync_state.etag` column stores the target system's version token (if available) so the CAS check can happen without an extra API read when the target supports conditional writes (e.g. `If-Match` header).

## Deletion Propagation Policy (Phase 1)

- Consume canonical deletion state produced by deletion tracking/reconciliation.
- Propagate deletes to targets only from canonical tombstone state (not from provisional single-source signals).
- Prefer soft-delete/deactivation when supported; avoid immediate hard-delete in Phase 1.
- Keep target-specific propagation audit fields in `sync_state`/writeback audit tables.

## Association Write-Back

- Treat contact→company links as first-class write-back entities.
- Diff desired association set vs source association set.
- Apply create/delete association operations with CAS/idempotency keys.
- Use many-to-many semantics in Phase 1.

### Tripletex contact-company write rule

- Tripletex target expects one company per contact.
- Write the projected primary company from `person_tripletex_projection`.
- If multiple HubSpot companies are marked as primary for the same person:
  - do not create duplicate Tripletex contacts in Phase 1
  - select one deterministically (same logic as ingestion/linkage)
  - write conflict row to `mapping_conflicts` with severity `warning`

### Optional extension (Phase 2)

- Enable `split_contact_on_multi_primary=true` feature flag to create multiple Tripletex contacts from one golden person only when explicitly approved.
- Requires stable synthetic key strategy (`person_id + company_id`) and explicit downstream reporting impact acknowledgement.

## Cross-System Link Capture

When write-back creates a new record in a target system (e.g. a HubSpot contact is pushed to Tripletex for the first time), the relationship between the source record and the newly created target record must be captured in a **cross-system link table**.

This link is then consumed by the deterministic matcher in [Record Linkage](../model/record-linkage.md) as a highest-priority rule — it takes precedence over fuzzy or key-based matching because the provenance is exact: *we* created the target record from a known source record.

### Write-Back Service Responsibility

1. Call target API to create the record.
2. On success, insert a row into the link table with the returned target ID.
3. On failure or rollback, do **not** insert — the absence of the row means no deterministic assumption is made.
4. The deterministic link writer (in the record-linkage pipeline) reads this table every ingestion cycle and emits `entity_link` rows with `link_source = 'writeback'` and `confidence = 1.0`.

### Table Strategy Options

#### Option A: Single Shared Table — Recommended

```sql
CREATE TABLE cross_system_link (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_type   TEXT NOT NULL,          -- 'person' | 'company'
  source_system TEXT NOT NULL,          -- system that owned the original record
  source_id     TEXT NOT NULL,
  target_system TEXT NOT NULL,          -- system where the new record was created
  target_id     TEXT NOT NULL,          -- id returned by the target API
  created_by    TEXT NOT NULL DEFAULT 'writeback',  -- provenance tag
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (entity_type, source_system, source_id, target_system)
);
```

- **Pros:**
  - One query to emit all write-back links into `entity_link` — no per-destination logic
  - Adding a new system requires zero schema changes
  - Simpler deterministic link writer SQL (single `SELECT ... FROM cross_system_link`)
  - Easier to audit/query all cross-system relationships in one place
- **Cons:**
  - Shared table can grow large if many systems and entity types are added
  - Indexes must cover `(entity_type, source_system, target_system)` to stay fast
  - Harder to apply per-destination constraints (e.g. different uniqueness rules per target)

#### Option B: One Table Per Destination

E.g. `cross_system_link_tripletex`, `cross_system_link_hubspot`, etc.

- **Pros:**
  - Each table is smaller and can have destination-specific columns (e.g. Tripletex-specific metadata)
  - Uniqueness constraints are simpler and naturally scoped
  - Easier to drop/rebuild one destination without affecting others
  - Per-table permissions/row-level security is trivial
- **Cons:**
  - Deterministic link writer must `UNION ALL` across all destination tables — query grows with each new system
  - Adding a new system requires a new table + migration + updating the link writer SQL
  - Harder to get a holistic cross-system view without joining multiple tables
  - More schema maintenance overhead

#### Decision

Use **Option A (single shared table)** for Phase 1. The PoC has two systems; the single-table approach keeps the link writer trivial and avoids premature schema proliferation. If per-destination columns become necessary, they can be added as nullable columns or moved to a companion detail table.

### How It Feeds the Link Pipeline

The deterministic link writer in [Record Linkage](../model/record-linkage.md) is extended to emit links from `cross_system_link` **before** key-based matching:

```sql
-- 0. Deterministic link writer: cross-system write-back links (highest priority)
INSERT INTO entity_link (id, entity_type, left_system, left_id, right_system, right_id,
                         link_source, confidence, created_at)
SELECT
  gen_random_uuid(),
  csl.entity_type,
  csl.source_system, csl.source_id,
  csl.target_system, csl.target_id,
  'writeback', 1.0, now()
FROM cross_system_link csl
ON CONFLICT DO NOTHING;
```

### Rules

- Write-back links have **confidence 1.0** and `link_source = 'writeback'`.
- They are evaluated before key-based deterministic rules; if a write-back link exists, no further matching is attempted for that record pair.
- If a record in the target system already exists and is linked via write-back, subsequent ingestion cycles must reuse that link rather than create a duplicate.

## Related Plans

- [Record Linkage](../model/record-linkage.md)
- [Common Data Model](../model/common-data-model.md)
- [Data Ingestion](../ingest/data-ingestion.md)
- [Webhooks](../ingest/webhooks.md)
- [API Gateway & Traffic Control](../ops/api-gateway-traffic-control.md)
- [Traceability](../ops/traceability.md)

## Open Questions

- Does Tripletex support partial updates (PATCH) or only full PUT?
- Does HubSpot expose ETags or version numbers for CAS?
- Should we start with Option A (SQL rules) and extract to Option C (declarative config) later?
- Should `sync_queue` processing order prioritize creates over updates, or process in golden_id order for predictability?
- Do we need a `sync_error` table to track repeated failures and apply back-off per record?
