# Private pilot setup checklist (not yet deployable)

This is a configuration plan, not evidence that credentials, host permissions,
network policy or isolation have been provisioned or tested. The owner has
authorized the existing local Codex sign-in. Obtain the owner's specific
approval before new registration, secret installation, host
changes, deployment, external writes or publication. The public source checkout
must not contain private skills, credentials, user data or session histories.

## Exact private inputs

- An owner-only state directory, a distinct owner-only control-secret file,
  `CONTROL_SECRET_FILE` and `WORKFLOW_DATA_DIR`; choose a manual worker target
  and distinct incarnation using `WORKFLOW_LIFECYCLE_MODE=manual`,
  `MANUAL_WORKER_TARGET` and `MANUAL_WORKER_INCARNATION`.
- For the local marketing runtime, set `WORKFLOW_EXECUTION_MODE=local` and
  `LOCAL_RUNTIME_CONFIG_FILE` to an owner-only JSON file containing exactly
  `codex_executable` (the absolute native Codex binary path), `codex_sha256`
  (its SHA-256), and `disabled_integrations` (an object with `mcp_servers` and
  `plugins` arrays naming every integration in the effective Codex config).
  The worker checks the executable hash and runs synthetic sandbox and scope
  preflights. It checks the effective configuration before each thread and
  fails closed if any integration is still enabled. Workspaces are created
  under the private state directory. Local engineering uses the separately
  provisioned and explicitly bound checkout described below. Never put
  credentials in this JSON file.
  To inspect the current launch inputs without printing configuration values,
  run `python -m runner.local_config_inspect /absolute/path/to/codex /private/state`.
  The directory must already exist and be owner-only. The command prints the
  binary path and hash and the integration names needed for the JSON; save its
  output only in an owner-only file outside the repository. It makes no model
  turn and does not copy credentials. Names and paths can still be private.
  On Linux, the standard npm `codex` launcher is resolved to its native binary;
  the resulting configuration pins that binary rather than the Node launcher.
  This is a candidate for the inspected directory, not proof of isolation:
  each actual workspace is checked separately at runtime, and changed binaries
  or inherited integrations fail closed.
