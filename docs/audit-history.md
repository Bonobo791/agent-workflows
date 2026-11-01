# Local audit history

The SQLite state database is authoritative and private. `graph_audit` records
version 1 stage outcomes, invalidations and rate-limit resumptions, including the graph revision, run,
stage and version. `action_audit` records version 1 action approval and status
changes, including the run, action, version and actor. These histories are written
in the transaction that changes the corresponding state. `attempt_audit` records
attempt counts and outcomes in that same manner. Failure to write an
entry rolls that transaction back. Existing databases are not backfilled: absence
of an entry is not evidence that an older action did not happen.

`claim_audit` records successful claims, rejected duplicate claims, releases and
cancellations without recording lease tokens. Renewals are not recorded.
`cache_audit` records verified stage-result reuse after the gate is rechecked;
its private record retains input and output hashes. Reuse and its audit are
checked against the current claim, graph revision, result and quota state in one
transaction. Failure to persist the audit prevents returning the cached result.
Prior reuse is not backfilled. Stage invalidations are in `graph_audit`, but
`invalidation_audit` additionally records bounded reasons for explicit
invalidation calls, stage completion and graph migration, and labels affected
downstream stages. Both audit inserts and the affected stage deletions share one
transaction. Each insert is verified before deleting the stage; a suppressed or
altered audit row rolls back the invalidation. Historical invalidation reasons
are not backfilled.
`task_admission_audit` records successful intake, readiness blocks and duplicate
admission rejection, with the normalized readiness reasons and an admitted task
artifact reference kept private. Successful admission and its audit are atomic;
failed discovery before a task snapshot is available is recorded as unavailable
without copying the underlying error or inventing a task identity.
`job_audit` records queue state changes without worker identities or lease tokens;
`cancelling` is a request, while `cancelled` after a running job requires the
queue's trusted stop verification, which may be operator-attested rather than
automatically observed. An expired lease is recorded as uncertain; this table
alone does not record the verification method. `manual_lifecycle_audit` records
manual assignment state and labels stop confirmation as operator-attested.
`execution_audit` records session binding and completion transactionally,
including private role, session, supplied skill hashes and response hash. The
binding records supplied skill fingerprints; it does not independently prove
that the model followed or actually loaded a skill. Historical executions are
not backfilled. The export projection correlates sessions using the private key
and excludes raw session IDs, skill names and response hashes.
`execution_turn_audit` records each successfully completed executor turn in the
same transaction as its completion, with the selected model, input/output
references and private response and tool-evidence references. The projection
adds keyed turn correlation but exports none of those private references or
contents. Model and token usage are not inferred from the response; token and
quota usage remain unavailable unless the provider supplies validated evidence.
`execution_launch_audit` records a launch request before the supervisor starts,
then `completed` with the successful turn transaction. A failed launch or
unconfirmed stop is labeled `uncertain`; a failure after supervisor-confirmed
termination is labeled `failed`. These are observations, not proof that an
uncertain process stopped. A failed request-audit write prevents launch. If a
later failure observation cannot be stored, the request remains without a
terminal observation; the executor still requests cancellation and fencing.
The private row includes the selected model, but its projection exports only a
keyed execution ID and allowlisted role and state. Prior launches are not
backfilled. `execution_link_audit` records an `observed` link when the turn ID
is received and a `completed` link with the turn completion transaction; the
projection uses keyed IDs for each. The observed link can survive a failed turn.
This observation does not imply
the turn completed or the worker stopped.
`tool_audit` records each observed MCP, web-search or command-execution item as
it arrives for a bound session and turn, including items before an interrupted
turn. Command notifications are labeled `observed`, never asserted to have
succeeded; they are distinct from supervisor-verified `command_audit` results.
Its private record references the raw
tool-evidence artifact and records the allowlisted item type, reported status,
and whether input and result fields were present. Export includes only those
bounded fields and keyed session/turn IDs, never inputs, outputs, server names
or artifact references. Field presence does not establish content validity or
execution time. Audit failure interrupts the executor and requests fencing, but
cannot undo a tool call already observed. Unreceived or unbound events remain
unknown; their absence does not prove no call occurred.
`command_audit` records supervised command reservations and terminal passed or
failed states in the state-changing transaction. The private terminal record
references the exact command policy, commit, redacted output hashes and timing;
an uncertain reservation remains distinct from a verified failure. Export uses
a keyed command identifier and excludes command arguments and output contents.
`local_scope_audit` records local scope reservations, observed active bindings,
stop requests and verified termination in the same SQLite statement as each
state transition. An audit write failure prevents the transition; if recording
termination fails after the stop request, the scope remains in `stopping` and
requires reconciliation. `local_turn_scope_audit` records a completed turn's
scope binding in the same transaction as the turn. The export uses a keyed
scope identifier to correlate these records without exposing the scope unit;
the binding does not imply that earlier unbound attempts completed. Existing
scope states and turn bindings are not backfilled; `starting` and `stopping`
do not establish a process outcome.
`local_execution_scope_audit` links a local execution and session to its
recorded scope before its turn starts. Its keyed identifiers correlate with
launch, turn and scope records without exposing the private values. Audit failure
prevents the binding; older bindings are not backfilled. A binding alone does
not establish that a turn was sent or that a worker stopped.
`local_scope_launch_audit` records a guarded launch receipt with the scope
identity in the same transaction as the running binding. The target waits for
authorization until that transaction succeeds. Export correlates the record
with the scope using a keyed identifier and omits the process ID, cgroup and
nonce hash. Older launches have no receipt. A receipt does not establish that
the target completed or that an interrupted stop is safe to reconcile.
`local_scope_recovery_audit` records explicit reconciliation of a confirmed,
inactive scope in the same transaction as its termination state. A missing
recovery record rolls back that state change. Export uses the keyed scope ID.
`local_scope_stop_audit` records a stop reservation, witness claim and completed
systemd client in order. Each state change requires its matching audit record;
a missing completion leaves the stop unresolved. The projection omits the
invocation, cgroup and nonce hash while correlating the scope with a keyed ID.
`local_command_launch_audit` records the single launch claim for a reserved
local command in the same transaction. A failed audit write prevents launch;
the claim does not establish whether a later launch succeeded. Older commands
are not backfilled. Export correlates its scope and assignment using keyed IDs.
`workspace_scope_audit` records each new local workspace-to-scope binding in
the binding transaction. Its projection correlates the assignment and scope
using keyed identifiers; failure to record the binding rolls back the claim and
scope reservation. Older bindings are not backfilled. A binding is not proof
that the scope launched or terminated.
`workspace_recovery_audit` records a fenced operator release of a local
workspace claim in the same transaction as the release. Export correlates the
assignment and scope with keyed identifiers without exposing their raw values;
an audit write failure rolls back the release. Normal non-recovery releases and
claims predating this record are not backfilled.
`lifecycle_audit` separately labels an observer-verified stop as independently
verified; it does not export observer evidence or target identifiers.
`review_audit` records the opening and resolution of an engineering PR blocker
in the same transaction as its state change. The private record retains the
exact head and review-evidence reference; its export only includes the keyed run
and allowlisted state. It does not backfill prior blockers.
`human_review_audit` similarly retains marketing package decisions in the same
transaction as the decision, with the exact package version and actor kept
private. Its projection exports only keyed run and approved/rejected status.

