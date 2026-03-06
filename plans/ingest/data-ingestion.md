# Data Ingestion (HubSpot & Tripletex)

Read contacts/customers from HubSpot and companies/contacts from Tripletex into PostgreSQL staging tables.
Contact→company associations are core and must be ingested as first-class datasets.

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
  - Delete detection is not automatic when source APIs do not emit tombstones
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

| System     | Entities                              | Key Fields                                       |
|------------|---------------------------------------|--------------------------------------------------|
| HubSpot    | Contacts, Companies, Associations     | name, email, phone, address, org ID, association |
| Tripletex  | Contacts, Customers, Relationships    | name, phone, address, org number, relationship   |

## Tripletex OpenAPI Strategy

- Use `https://tripletex.no/v2/openapi.json` as the canonical API contract for the custom Tripletex source.
- Keep a checked-in snapshot at `specs/tripletex/openapi.json` and regenerate clients/schemas from that snapshot when the endpoint is down.
- Regenerate periodically when endpoint is available and diff for breaking changes.

## Tripletex Authentication

- Use **employee token** authentication for Phase 1.

## Contact→Company Association Ingestion

- Create separate staging relation tables:
  - `stg_hubspot_contact_company_assoc`
  - `stg_tripletex_contact_company_assoc`
- Store source relationship ids and timestamps (`source_contact_id`, `source_company_id`, `role`, `updated_at`).
- Associations participate in linkage and in write-back planning.

## Delete Signal Limitations (Ingestion)

`dlt` note: dlt supports incremental extraction and merge loading, but generic REST delete detection is source-dependent. If API/webhook does not emit delete signals, snapshot-diff + verification is required.

## Related Plans

- [Webhooks](webhooks.md)
- [Deletion Tracking & Verification](deletion-tracking.md)
- [Record Linkage](../model/record-linkage.md)
- [API Gateway & Traffic Control](../ops/api-gateway-traffic-control.md)