- `WORKFLOW_TASK_CONFIG_FILE`: an owner-only JSON object with `run`, `workflow`,
  `project`, `number`, `ready_columns`, `completed_columns`,
  `blocker_direction`, `plan_endpoint`, `github_owner`, `github_repo` and
  `github_branch`; marketing also requires `task_format`. Set `plan_endpoint`
  to `codex-apps` in local mode to use the existing signed-in Codex app gateway
  for read-only It's a Plan tools. This starts an ephemeral app-server thread
  without a model turn and uses the pinned local executable. Each call uses a
  private temporary Codex home and working directory linked to the existing
  `auth.json`; other sign-in storage methods may not work. Normal cleanup removes
  this directory; a killed caller can leave a private directory for inspection.
  System-wide Codex configuration and escaped descendants remain unverified.
  Alternatively, an
  HTTPS MCP endpoint requires an owner-only credential file referenced by
  `PLAN_TOKEN_FILE`. Do not configure both gateway mode and a Plan token.
  To verify this access without admitting a task, run
  `WORKFLOW_PLAN_LIVE_PROJECT=team.PROJECT WORKFLOW_PLAN_LIVE_NUMBER=1 python -m unittest tests.test_plan_codex.CodexPlanTests.test_signed_in_gateway_reads_issue_attachments_and_activity`
  with an accessible project and issue number. This reads the issue, attachments
  and activity through the existing sign-in.
  For an issue with known records, also set `WORKFLOW_PLAN_LIVE_ATTACHMENT_ID`
  and/or `WORKFLOW_PLAN_LIVE_COMMENT_ID` to require those exact IDs in the
  returned lists. Without these expectations, empty lists are valid.
  The optional `test_signed_in_gateway_process_group_exits_after_caller_crash`
  uses the same variables, kills a disposable caller after it sends a tracker
  request, and checks the original gateway process group for live processes.
  Gateway mode requires Linux parent-death signalling; if it cannot be set,
  the gateway fails closed. The watchdog terminates the original process group
  when its caller dies; it does not prove cleanup of processes that leave it.
  The app-server tool-call interface is experimental; revalidate it when the
  pinned Codex executable or connected account changes.
  The CLI waits up to 180 seconds for a task-admission response. A timeout or
  lost response after sending means the outcome is unknown: allow the worker
  to finish and inspect the configured run before retrying.
  `project` accepts either a project key such as `MC` or a team-qualified
  reference such as `moderaty.MC`; the exact configured reference is sent to
  It's a Plan without rewriting it.
  Task intake requires owner fields to be null or nonempty strings and dates
  to be null or canonical `YYYY-MM-DD`; malformed values block admission.
  The returned issue identifier and blocker IDs must match the requested
  records, and status IDs must be positive integers rather than booleans.
  Durable task and claim identities use `plan:<internal issue ID>`, so matching
  display identifiers in different teams remain distinct and qualified/unqualified
  aliases of one issue cannot be admitted twice. The snapshot's `identifier` and
  admission response retain the tracker display identifier. Use one tracker
  instance per state directory. Older display-only admissions and snapshots stay
  fenced for inspection; this change does not rewrite existing runs or approvals.
  In local mode, omit `GITHUB_TOKEN_FILE` to use the existing `gh` sign-in for
  read-only GitHub access; an explicit token file remains supported. Do not put
  credentials in the task JSON, model input or images. When the local worker uses
  the existing `gh` sign-in, it constructs an authority for push, draft and
  readiness; each action still requires an exact, current approval. These local
  mutation paths have only been tested against synthetic services and a local
  Git remote, not a real GitHub write.
- For the Docker runtime, `DOCKER_RUNTIME_CONFIG_FILE` is owner-only JSON with `socket`, digest-pinned
  `image`, restricted `network` and `job_root` equal to the private state
  directory's `jobs` child. The controller must be separate from jobs, and the
  Engine must enforce target-scoped authorization. Never mount its socket into
  a job. Explicitly approve the exact Docker policy hash through private control.
- `WORKFLOW_ROLE_CONFIG_FILE`: owner-only JSON with absolute private `skill_roots`
  and `required_skills` for each stage, including `source_review` for marketing.
  Skill content is loaded at runtime and must not enter the public repository.
- Optional `WORKFLOW_SOURCE_CONFIG_FILE`: owner-only JSON with exactly `urls`,
  a list of individually approved HTTPS source URLs. The local source reviewer
  fetches only those exact URLs, rejects private addresses and redirects, and
  records private, time-limited snapshots bound to the reviewer turn. Fetching
  establishes what was retrieved, not that a source is primary or a claim true;
  the independent reviewer makes those judgments. Requests with invented paths
  or queries are rejected even on an approved host.
  Each retrieval has one total deadline covering DNS and response reads; a
  cancelled or timed-out retrieval stops its helper and produces no snapshot.
  Approved JSON sources are size-limited and must parse as valid JSON. Changing
  the fetcher changes its snapshot policy, so older snapshots require a fresh
  fetch and review.
- Optional `WORKFLOW_PLATFORM_CONFIG_FILE`: owner-only JSON containing
  `sources`, a mapping from each supported channel to an object with its
  exact official documentation `url` and the intended posting `surface`.
  Configure a `platform_review` role skill as well. A fresh local reviewer
  evaluates the requested asset against the fetched source; missing, ambiguous
  or unsupported requirements block production. These judgments are not a
  guarantee that a platform will accept the final media.
