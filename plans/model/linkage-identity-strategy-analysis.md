# Linkage & Identity Strategy Analysis

Purpose: capture the current linkage/identity design and compare alternative approaches before final commitment.

## Current Baseline (as documented)

- Merge inputs come from outside IVM via pairwise links in `entity_link`.
- Sources of links:
  - deterministic matcher (Phase 1)
  - fuzzy/probabilistic matcher (Phase 2)
  - human curation (Phase 2)
- A cluster resolver computes connected components and writes resolved memberships to `entity_cluster_member`.
- Golden IDs (`golden_id`) are assigned by cluster resolver and reused by IVM projections.
- IVM responsibility: projection only (joins/aggregation), not linkage or ID generation.

## Evaluation Criteria

Use these criteria when comparing options:

1. Supports deterministic + fuzzy + human merge inputs in one flow
2. Preserves stable golden IDs across re-runs
3. Keeps IVM logic deterministic and maintainable
4. Handles cluster split/merge events explicitly
5. Operational complexity (state, backfills, reconciliation)
6. Traceability/auditability of merge decisions

## Option 1 — Pure IVM deterministic IDs from match keys

Summary:
- IVM computes IDs directly from canonical deterministic keys (e.g. email/org_number).

Pros:
- Very simple runtime architecture
- No extra resolver state
- Fully in-IVM transformation

Cons:
- Hard to incorporate fuzzy + human pairwise links naturally
- ID churn when canonical keys change
- Weaker fit for many-to-many / transitive merge workflows

Best fit when:
- Linkage is mostly deterministic and key quality is high/stable.

## Option 2 — External pairwise links + cluster resolver (Current baseline)

Summary:
- Pairwise links are first-class inputs; cluster resolver assigns stable golden IDs; IVM joins resolved clusters.

Pros:
- Unified path for deterministic, fuzzy, and human decisions
- Explicit support for transitive merges
- Stable IDs not tied to source business keys
- Clear audit boundary for “why these records are merged”

Cons:
- Additional moving parts (resolver, versioning, retries)
- Requires cluster lifecycle handling (split/merge/remap)

Best fit when:
- Human curation and fuzzy matching are expected to become core, not edge cases.

## Option 3 — Hybrid (deterministic in IVM, external overrides)

Summary:
- Deterministic linkage happens in IVM; external system applies only fuzzy/human overrides.

Pros:
- Lower initial complexity than full externalized linkage
- Faster Phase 1 startup

Cons:
- Two sources of truth for linkage
- Harder conflict semantics and observability
- Migration risk if external linkage later becomes primary

Best fit when:
- Team wants quick deterministic rollout with limited near-term override volume.

## Option 4 — Canonical match-key registry (outside IVM, no pairwise graph)

Summary:
- External process writes `source_record -> canonical_match_key`; IVM groups by key.

Pros:
- Simpler than graph clustering
- Easier SQL joins than pairwise connected components

Cons:
- Less expressive for pairwise “A~B” uncertainty workflows
- Human curation UX usually wants pairwise evidence, not only final keys

Best fit when:
- Matching process can always emit a final canonical key directly.

## Recommended Direction for Exploration

Primary path to evaluate further: Option 2.

Why:
- Best alignment with planned fuzzy merge + human curation capabilities.
- Keeps IVM deterministic while allowing richer, externally curated linkage evidence.

## Open Design Questions for Option 2

1. Cluster resolver algorithm:
   - Union-Find in app layer
   - Recursive SQL CTE
   - Graph engine/library
2. Recompute mode:
   - Incremental (delta links)
   - Full recompute with versioned outputs
3. Split/merge policy:
   - How to preserve/reassign `golden_id`
   - How to expose remap history to downstream consumers
4. Confidence governance:
   - Thresholds for auto-accept fuzzy links
   - Human approval queue and audit model
5. Idempotency and replay:
   - Contract for reprocessing same links without identity drift

## Suggested Next Decision Gate

Before implementation starts, choose and document:

- Resolver algorithm (initial)
- Recompute mode (initial)
- Split/merge ID policy
- Minimal audit fields for link evidence and curator actions
