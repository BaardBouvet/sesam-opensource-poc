# Plan 01: Data Ingestion (HubSpot & Tripletex)

Read contacts/customers from HubSpot and companies/contacts from Tripletex into PostgreSQL staging tables.

## Options

### Option A: dlt (dlthub) — Recommended

- **What:** Use [dlt](https://dlthub.com/) pipelines with its existing HubSpot verified source; build a custom Tripletex source.
- **Pros:**
  - HubSpot source already exists and is maintained
  - Handles schema inference, incremental loading, and Postgres destination natively
  - Built-in state management for cursor-based pagination
  - Automatic schema evolution and data normalization
- **Cons:**
  - Tripletex source doesn't exist — must write a custom dlt source
  - Less control over exact HTTP behavior (harder to add fine-grained tracing)
- **Effort:** Low for HubSpot, Medium for Tripletex custom source

### Option B: Raw API Clients (httpx/requests)

- **What:** Write Python API clients directly against HubSpot and Tripletex REST APIs. Load into Postgres via SQLAlchemy/psycopg.
- **Pros:**
  - Full control over every HTTP call (great for tracing/OpenTelemetry)
  - No dependency on third-party pipeline framework
  - Can match API docs exactly
- **Cons:**
  - Must handle pagination, rate limiting, retries, schema management manually
  - More code to write and maintain
- **Effort:** High

### Option C: Airbyte Connectors

- **What:** Use Airbyte's existing connectors for HubSpot and Tripletex (if available).
- **Pros:**
  - Large connector catalog, HubSpot connector exists
  - Handles CDC, schema, incremental sync
- **Cons:**
  - Heavy infrastructure (needs Airbyte server or use `airbyte-lib`)
  - Tripletex connector may not exist or be low quality
  - Harder to integrate with custom orchestration
  - Overkill for a PoC
- **Effort:** Medium (setup), Low (if connectors fit)

## Data to Ingest

| System     | Entities             | Key Fields                          |
|------------|----------------------|-------------------------------------|
| HubSpot    | Contacts, Companies  | name, email, phone, address, org ID |
| Tripletex  | Contacts, Customers  | name, phone, address, org number    |

## Open Questions

- Does Tripletex have a public OpenAPI spec we can use to generate the dlt source?
- What authentication does Tripletex use? (employee token? OAuth2?)
- Do we need to ingest associations/relationships (e.g. contact→company in HubSpot)?
