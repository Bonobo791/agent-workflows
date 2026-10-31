# Agent workflows

An experimental, unfinished workflow runner for a **private deployment**. It is
not production-ready. The local test suite uses synthetic services, credentials
and process boundaries; it does not establish real authentication, container
isolation, deployment, or permission to publish or change a repository.

The intended workflows are:

- A marketing task becomes a researched canonical draft, independent review and
  correction, channel adaptations, and a human-review package. Missing media,
  source evidence and unsuitable channels remain visible blockers. No social
  content is published automatically.
- An engineering task runs on a bound task workspace, receives independent QA,
  and advances through separately approved draft-PR, review and readiness steps
  to human merge review. Merge and deployment are not implemented.

The private control service, durable graph, exact-version approvals, correction
fences, source-review adapter, Docker command adapter and GitHub control bridge
have local fixture coverage. The worker has **no configured real Codex
credential delivery, trusted source-tool authority, isolated Git push authority,
or independent remote-outcome observer**. The claim-scoped PR authority can use the Docker push adapter and a read-only observer, but no real credential provider is configured; isolated draft/readiness requires a separately supplied scoped credential issuer. Docker authorization, real filesystem and network isolation, actual
app-server session recovery and host lifecycle behavior remain unverified.

`WORKFLOW_EXECUTION_MODE=live` is rejected. This is not a network sandbox:
private control actions can contact configured services or the Docker controller
when their separate configuration and approvals permit it. Do not point them at
real systems until those actions and the host boundary have been specifically
approved and validated. The included Compose setup is not a tested deployment.

Run credential-free local tests with:

```sh
python -m unittest discover -s tests
```

To exercise clean writer-session resume with the installed, signed-in Codex and
temporary user systemd scopes, explicitly opt in with:

```sh
CODEX_TEST_EXECUTABLE="$(command -v codex)" python -m unittest tests.test_local_session_live
```

This sends two short model turns and checks clean scope termination. It does not
verify recovery from a crash or an interrupted provider request.

The media verification tests require `ffmpeg` and `ffprobe` on `PATH`.
Signed-token tests additionally require `requirements-auth.txt`. The full
synthetic suite may require local Unix sockets. See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)
for current boundaries and [docs/PILOT_SETUP.md](docs/PILOT_SETUP.md) for the
private setup checklist. Do not commit private skills, prompts, credentials,
task data, session histories, local databases or environment files.

## Operator control

The owner-only Unix socket can admit one explicitly configured task and advance
stages manually. With the exact task, role, Docker and manual-lifecycle settings,
the owner can explicitly start a one-task background loop; it renews queue and
graph claims together and waits at blockers and human gates. A local owner may
record a decision bound to the exact marketing review package; that is not a
publication action. Rejected work remains revision-required. Uncertain execution
and remote-action outcomes remain fenced for inspection.

An engineering test gate requires a supervisor-observed command record bound to
an approved exact command, workspace and commit. The Docker command adapter is
constructed only from private command configuration and requires separate
Docker and command-policy approval. Model-written test reports remain role
assertions; fixture execution is not real host verification.

Marketing source checks use a distinct claim/source/evidence register and fresh
source reviews for the register and current draft. A separately trusted source
tool authority must authenticate tool evidence; none is configured in the
shipped worker. Reviewer judgments cannot prove every claim is true or that the
register is complete.

## Model and authentication

Roles request GPT-6.1 Terra with medium reasoning. The app-server catalog is
used to resolve a provider model ID without substituting another model or paid
API billing; catalog discovery is not an entitlement test. Codex app-server is
the selected integration. OpenAI describes its app-server command as experimental
and not supported for production workloads. No real sign-in, model entitlement,
source-tool access or session resume has been tested.
