# Sesam Open-Source PoC — Master Plan

A PoC for collecting contacts and companies from **HubSpot** (CRM) and **Tripletex** (ERP), merging them into a common model for BI/reporting, preserving many-to-many contact→company associations, and syncing changes back with configurable MDM rules (including singleton projection for Tripletex contacts).

## Key Decisions (ADRs)

| ADR | Decision |
|-----|----------|
| [ADR-001](adrs/001-python-stack.md) | Python as primary language |
| [ADR-002](adrs/002-postgresql-storage.md) | PostgreSQL as primary storage |
| [ADR-003](adrs/003-deployment-strategy.md) | Skaffold + Testcontainers + Devcontainers |

## API Artifacts in Repository

- Tripletex OpenAPI snapshot: `specs/tripletex/openapi.json`
- Tripletex webhook reference: `specs/tripletex/webhook-reference.md`
- HubSpot required API docs index: `specs/hubspot/required-api-docs.md`

## Naming Conventions

- Plan files are **not numbered**.
- Use lowercase kebab-case filenames (e.g. `data-ingestion.md`, `record-linkage.md`).
- Group folders use short names: `ingest/`, `model/`, `sync/`, `ops/`, `test/`.
- One plan per concern/capability; avoid bundling unrelated items.
- Keep phase assignment in this `README.md` only.
- Keep cross-plan links relative and stable (`[Name](file-name.md)`).
- Do not summarize one plan inside another plan.
- A concise `Related Plans` section is encouraged when it improves navigation.
- If another plan is mentioned, always link to it.
- `Open Questions` sections must contain only unresolved questions.
- When a question is answered, remove it from `Open Questions` and move the outcome to the relevant decision/rules section in the same plan.

## Architecture Overview

```
┌──────────────┐     ┌──────────────┐
│   HubSpot    │     │  Tripletex   │
│  (or sim)    │     │  (or sim)    │
└──────┬───────┘     └──────┬───────┘
       │  read                │  read
       ▼                      ▼
┌─────────────────────────────────────┐
│          Ingestion (dlt)            │
├─────────────────────────────────────┤
│     Staging Tables (Postgres)       │
├─────────────────────────────────────┤
│  Record Linkage (static rules)      │
├─────────────────────────────────────┤
│  Association Linkage (contact↔company) │
├─────────────────────────────────────┤
│  MDM Rules (SQL views / IVM)        │
├─────────────────────────────────────┤
│  Golden Records: Person & Company   │
└──────────────┬──────────────┬───────┘
               │              │
               │ BI path      │ Write-back path
               ▼              ▼
┌──────────────────────────┐  ┌──────────────────────────────┐
│ Analytics Views (OLAP)   │  │ Reference Mapping (reverse)  │
├──────────────────────────┤  ├──────────────────────────────┤
│ BI / Reporting           │  │ Target-Specific Transforms   │
└──────────────────────────┘  │ (HubSpot/Tripletex payloads) │
                              └──────────────┬───────────────┘
                                             │  write-back
                                             ▼
                                 ┌──────────────┐     ┌──────────────┐
                                 │   HubSpot    │     │  Tripletex   │
                                 └──────────────┘     └──────────────┘

Orchestration: Dagster (schedules + sensors)
Webhooks: Phase 2 optional (see `ingest/webhooks.md`)
Tracing: Phase 2 optional (OpenTelemetry)
Monitoring: Phase 2 optional (Prometheus + Grafana)
Simulators: FastAPI mock servers
```

## Phase 1 — Core PoC

Goal: End-to-end data flow from simulated sources through golden records and back.

Phase 1 is polling/scheduled-sync first (no webhook dependency).

## Phase 1 — Core Plans

| Area | Plan | Summary | Key Decision / Notes |
|------|------|---------|----------------------|
| Testing & Simulation | [Simulators](test/simulators.md) | Fake HubSpot & Tripletex | FastAPI (recommended) vs WireMock vs fixtures |
| Source & Ingestion | [Data Ingestion](ingest/data-ingestion.md) | Read from HubSpot & Tripletex | dlt (recommended) vs raw API vs Airbyte |
| Transformation & Modeling | [Record Linkage](model/record-linkage.md) | Merge records + associations across systems | Deterministic keys (recommended) vs scoring vs fuzzy |
| Transformation & Modeling | [Common Data Model](model/common-data-model.md) | Person, Company, Association schema | SQL views (recommended) vs ETL tables vs dbt |
| Write-Back & Governance | [MDM Rules & Write-Back](sync/mdm-rules-writeback.md) | Per-property mastering, CAS writes | SQL rules (recommended) vs Python vs declarative config |
| Platform & Operations | [Orchestration](ops/orchestration.md) | Scheduling & coordination | Dagster (recommended) vs Prefect vs cron vs Temporal |

## Phase 2 — Optional Plans

| Area | Plan | Summary | Key Decision / Notes |
|------|------|---------|----------------------|
| Source & Ingestion | [Webhooks](ingest/webhooks.md) | Event-driven ingestion and replay | After polling-first baseline |
| Source & Ingestion | [Deletion Tracking & Verification](ingest/deletion-tracking.md) | Tombstones and delete verification | Hardening beyond baseline polling sync |
| Transformation & Modeling | [Reference Mapping](model/reference-mapping.md) | Value translation (countries, etc.) | Late binding: apply after golden records are materialized |
| Transformation & Modeling | [Analytics Views](model/analytics-views.md) | OLAP-friendly projections for BI/reporting | Separate plan; late-binding over golden records |
| Write-Back & Governance | [Deletion Conflict Policies](sync/deletion-conflict-policies.md) | Delete-vs-update conflict handling | Optional policy hardening |
| Platform & Operations | [API Gateway & Traffic Control](ops/api-gateway-traffic-control.md) | API gateway controls for ingress/egress | For event-driven and higher-scale operation |
| Platform & Operations | [Traceability](ops/traceability.md) | Audit trail for all writes | OpenTelemetry + audit table |
| Platform & Operations | [Monitoring](ops/monitoring.md) | Dashboards & alerting | Prometheus+Grafana + Dagster UI |
| Platform & Operations | [Alternative Backends](ops/alternative-backends.md) | Other warehouse/runtime choices | Snowflake, DuckDB |
| Transformation & Modeling | [Last Modified Wins](model/last-modified-wins.md) | Per-field timestamp conflict resolution | Requires source timestamps |
| Transformation & Modeling | [Fuzzy Merging with Agent & Human Curation](model/fuzzy-merging-curation.md) | Probabilistic matching workflow | Potential spin-off project |

## Backlog / Spin-Offs

- [Spin-Off Ideas](spin-off-ideas.md)

## Implementation Order

Suggested order based on dependencies:

1. **Simulators** — enables all development without real accounts
2. **Data Ingestion** — read entities + associations into Postgres, include tombstones
3. **Record Linkage** — merge records across systems and link associations
4. **Common Data Model** — golden record views (person/company/association)
5. **MDM Rules & Write-Back** — per-property rules and CAS writes
6. **Orchestration** — schedules/sensors and dependency graph

Phase 2 sequencing note:
- **Reference Mapping** is late binding and runs after golden records are materialized.

## Open Questions (Cross-Cutting)

- What BI tool will consume the golden records?
- Are there real HubSpot/Tripletex test accounts available, or simulators only for now?
- What's the target timeline for Phase 1?
- How many team members will work on this?
