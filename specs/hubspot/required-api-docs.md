# HubSpot required API docs (PoC scope)

This file captures the minimum HubSpot API documentation and endpoints required for this PoC.

## 1) Contacts API (v3)

Docs:
- https://developers.hubspot.com/docs/api/crm/contacts

Required operations:
- `GET /crm/v3/objects/contacts` (list/incremental reads)
- `GET /crm/v3/objects/contacts/{contactId}` (single read)
- `POST /crm/v3/objects/contacts` (insert)
- `PATCH /crm/v3/objects/contacts/{contactId}` (update)
- `DELETE /crm/v3/objects/contacts/{contactId}` (delete/archival)
- `POST /crm/v3/objects/contacts/batch/read` (batch fetch)
- `POST /crm/v3/objects/contacts/batch/upsert` (optional upsert route)

Fields to include:
- `email`, `firstname`, `lastname`, `phone`, `address`, `city`, `country`, `lastmodifieddate`, `hs_object_id`, `archived`

## 2) Companies API (v3)

Docs:
- https://developers.hubspot.com/docs/api/crm/companies

Required operations:
- `GET /crm/v3/objects/companies`
- `GET /crm/v3/objects/companies/{companyId}`
- `POST /crm/v3/objects/companies`
- `PATCH /crm/v3/objects/companies/{companyId}`
- `DELETE /crm/v3/objects/companies/{companyId}`
- `POST /crm/v3/objects/companies/batch/read`

Fields to include:
- `name`, `domain`, `phone`, `address`, `city`, `country`, `lastmodifieddate`, `hs_object_id`, `archived`

## 3) Associations API (v4) — core for contact→company

Docs:
- https://developers.hubspot.com/docs/api/crm/associations
- https://developers.hubspot.com/docs/api-reference/crm-associations-v4/guide

Required operations:
- `POST /crm/v4/associations/contacts/companies/batch/read` (read associations)
- `PUT /crm/v4/objects/contact/{contactId}/associations/{toObjectType}/{toObjectId}` (associate with label)
- `DELETE /crm/v4/objects/{objectType}/{objectId}/associations/{toObjectType}/{toObjectId}` (remove all labels)
- `GET /crm/v4/associations/contact/company/labels` (resolve label/type ids)

Critical type ids/direction:
- contact → company primary: `typeId=1`
- contact → company unlabeled/default: `typeId=279`
- company → contact primary: `typeId=2`

Mapping rule used by PoC:
- Tripletex single-company contact link maps to HubSpot contact→company association where label/type indicates `Primary` (`typeId=1`).

## 4) Webhooks API (v3)

Docs:
- https://developers.hubspot.com/docs/api/webhooks

Required operations:
- `GET /webhooks/v3/{appId}/settings`
- `PUT /webhooks/v3/{appId}/settings`
- `GET /webhooks/v3/{appId}/subscriptions`
- `POST /webhooks/v3/{appId}/subscriptions`
- `PUT /webhooks/v3/{appId}/subscriptions/{subscriptionId}`
- `DELETE /webhooks/v3/{appId}/subscriptions/{subscriptionId}`

Required subscriptions (minimum):
- `contact.creation`
- `contact.propertyChange`
- `contact.deletion`
- `contact.restore`
- `contact.associationChange`
- `company.creation`
- `company.propertyChange`
- `company.deletion`
- `company.restore`
- `company.associationChange`

Receiver requirements:
- HTTPS endpoint
- signature validation (`X-HubSpot-Signature` family)
- fast ack (<5s)
- idempotent event handling (duplicates possible)
- tolerate out-of-order event delivery

## 5) Properties API (v3) — schema introspection

Docs:
- https://developers.hubspot.com/docs/api-reference/crm-properties-v3/guide

Required operations:
- `GET /crm/v3/properties/0-1` (contact properties)
- `GET /crm/v3/properties/companies` (company properties)

Purpose:
- discover custom properties and internal names
- guard against missing property assumptions

## 6) OAuth/scopes reference

Docs:
- https://developers.hubspot.com/docs/apps/developer-platform/build-apps/authentication/scopes

Minimum scopes (expected):
- `crm.objects.contacts.read`
- `crm.objects.contacts.write`
- `crm.objects.companies.read`
- `crm.objects.companies.write`
- `crm.schemas.contacts.read` (if needed for property introspection)
- `crm.schemas.companies.read` (if needed for property introspection)

## Notes

- Keep this document in sync with implementation as endpoint usage evolves.
- If we add custom association labels in HubSpot, include those label ids and retrieval flow here.
