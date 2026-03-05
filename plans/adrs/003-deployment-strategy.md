# ADR-003: Deployment with Skaffold, Testcontainers, and Devcontainers

**Status:** Accepted  
**Date:** 2026-03-05

## Context

We need a deployment strategy that supports local development, testing, and a path to production-like environments.

## Decision

- **Development:** Devcontainers for reproducible dev environments
- **Testing:** Testcontainers for integration tests with real Postgres, simulators, etc.
- **Deployment:** Skaffold.dev for building, pushing, and deploying to Kubernetes

## Rationale

- Devcontainers ensure consistent environments across team members
- Testcontainers allows spinning up real dependencies in tests without manual setup
- Skaffold provides fast inner-loop development with hot-reload to K8s
- All three work well together: develop in devcontainer, test with testcontainers, deploy with Skaffold

## Consequences

- Each component needs a Dockerfile and K8s manifests
- Skaffold config (`skaffold.yaml`) defines the build/deploy pipeline
- Devcontainer config (`.devcontainer/`) includes all dev dependencies
- Integration tests use testcontainers-python to spin up Postgres, simulators, etc.
