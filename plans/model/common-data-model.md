# Common Data Model (Person & Company)

Define a unified schema for persons and companies that merges data from HubSpot and Tripletex.
Contact→company associations and deletion state are core parts of the common model.

Phase 1 decision: associations are modeled as many-to-many by default.

## Options

### Option A: SQL Views over Staging Tables — Recommended

- **What:** Keep raw HubSpot/Tripletex data in staging tables. Define the common model as Postgres views (or materialized views via pg-trickle).
- **Pros:**
  - Raw data always available for debugging and re-processing
  - Views can be incrementally maintained with pg-trickle
  - BI tools can query views directly
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
  id: uuid (generated)
  source_hubspot_id: string?
  source_tripletex_id: string?
  first_name: string
  last_name: string
  email: string?
  phone: string?
  company_id: uuid? (FK)
  is_deleted: boolean default false
  deleted_at: timestamp?
  created_at: timestamp
  updated_at: timestamp

company:
  id: uuid (generated)
  source_hubspot_id: string?
  source_tripletex_id: string?
  name: string
  org_number: string?
  address: string?
  city: string?
  country: string (normalized)
  is_deleted: boolean default false
  deleted_at: timestamp?
  created_at: timestamp
  updated_at: timestamp

person_company_association:
  id: uuid (generated)
  person_id: uuid (FK)
  company_id: uuid (FK)
  relation_type: string?   # employee, billing_contact, owner, etc.
  source_hubspot_assoc_id: string?
  source_tripletex_assoc_id: string?
  is_deleted: boolean default false
  deleted_at: timestamp?
  created_at: timestamp
  updated_at: timestamp
```

## Open Questions

- What BI tool will consume this? (affects whether we need OLAP-friendly schema)
- Should we track per-field provenance (which source each field came from) in the common model itself?
