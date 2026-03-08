# Mappings

Declarative integration mappings using the [OSI Mapping Schema](https://github.com/BaardBouvet/OSI-mapping) (v1.0).

Primary file for validation is [`mapping.yaml`](mapping.yaml), which consolidates all targets and mappings in one file.

## Files

| Mapping | Target Entity | Sources | Key Feature |
|---------|---------------|---------|-------------|
| [mapping.yaml](mapping.yaml) | all targets | all sources | Canonical single-file mapping for upstream validator |

## Resolution Strategy Summary

| Field | Strategy | Winner |
|-------|----------|--------|
| Person names | `coalesce` | HubSpot (priority 1) |
| Person address | `coalesce` | Tripletex (priority 1) |
| Person phone | `last_modified` | Most recent timestamp |
| Company name | `coalesce` | HubSpot (priority 1) |
| Company address | `coalesce` | Tripletex (priority 1) |
| Company phone | `last_modified` | Most recent timestamp |
| Associations | `identity` (link_group) | Both systems contribute |

## Cross-Entity References

- `person.country` → `country` entity (FK via country vocabulary)
- `company.country` → `country` entity (FK via country vocabulary)
- `person_company_association.person_id` → `person` entity
- `person_company_association.company_id` → `company` entity

## ADR

See [ADR-004](../plans/adrs/004-osi-mapping-spec.md) for the decision to adopt the OSI Mapping Schema.
