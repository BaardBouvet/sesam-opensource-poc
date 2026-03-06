# Last Modified Wins

Allow selected properties to be resolved by most-recent change timestamp instead of fixed source mastership.

## Options

### Option A: Source timestamp comparison (Recommended)
- Compare per-property timestamps from source payload/history.
- Pick the latest value when rule is `strategy=last_modified_wins`.
- Best fit for small set of opted-in fields.

### Option B: Internal change journal
- Store all observed field changes in `property_change_log`.
- Resolve from the journal even if source timestamp formats differ.
- Better auditability, more storage and processing cost.

## Prerequisites

- Reliable property-level timestamps from both systems.
- Normalized timezone handling (`UTC` everywhere).
- Clear tie-breaker when timestamps are equal.

## Edge Cases

- Equal timestamp conflict: fallback to source priority list.
- Missing timestamp on one side: use available timestamp and mark low confidence.
- Delayed webhook/out-of-order update: re-read source before final write-back.

## Open Questions

- Which properties are eligible in Phase 2 (e.g. phone, title, address)?
- Can Tripletex provide property-level timestamps, or only entity-level timestamps?
