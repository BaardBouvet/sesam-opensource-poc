# Plan 05: Orchestration

Schedule and coordinate reads from source systems, transformations, and write-backs.

## Options

### Option A: Dagster — Recommended

- **What:** Use [Dagster](https://dagster.io/) as the orchestrator. Define assets for each stage (ingest → transform → write-back).
- **Pros:**
  - Asset-based model fits naturally: each table/view is a software-defined asset
  - Built-in schedules, sensors, and partitioning
  - Great UI (Dagit/Dagster UI) for observability
  - First-class dlt integration (`dagster-dlt`)
  - First-class Postgres I/O manager
  - Active open-source community
- **Cons:**
  - Learning curve for asset/op/job concepts
  - Dagster daemon + webserver add infrastructure overhead
  - Might be heavy for a 2-source PoC
- **Deployment:** Runs as containers in Skaffold; needs Postgres for its own metadata
- **Effort:** Medium

### Option B: Prefect

- **What:** Use [Prefect](https://www.prefect.io/) for flow orchestration.
- **Pros:**
  - Pythonic decorator-based API, quick to learn
  - Good scheduling and retry support
  - Cloud UI available (or self-host Prefect server)
- **Cons:**
  - Less asset-centric than Dagster (more task-centric)
  - No built-in dlt integration
  - Community has shifted toward Prefect Cloud
- **Effort:** Medium

### Option C: Simple Cron + Python Scripts

- **What:** Use Kubernetes CronJobs (via Skaffold) to trigger Python scripts on schedule.
- **Pros:**
  - Zero framework overhead
  - Easy to understand and debug
  - Minimum dependencies
- **Cons:**
  - No built-in retries, dependency tracking, or UI
  - Harder to manage as pipeline complexity grows
  - Must build observability from scratch
- **Effort:** Low initially, High as complexity grows

### Option D: Temporal

- **What:** Use [Temporal](https://temporal.io/) for durable workflow execution.
- **Pros:**
  - Excellent retry, timeout, and saga support
  - Great for long-running write-back workflows with CAS
  - Language-agnostic (Python SDK available)
- **Cons:**
  - Not designed for data pipelines specifically
  - Heavy infrastructure (Temporal server + DB)
  - Overkill for scheduled batch reads
- **Effort:** High

## Proposed Schedule (Draft)

| Pipeline                | Frequency   | Notes                           |
|-------------------------|-------------|---------------------------------|
| HubSpot ingest          | Every 15 min | Incremental via `updatedAfter`  |
| Tripletex ingest        | Every 15 min | Incremental via last modified   |
| Record linkage + MDM    | After ingest | Triggered by asset dependency   |
| Write-back to HubSpot   | After MDM    | Only if changes detected        |
| Write-back to Tripletex | After MDM    | Only if changes detected        |

## Open Questions

- Should Dagster share the same Postgres instance as the data, or have its own?
- Do we need real-time sensors (e.g. webhook-triggered) in Phase 1, or is scheduled polling enough?