- Experimental `WORKFLOW_AUDIO_CONFIG_FILE`: owner-only JSON with exactly
  `runtime_root`, `python_root`, `model_root`, `fingerprint`, and `languages`.
  The first three are separate canonical owner-controlled directories; the
  runtime contains `faster-whisper` and `bin/python-sandbox`, a symlink directly
  into the Python directory. The model directory contains a local compatible
  model. Compute `fingerprint` with `python -m runner.audio_runtime` followed by
  the three paths in that order. Map each supported channel to its explicit
  two-letter speech language; automatic language detection is not used. Inspect
  and approve the exact `audio_policy` from the private control socket. Changing
  runtime files invalidates the policy. This experimental path runs offline and
  supports only one audio stream. It supplies a transcript and signal levels,
  not an audible quality or rights assessment; human listening is required.
- Marketing page structure uses an owner-only `WORKFLOW_PAGE_RULE_CONFIG_FILE`
  mapping each explicitly supported task format to ordered Markdown headings,
  for example `{"guide":{"headings":[{"level":1,"text":"Title"}]}}`.
  Supply the task's real requirements rather than this illustrative rule. The
  optional `requirements-marketing.txt` dependency must be present in the
  worker image. Missing rules or parser block canonical approval; the structural
  check does not judge factual accuracy, visual presentation or completeness.
- Engineering additionally needs an owner-provisioned checkout at
  `<state>/workspaces/<run>/repo`, a verified repository identity, full base
  commit, dedicated branch and matching owner; approve the exact workspace
  binding. `WORKFLOW_COMMAND_CONFIG_FILE` is owner-only JSON containing `argv`
  (a fixed argument array), `timeout_seconds` and `output_limit`; the owner
  must approve its exact command policy hash. Commands run without a shell unless
  the approved argv explicitly invokes one, and command jobs use network mode
  `none`. The chosen image must contain the approved executable and a working
  Docker log driver. The local runtime uses its scoped command backend and the
  same explicit command-policy approval. A synthetic signed-in local run has
  reached the human merge-review gate using a temporary Git remote and fake
  GitHub; real GitHub mutation and production acceptance remain unverified.

## Missing trusted boundaries

For a local OpenFilm image, build the base with `podman build -f
containers/openfilm/Dockerfile -t localhost/agent-workflows-openfilm-base:0.2.6 .`.
Pass its full `sha256:` image ID as `OPENFILM_BASE_IMAGE` when building
`Dockerfile.openfilm`, then configure the resulting full image ID. The base
pins Playwright and npm package integrity, but Ubuntu package repositories can
change; approve and test the resulting image ID rather than assuming rebuilds
are byte-identical. The local image smoke test does not establish Engine API,
workspace provenance, visual review or production policy verification.
On an SELinux-enforcing rootless Podman host, select `engine` as
`podman-rootless` in the render runtime configuration. This policy uses
`keep-id` and private relabeling for the two job mounts; its terminal check
requires the recorded image ID, namespace annotation and matching SELinux
labels. An opt-in synthetic test exercised the actual adapter on this host.
A separate opt-in cancellation test stops a running synthetic renderer and
checks the container is terminated while the attempt remains uncertain and
no result is committed. This does not reconcile an interrupted stop or
establish full marketing workflow acceptance.
The standard local worker constructs the renderer when the Podman runtime
configuration is present. Its policy check verifies the socket owner and
access, rootless Podman, seccomp, SELinux, the pinned image ID and the renderer
helper copied from a stopped container; it removes that exact probe container.
The exact runtime, builder and render policies still require separate local
approvals. A synthetic local Codex-to-PNG run passed using the installed
OpenFilm manual and this verifier. A configured platform reviewer has passed a
synthetic test against a live official source. With an `asset_review` role
skill, the local worker can independently review PNG assets and their previews
as attached images, bound to the completed reviewer turn and scope. Synthetic
reviews passed a matching still and blocked one with missing approved text.
For silent MP4s it extracts 12 timestamp-verified frames from the exact video
in a bounded sandbox and reviews the resulting sheet. The package must state
that this is sampled coverage and require full human playback. Experimental
single-stream audio review can use an explicitly configured, fingerprinted
offline transcription runtime and language per channel. Its machine transcript
may be wrong and does not establish audio quality: the package must require full
human playback and listening. Audio without that policy remains awaiting review;
the full marketing workflow is not yet production-ready.