The tables are limited to the fields above, but those fields are not redacted or
validated as non-sensitive. They remain private. `runner.audit_export` offers a
bounded, read-only projection: it uses a separately supplied private key to HMAC
the run identifier and each table-scoped event identifier, permits only known
stage and state values, and drops all other stored fields. Use a distinct key
per database; keep it private and stable for the desired correlation period.
Changing the key breaks correlation and event deduplication. Cursors are per
table.
Projection schema version 2 adds keyed task IDs on admission and claim events,
and keyed attempt IDs on attempt events. Attempts are scoped to the run, stage,
input version and attempt count; missing identifiers stay unavailable.
Projection version 3 adds an allowlisted invalidation reason; the OTLP adapter
accepts projection versions 1 through 7. Version 4 adds a keyed launch
correlation ID; version 5 adds a separate allowlisted role, including source and
asset and platform review roles that are not graph stages. Version 6 adds bounded tool type
and input/output presence fields. Version 7 adds a keyed scope identifier to
correlate local scope transitions and completed turn bindings. Raw audit rows
remain version 1.
Event identifiers assume these tables are append-only; retention or
database restoration requires a reviewed identity scheme before rows are removed
or IDs are reused. The optional `export_page` caller is disabled unless supplied an exporter;
it advances its returned cursor only when that exporter returns `True`. A failed
or ambiguous export can be retried and may duplicate events. `deliver_page`
optionally persists per-table, per-destination progress in a separate durable
SQLite database, advances after the supplied exporter acknowledges success,
and uses compare-and-swap when senders race. Both databases and the correlation
key must be kept private; the caller supplies connections and a bounded-time
exporter. Changing the key, source database path or destination replays history;
a keyed digest of the last exported projected event detects a missing or changed
checkpoint after a restore and returns `inspection_required` without advancing.
A nonzero cursor created before checkpoint digests were introduced also requires
inspection; do not silently reset it. This detects a changed checkpoint, not all
possible alterations to earlier history, so stop delivery while restoring the
source and explicitly reconcile cursors before resuming. A restore racing with
an export is not atomic across the two databases and may cause duplicate or
missed delivery. No background dispatcher or cursor database is configured, so delivery is not
called by the worker. An operator can explicitly deliver one bounded page per selected table:

