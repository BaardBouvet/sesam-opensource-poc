# Last Modified Wins

Allow selected properties to be resolved by most-recent change timestamp instead of fixed source mastership.

This feature is the primary driver for tracking per-property timestamps in the common model.

## Timestamp Columns (Feature-Owned)

When `last_modified_wins` is enabled, add/maintain these columns in golden entities:

- `source_last_modified_at_hubspot` (timestamp)
- `source_last_modified_at_tripletex` (timestamp)
- `property_last_modified_at_json` (jsonb, per-property timestamps)
- `golden_last_modified_at` (timestamp)

Apply to:
- `person`
- `company`
- `person_company_association` (as relevant for association-level conflict resolution)

## Options

### Option A: Source timestamp comparison (Recommended)
- Compare per-property timestamps from source payload/history.
- Use `property_last_modified_at_json` in the common model as canonical timestamp input.
- Pick the latest value when rule is `strategy=last_modified_wins`.
- Best fit for small set of opted-in fields.

### Option B: Internal change journal
- Store all observed field changes in `property_change_log`.
- Resolve from the journal even if source timestamp formats differ.
- Better auditability, more storage and processing cost.

## Prerequisites

- Reliable property-level timestamps from both systems.
- `property_last_modified_at_json` populated for opted-in properties.
- Normalized timezone handling (`UTC` everywhere).
- Clear tie-breaker when timestamps are equal.

## Edge Cases

- Equal timestamp conflict: fallback to source priority list.
- Missing timestamp on one side: use available timestamp and mark low confidence.
- Delayed webhook/out-of-order update: re-read source before final write-back.

## Open Questions

- Which properties are eligible in Phase 2 (e.g. phone, title, address)?
- How should we handle missing `property_last_modified_at_json` values for one source/property?