The local managed-sign-in supervisor uses the existing Codex sign-in without copying its
credentials. The separately deferred container OAuth broker
must authenticate an exact run, container ID and assignment, fence replay and
revocation, and deliver only a short-lived access token over protected IPC to a
fixed launcher. Refresh credentials stay outside jobs; tokens must never appear
in Engine metadata, prompts, job files, images, logs or artifacts. No concrete
broker/launcher, live OAuth registration or token provisioning exists here.
The [OpenAI app-server guide](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)
describes `ACCESS_TOKEN` for the child and refresh/restart with `thread/resume`.
A host review must establish how the launcher receives that token without
exposing it to untrusted job processes; shell environment filtering alone is not
proof. No legacy personal Codex home should be copied.

An interrupted session backup stays fenced. `SessionStore.recover_backup`
requires trusted checks that the exact backup operation is quiescent and fenced
and that the home is safe, then rechecks its private files before making it idle.
The home must remain an owner-only directory owned by the worker, and any directory scan error blocks recovery;
an incomplete scan is not evidence that the home is safe.
Recovery is bound to the exact backup generation; legacy interrupted backups
without a generation remain fenced for inspection. The trusted exporter receives
`(home_path, generation)`, and the quiescence verifier receives
`(job, home_path, generation)`; it must verify that exact operation. Older
callback signatures cannot recover a backup.
No production verifier or control endpoint for that recovery is configured.

The controller image provides a `/data` directory owned by UID/GID 10001 with
mode 0700. Fresh Podman named volumes copy those permissions from the image;
existing volumes retain their prior ownership and must be provisioned privately
for that UID before startup. The control secret must also be an owner-only file
owned by the worker's mapped UID. Other runtimes and volume drivers require
independent verification of their copy-up and UID-mapping behavior.

For rootless Podman on the validated host, use `compose.podman.yaml` alongside
`compose.yaml` to map the host user to UID/GID 10001 and relabel the secret
bind mount. Run `docker-compose -f compose.yaml -f compose.podman.yaml up -d`
with `CONTROL_SECRET_PATH` set to a private
owner-only host file and the Podman socket configured for the Compose provider.
The override puts the private control socket in container `/tmp` and leaves
durable state in `/data`. The CLI must use the same `WORKFLOW_CONTROL_DIR`;
the Compose service sets it to `/tmp/workflow-control`. A state-directory lock
prevents two current-version workers sharing a volume. Stop any older worker
before upgrading: a legacy `/data/control.sock` blocks the new socket path and
requires inspection rather than automatic removal. Do not run mixed versions
against the same state directory.

On the tested host, startup, stop/start, default restart, restart with
`--timeout 5`, and explicit start after a killed container preserved a synthetic
run. These tests used disabled execution; recovery of in-flight work, scopes,
and remote request outcomes remains unresolved. A separate signed-in host probe
killed a worker after a turn started, restarted control with a new private socket
directory, fenced the run and stopped its recorded scope before operator
confirmation. It does not prove whether the provider completed the turn or
whether its request was cancelled. Do not remove a legacy socket
without independently verifying that the previous worker and its work are
quiescent.

