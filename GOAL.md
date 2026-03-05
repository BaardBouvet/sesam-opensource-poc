> **Detailed plans:** See [plans/README.md](plans/README.md) for the master plan, individual feature plans, and ADRs.

# high level

we are going do to do a poc on how to collect contacts and customers from hubspot (the crm) and tripletex (a nordic erp). 

the collected data should be merged into a common model (person and companies) and be possible to use for bi and reporting

we also want to be able sync the data back to hubspot and tripletex, based on some rules (e.g. hubspot should be master for contacts, tripletex master for companies with the possibility to define much more fine grained rules)

# features

- trace written change to hubspot and tripletex we need to be able to figure out why that change happened, and trace any api calls as the providers might complain (opentelemetry?)
- record linkage, merge the contacts and companies with static rules
- orchestrated reads and writes, we need to be able to define schedules (dagster?)
- monitoring, need to be able to see when data was read and written and how many changes happened, and any failures (prometheus and grafana?)
- simulated source systems. build a fake hubspot and tripletex so we can test this without real accounts. should be able to turn on and off jitter and failures so we can test the robustness
- compare-and-swap during writes to avoid lost writes (important for fine grained mdm rules - like name coming from one system and phone from another)
- handle references with mapping table in the mapping data, where tripletex uses norwegian country names and hubspot uses english names
- master data management rules on each property, so for name hubspot might be master and for address tripletex might be master
- support both updates and inserts into the target system

# optional features

- support last modified wins on some properties as this is a much cooler example
- fuzzy merging with agent and human curation (probably a spin off project)
- support webhooks in addition to regular fetches to get near real time sync (both systems supports that for some datatypes)
- support various backends (e.g. snowflake with dynamic tables, postgres with ivm)

# ideas 

we probably want to come up with a declarative way to define the mappings and the master data management rules that render to sql? (spin off project?)

use dlthub for reading and writing? both systems have api documentation that we can use (or another spin off project)

use postgres with a great ivm (spin off project - already done https://github.com/grove/pg-trickle)

generic simulator tool? map the api specs to some declarative language (another spin off project?)