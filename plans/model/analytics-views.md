# Analytics Views (OLAP-Friendly Projections)

Define BI/analytics-friendly projections on top of golden records without changing the operational core model.

## Scope

- Star-like analytical views/marts derived from golden records
- Denormalized dimensions/facts for reporting use-cases
- Aggregation-friendly schemas and query performance tuning
- Late-binding transformations after golden record materialization

## Options

### Option A: SQL analytics views in Postgres — Recommended

- **What:** Build dedicated OLAP-friendly views/materialized views from `person`, `company`, and `person_company_association` golden datasets.
- **Pros:**
  - Fastest delivery with current stack
  - Clear separation from operational model
  - Easy iteration on BI requirements
- **Cons:**
  - Additional view maintenance and refresh concerns
- **Effort:** Medium

### Option B: Dedicated analytics tables/marts

- **What:** Persist denormalized analytics tables refreshed on schedule.
- **Pros:**
  - Better performance for heavy BI workloads
  - Stable contracts for dashboards
- **Cons:**
  - Extra ETL/refresh complexity
- **Effort:** Medium-High

### Option C: External BI warehouse/backend

- **What:** Publish golden data to external analytics backend and model there.
- **Pros:**
  - Strong OLAP capabilities at scale
- **Cons:**
  - Additional infrastructure and movement
  - Out of scope for lean PoC phase
- **Effort:** High

## Recommendation

Use **Option A** in Phase 2: OLAP-friendly Postgres views/materialized views built as late-binding projections after golden records are produced.

## Candidate Outputs

- `analytics_person_dim`
- `analytics_company_dim`
- `analytics_contact_company_fact`
- `analytics_change_events_fact`

## Design Rules

- Do not alter operational golden schemas to satisfy BI layout needs.
- Keep analytics models versioned and reproducible.
- Preserve provenance/last-modified context from core model where relevant.

## Related Plans

- [Common Data Model](common-data-model.md)
- [Reference Mapping](reference-mapping.md)
- [MDM Rules & Write-Back](../sync/mdm-rules-writeback.md)

## Open Questions

- Which first BI metrics/dashboards should define the initial marts?
- Do we materialize all analytics views in Phase 2, or start with logical views only?