For a planned local worker restart, use `automatic_status` to check that the
worker reports `checkpoint_available`, then send `checkpoint_configured_auto`
with the exact `run`. Wait for `automatic_status` to report `checkpointed`.
Its `last_checkpoint` also reports whether the request is pending, completed,
rejected, or requires inspection; rejection retains the existing claims.
The checkpoint releases the queue and graph claims only when the run has no
unresolved local work, remote outcomes, required follow-ups, or unconsumed
approvals. The owner may
withdraw an unused freeze approval with `revoke_freeze_approval`, specifying its
exact `run` and `version`; a started or uncertain action cannot be withdrawn.
For an unused GitHub proposal approval, submit `pr_withdraw` with the exact
proposal and current claims (or through `automatic_control`). Both records are
withdrawn atomically; uncertain actions still require reconciliation. Approval
and proposal state now commit together. The same control can withdraw a legacy
orphaned approval while its proposal is still pending, only when the exact target
binding matches and no other operation is active or uncertain. If the
withdrawal response is lost, inspect the private approval records before retrying.
If a checkpoint
response is lost, inspect status before retrying; do not infer success from a
transport error.

While the original control process is still running, send the owner-only action
`register_checkpoint_replacement` with `run`, `old_incarnation`, and a distinct,
never-used `new_incarnation`. The replacement is registered only if the run is
still quiescent. The private state database records each replacement with its
run, target, and old and new incarnations; exported checkpoint events omit
those raw identities. Inspect `lifecycle_status` if the response is lost. Stop the
original worker process, set `MANUAL_WORKER_INCARNATION` to the new value, then
start the replacement against the same private state directory. Its worker lock
prevents concurrent processes, and it obtains fresh queue and graph claims. A
checkpoint does not recover a crash or authorize replay of uncertain work; use
the recovery procedure below for those cases.

For local scope recovery, first request `manual_stop` for the run. The returned
scope list identifies unresolved units. The owner can call `stop_local_scope`
with that run and unit: it binds an observed active scope and verifies termination.
For a previously guarded launch that has already exited, the owner can call
`reconcile_local_scope` with that run and unit. It requires the recorded launch
receipt and audit, the exact fenced run, an inactive unit with no systemd job,
and an empty recorded cgroup. A `stopping` scope also qualifies when its exact
audited stop witness has completed; a missing or incomplete receipt remains
unresolved. This operation does not confirm the worker stopped, release a
workspace, or resolve remote requests.
Review [local scope recovery evidence](local-scope-recovery.md) and its host
and systemd assumptions before using the reconciliation control.

For new local model executions, approved commands, and push snapshots, the
workspace claim and its reserved scope are recorded together. A reserved scope
is not proof of a launch; an absent or ambiguous launch still remains fenced.
Older claims without a scope binding are not backfilled.
Guarded launches wait for the scope identity and launch receipt to be committed;
the final run-fence check and authorization share a database write lock with
cancellation. A missing receipt or a lost fence prevents the target from starting.

If the stop response includes `local_workspace`, the owner can call
`release_local_workspace` with the exact `run`, `assignment`, and `unit` after
the bound local scope is recorded as terminated. The run must remain fenced;
this only releases that workspace claim and does not confirm worker termination
or reconcile a remote outcome. A null unit means the claim has no recorded local
scope binding and cannot be released through this control.
The recorded claim can be inspected and a terminated bound claim released
after restart without a local model supervisor. Stopping an active scope still
requires the configured local supervisor and its systemd backend.
If `manual_stop` returns `local_scopes_unavailable`, the stored scope schema
cannot provide unit identities; inspect and reconcile that state before any
stop confirmation.
An unknown scope state is treated as unresolved. If an older database contains
multiple unresolved scopes for one run, the stricter scope index fails closed;
inspect that state before attempting recovery.
Manual confirmation and local workspace release also require the recorded
terminated scope to have a valid invocation ID and matching cgroup path.
An invalid terminal identity is reported as `unverified` and also blocks a new
scope reservation, session resume, mutation and correction recovery.

