# Tripletex Webhook Reference (PoC)

Source documentation:
- https://developer.tripletex.no/docs/documentation/webhooks/

## Scope

This reference captures the Tripletex webhook details used by this project.

## API Endpoints

- Event catalog:
  - `GET /v2/event`
- Subscription management:
  - `GET /v2/event/subscription`
  - `POST /v2/event/subscription`
  - `PUT /v2/event/subscription/{id}`

## Subscription Payload (create/update)

`POST /v2/event/subscription`

```json
{
  "event": "product.create",
  "targetUrl": "https://your.receiver",
  "fields": "(optional) field projection",
  "authHeaderName": "(optional) custom header name",
  "authHeaderValue": "(optional) custom header value"
}
```

Notes:
- `fields` follows regular API field projection semantics.
- If an object emits several verbs, subscribe per verb (`object.create`, `object.update`, `object.delete`).

## Relevant Events (PoC)

- `contact.create`
- `contact.update`
- `contact.delete`
- `customer.create`
- `customer.update`
- `customer.delete`

## Callback Authentication Modes

Supported by Tripletex docs:
1. Custom header (`authHeaderName` + `authHeaderValue`) — recommended
2. Basic auth in callback URL
3. Secret token in query/path

## Webhook Callback Body

`POST <targetUrl>`

```json
{
  "subscriptionId": 123,
  "event": "object.verb",
  "id": 456,
  "value": {}
}
```

Behavior:
- For delete events (`*.delete`): `value = null` and `id` is the main identifier.
- For create/update: `value` contains object data (possibly filtered by `fields`).

## Delivery & Reliability

- Delivery is guaranteed with retries when callback delivery fails.
- Retry intervals increase over a span of approximately 30 hours.
- If failures persist, subscription can be disabled with status `DISABLED_TOO_MANY_ERRORS`.
- Status inspection: `GET /v2/event/subscription`.
- Re-enable path: `PUT /v2/event/subscription/{id}`.

## Sync Guidance from Provider

- After enabling subscriptions, wait briefly for propagation.
- Perform bulk sync and maintain periodic nightly reconciliation.
- Rapid consecutive updates may skip intermediate versions in webhook stream while still delivering latest version.

## Project Defaults (Phase 1)

- Subscription field profiles:
  - `contact.*` with `fields=*`
  - `customer.*` with `fields=*`
- Keep nightly reconciliation enabled.
- Always perform read-after-delete handling.
