# Reference Mapping

Handle value mappings between systems (e.g. Tripletex uses Norwegian country names, HubSpot uses English).

## Options

### Option A: Mapping Table in Postgres — Recommended

- **What:** A `reference_mapping` table that translates values between systems.
- **Schema:**
  ```sql
  CREATE TABLE reference_mapping (
    id SERIAL PRIMARY KEY,
    domain TEXT NOT NULL,       -- e.g. 'country', 'currency', 'industry'
    source_system TEXT NOT NULL, -- e.g. 'tripletex', 'hubspot'
    source_value TEXT NOT NULL,
    canonical_value TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT now()
  );
  -- Example rows:
  -- ('country', 'tripletex', 'Norge', 'Norway')
  -- ('country', 'tripletex', 'Sverige', 'Sweden')
  -- ('country', 'hubspot', 'Norway', 'Norway')
  ```
- **Country merge input table (Phase 2 extension):**
  ```sql
  CREATE TABLE country_merge_input (
    id SERIAL PRIMARY KEY,
    source_system TEXT NOT NULL,       -- 'tripletex' | 'hubspot'
    source_value TEXT NOT NULL,        -- raw country value from source
    canonical_iso_code TEXT NOT NULL,  -- e.g. NO, SE
    canonical_name TEXT NOT NULL,      -- e.g. Norway, Sweden
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT now()
  );
  ```
  Use this as the authoritative merge input for building/updating the `country` entity in the common model.
- **Pros:**
  - Simple, queryable, easy to maintain
  - Can be joined in SQL views (works with IVM)
  - Easy to seed with known mappings
  - Can add new domains without code changes
- **Cons:**
  - Manual maintenance — must add mappings for new values
  - No fuzzy matching for unknown values
- **Effort:** Low

### Option B: Python Mapping Functions

- **What:** Define mapping logic in Python dictionaries or functions.
- **Pros:**
  - Can include logic (e.g. fuzzy matching, case normalization)
  - Easy to test
- **Cons:**
  - Can't use in SQL views / IVM
  - Mapping data lives in code, not database
- **Effort:** Low

### Option C: External Reference Data Service

- **What:** Use an external service or dataset (e.g. ISO country codes) as the canonical reference.
- **Pros:**
  - Standardized, authoritative data
  - Covers edge cases we might miss
- **Cons:**
  - Overkill for a PoC
  - External dependency
- **Effort:** Medium

## Known Mappings Needed

| Domain   | Tripletex Value | HubSpot Value | Canonical |
|----------|-----------------|---------------|-----------|
| Country  | Norge           | Norway        | NO (ISO)  |
| Country  | Sverige         | Sweden        | SE (ISO)  |
| Country  | Danmark         | Denmark       | DK (ISO)  |
| ...      | ...             | ...           | ...       |

Country-specific note:
- Country mappings should be managed in `country_merge_input` and then projected into `reference_mapping`/`country` views as needed.

## Open Questions

- Should canonical values use ISO codes (NO, SE) or English names (Norway, Sweden)?
- Are there other domains besides country that need mapping? (currency, industry codes, etc.)
- How do we handle unmapped values? (reject, pass through, flag for review?)
