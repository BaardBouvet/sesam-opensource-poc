# ADR-001: Python as Primary Language

**Status:** Accepted  
**Date:** 2026-03-05

## Context

We need a primary language for the PoC that covers data ingestion, transformation, orchestration, and API integration with HubSpot and Tripletex.

## Decision

Use **Python** as the primary language for the entire PoC.

## Rationale

- Best ecosystem for data integration: Dagster, dlt, pandas, SQLAlchemy
- Both HubSpot and Tripletex have Python-friendly REST APIs
- Native support in all candidate orchestrators (Dagster, Prefect, Airflow)
- dlt (dlthub) is Python-native and has existing HubSpot connectors
- Team familiarity and hiring pool for data engineering roles

## Consequences

- Simulators will also be Python (e.g. FastAPI)
- Testing via pytest + testcontainers-python
- Need to manage Python environments (uv/poetry)
