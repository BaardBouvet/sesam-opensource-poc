# Fuzzy Merging with Agent & Human Curation

Add probabilistic matching for ambiguous records and a review process for low-confidence links.

## Options

### Option A: Splink-based probabilistic linkage (Recommended)
- Use Splink to compute match probabilities over names, email, phone, org refs.
- Auto-accept above threshold, auto-reject below threshold, review middle band.

### Option B: dedupe.py + labeling workflow
- Build a smaller custom pipeline with explicit human labeling rounds.
- More manual setup, useful if feature vectors are highly custom.

### Option C: LLM-assisted triage
- LLM suggests merge decisions for uncertain cases.
- Keep human final approval for safety.

## Curation Flow

1. Candidate pairs generated.
2. Confidence score assigned.
3. `review_queue` receives uncertain pairs.
4. Human accepts/rejects.
5. Decision written back to linkage overrides.

## Data Model Additions

- `linkage_candidates`
- `linkage_reviews`
- `linkage_overrides`

## Open Questions

- Which confidence thresholds should we start with?
- Do we need a dedicated UI in Phase 2, or simple SQL/CSV review first?
