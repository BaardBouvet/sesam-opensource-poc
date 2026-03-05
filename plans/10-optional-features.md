# Plan 10: Optional Features & Future Work (Phase 2)

Features from `GOAL.md` that extend the core PoC. Each can be tackled independently.

---

## 10a: Last Modified Wins

**What:** For some properties, the most recently changed value wins regardless of source system.

**Implementation options:**
1. **Timestamp comparison in SQL** — requires both systems to expose `updatedAt` per field (HubSpot does via property history; Tripletex unclear)
2. **Change tracking table** — record every field change with timestamp, pick latest during merge

**Prerequisite:** Property-level timestamps from source systems.

**Effort:** Medium

---

## 10b: Fuzzy Merging with Agent & Human Curation

**What:** Use probabilistic matching (Splink, dedupe.py) with an LLM agent for uncertain cases and a human review UI.

**Implementation options:**
1. **Splink** — probabilistic record linkage, outputs match probabilities
2. **LLM-based** — use an LLM to evaluate uncertain matches with context
3. **Review UI** — Streamlit/Retool app showing uncertain matches for human decision

**Spin-off potential:** High — could be a standalone product.

**Effort:** High

---

## 10c: Webhook Support for Near Real-Time Sync

**What:** Use HubSpot and Tripletex webhooks to trigger sync immediately on changes.

**Implementation options:**
1. **Dagster sensor** — listens for webhook events and triggers pipeline runs
2. **Standalone webhook receiver** — FastAPI app that receives webhooks and enqueues work
3. **Hybrid** — webhooks for low-latency, scheduled polling as fallback

**Note:** Both HubSpot (webhook subscriptions) and Tripletex (callback API) support webhooks for some entity types.

**Effort:** Medium

---

## 10d: Alternative Backends

**What:** Support Snowflake (dynamic tables) or other DBs as alternatives to Postgres.

**Implementation options:**
1. **Snowflake with dynamic tables** — dynamic tables replace IVM, SQL views work similarly
2. **DuckDB** — embedded analytics DB, good for local development
3. **Abstraction layer** — use SQLAlchemy or a query builder to keep backend-agnostic

**Prerequisite:** Core PoC working on Postgres first.

**Effort:** Medium-High (per backend)

---

## Spin-Off Project Ideas

These are larger efforts mentioned in GOAL.md. Tracked here for visibility:

| Idea                                    | Status       | Reference                                                      |
|-----------------------------------------|--------------|----------------------------------------------------------------|
| Declarative MDM rules → SQL compiler    | Idea         | See [Plan 04](./04-mdm-rules-writeback.md) Option C            |
| dlt source for Tripletex                | Needed       | See [Plan 01](./01-data-ingestion.md) — may become OSS library |
| pg-trickle IVM for Postgres             | Existing     | https://github.com/grove/pg-trickle                            |
| Generic API simulator tool              | Idea         | See [Plan 08](./08-simulators.md)                              |
