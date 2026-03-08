# ADR-004: Use OSI Mapping Schema for Model & Mappings

**Status:** Experimental  
**Date:** 2026-03-08

## Context

The PoC needs a way to declare source→target field mappings, identity/linkage rules, resolution strategies, and MDM mastering rules. The existing plan outlines SQL-based rules and hand-rolled views. We want to evaluate a declarative approach.

## Decision

Use the [OSI Mapping Schema](https://github.com/BaardBouvet/OSI-mapping) (v1.0) to declare the common data model, field mappings, resolution strategies, and inline test cases for HubSpot/Tripletex integration.

Mapping YAML files live in `mappings/` at the repository root. The schema covers:
- Target entities (person, company, association, country) with resolution strategies
- Source-to-target field mappings for HubSpot and Tripletex
- Identity fields for record linkage
- Coalesce / last_modified / expression strategies for MDM rules
- Foreign-key references between entities
- Country vocabulary translation
- Inline test cases capturing expected merge/sync behavior

## Rationale

- Single declarative file captures model, mappings, resolution, and tests — replaces hand-maintained SQL views
- Schema is validated with JSON Schema + multi-pass validator
- Resolution strategies (`coalesce`, `last_modified`, `expression`) map directly to MDM mastering rules from [MDM Rules & Write-Back](../sync/mdm-rules-writeback.md)
- `references` feature solves cross-system FK resolution — one of the hardest integration problems
- Inline tests document expected behavior and serve as regression suite
- Can be compiled to SQL views (potential pg-trickle IVM input) or used as engine config

## Consequences

- `mappings/` directory added to the repository
- Validation via `python3 validation/validate.py` (from OSI-mapping repo)
- Existing SQL-based MDM rules plan remains valid — mapping YAML can compile to SQL
- May need a mapping→SQL compiler (future work, see [Spin-Off Ideas](../spin-off-ideas.md))
