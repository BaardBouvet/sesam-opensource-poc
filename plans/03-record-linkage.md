# Plan 03: Record Linkage

Merge contacts and companies across HubSpot and Tripletex into unified golden records using static rules.

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
  - Companies: `org_number` (exact), `name` (normalized) as fallback
  - Persons: `email` (exact), `phone` (normalized), `first_name + last_name + company` (composite)
- **Effort:** Low

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
-- Company linkage via org number
SELECT
  COALESCE(h.company_id, gen_random_uuid()) AS id,
  h.hubspot_id AS source_hubspot_id,
  t.tripletex_id AS source_tripletex_id,
  ...
FROM hubspot_companies h
FULL OUTER JOIN tripletex_customers t
  ON normalize_org_nr(h.org_number) = normalize_org_nr(t.org_number)
```

## Open Questions

- Is org number reliably present in both systems?
- For persons, is email the best primary key or do we need phone-based matching too?
- How do we handle 1:N matches (one HubSpot contact matching multiple Tripletex contacts)?
