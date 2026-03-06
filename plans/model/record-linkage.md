# Record Linkage

Merge contacts and companies across HubSpot and Tripletex into unified golden records using static rules.
Link and preserve contact→company associations as core output.

## Options

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

## Implementation Sketch (Option A)

```sql
-- Company linkage via org number (deterministic match key)
WITH linked AS (
  SELECT
    COALESCE(normalize_org_nr(h.org_number), normalize_org_nr(t.org_number)) AS match_key,
    ARRAY_REMOVE(ARRAY_AGG(DISTINCT h.hubspot_id), NULL) AS source_hubspot_ids,
    ARRAY_REMOVE(ARRAY_AGG(DISTINCT t.tripletex_id), NULL) AS source_tripletex_ids
  FROM hubspot_companies h
  FULL OUTER JOIN tripletex_customers t
    ON normalize_org_nr(h.org_number) = normalize_org_nr(t.org_number)
  GROUP BY COALESCE(normalize_org_nr(h.org_number), normalize_org_nr(t.org_number))
)
SELECT
  <derive_entity_id_from_match_key_or_use_match_key>(l.match_key) AS id,
  l.source_hubspot_ids,
  l.source_tripletex_ids,
  ...
FROM linked l
```

Identity assignment note:
- Primary key strategy is currently open; see options in `common-data-model.md`.

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

## Open Questions

- Should deleted associations be propagated immediately to targets, or delayed until a reconciliation run confirms they are not transient API inconsistencies?
