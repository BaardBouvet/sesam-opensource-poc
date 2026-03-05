# Sesam Open-Source PoC — Master Plan

A PoC for collecting contacts and companies from **HubSpot** (CRM) and **Tripletex** (ERP), merging them into a common model for BI/reporting, and syncing changes back with configurable MDM rules.

## Key Decisions (ADRs)

| ADR | Decision |
|-----|----------|
| [ADR-001](adrs/001-python-stack.md) | Python as primary language |
| [ADR-002](adrs/002-postgresql-storage.md) | PostgreSQL as primary storage |
| [ADR-003](adrs/003-deployment-strategy.md) | Skaffold + Testcontainers + Devcontainers |

## Architecture Overview

```
┌──────────────┐     ┌──────────────┐
│   HubSpot    │     │  Tripletex   │
│  (or sim)    │     │  (or sim)    │
└──────┬───────┘     └──────┬───────┘
       │  read                │  read
       ▼                      ▼
┌─────────────────────────────────────┐
│          Ingestion (dlt)            │  ← Plan 01
├─────────────────────────────────────┤
│     Staging Tables (Postgres)       │
├─────────────────────────────────────┤
│  Record Linkage (static rules)      │  ← Plan 03
├─────────────────────────────────────┤
│  Reference Mapping (mapping table)  │  ← Plan 09
├─────────────────────────────────────┤
│  MDM Rules (SQL views / IVM)        │  ← Plan 04
├─────────────────────────────────────┤
│  Golden Records: Person & Company   │  ← Plan 02
├─────────────────────────────────────┤
│  BI / Reporting                     │
└──────┬──────────────────────┬───────┘
       │  write-back          │  write-back
       ▼                      ▼
┌──────────────┐     ┌──────────────┐
│   HubSpot    │     │  Tripletex   │  ← Plan 04 (CAS writes)
└──────────────┘     └──────────────┘

Orchestration: Dagster  ← Plan 05
Tracing: OpenTelemetry  ← Plan 06
Monitoring: Prometheus + Grafana  ← Plan 07
Simulators: FastAPI mock servers  ← Plan 08
```

## Phase 1 — Core PoC

Goal: End-to-end data flow from simulated sources through golden records and back.

| # | Plan | Summary | Key Decision |
|---|------|---------|--------------|
| 1 | [Data Ingestion](01-data-ingestion.md) | Read from HubSpot & Tripletex | dlt (recommended) vs raw API vs Airbyte |
| 2 | [Common Data Model](02-common-data-model.md) | Person & Company golden schema | SQL views (recommended) vs ETL tables vs dbt |
| 3 | [Record Linkage](03-record-linkage.md) | Merge records across systems | Deterministic keys (recommended) vs scoring vs fuzzy |
| 4 | [MDM Rules & Write-Back](04-mdm-rules-writeback.md) | Per-property mastering, CAS writes | SQL rules (recommended) vs Python vs declarative config |
| 5 | [Orchestration](05-orchestration.md) | Scheduling & coordination | Dagster (recommended) vs Prefect vs cron vs Temporal |
| 6 | [Traceability](06-traceability.md) | Audit trail for all writes | OpenTelemetry + audit table (recommended) vs logging |
| 7 | [Monitoring](07-monitoring.md) | Dashboards & alerting | Prometheus+Grafana (recommended) + Dagster UI |
| 8 | [Simulators](08-simulators.md) | Fake HubSpot & Tripletex | FastAPI (recommended) vs WireMock vs fixtures |
| 9 | [Reference Mapping](09-reference-mapping.md) | Value translation (countries, etc.) | Postgres mapping table (recommended) |

## Phase 2 — Optional Features

| Feature | Plan | Notes |
|---------|------|-------|
| Last modified wins | [10a](10-optional-features.md#10a-last-modified-wins) | Needs per-field timestamps from sources |
| Fuzzy merging + curation | [10b](10-optional-features.md#10b-fuzzy-merging-with-agent--human-curation) | Potential spin-off project |
| Webhook real-time sync | [10c](10-optional-features.md#10c-webhook-support-for-near-real-time-sync) | Both systems support webhooks |
| Alternative backends | [10d](10-optional-features.md#10d-alternative-backends) | Snowflake, DuckDB |

## Implementation Order

Suggested order based on dependencies:

1. **Simulators** (Plan 08) — enables all development without real accounts
2. **Data Ingestion** (Plan 01) — read from simulators into Postgres
3. **Reference Mapping** (Plan 09) — needed before building the common model
4. **Record Linkage** (Plan 03) — merge records across systems
5. **Common Data Model** (Plan 02) — golden record views
6. **MDM Rules & Write-Back** (Plan 04) — per-property rules and CAS writes
7. **Orchestration** (Plan 05) — schedule everything (can start earlier in parallel)
8. **Traceability** (Plan 06) — instrument write-back with OTel
9. **Monitoring** (Plan 07) — dashboards and alerting

## Open Questions (Cross-Cutting)

- What BI tool will consume the golden records?
- Are there real HubSpot/Tripletex test accounts available, or simulators only for now?
- What's the target timeline for Phase 1?
- How many team members will work on this?
