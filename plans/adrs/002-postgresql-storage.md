# ADR-002: PostgreSQL as Primary Storage

**Status:** Accepted  
**Date:** 2026-03-05

## Context

We need a data store for the merged/golden records (persons, companies), staging tables, mapping tables, and audit trails. Must support BI/reporting and write-back scenarios.

## Decision

Use **PostgreSQL** as the primary data store for the PoC.

## Rationale

- Proven, widely available, great tooling support
- Supports IVM via [pg-trickle](https://github.com/grove/pg-trickle) for incremental materialized views
- dlt has first-class Postgres support as a destination
- Dagster has native Postgres I/O managers
- Easy to run in containers (testcontainers, devcontainers, Skaffold)
- SQL-based golden record views work well with BI tools

## Consequences

- Golden records can be views or incrementally materialized views
- MDM rule logic can live in SQL or Python (decision deferred)
- Simulator storage also uses the same Postgres instance, but in isolated simulator schemas
- Access control must prevent model/MDM roles from reading simulator schemas directly
- Alternative backends (Snowflake, DuckDB) out of scope for now; see [ADR-002 consequences](./002-postgresql-storage.md)
