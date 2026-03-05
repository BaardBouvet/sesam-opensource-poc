# Plan 04: MDM Rules & Write-Back

Master data management rules per property, compare-and-swap writes, and syncing data back to HubSpot and Tripletex.

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

- **What:** Compare golden record state with last-known state of each target system. Write only the changed fields.
- **Steps:**
  1. Read current golden record
  2. Compare with last-synced snapshot (stored in a `sync_state` table)
  3. Build minimal API payload with only changed fields
  4. Write via API, update sync_state on success
- **Pros:**
  - Minimal API calls, respects rate limits
  - Natural compare-and-swap: include version/etag in sync_state
  - Clear audit trail of what was written and when
- **Effort:** Medium

### Option B: Full Record Write-Back

- **What:** Always push the full golden record to target systems.
- **Pros:** Simpler logic, no diff needed
- **Cons:** More API calls, risk of overwriting concurrent changes, wasteful
- **Effort:** Low

## Compare-and-Swap

To avoid lost writes when MDM rules say "name from HubSpot, phone from Tripletex":

1. Read current value from target system before write
2. Verify it matches expected state (from last sync)
3. If changed externally, re-evaluate MDM rules with fresh data
4. Write only if state matches expectations

Both HubSpot and Tripletex support conditional updates or at least return current state on read.

## Open Questions

- Does Tripletex support partial updates (PATCH) or only full PUT?
- Does HubSpot expose ETags or version numbers for CAS?
- Should we start with Option A (SQL rules) and extract to Option C (declarative config) later?
- How do we handle new records? (insert into target system that doesn't have the record yet)
