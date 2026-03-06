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

## Option 5 — Cluster grouping without canonical ID; namespaced member references

Summary:
- No synthetic golden ID is assigned to a cluster.
- The golden record's identity is the cluster itself — represented as the set of its namespaced source members (e.g. `hubspot:hs-12345`, `tripletex:tx-9876`).
- The golden record may use any cluster member as a representative reference, or carry the full member set.
- On write-back, the outbound transform resolves the cluster to all local identifiers in the target namespace (e.g. all `hubspot:*` members when writing to HubSpot).
- All source identifiers are namespaced as `{source_system}:{source_id}` throughout the system.

Namespaced identifier format:
- `hubspot:12345`, `tripletex:98765`
- Compact, unambiguous, greppable, no extra lookup needed to know which system owns the id.
- Used in `entity_link`, cluster membership, golden record source-id arrays, and write-back resolution.

Pros:
- **Pure IVM solution**: the entire pipeline from `entity_link` to golden records can be expressed as SQL views maintained by IVM. The cluster resolver is a SQL view (e.g. recursive CTE for connected components + deterministic representative selection), not an imperative process. The only non-IVM writes are link writers inserting into `entity_link`.
- No synthetic ID lifecycle to manage (no split/merge/remap of golden IDs).
- Cluster identity is inherent in its membership — no extra state to keep in sync.
- Write-back is naturally scoped: expand all members whose namespace matches the target system.
- Namespaced ids make provenance and debugging self-describing everywhere in the pipeline.
- No ID churn risk from surrogate reassignment.

Cons:
- If the member set changes (new source added, duplicate discovered), the cluster representative changes — downstream bookmarks/URLs break.
- External APIs that accept a single entity ID need a resolution step to choose the right namespaced member.
- Ordering/indexing on a derived grouping key is less natural than on a stored scalar UUID.

Cluster representative strategy:

The representative is a deterministic value derived from the cluster's member set. Two sub-options:

**A. Deterministic hash (preferred)**
- `md5(sort(members))` or similar — opaque, fixed-length, good for indexes.
- Does NOT leak source system internals into BI reports or external URLs.
- Changes whenever any member is added or removed (same instability as any derived key).

**B. MIN(namespaced member) (kept for reference)**
- `MIN(member)` — e.g. picks `hubspot:150` over `tripletex:900` lexicographically.
- Human-readable, but exposes source system naming in BI/API surfaces.
- More stable than hash (survives additions/removals of non-minimum members) but still changes when a new minimum is introduced or the current minimum is removed.

The stability comparison:

| Event | Hash | MIN(member) |
|---|---|---|
| Member added, not new min | **changes** | stable |
| Member added, is new min | **changes** | **changes** |
| Member removed, not current min | **changes** | stable |
| Member removed, is current min | **changes** | **changes** |

Verdict: if instability is acceptable, hash wins because it hides implementation details. If instability is *not* acceptable, neither works and Option 5 is off the table — fall back to Option 2/3 with a persistent surrogate.

Design sketch:
```
entity_link:
  left:  namespaced_id   # e.g. 'hubspot:hs-12345'
  right: namespaced_id   # e.g. 'tripletex:tx-9876'
  entity_type: string
  link_source: string
  confidence: float?
  ...

cluster_membership:       # SQL view (IVM-maintained), not a persisted imperative output
  entity_type: string
  cluster_representative: text  # deterministic hash of sorted members, e.g. md5(sort(members))
  member: namespaced_id

golden record (IVM projection):
  cluster_representative: text  # deterministic hash; GROUP BY key; exposed as scalar handle for consumers
  members: namespaced_id[]
  source_hubspot_ids: string[]   # extracted from members where namespace = 'hubspot'
  source_tripletex_ids: string[] # extracted from members where namespace = 'tripletex'
  ...

write-back resolution:
  target_ids = members
    .filter(m => m.namespace == target_system)
    .map(m => m.local_id)
```