```sh
python -m runner.audit_delivery --source /private/state.db --cursor /private/audit-cursors.db \
  --key-file /private/audit-key --endpoint http://127.0.0.1:5173/api/v1/private/otel/v1/traces \
  --table graph_audit --table claim_audit
```

Select one to eight distinct existing tables. The command checks that every
selected table exists before sending, processes them in the supplied order, and
stops at the first unavailable or ambiguous result. Each table has its own
cursor; repeat the command to drain additional pages. Multiple tables produce a
JSON object of per-table results. A nonzero exit can follow successful earlier
tables, whose cursors remain committed.

The database directories and files must be owned by the current user and private;
the source must already exist. The key is an owner-only file with at least 32
characters and must remain stable for replay. An optional `--token-file` supplies
an owner-only bearer token. Install `requirements-observability.txt` separately.
The endpoint must be explicit; HTTP is allowed only on loopback. The command
prints only a delivery status and returns nonzero for unavailable or ambiguous
outcomes. Schedule it only after the destination's authentication, retention and
access controls have been verified. The local receiver test does not establish
compatibility with a deployed Opik server.

A disposable compatibility test also sends synthetic events to the official
OpenTelemetry Collector with its OTLP/HTTP trace path explicitly configured
([receiver documentation](https://github.com/open-telemetry/opentelemetry-collector/blob/main/receiver/otlpreceiver/README.md)).
On 2026-11-01 it passed using collector 0.164.0, local image ID
`sha256:ee8d3ccd0de2384b135504d71fa9b99a2a585572384bcd01e01b509c9f9c4d89`
and manifest digest `sha256:28b6d41ac51bff6b41ba5e8fe503569ffb9d6a1ea90f7a78419e86f34e44f750`.
The test stops and restarts the collector, checks retry and cursor persistence,
and verifies debug output excludes the synthetic raw run and content. Run it
only with a trusted local image using:

```sh
WORKFLOW_TEST_OTEL_IMAGE=<image-id> python -m unittest tests.test_audit_collector_integration
```
 Its debug exporter and
loopback listener do not establish durable storage, authentication, retention,
or Opik compatibility.
 `OpenTelemetryAuditExporter` adapts these events to an explicitly
provided OpenTelemetry SDK log exporter and acknowledges only SDK success. Its
optional dependencies are pinned in `requirements-observability.txt`; no
endpoint or exporter is enabled automatically. The caller must bound exporter
timeouts and run it outside workflow transactions. The projection does not manage
retention or establish that the recipient is appropriately protected.
The pinned SDK regards an HTTP success as export success without interpreting
OTLP partial-success responses. `StrictOTLPTraceSession` can wrap its HTTP
session to reject malformed protobuf responses or any reported rejected spans;
tests cover both. A successful acknowledgement still does not prove storage,
retention or Opik's actual response behavior. Do not treat it as deployed
verification, and do not use the SDK's unwrapped HTTP result as proof that an
entire batch was accepted.
`runner.opik_export.create_opik_trace_exporter` constructs an explicit trace
exporter for the documented self-hosted OTLP path. It requires an HTTP session
with environment proxy discovery disabled, a finite timeout, and HTTPS except
for loopback HTTP. Redirects are disabled; TLS verification is enabled; ambient
OTLP headers and client certificates are not used. The caller supplies any
separately approved authentication through the private session. Pair the returned
adapter with `deliver_page` and separate private cursor storage; constructing it
does not start a worker or send data. Its strict acknowledgement and privacy
behavior have been tested with in-memory and loopback HTTP fixtures, not with an
Opik server or a verified access-control and retention configuration. The
loopback fixtures check serialized spans, partial acknowledgements, receiver
unavailability, and replay after reopening the cursor database. Replayed spans
retain their trace and span identifiers. They do not verify an Opik backend.
`OpenTelemetryAuditTraceExporter` can also send a bounded projected batch as
synthetic snapshot traces, grouped by keyed run ID. Each trace has a root and
zero-duration child spans at the recorded event timestamps. These are audit
observations, not measured execution spans or a complete live trace; separate
pages create separate snapshots. Stable event and snapshot IDs make identical
replays identifiable, subject to the append-only and per-database-key limits
above. The HTTP trace serializer is tested with in-memory and loopback fixtures.
[Opik's OpenTelemetry documentation](https://www.comet.com/docs/opik/integrations/opentelemetry)
documents HTTP trace ingestion and a self-hosted `/api/v1/private/otel/v1/traces`
endpoint; it does not establish that the log adapter is accepted. Pinned server
compatibility, access control, retention and actual ingestion remain unverified.
These histories do not yet cover every
rejected claim, review, command, process termination or external operation; they are
not a complete audit or a configured OpenTelemetry integration. Backend and
retention configuration remain unimplemented.

`runner.metrics.snapshot` reads one consistent SQLite snapshot and emits aggregate
counts with allowlisted workflow, stage and status labels. It includes current
jobs, stages, action states, quota pauses, expired leases and inputs with more
than one attempt, plus recorded cache reuses by workflow and stage; it does not
emit run IDs, actors, versions or tokens. Cache counts only cover events recorded
since instrumentation was installed and are not an efficiency ratio. An
aggregate `workflow_launch_observations` reports the latest recorded launch
state per execution. An uncertain or request-only record is historical evidence,
not proof of a currently running worker; separate recovery may already have
occurred and is not linked back into this metric. It is not used as a live-process
alert. The aggregate `workflow_observed_tools` counts received tool observations by
workflow, role, allowlisted tool type and status. It does not report tool names,
arguments, outputs or run IDs; counts may decrease after retention or restore,
and unreceived events are unknown. An expired lease is an observation, not proof
that its worker stopped. The counts
are gauges, not lifetime counters: deletions and state changes can reduce them.
The snapshot timestamp supports stale-data detection; an absent series is a
telemetry gap, not proof of zero activity. These alerts apply only once a
collector and its private scrape target are intentionally configured.
Missing tables or query failures raise an error instead of inventing zero values.
`prometheus_text` encodes this projection. There is no configured scrape endpoint,
retention or background collector; an operator must arrange private access and
call it outside state-changing transactions. Authenticated local operators can
request `{"action":"metrics"}` on the existing owner-only control socket; its
response contains the Prometheus text and is capped at the control client's
response limit. `python -m runner.metrics_cli` uses the same `CONTROL_SECRET_FILE`
and `WORKFLOW_DATA_DIR` as the existing control CLI and writes only the snapshot
to stdout. A failed request writes no metrics and exits nonzero; callers must
detect stale snapshots rather than treating them as current. A failed snapshot
fails that request only.

For a private node-exporter textfile collector, an operator can create an
owner-only directory and explicitly run `python -m runner.metrics_textfile DIR`
with the same control environment. Supply an absolute directory path without
dot components or symlinks. The publisher opens each path component without
following symlinks, writes `workflow.prom` atomically with mode 0600, and
rejects concurrent publishers. The collector must
run as the same user and read that directory; choose a private listener and
restrict access to it separately. If collection or writing fails before the
atomic replacement, the last snapshot retains its original timestamp so the
existing stale-snapshot alert can fire. A failure after replacement may leave
the new snapshot with uncertain durability. Schedule refreshes more frequently
than the stale threshold and monitor publication failures. One disposable
rootless node-exporter test on loopback scraped an actual local worker snapshot.
This does not configure a collector,
listener authentication, notification routing, or production deployment.
A separate opt-in integration test runs disposable Prometheus and Alertmanager
instances with a synthetic loopback webhook. It verified firing and resolution
of all six shipped alerts using their expressions with waits shortened to one
second for the test. It does not verify a real notification destination.
To rerun it, make node-exporter v1.9.1, Prometheus v3.4.2 and
Alertmanager v0.28.1 images available locally, then explicitly run
`WORKFLOW_ALERT_ROUTING_TEST=1 python -m unittest tests.test_alert_routing_integration`.
The test uses loopback listeners and removes its temporary containers and data.

`observability/alerts.yml` is a
non-activated Prometheus rule file. Alertmanager deduplication and routing are
deployment configuration and have not been configured or verified here.
`observability/dashboard.json` is an importable Grafana dashboard definition
for the same snapshot metrics; its evidence-gaps panel explicitly identifies
unavailable measurements. Neither the dashboard nor alert rules have been
imported into a live service. Prometheus `promtool` 3.15.1 validated the rule
syntax and `promtool test rules observability/alerts_test.yml` exercised alert
wait periods, resolution, and stale versus missing snapshots locally. This does
not verify collection, alert routing, notifications, or deployment.
