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
- All linkage decisions (deterministic, fuzzy, human) are written as pairwise links into an external link table; a cluster resolver assigns golden IDs before IVM runs.
- IVM only joins staging data against resolved cluster memberships — no ID generation or linkage logic inside IVM.

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
entity_link:                              # pairwise link table (input to cluster resolver)
  id: uuid
  entity_type: string                     # person | company | association
  left_system: string                     # hubspot | tripletex
  left_id: string
  right_system: string                    # hubspot | tripletex
  right_id: string
  link_source: string                     # deterministic | fuzzy | human
  confidence: float?
  created_by: string?
  created_at: timestamp

entity_cluster_member:                    # resolved cluster memberships (output of cluster resolver)
  entity_type: string
  source_system: string
  source_id: string
  golden_id: uuid                         # assigned by cluster resolver
  cluster_version: int                    # incremented on re-resolution

person:                                   # golden record (IVM projection)
  id: uuid (from entity_cluster_member.golden_id)
  source_hubspot_ids: string[]
  source_tripletex_ids: string[]
  first_name: string
  last_name: string
  email: string?
  phone: string?
  field_provenance_json: jsonb

company:                                  # golden record (IVM projection)
  id: uuid (from entity_cluster_member.golden_id)
  source_hubspot_ids: string[]
  source_tripletex_ids: string[]
  name: string
  org_number: string?
  address: string?
  city: string?
  country_id: uuid? (FK -> country.id)
  field_provenance_json: jsonb

country:
  id: uuid
  iso_code: string (e.g. NO, SE)
  canonical_name: string (e.g. Norway, Sweden)
  source_hubspot_values: string[]
  source_tripletex_values: string[]
  is_active: boolean default true

person_company_association:               # golden record (IVM projection)
  id: uuid (from entity_cluster_member.golden_id)
  person_id: uuid (FK)
  company_id: uuid (FK)
  relation_type: string?
  source_hubspot_assoc_ids: string[]
  source_tripletex_assoc_ids: string[]
  linkage_provenance_json: jsonb
```

Linkage architecture note:
- All merge decisions flow through `entity_link` (pairwise) → cluster resolver → `entity_cluster_member`.
- IVM joins staging tables against `entity_cluster_member` to produce golden records; no linkage logic or ID generation inside IVM.
- `golden_id` in `entity_cluster_member` is stable across re-resolution unless clusters split/merge.

Identity note:
- `source_hubspot_ids` and `source_tripletex_ids` are aggregated from cluster members in IVM.

Association note:
- Person↔company linkage is represented only in `person_company_association` (no `company_id` on `person`).

Country note:
- Country normalization and merge inputs are maintained via the reference-mapping plan and joined into `country`.

Deletion note:
- Consumers that need explicit delete markers should use a derived projection/view (for example `deleted_at` inferred from provenance and source presence tables).

## Related Plans

- [Analytics Views](analytics-views.md)
- [Reference Mapping](reference-mapping.md)
- [Linkage & Identity Strategy Analysis](linkage-identity-strategy-analysis.md)
