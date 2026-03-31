# TODOs

## Dos

- inandout engine handle sigterm
- ingest, find a better way to signal that the input_src_ tables are ready so we don't have to hardcode that in the pgtrickle-setup container
- we also need to update last modified timestamp after an update from the api
- should also set these properites when editing the json (both insert and update) from the ui by default, but we could leave toggle to override to override that behaviour in case the user want's to simulate some explicit timestamp situation

## Not yet

- need a property on a mapping to specify the column that all fields are in, to avoid source_path with data. everywhere
- ingest and writeback could've been one container - easier k8s setup then (less duplication)
- X-headers from in-and-out to trace why it does stuff
