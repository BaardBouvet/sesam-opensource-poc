# Common Data Model (Person & Company)

Define a unified schema for persons and companies that merges data from HubSpot and Tripletex.
Contact→company associations and deletion state are core parts of the common model.

Phase 1 decision: associations are modeled as many-to-many by default.

Decisions:
- Keep core model normalized for operational sync/use-cases.
- Build separate OLAP-friendly analytics views in a dedicated analytics plan.
- Track provenance metadata in core entities.
- Use per-field provenance in Phase 1 to simplify debugging.
- Do not exclude high-volume modeled fields from per-field provenance in Phase 1.
- When multiple Tripletex contacts share the same email and resolve to one person, keep all source contact ids in an array.
- Track source identifiers as arrays for both HubSpot and Tripletex across person/company/association entities.
- Do not include canonical `created_at`/`updated_at` in common model; use provenance/source metadata and audit layers instead.
- Model country as a separate entity and reference it from `company` via `country_id`.
- Derive deletion state from provenance/presence signals rather than storing canonical `is_deleted` or `deleted_at` in core entities.

## Options

### Option A: SQL Views over Staging Tables — Recommended

- **What:** Keep raw HubSpot/Tripletex data in staging tables. Define the common model as Postgres views (or materialized views via pg-trickle).
- **Pros:**
  - Raw data always available for debugging and re-processing
  - Views can be incrementally maintained with pg-trickle
  - Clear separation between operational core model and analytics projections
  - Easy to evolve the model without data migration
- **Cons:**
  - View logic can become complex as MDM rules grow
  - Performance depends on view complexity (mitigated by materialization)
- **Effort:** Medium

### Option B: ETL into Dedicated Tables

- **What:** Transform and load merged data into dedicated `person` and `company` tables via Python ETL.
- **Pros:**
  - Simple to query — clean tables with known schema
  - Easy to add indexes and constraints
- **Cons:**
  - Requires explicit ETL step (more orchestration)
  - Loses raw-to-golden lineage unless carefully tracked
  - Harder to evolve — schema changes require migration
- **Effort:** Medium

### Option C: dbt Models

- **What:** Use dbt to define staging → intermediate → mart models.
- **Pros:**
  - Industry-standard transformation layer
  - Great documentation and lineage features
  - Works well with Dagster (dagster-dbt integration)
- **Cons:**
  - Another tool in the stack
  - Might be overkill for 2 sources and 2 entities
  - dbt incremental models are less elegant than pg-trickle IVM
- **Effort:** Medium

## Proposed Common Model (Draft)

```
person:
  id: primary key (strategy TBD)
  source_hubspot_ids: string[]
  source_tripletex_ids: string[]
  first_name: string
  last_name: string
  email: string?
  phone: string?
  field_provenance_json: jsonb  # per-field source provenance map

company:
  id: primary key (strategy TBD)
  source_hubspot_ids: string[]
  source_tripletex_ids: string[]
  name: string
  org_number: string?
  address: string?
  city: string?
  country_id: uuid? (FK -> country.id)
  field_provenance_json: jsonb  # per-field source provenance map

country:
  id: primary key (strategy TBD)
  iso_code: string (e.g. NO, SE)
  canonical_name: string (e.g. Norway, Sweden)
  source_hubspot_values: string[]
  source_tripletex_values: string[]
  is_active: boolean default true

person_company_association:
  id: primary key (strategy TBD)
  person_id: uuid (FK)
  company_id: uuid (FK)
  relation_type: string?   # employee, billing_contact, owner, etc.
  source_hubspot_assoc_ids: string[]
  source_tripletex_assoc_ids: string[]
  linkage_provenance_json: jsonb
```

Identity note:
- `source_hubspot_ids` and `source_tripletex_ids` may contain multiple ids when deterministic linkage resolves duplicates/merges into one canonical record.

Association note:
- Person↔company linkage is represented only in `person_company_association` (no `company_id` on `person`).

Country note:
- Country normalization and merge inputs are maintained via the reference-mapping plan and joined into `country`.

Deletion note:
- Consumers that need explicit delete markers should use a derived projection/view (for example `deleted_at` inferred from provenance and source presence tables).

## Open Questions

- Primary key strategy for pure IVM:
  - Option A: deterministic UUID derived from canonical `match_key` in IVM
    - Pros: all transformation stays in IVM; reproducible ids
    - Cons: ids change if `match_key` changes
  - Option B: deterministic text key (`match_key`) as primary key
    - Pros: simplest pure-IVM approach; no UUID helper function needed
    - Cons: larger keys and indexes; leaks business-key semantics into PK
  - Option C: separate stable surrogate id assignment outside IVM
    - Pros: stable ids even if `match_key` changes
    - Cons: breaks strict pure-IVM transformation boundary

Option C design sketch (if selected):
- Creation timing:
  - Create surrogate ids immediately after deterministic linkage yields canonical `match_key` values for person/company/association.
  - Run on every ingestion cycle; create only for unseen `(entity_type, match_key)`.
- Creation mechanism:
  - Maintain `identity_registry(entity_type, match_key, surrogate_id, created_at, updated_at)`.
  - Upsert by `(entity_type, match_key)`:
    - existing row -> reuse `surrogate_id`
    - missing row -> insert with new surrogate id
  - Join golden projections to `identity_registry` to expose stable ids.
- Operational safeguards:
  - Unique constraint on `(entity_type, match_key)`.
  - Idempotent upsert and retry-safe execution.
  - Add remap history table for key evolution (`old_match_key`, `new_match_key`, `changed_at`, `reason`).
  - One-time backfill of `identity_registry` before enabling live upserts.

## Related Plans

- [Analytics Views](analytics-views.md)
- [Reference Mapping](reference-mapping.md)
