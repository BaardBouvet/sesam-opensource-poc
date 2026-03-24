# Devcontainer

PostgreSQL 18 with `pg_trickle` preinstalled. No example data is loaded automatically — start fresh and load whichever example you want.

## What is preconfigured

- `pg_trickle` installed from the official release archive (`v0.9.0` by default)
- `shared_preload_libraries=pg_trickle`, `max_worker_processes=16`
- `CREATE EXTENSION pg_trickle` runs on first database initialization
- PostgreSQL data persisted in a named Docker volume at `/var/lib/postgresql`
- Connection env vars pre-set: `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`

## Running an example

1. Open this repository in VS Code.
2. Run **Dev Containers: Reopen in Container**.
3. Load the example's seed data and stream tables:

```bash
psql -v ON_ERROR_STOP=1 -f examples/<name>/seed.sql
psql -v ON_ERROR_STOP=1 -f examples/<name>/pgtrickle.sql
```

For example:

```bash
psql -v ON_ERROR_STOP=1 -f examples/person_with_orders/seed.sql
psql -v ON_ERROR_STOP=1 -f examples/person_with_orders/pgtrickle.sql
```

## Reset database

Remove the named volume and rebuild to get a clean Postgres instance:

```bash
docker volume rm sesam-opensource-poc-pgroot
```