The owner-only control action `inspect_local_scope` takes that exact `run` and
`unit` and returns the recorded state, invocation and cgroup alongside the
current systemd observation and whether the recorded cgroup is empty. A null
observation means systemd inspection failed; a null empty result means no cgroup
was recorded or systemd inspection failed. These observations are not atomic
and do not prove a launch or stop request is quiescent; inspection changes no
recovery state.
For executions recorded by this version, the inspection also reports the
associated execution, role, session and observed turn IDs, plus whether a
completed audit exists. A null execution or turn can also mean the process
stopped before recording it; it does not prove that no request was sent.
These identifiers are recovery evidence and do not authorize session reuse.
The controller SIGKILL regression exercises persistence and the resume fence
with a synthetic app server and scope backend; it is not a live-provider or
systemd crash-recovery acceptance test.
The opt-in `tests.test_local_executor_crash_live` probe also interrupts a real
signed-in local turn in a real user scope and checks that the session stays
active and cannot resume. A separately scoped app server reads the exact saved
thread and checks any matching turn without treating its status as permission
to resume. The probe does not establish the upstream turn outcome or provide a
production control to inspect or reconcile it.
An unobserved launch or a stop without a completed witness remains fenced, as
does any unresolved engineering workspace claim. Operator stop confirmation
still requires the exact incarnation and termination attestation.
If a legacy bound scope without a guarded receipt exits and systemd forgets the
unit before the stop control runs, it stays unresolved even with an empty cgroup.
Only the guarded-launch evidence described above qualifies for
`reconcile_local_scope`; an empty observation alone never authorizes stop
confirmation or replay. Interrupted stops require their completed, audited witness.

An opt-in real sandbox probe can check the specific parent cgroup file and user
D-Bus socket on the local host:
`WORKFLOW_TEST_SYSTEMD=1 CODEX_TEST_EXECUTABLE=/absolute/path/to/codex python -m unittest tests.test_scope_isolation_live`.
It verifies that the host paths exist and the user bus accepts a connection
before requiring a permission, read-only, or hidden-path error inside the
sandbox. It uses a recorded disposable scope and attempts no cgroup write or
D-Bus request. A
passing result covers only those paths, not all possible descendant escapes or
the quiescence of an interrupted launch.

Marketing source review requires either the configured local snapshot authority
or an independently authenticated source-tool authority. Missing, stale or
unavailable evidence blocks approval. The local snapshot path has focused tests
and a real public fetch; full marketing-task validation remains outstanding.

The engineering PR bridge can construct a claim-scoped local authority from the existing `gh` sign-in or a separate exact-container authority. Local push, draft and readiness require individual exact approvals and have only synthetic write tests. A matching remote state after the exact client exits establishes current state, not which request caused it or whether an upstream request is still in flight. Negative and conflicting outcomes remain fenced. The deferred Docker path requires a separately configured scoped credential issuer, restricted GitHub network, and verified daemon policy. Never automatically merge,
deploy or publish social content.

## Before a real one-task smoke run

After the missing implementations and independent review, establish the precise
local host/network/isolation policy. New OAuth registration, credential or volume
installation, and deployment need their own authorization. Then
verify the pinned app-server version and requested Terra-medium model with a
real authorized turn, confirm session resume and container stop/fencing, and run
one expressly approved task. Model catalog discovery is not an entitlement
test. Local fake-Engine tests do not prove any of these host properties.

# Approval-wait metrics

The private metrics snapshot reports an approval wait after the dispatcher reaches a marketing freeze or engineering PR gate. It counts the observation only while both claims are live, the recorded upstream stage revision remains current, and the gate has not completed. A matching freeze approval removes that wait immediately. The observation does not grant approval; inspect the private control plane for the current action. Existing waits appear after the worker next dispatches. Alert rules and dashboards are local definitions and do not establish a deployed collector or notifications.

The approval alert measures a continuous nonzero aggregate for one hour; it does not measure the age of each individual wait.

On worker shutdown, an active automatic assignment is fenced before the control process exits. Fencing invalidates its claims and requests a manual lifecycle stop; it does not prove that an in-flight process or remote request has stopped. Inspect unresolved scopes and confirm termination before replacement or recovery.
