# Alternative Backends

Support additional storage backends beyond PostgreSQL after core PoC is stable.

## Options

### Option A: Snowflake + dynamic tables (Recommended first alternative)
- Keep transformation model SQL-first.
- Replace Postgres-specific IVM with Snowflake-native dynamic table patterns.

### Option B: DuckDB local analytics backend
- Useful for local demos, reproducible tests, and lightweight BI extracts.
- Strong developer ergonomics, limited multi-writer capabilities.

### Option C: Backend abstraction layer
- Introduce backend adapters for ingestion/transforms/write-back state tables.
- Higher upfront complexity, better portability.

## Migration Approach

1. Keep canonical model contracts backend-agnostic.
2. Isolate Postgres-specific SQL in adapter folders.
3. Port one pipeline slice first (contacts + companies read path).

## Open Questions

- Which backend is first priority for stakeholders: Snowflake or DuckDB?
- Do we need parity for write-back state tables on day one of migration?