Primary key resolution (resolved):
- No stored PK column. Golden records are view rows grouped by `cluster_representative`.
- `cluster_representative` is a deterministic hash of sorted cluster members (e.g. `md5(sort(members))`), exposed as a scalar handle for BI dashboards and external APIs.
- Opaque — does not leak source system naming into consumer surfaces.
- FKs between golden entities (e.g. association → person/company) resolve through `cluster_membership` joins on namespaced source IDs — not through a stored FK column.

| Consumer | Needs scalar PK? | What they use |
|----------|-------------------|---------------|
| IVM golden views | No | GROUP BY `cluster_representative` |
| IVM inter-entity joins | No | Join through `cluster_membership` on namespaced IDs |
| Write-back | No | All namespaced members for target system |
| BI / dashboards | Useful | `cluster_representative` column |
| External APIs | Useful | `cluster_representative` or namespaced ID |

FK convention options:
- A: join through `cluster_membership` view on namespaced source IDs (pure IVM, no stored FK).
- B: use `cluster_representative` as a derived FK column (simpler for consumers, changes if representative changes).

Best fit when:
- The team wants to avoid synthetic ID lifecycle entirely.
- Write-back is always scoped to a known target system namespace.
- Consumers can tolerate set-based or representative-based identity.

Comparison with Option 2:

| Criterion | Option 2 (cluster + golden ID) | Option 5 (cluster + member refs) |
|-----------|-------------------------------|----------------------------------|
| **Pure IVM** | No — resolver is imperative (assigns/persists IDs) | **Yes** — resolver is a SQL view; full pipeline is IVM |
| Stored PK | Yes (assigned by resolver) | No — `cluster_representative` is derived GROUP BY key |
| ID lifecycle complexity | Split/merge/remap needed | No synthetic ID to manage |
| FK between golden entities | Simple UUID FK | Join through `cluster_membership` view or derived `cluster_representative` |
| Write-back resolution | Lookup source member by golden ID | Direct: expand all namespaced members in target namespace |
| Namespace clarity | Source IDs in arrays; golden ID separate | Namespaced everywhere; self-describing |
| Cluster membership change impact | Golden ID can stay stable | Representative may change (deterministically) |
| Audit/provenance | Link → cluster → golden ID chain | Link → cluster → member set (shorter chain) |

## Recommended Direction

**Current favourite: Option 5 with deterministic hash representative.**

Why:
- Pure IVM — the entire pipeline from `entity_link` to golden records is SQL views. The only imperative writes are link writers inserting into `entity_link`.
- No synthetic ID lifecycle to manage (no split/merge/remap).
- Deterministic hash hides implementation details from BI/API consumers.
- The key trade-off (representative instability on membership changes) is acceptable for a PoC. If it becomes a problem in production, Option 2's persistent surrogates can be adopted later — the `entity_link` input architecture is the same.

Fallback: Option 2 (cluster + golden ID) if stable scalar IDs turn out to be essential.

## Open Design Questions

### Shared (Options 2 and 5)

1. Cluster resolver algorithm:
   - Union-Find in app layer
   - Recursive SQL CTE
   - Graph engine/library
2. Recompute mode:
   - Incremental (delta links)
   - Full recompute with versioned outputs
3. Confidence governance:
   - Thresholds for auto-accept fuzzy links
   - Human approval queue and audit model
4. Idempotency and replay:
   - Contract for reprocessing same links without identity drift

### Option 2 specific

5. Split/merge policy:
   - How to preserve/reassign `golden_id`
   - How to expose remap history to downstream consumers

### Option 5 specific

6. Representative selection rule: **resolved → deterministic hash of sorted members** (see cluster representative strategy section above). MIN(member) kept as documented alternative.

## Suggested Next Decision Gate

Before implementation starts, choose and document:

- Option 2 vs Option 5 (or hybrid)
- Cluster resolver algorithm (initial)
- Recompute mode (initial)
- Namespaced identifier format convention
- FK convention for inter-entity references
- Minimal audit fields for link evidence and curator actions
