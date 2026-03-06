# Golden Record Identity: Literature & Research

A survey of academic and practitioner literature on the problem of assigning and maintaining stable identifiers for golden records in entity resolution / master data management systems.

## Why This Matters

When multiple source records are linked into a single golden record (cluster), the cluster needs an identity. This identity must be usable by downstream consumers (BI dashboards, APIs, write-back pipelines) and must behave predictably when the cluster's membership changes (records added, removed, or clusters split/merged). The choice between **persistent surrogates** and **derived/transient identifiers** is a fundamental architectural decision with significant downstream consequences.

See [linkage-identity-strategy-analysis.md](../plans/model/linkage-identity-strategy-analysis.md) for how these options map to our system design.

---

## 1 — Core Entity Resolution / Record Linkage Theory

### Fellegi & Sunter (1969)
*"A Theory for Record Linkage"*, Journal of the American Statistical Association.

The foundational probabilistic framework for record linkage. Introduces the match/non-match/possible-match classification model. Splink and most modern probabilistic matchers are built on this theory.

### Christen (2012)
*"Data Matching: Concepts and Techniques for Record Linkage, Entity Resolution, and Duplicate Detection"*, Springer.

The most comprehensive textbook covering the full pipeline: blocking, comparison, classification, and — critically — **cluster-level identity assignment**. Directly relevant to the connected-components approach used in our cluster resolver design.

### Elmagarmid, Ipeirotis & Verykios (2007)
*"Duplicate Record Detection: A Survey"*, IEEE Transactions on Knowledge and Data Engineering (TKDE).

Excellent survey covering matching techniques, scalability approaches, and evaluation. Good reference for understanding the landscape of techniques available beyond simple deterministic matching.

---

## 2 — The Golden Record Identity Problem

This is often called the **"entity key persistence"** or **"surrogate key stability"** problem in MDM literature.

### Talburt (2011) ⭐ Most Relevant
*"Entity Resolution and Information Quality"*, Morgan Kaufmann.

**The single most relevant book for the golden record ID problem.** Introduces the concept of **Entity Identity Information (EII)** and distinguishes between:

- **Transient entity references** — derived from cluster membership (analogous to our hash-of-members approach). Cheap to compute, no lifecycle management, but unstable when membership changes.
- **Persistent entity identifiers** — assigned once and maintained across cluster evolution (analogous to our golden_id / Option 2). Requires explicit lifecycle machinery (creation, persistence, split, merge, retirement) but provides stability.

Talburt's EII lifecycle maps directly to the open design questions in our linkage-identity analysis: split/merge policy, remap history, and the trade-off between operational complexity and downstream stability.

### Dreibelbis et al. (2008)
*"Enterprise Master Data Management: An SOA Approach to Managing Core Information"*, IBM Press.

The chapter on **cross-reference management** covers the persistent-surrogate vs derived-cluster-key trade-off in a production enterprise context. Discusses how cross-reference tables (similar to our `entity_cluster_member`) mediate between source system IDs and golden IDs, and the operational patterns for keeping them consistent.

### Loshin (2009)
*"Master Data Management"*, Morgan Kaufmann.

Covers the **golden record lifecycle** including split/merge/remap scenarios. Useful for understanding the operational cost of persistent surrogates — the machinery that our Option 2 would require.

### Allen & Cervo (2015)
*"Multi-Domain Master Data Management: Advanced MDM and Data Governance in Practice"*.

Covers cross-domain identity resolution and the challenge of maintaining stable IDs when cluster membership evolves across multiple entity types (companies, persons, associations). Relevant because our system handles both company and person entities with cross-entity associations.

---

## 3 — Cluster Evolution: Split, Merge, and Recomputation

The hardest part of the identity problem: what happens when clusters change.

### Benjelloun et al. (2009)
*"Swoosh: A Generic Approach to Entity Resolution"*, VLDB Journal.

Formalizes merge operations and their algebraic properties: **idempotence, commutativity, associativity**. These properties determine whether incremental recomputation is safe or whether full recomputation is needed. Directly relevant to the "recompute mode" design question (incremental delta-links vs full recompute with versioned outputs).

### Whang & Garcia-Molina (2014)
*"Entity Resolution with Evolving Rules"*, Proceedings of the VLDB Endowment.

Addresses what happens when matching rules change and clusters need to be recomputed — not just when data changes, but when the logic changes. This is a real concern for any system that will evolve its matching strategy over time (e.g., adding fuzzy matching in Phase 2).

### Gruenheid, Dong & Srivastava (2014)
*"Incremental Record Linkage"*, Proceedings of the VLDB Endowment.

Tackles the delta-link problem: how to update clusters incrementally when new links arrive, without full recomputation. Relevant to the operational efficiency of our cluster resolver, especially as data volume grows.

---

## 4 — The Persistent Surrogate vs Derived Key Debate

### Kimball & Ross (2013)
*"The Data Warehouse Toolkit"*, 3rd edition.

The **surrogate key** chapter is a classic in data warehousing. Their argument for persistent surrogates over natural/derived keys — insulation from source system changes, stability for slowly changing dimensions, join performance — applies directly to the golden record identity choice.

### Dong & Srivastava (2015)
*"Big Data Integration"*, Morgan Kaufmann.

Chapter 4 on entity matching discusses cluster representatives and the stability problem. Covers the trade-off between deterministic-hash representatives (compact, opaque, but unstable on membership change) and persistent surrogates (stable, but requiring lifecycle management).

---

## 5 — Practical / Engineering References

### Splink
[moj-analytical-services/splink](https://github.com/moj-analytical-services/splink) — Open-source probabilistic record linkage library built on Fellegi-Sunter. Their clustering approach and how they handle entity IDs in practice is a useful reference for implementation.

### Zingg
[zingg.ai](https://www.zingg.ai/) — Open-source entity resolution tool that explicitly deals with cluster IDs and incremental matching. Worth evaluating for how they approach the cluster identity lifecycle.

### dbt + Entity Resolution Patterns
The analytics engineering community (e.g., Brooklyn Data Co) has written about the "synthetic surrogate for merged entities" pattern in the context of dbt-based data pipelines. Useful for SQL-centric implementation ideas.

---

## 6 — Summary: How the Literature Maps to Our Options

| Our Option | Literature Equivalent | Key References |
|---|---|---|
| **Option 2** — Persistent golden_id assigned by cluster resolver | Talburt's "persistent entity identifier"; Kimball's surrogate key | Talburt (2011), Kimball & Ross (2013), Loshin (2009) |
| **Option 5 (hash)** — Deterministic hash of sorted members | Talburt's "transient entity reference"; Dong & Srivastava's derived cluster representative | Talburt (2011), Dong & Srivastava (2015) |
| **Option 5 (MIN)** — Lexicographic minimum member as representative | Variant of canonical representative selection | Christen (2012) |
| **Incremental recompute** | Delta-link cluster update | Gruenheid et al. (2014), Benjelloun et al. (2009) |
| **Full recompute with versioning** | Rule-evolution-safe reprocessing | Whang & Garcia-Molina (2014) |

## 7 — Key Takeaway

The literature consistently confirms that:

1. **Transient/derived identifiers** (hashes, MIN-member) are suitable for analytical workloads and PoCs where cluster stability is not critical.
2. **Persistent surrogates** are the industry standard for production MDM systems that need stable references across cluster evolution.
3. The **input architecture** (pairwise links → cluster resolution → golden projection) is sound regardless of which identity strategy is chosen — the link layer is the durable foundation.
4. Designing the link-input layer as the stable contract means the identity strategy can be upgraded from transient to persistent as a **schema addition, not a rewrite**.
