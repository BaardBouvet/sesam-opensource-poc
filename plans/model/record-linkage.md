# Record Linkage

Merge contacts and companies across HubSpot and Tripletex into unified golden records.
All linkage decisions (deterministic, fuzzy, human) are written as pairwise links into `entity_link`.
A cluster resolver computes connected components and assigns stable golden IDs in `entity_cluster_member`.
IVM joins staging data against resolved clusters to produce golden records — no linkage logic inside IVM.

## Link-Input Architecture

All merge sources write pairwise links into `entity_link`:

| Writer | Phase | When |
|--------|-------|------|
| Deterministic matcher (email / org_number) | Phase 1 | Every ingestion cycle |
| Fuzzy / probabilistic matcher | Phase 2 | Batch scoring run |
| Human curator | Phase 2 | Manual review UI |

Pipeline flow:
1. Ingestion writes raw data to staging tables.
2. Link writers read staging data and insert/update pairwise links in `entity_link`.
3. Cluster resolver reads `entity_link`, computes connected components, assigns `golden_id`, writes `entity_cluster_member`.
4. IVM joins staging tables against `entity_cluster_member` to produce golden records.

## Options (Link-Source Strategies)

### Option A: Deterministic Matching on Business Keys — Recommended

- **What:** Match records using exact or normalized matches on org number (companies) and email/phone (persons).
- **Pros:**
  - Simple, predictable, easy to debug
  - Org number is a strong identifier for Norwegian companies
  - Email is a strong identifier for persons
  - No ML needed
- **Cons:**
  - Misses matches when keys differ (typos, missing data)
  - Needs a fallback strategy for unmatched records
- **Matching keys:**
  - Companies: `org_number` (exact; HubSpot value stored in custom field), `name` (normalized) as fallback
  - Persons: `email` (exact)
- **Effort:** Low

## Phase 1 Decisions

- HubSpot org number is sourced from a custom property/field.
- Person linkage key for Phase 1 is `email`.
- Multiple Tripletex contacts with the same normalized email resolve to the same person.
- Consolidated person stores all HubSpot/Tripletex source contact ids in `source_hubspot_ids` and `source_tripletex_ids`.
- Company and association source identifiers are also tracked as arrays to support merged duplicates.
- All linkage decisions, including Phase 1 deterministic rules, flow through the pairwise link table.

### Option B: Rule-Based with Scoring

- **What:** Define matching rules that produce a confidence score. Auto-merge above threshold, flag for review below.
- **Pros:**
  - More flexible than pure deterministic matching
  - Can combine multiple weak signals
  - Supports a review queue for uncertain matches
- **Cons:**
  - More complex to implement and tune thresholds
  - Still not fuzzy — depends on rule quality
- **Effort:** Medium

### Option C: Probabilistic / Fuzzy Matching (Phase 2)

- **What:** Use libraries like Splink or dedupe.py for probabilistic record linkage.
- **Pros:**
  - Handles typos, abbreviations, missing data
  - Well-studied approach (Fellegi-Sunter model)
- **Cons:**
  - Needs training data or manual labeling
  - More complex infrastructure
  - Listed as optional/spin-off in GOAL.md
- **Effort:** High

## Implementation Sketch (Phase 1 Deterministic Link Writer)

```sql
-- 1. Deterministic link writer: company pairs by org number
INSERT INTO entity_link (id, entity_type, left_system, left_id, right_system, right_id, link_source, confidence, created_at)
SELECT
  gen_random_uuid(),
  'company',
  'hubspot', h.hubspot_id,
  'tripletex', t.tripletex_id,
  'deterministic', 1.0, now()
FROM hubspot_companies h
JOIN tripletex_customers t
  ON normalize_org_nr(h.org_number) = normalize_org_nr(t.org_number)
ON CONFLICT DO NOTHING;

-- 2. Cluster resolver (pseudo): compute connected components from entity_link,
--    assign golden_id per cluster, write to entity_cluster_member.

-- 3. IVM golden projection (pure join, no linkage logic):
SELECT
  cm.golden_id AS id,
  ARRAY_AGG(DISTINCT cm.source_id) FILTER (WHERE cm.source_system = 'hubspot') AS source_hubspot_ids,
  ARRAY_AGG(DISTINCT cm.source_id) FILTER (WHERE cm.source_system = 'tripletex') AS source_tripletex_ids,
  ...
FROM entity_cluster_member cm
JOIN hubspot_companies h ON cm.source_system = 'hubspot' AND cm.source_id = h.hubspot_id
FULL OUTER JOIN tripletex_customers t ON cm.source_system = 'tripletex' AND cm.source_id = t.tripletex_id
WHERE cm.entity_type = 'company'
GROUP BY cm.golden_id
```

Cluster resolver note:
- Runs outside IVM as a pre-processing step.
- Assigns `golden_id` once per cluster; reuses on subsequent runs unless clusters split/merge.
- `cluster_version` incremented on each re-resolution for change detection.

## Association Linkage (Core)

- Resolve `person_id` and `company_id` first via deterministic linkage.
- Build unified relationship rows in `person_company_association` by mapping source association rows to golden ids.
- If either side is deleted/tombstoned, emit deletion provenance/presence signals for the association unless source explicitly indicates re-association.
- Handle many-to-many as default in Phase 1.
- If needed for downstream projection, compute `primary_company_id` in a dedicated projection/view, not as canonical `person` field.

## Source-Specific Mapping Rules (HubSpot ↔ Tripletex)

- Tripletex contact has one company link (`Contact.customer`), while HubSpot supports many contact→company associations.
- Projection rule to singleton Tripletex mapping:
  - preferred source: HubSpot contact→company association `Primary` (`associationTypeId=1`)
  - fallback order when primary is missing/invalid:
    1. most recently updated associated company
    2. lowest HubSpot company id (stable tie-breaker)
- Multiple HubSpot primaries for one person are treated as data-quality conflicts.
- Phase 1 policy for multi-primary conflict:
  - do not create duplicate Tripletex contacts
  - keep one deterministic projected company link
  - record conflict in `mapping_conflicts` for review

## Related Plans

- [Common Data Model](common-data-model.md)
- [Fuzzy Merging with Agent & Human Curation](fuzzy-merging-curation.md)
- [Linkage & Identity Strategy Analysis](linkage-identity-strategy-analysis.md)

## Open Questions

- Should deleted associations be propagated immediately to targets, or delayed until a reconciliation run confirms they are not transient API inconsistencies?
- What algorithm for connected-component resolution in cluster resolver? (Union-Find, BFS, SQL recursive CTE?)
- Should cluster resolver run incrementally (delta links only) or full recomputation each cycle?
