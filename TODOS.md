# TODOs

## Dos

- remove the matview script of the osi-mapping dockerfile, no need
- we can also just move the osi-mapping build into the schema-manager dockerfile, no need to keep it alone
- simulator schema should not be the schema-manager responsibility, that one is only for ingest, writeback and osi-mapping concerns, we can do it in the pg-trickle deployment
- inandout engine handle sigterm
- ingest, find a better way to signal that the input_src_ tables are ready so we don't have to hardcode that in the pgtrickle-setup container
- we also need to update last modified timestamp after an update from the api
- simulator diff doesn't look good with the hubspot "properties" wrapper

## Not yet

- need a property on a mapping to specify the column that all fields are in, to avoid source_path with data. everywhere
- ingest and writeback could've been one container - easier k8s setup then (less duplication)
- X-headers from in-and-out to trace why it does stuff
