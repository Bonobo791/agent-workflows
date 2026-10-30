# Private pilot setup checklist (not yet deployable)

This is a configuration plan, not evidence that credentials, host permissions,
network policy or isolation have been provisioned or tested. Obtain the owner's
specific approval before registration, sign-in, secret installation, host
changes, deployment, external writes or publication. The public source checkout
must not contain private skills, credentials, user data or session histories.

## Exact private inputs

- An owner-only state directory, a distinct owner-only control-secret file,
  `CONTROL_SECRET_FILE` and `WORKFLOW_DATA_DIR`; choose a manual worker target
  and distinct incarnation using `WORKFLOW_LIFECYCLE_MODE=manual`,
  `MANUAL_WORKER_TARGET` and `MANUAL_WORKER_INCARNATION`.
- `WORKFLOW_TASK_CONFIG_FILE`: an owner-only JSON object with `run`, `workflow`,
  `project`, `number`, `ready_columns`, `completed_columns`,
  `blocker_direction`, `plan_endpoint`, `github_owner`, `github_repo` and
  `github_branch`; marketing also requires `task_format`. The Plan and GitHub
  credentials belong in separate owner-only files referenced by `PLAN_TOKEN_FILE`
  and `GITHUB_TOKEN_FILE`, not in the task JSON, model input or images.
- `DOCKER_RUNTIME_CONFIG_FILE`: owner-only JSON with `socket`, digest-pinned
  `image`, restricted `network` and `job_root` equal to the private state
  directory's `jobs` child. The controller must be separate from jobs, and the
  Engine must enforce target-scoped authorization. Never mount its socket into
  a job. Explicitly approve the exact Docker policy hash through private control.
- `WORKFLOW_ROLE_CONFIG_FILE`: owner-only JSON with absolute private `skill_roots`
  and `required_skills` for each stage, including `source_review` for marketing.
  Skill content is loaded at runtime and must not enter the public repository.
- Engineering additionally needs an owner-provisioned checkout at
  `<state>/workspaces/<run>/repo`, a verified repository identity, full base
  commit, dedicated branch and matching owner; approve the exact workspace
  binding. `WORKFLOW_COMMAND_CONFIG_FILE` is owner-only JSON containing `argv`
  (a fixed argument array), `timeout_seconds` and `output_limit`; the owner
  must approve its exact command policy hash. Commands run without a shell unless
  the approved argv explicitly invokes one, and command jobs use network mode
  `none`. The chosen image must contain the approved executable and a working
  Docker log driver. Real host behavior and permissions remain unverified.

## Missing trusted boundaries

The worker cannot yet receive real Codex OAuth credentials. The intended broker
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
No production verifier or control endpoint for that recovery is configured.

Marketing also needs an explicitly configured trusted source-tool authority
that independently authenticates exact reviewer-session tool receipts against
the source register and the current draft. The repository currently supplies
only the adapter interface and synthetic authority; it cannot make real source
requests. It must reject missing, stale, untrusted or unavailable receipts.
Source judgments are review evidence, not guarantees of completeness or truth.

The engineering PR bridge can construct a claim-scoped authority using the exact-container Git push adapter and a read-only GitHub observer, but no real authority is configured by the shipped worker. A deployment needs separately approved Git credential delivery to the isolated push process, a restricted GitHub network, and independently verified host policy. A matching remote state after the exact client exits establishes current state, not which request caused it or whether an upstream request is still in flight. Negative and conflicting outcomes remain fenced. Draft and readiness use a fixed non-model container without workspace or session mounts. Its controller-owned Docker attach stdin carries a short-lived scoped credential in memory; a separately configured trusted issuer must bind it to the exact run, action, assignment and container. No real issuer is supplied. The Docker daemon and transport must be trusted and independently secured; no host behavior has been validated. Review host and daemon authorization before enabling any of these operations. Never automatically merge,
deploy or publish social content.

## Before a real one-task smoke run

After the missing implementations and independent review, the owner separately
approves OAuth registration/sign-in, secure credential and private volume
installation, the precise host/network/isolation policy, and deployment. Then
verify the pinned app-server version and requested Terra-medium model with a
real authorized turn, confirm session resume and container stop/fencing, and run
one expressly approved task. Model catalog discovery is not an entitlement
test. Local fake-Engine tests do not prove any of these host properties.
