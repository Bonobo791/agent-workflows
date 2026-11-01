# QA and PR review — October 31, 2026

> Historical review of PR head `6e5fe490`. PR #4 subsequently merged with fixes
> for all four findings and the artifact-response limit. See
> [current status](IMPLEMENTATION_STATUS.md) and the
> [remaining implementation plan](IMPLEMENTATION_PLAN.md) for the later verified
> checkpoint. Findings and test results below preserve the original review.
> A later verification of open PRs #5 and #6 is appended at the end of this file.

## Decision

**QA does not support release or merge approval yet.** Both full fixture suites pass, but independent failure probes found four functional issues in the PR: unsafe shutdown state, a command request-size mismatch, unbounded source retrieval, and missing approval-wait metrics. Two of those are already addressed in the local working changes; those fixes are not in the reviewed PR head. Hosted quality gates also remain failing.

This is a broad source, integration, boundary, concurrency, recovery, and test-coverage review. Passing results below apply to the specific layers exercised. They do not establish a complete live marketing campaign, real GitHub mutation acceptance, or production deployment readiness.

## Scope and version identity

| Item | Reviewed value |
| --- | --- |
| Repository | `Bonobo791/agent-workflows` |
| PR | [#4 — feat: build local agent workflows and review infrastructure](https://github.com/Bonobo791/agent-workflows/pull/4) |
| Exact PR head | `6e5fe490a1d0d443d7afe15dd8a9fe31ed08f3ca` |
| PR base | `80a3035695b3e731ad65c43eaf2149bd28117633` |
| PR size reported by GitHub | 144 changed files; 8,744 additions; 190 deletions; five commits |
| Isolated PR checkout | `/tmp/agent-workflows-pr4-review` |
| Local working directory | `/var/home/user/Documents/agent-workflows` |
| Local HEAD | `6a18ab6` on `main` |
| Local scope before adding this report | 69 tracked files modified; 84 untracked files |
| Source/test/docs manifest | 232 files; SHA-256 `39a3d127d2fbd705b643a9de8f4d36eaa85221e0388e62f1608e4f4b215b10a9` |

The local tree is not the PR checkout. It contains additional fixes, tests, runtime configuration inspection, approval-wait instrumentation, source-content changes, and media/dispatcher changes. Local files were being updated during the review: the initial full run discovered 666 tests and the later coverage run discovered 667. Each run's result is reported separately; the PR head stayed pinned. The local manifest was captured near the end of review and is not a claim that every earlier run used that exact snapshot.

Final recheck: GitHub still reported the same PR head/base. Three local files changed after the recorded manifest: `runner/service.py`, `tests/test_stage_entrypoint.py`, and `workflows/marketing/README.md`. Those final concurrent edits were not included in the completed full-suite/coverage runs. Local QA results therefore apply to the tested versions, not an assertion that the latest moving working tree is fully validated.

Relevant skills: `code-review` and `senior-qa`. Independent review passes covered engineering/runtime, marketing/media, and persistence/observability. No production source or test files were edited by this review. The requested report is the workspace deliverable. Reproduction scripts, logs, scanner responses, coverage data, and downloaded tooling were written under `/tmp`.

## Findings

### QA-01 — P1: PR shutdown exits before fencing an in-flight assignment

**Affected:** PR. **Local status:** addressed in the inspected working changes and passed the independent state probe.

PR anchors: [`runner/auto_worker.py:52`](https://github.com/Bonobo791/agent-workflows/blob/6e5fe490a1d0d443d7afe15dd8a9fe31ed08f3ca/runner/auto_worker.py#L52), [`runner/worker.py:252`](https://github.com/Bonobo791/agent-workflows/blob/6e5fe490a1d0d443d7afe15dd8a9fe31ed08f3ca/runner/worker.py#L252).

`AutoWorker.stop()` only sets an event. Worker shutdown then closes its controller service and exits without waiting for the daemon worker to leave an in-flight dispatcher call. That thread's exception/finally fencing does not run before process exit. A local execution scope managed by systemd can outlive the controller process, so loss of the controller must not leave the assignment authorized and recorded as running.

Independent reproduction `/tmp/sigterm_daemon_qa.py` launches a subprocess using real `Service`, `ManualLifecycle`, and `AutoWorker`, blocks its dispatcher, sends actual SIGTERM, and executes the worker's shutdown sequence. Both processes exit successfully, but durable state differs:

| State after exit | PR | Local |
| --- | --- | --- |
| Queue job | `running` | `cancelling` |
| Graph run | `active` | `cancelled` |
| Manual assignment | `running` | `pending` |

The probe proves the durable fencing failure. It does not itself prove an escaped model process; that consequence follows from independently managed scopes and remains an operational risk. A separate blocked-dispatch probe produces the same difference before dispatch returns.

Required resolution: bring the local claim/start/stop serialization and synchronous fencing into the PR, with the real worker-process SIGTERM regression. Keep unknown physical termination fenced for inspection; do not equate logical cancellation with process termination.

### QA-02 — P2: relay rejects valid approved commands above its input limit

**Affected:** PR and local. **Status:** unresolved.

Anchor: [local `runner/local_command_child.py:17`](runner/local_command_child.py#L17); [exact PR line](https://github.com/Bonobo791/agent-workflows/blob/6e5fe490a1d0d443d7afe15dd8a9fe31ed08f3ca/runner/local_command_child.py#L17).

The command policy permits up to 32 arguments of 4,096 characters each, while the relay reads only 65,537 characters of JSON. `ProcessTransport` permits a larger frame. An accepted command can therefore be truncated before JSON parsing. The controller records an uncertain command outcome and retains the workspace fence even though the command never started.

Reproduced on both trees with:

```python
argv = ['/bin/true'] + ['x' * 4096] * 20
```

The request is approximately 82 KB, depending on environment fields. Direct execution returns `0`; each relay emits `{"ready":true}` followed by `{"error":"command unavailable or limit exceeded"}`. Independent engineering boundary results reproduce the same defect.

Required resolution: use a consistent bounded frame size across policy validation, transport, and relay, or reject the oversized request before reserving an execution. Add a regression above 65,537 request characters that otherwise satisfies the exact command policy. Include JSON escaping and environment overhead in the size decision.

### QA-03 — P2: source fetch has no overall deadline or mid-read cancellation

**Affected:** PR and local. **Status:** unresolved.

Anchors: [PR `runner/source_fetch.py:44`](https://github.com/Bonobo791/agent-workflows/blob/6e5fe490a1d0d443d7afe15dd8a9fe31ed08f3ca/runner/source_fetch.py#L44), [PR `runner/source_fetch.py:60`](https://github.com/Bonobo791/agent-workflows/blob/6e5fe490a1d0d443d7afe15dd8a9fe31ed08f3ca/runner/source_fetch.py#L60), [local source fetcher](runner/source_fetch.py), [snapshot capture](runner/source_snapshots.py).

The configured timeout is a socket inactivity timeout. `HTTPResponse.read(max_bytes + 1)` can continue as long as the approved peer sends a little data before each timeout. DNS also has no overall deadline. Snapshot capture calls this synchronously and checks cancellation around complete fetches rather than while reading a response. Source review can therefore hold its worker for hours or days despite its apparent 1–30 second timeout policy.

Independent reproduction `/tmp/marketing_source_timeout_probe.py` uses a real socket pair and `http.client.HTTPResponse`. With `timeout=1`, a peer sends five bytes at one byte per 0.6 seconds. On both trees fetch succeeds after **3.0 seconds**, returning `hello`. DNS and the connection factory are substituted to avoid external traffic; actual socket timeout and response-read behavior are exercised. The mechanism scales to the default 512 KB body bound.

Required resolution: enforce a monotonic total deadline over DNS, connect, headers, and body; perform bounded reads with cancellation checks; and bound DNS resolution. Merely adjusting the inactivity timeout does not fix this behavior. Shared platform retrieval uses the fetcher too, although the outer render-stage timeout limits that caller's wait.

### QA-04 — P2: PR approval-wait alert cannot observe normal approval waits

**Affected:** PR. **Local status:** addressed by local durable `approval_waits` instrumentation; independent probe passes locally.

Anchors: [PR metrics](https://github.com/Bonobo791/agent-workflows/blob/6e5fe490a1d0d443d7afe15dd8a9fe31ed08f3ca/runner/metrics.py), [PR alert](https://github.com/Bonobo791/agent-workflows/blob/6e5fe490a1d0d443d7afe15dd8a9fe31ed08f3ca/observability/alerts.yml#L29).

The dispatcher returns `awaiting_approval` without persisting that outcome in `graph_stages`; PR metrics derive their stage statuses from that table. Consequently normal freeze/PR approval waits do not produce the series used by `WorkflowAwaitingApproval` or its dashboard.

Independent `/tmp/approval_metrics_qa.py` prepares a real engineering graph through `open_draft` and calls the real dispatcher. It returns `{'stage':'triage','status':'awaiting_approval'}`. The PR snapshot contains no matching sample; the local snapshot contains `triage=1` for `awaiting_approval`.

This is an existing acknowledged gap, not a newly discovered regression: the [PR discussion](https://github.com/Bonobo791/agent-workflows/pull/4#discussion_r4431632870) explicitly deferred it. Include the local implementation if this PR is intended to supply functioning approval-wait monitoring. Otherwise retain the limitation explicitly in acceptance criteria. Passing PromQL unit tests alone does not prove that the producer emits the required series.

## Executed QA results

### Additional existing defect — artifact inspection response size

The broader review also reproduced a **pre-existing P2 usability defect**, not introduced by this PR: the control plane allows a JSON artifact up to 1 MiB, while `request_control()` rejects responses above 65,536 bytes. A temporary owner-only socket serving an actual stored 70,000-character JSON artifact produced `ConfigurationError: invalid response` in the real CLI client. The server successfully generated its response. See `runner/control.py`'s `artifact` action and `runner/cli.py`'s response framing.

This prevents operators using the supplied CLI from inspecting larger review artifacts. Treat it as existing QA backlog: align server/client response contracts or provide bounded artifact retrieval, with a regression across the socket. It is separate from the four PR findings and from the command relay's request-size bug.

### Full suites and coverage

All successful full runs used local socket access, synthetic credentials/services, and no real remote mutations.

| Run | Result | Elapsed |
| --- | --- | --- |
| Exact PR, ordinary full unittest discovery | 648 discovered; 641 passed; 7 skipped; zero failures/errors | 54.148 s |
| Local, ordinary full discovery | 666 discovered; 657 passed; 9 skipped; zero failures/errors | 54.962 s |
| Exact PR, branch-instrumented full discovery | 648 discovered; 641 passed; 7 skipped; zero failures/errors | 49.392 s |
| Local, branch-instrumented full discovery | 667 discovered; 658 passed; 9 skipped; zero failures/errors | 53.829 s |
| Focused marketing/review suites | PR: 120 passed; local: 126 passed | Separate targeted runs |
| Focused engineering/runtime suites | PR: 61 passed; local: 65 passed | Separate targeted runs |
| Whitespace validation | PR merge-base diff and local tracked diff passed `git diff --check` | Completed |

The initial restricted local run had 4 failures and 22 errors, mostly from prohibited Unix/TCP socket creation; it ran 666 tests with 10 skipped. Rerunning with socket access resolved the failures. Those environmental results are not counted as product defects. Expected negative OTLP acknowledgement tests emit exporter errors even on successful runs.

Coverage tooling was isolated under `/tmp/agent-workflows-qa-tools`: coverage 7.13.4, PyJWT 2.15.1, cryptography 50.0.2 and their dependencies. No repository dependency files changed. Python was 3.14.8. The controller Dockerfile specifies Python 3.12; that interpreter/image was not validated by these host suite runs.

| Measured in the parent test process | Exact PR | Local |
| --- | --- | --- |
| Statement coverage | 89.98% — 7,917 / 8,799 | 90.01% — 8,055 / 8,949 |
| Branch coverage | 58.80% — 2,446 / 4,160 | 58.95% — 2,489 / 4,222 |
| Coverage.py combined percentage | 79.97% | 80.05% |
| Missing statements | 882 | 894 |
| Missing branches | 1,714 | 1,733 |

Coverage did not automatically instrument child processes. Therefore 0% in `local_command_child.py`, `audio_transcribe_child.py`, and CLI modules does not mean they were never executed. Direct subprocess tests and independent probes exercise some of them. Conversely, the ~90% statement figure must not be presented as 90% decision-path coverage: branch coverage is about 59%, and some runtime modules rely heavily on mocks.

Local modules with low combined parent-process coverage include `local_command.py` (23.7%), `local_preflight.py` (54.5%), `local_scope.py` (63.7%), `podman_policy.py` (63.4%), `worker.py` (63.3%), `pr_authority.py` (62.7%), and `pr_observer.py` (64.6%). Real runtime probes below add evidence but were not merged into these coverage numbers.

### Independent failure, boundary, and concurrency probes

| Probe | PR result | Local result | Evidence |
| --- | --- | --- | --- |
| Actual SIGTERM during blocked automatic dispatch | Fails fencing invariant | Passes fencing invariant | `sigterm_daemon_qa.py` |
| Stop while dispatch blocked, before process exit | Assignment remains authorized/running | Synchronously fenced | `persistence_qa_probes.py` |
| Accepted command above relay frame limit | Fails | Fails | `engineering_boundary_qa.py` |
| Ordinary nonzero command exit | Correct exit 7 | Correct exit 7 | Engineering boundary results |
| Exactly 1 MiB stdout | Pass | Pass | Engineering boundary results |
| Exactly 1 MiB stderr | Pass | Pass | Engineering boundary results |
| Exactly 32 KiB private stdin | Pass | Pass | Engineering boundary results |
| Command timeout | No completed exit evidence | No completed exit evidence | Engineering boundary results |
| Command output overflow | No completed exit evidence | No completed exit evidence | Engineering boundary results |
| Eight competing scope reservations for one run | Exactly one reservation | Exactly one reservation | Engineering boundary results |
| Native Codex sandbox canary preflight | Shared host runtime probe | Workspace write allowed; private read and network denied | Actual native executable, no model turn |
| systemd scope detached-child preflight | Shared host runtime probe | Pass | Actual user scope, no model turn |
| Actual scoped command and exact 1 MiB output | Shared runtime implementation exercised | Pass, exact 1,048,576 bytes | Native sandbox → systemd → relay → transport |
| Cancel scoped command with detached child | Scope terminated; no survival marker | Same | `engineering_real_scope_qa.py` |
| Timeout scoped command with detached child | Scope terminated; no survival marker | Same | `engineering_real_scope_qa.py` |
| Two real SQLite connections compete for one claim | Exactly one claim | Exactly one claim | `graph_transaction_qa.py` |
| Descendant-only invalidation; sibling preservation | Pass | Pass | Graph transaction probe |
| Stale prerequisite snapshot | Rejected | Rejected | Graph transaction probe |
| Injected audit-write failure during completion | Stage and attempt roll back atomically | Same | Graph transaction probe |
| Expired/replaced claim token | Late write rejected | Same | Graph transaction probe |
| Cancelled run reclaim | Rejected | Rejected | Graph transaction probe |
| Uncertain external action / required follow-up | New approval fenced | Same | Graph transaction probe |
| Mutate every marketing graph node | Correct full descendant closure | Same | 324 graph assertions per tree |
| Source proof binding/coverage mutations | Rejected | Rejected | Marketing boundary probe |
| Platform proof binding/type/time mutations | Rejected | Rejected | Marketing boundary probe |
| Slow source response exceeding configured timeout | Fetch exceeds timeout and succeeds | Same | `marketing_source_timeout_probe.py` |
| Real ffmpeg sample filter, generated 0.1s/1s/2.3s MP4s | Shared production filter exercised | Exactly 12 expected timestamps each | Independent media probe |

Marketing boundary probes made **344 assertions per tree**, including the 324 graph assertions. The source cases covered wrong register binding, empty/duplicate checks, false support/primary flags, and wrong evidence IDs. Platform cases covered wrong brief/channel/kind/dimensions/duration, missing assets, numeric support flags, float-for-int dimensions, future/expired/over-24-hour proofs. These are explicit invariant checks, not mutation-score measurements.

The real detached-child cleanup tests used workspace survival markers because an inner sandbox PID is not necessarily a host PID. After cancellation or timeout, scope state was durably `terminated`, the controller reported `LocalCommandError`, and the child failed to write its six-second survival marker. Both probes passed on both trees. This establishes actual synthetic descendant cleanup through the native sandbox/systemd implementation; it does not test the PR controller's SIGTERM shutdown defect or prove real model-session recovery. An initial PID-based harness was corrected rather than reported as a product failure.

An additional hypothetical `uncertain` dispatcher status caused the PR automatic loop to spin while local fenced immediately. This is **not a separate confirmed PR bug**: the inspected PR dispatcher does not emit that new status on the tested path. It is retained as defensive QA context only.

### Alert validation

Downloaded the official pinned Prometheus 3.15.1 release into `/tmp`; used its `promtool` without changing host or repository configuration.

| Check | Exact PR | Local |
| --- | --- | --- |
| `promtool check rules observability/alerts.yml` | Pass: six rules | Pass: six rules |
| `promtool test rules observability/alerts_test.yml` | Pass | Pass |

Validated rule syntax, synthetic delay/recovery, and stale-versus-missing behavior. Real producer integration exposes QA-04 despite passing rule fixtures. Deployed scraping, collector scheduling, Grafana import, alert routing, and notifications remain unverified.

## Subsystem review and coverage matrix

| Subsystem / user journey | Source and executed coverage | Assessment / remaining boundary |
| --- | --- | --- |
| Task admission and configuration | `worker`, `configured_task`, `task_intake`, private config loaders; full fixture suite | Exact configured task/runtime validation covered. No live Plan task admission performed. |
| Private control socket and CLI | Authentication, owner-only socket directory, request limits, process startup/control fixtures | Synthetic socket integration passes. No public listener introduced. Long-running synchronous control operations still need operational latency characterization. |
| Queue and graph leases | `queue`, `graph`, renewal and automatic worker paths; replacement-token, concurrent-claim probes | Claim serialization and stale write fencing pass. SIGTERM PR gap is QA-01. |
| Durable graph, attempts and cache | `graph`, `graph_runner`, `stage_dispatch`; descendant, stale snapshot, audit rollback probes | Confirmed atomic tested transitions and bounded uncertain-attempt behavior. Broad branch coverage remains incomplete. |
| Approvals and action ledger | `actions`, PR bridge, graph approval waits, human review | Exact version/action, unknown outcome, and required follow-up fencing exercised. PR wait-monitoring gap QA-04. |
| Session provenance and resume | `session_store`, `provenance`, Codex executor/session; fixture launch/turn/home evidence | Synthetic resume and provenance checks pass. Real signed-in model session recovery not established. |
| Local executable/config policy | `local_config`, local-only `local_config_inspect`, managed app-server specification, preflight | Native executable/sandbox inspected and synthetic preflight exercised. Config/skill/model hashes remain explicit boundaries. |
| Local scopes and supervised command | `local_process`, `local_scope`, `local_supervisor`, `local_command`, relay | Real scope/sandbox evidence plus synthetic and boundary tests. Input framing bug QA-02. |
| Engineering workspace binding | `engineering_workspace`, `local_git_snapshot`, dispatch and command supervisor | Workspace/branch/head/policy binding, commit changes and command evidence exercised by fixtures. |
| Engineering QA to PR approval | Engineering entrypoint, review loop, PR bridge and local PR authority | Synthetic full journey reaches human merge review; real remote mutation and independent actual-host acceptance remain absent. |
| Push/create-draft/ready actions | `local_push`, `local_mutation`, `local_pr_authority`, observer, GitHub read adapter | Approval scopes, branch/head checks, unknown results, and observer loop fix reviewed. No actual GitHub write issued. |
| Credentials and isolation | Credential handling, local environment allowlists, Docker config/controller, Podman policy | Trust boundaries reviewed; canary sandbox and detached-scope probes add actual host evidence. No credential plaintext inspected or exported. |
| Source acquisition and snapshots | `source_fetch`, `source_snapshots`, source register/reviewer | URL allowlist/public DNS/TLS, binding, expiry, and evidence checks reviewed. Wall-clock and cancellation gap QA-03. |
| Canonical content, independent review and correction | Marketing adapters/gates, dispatcher, reviewer identities, source coverage, page rules | Exact review inputs, canonical revision invalidation and correction flow covered by fixtures and graph mutation probes. No content-quality benchmark claimed. |
| Freeze and channel fan-out | Exact freeze approval, downstream invalidation, source/page policy changes | Tested canonical changes invalidate freeze and channels; graph closure independently checked across all 18 nodes. |
| Reddit eligibility | Community context, relevance/disclosure, current rule authority and expiry | Fails closed in tested synthetic boundaries; real community authority integration remains unverified. |
| Instagram eligibility | Verified media, platform authority, immutable evidence and channel review | Missing/stale/mismatched evidence rejected. A complete live media campaign remains unverified. |
| OpenFilm project authoring | Brief, supplied media, editable ZIP validation, builder policy/skill/session/attempt evidence | Missing/unsafe project paths, cancelled or mismatched attempts, and lost termination evidence covered in tests. Real full renderer path skipped without package/image configuration. |
| Rendering and asset ingestion | Docker OpenFilm backend, renderer JS, decoded assets, immutable manifests/previews | Synthetic backend integration and real ffmpeg technical checks pass. No fresh full OpenFilm browser/render image acceptance. |
| Independent visual/audio review | Local asset reviewer, OpenFilm review, video samples, audio runtime/transcriber | Access evidence, sample layout, transcript binding, booleans, source/audio absence, and failed reviews covered. Human playback/listening still required. |
| Platform review | Bound asset specifications, role skills in prompt, timestamps, current policy | Negative proof matrix passes. Real external platform-rule freshness/quality not established. |
| Assembly and human gate | Exact package versions, review evidence, decision audit, rejected package handling | Synthetic package/decision integration passes. Model report alone does not authorize publication. |
| Audit projection and telemetry | Audit exporters, HMAC identifiers, replay/cursors, OTLP partial/invalid acknowledgements | Fixture and loopback export tests pass. No real Opik destination or deployed delivery tested. |
| Metrics, dashboards and alerts | SQLite-derived bounded labels, private metrics CLI, rule fixtures | Syntax/fixtures pass; producer mismatch QA-04 identified. No deployed dashboard/notification acceptance. |
| Packaging and deployment | Controller and OpenFilm Dockerfiles, requirements, compose and dockerignore | Static review only; no controller image build or Compose deployment. Runtime Python3.12 and cold-start volume ownership need actual deployment checks. |

## Hosted gates and existing PR feedback

The exact head remained `6e5fe490` when GitHub metadata was refreshed during review. GitHub reported the PR open, mergeable, and not draft; its body still describes a work-in-progress draft. Technical mergeability is not QA approval.

| Gate | Observed status and evidence |
| --- | --- |
| Semgrep | Success at exact head |
| CodeAnt Quality Gates | Failed: 33 antipatterns and 36 complex functions |
| CodeAnt other categories | Reported coverage gates, secrets, duplicates, SAST, bugs, IaC, SCA passed; its comment supplied no coverage percentages |
| CodeAnt line-level review | Skipped because PR exceeds 100 changed files; do not treat the quality-gate result as a full AI code review |
| SonarCloud | Failed: new-code reliability C and security D, both require A |
| CodeRabbit | Skipped |

Sources: [CodeAnt gate summary](https://github.com/Bonobo791/agent-workflows/pull/4#issuecomment-6537623773), [CodeAnt skipped review](https://github.com/Bonobo791/agent-workflows/pull/4#issuecomment-6537550671), [Sonar gate summary](https://github.com/Bonobo791/agent-workflows/pull/4#issuecomment-6543120052).

Retrieved all **129 Sonar issue records** over two API pages. At inspection, 126 were open: 116 code smells, six bug classifications, four vulnerability classifications; three earlier security findings were closed/fixed. Scanner classifications below are independently adjudicated rather than treated as demonstrated exploits.

| Sonar record / area | Review disposition |
| --- | --- |
| `docker:S8541`, controller pip build scripts | Supply-chain hardening concern: no `--only-binary :all:`. No demonstrated exploit in this review. |
| `docker:S8544`, dependency locking | Reproducibility/hardening concern: top-level pins do not universally lock transitive resolved dependencies/hashes. No demonstrated exploit. |
| `docker:S6471`, root user in OpenFilm base Dockerfile | Build/base-stage context. Final renderer selects UID10001 and controller runtime selects nonzero owner UID. Not a confirmed runtime-root bug. |
| `python:S5443`, container `/tmp` tmpfs | Per-assignment isolated container tmpfs, not a shared host `/tmp` mount; no demonstrated cross-user path vulnerability. |
| `pythonbugs:S2259`, OpenFilm builder optional config | Constructor rejects missing supervisor/config before `_policy()`; no reachable initial null dereference found. |
| Five HTML bug classifications | `lang`, page title, iframe title omissions in the synthetic OpenFilm fixture pages. Genuine fixture metadata omissions; not evidence of a production workflow failure. |
| Three closed security findings | Audio argument/path and TLS context findings marked fixed. Current argument/layout, copy operation, and explicit TLS validation changes inspected. |

The code-smell inventory and CodeAnt complexity inventory remain open review debt; this QA does not silently waive either gate. Their existence is material to maintenance of dense validation/dispatch code, but the counts are not additional reproduced functional bugs.

Existing PR review threads were also checked. The observer action-kind shadowing, controller ffmpeg packaging, unsupported short command timeout, and platform-skill prompt omissions have fixes at this head. The allowlisted SQL table interpolation criticism did not establish an injection path: the allowlist is checked inside the same function. The approval-wait issue is acknowledged but still present in the PR, as QA-04 explains.

## Remaining acceptance work

| Priority | Required validation | Why it remains |
| --- | --- | --- |
| Before merge | Fix/retest QA-01, QA-02, QA-03; explicitly resolve or retain QA-04 as scoped acceptance debt | Confirmed functional failures remain in the exact PR |
| Before merge | Adjudicate Sonar/CodeAnt findings and rerun hosted gates at final SHA | Current hosted status fails; local suites do not replace these gates |
| Before runtime acceptance | Real controller process startup/restart with inspected native configuration and real model session recovery | A synthetic executable and persistence tests do not establish signed-in model recovery |
| Before PR-action acceptance | Controlled real GitHub push/draft/ready run with exact approval and independent remote observation | Only synthetic GitHub responses/local remotes were exercised |
| Before media acceptance | Configure OpenFilm package/browser and rootless renderer image/socket; run all opt-in integration tests | Seven PR/nine local skips include these missing paths |
| Before campaign acceptance | Complete source→canonical→freeze→channels→media/community→assembly→human-review run | Full live campaign with current source/platform/community authority remains unverified |
| Before content release | Human complete playback/listening and rights/editorial/platform review | Sampled frames and machine transcripts cannot establish full quality or rights |
| Before deployment | Build controller on Python3.12, initialize private persistent volume, start/restart Compose, validate image policies and filesystem/network isolation | Host Python3.14 and source inspection do not establish deployment |
| Before monitoring acceptance | Deploy collector/exporter/dashboard and test alert notification delivery, stale data, unavailable destination, and replay | Rule and loopback tests cover only local components |

The later local coverage run skipped five OpenFilm-host tests, three rootless-Podman render tests, and one real-Codex worker restart test. The PR skipped five OpenFilm-host and two Podman tests. These were configuration skips, not passing runtime acceptance. Optional authentication dependencies were installed for the coverage runs; no authentication tests remained skipped for missing PyJWT in those runs.

## Reproduction and evidence index

The commands below are read-only against production code; temporary SQLite databases, synthetic process trees, and generated media are disposable. Some require local socket/systemd access. `/tmp` evidence is session-local and may be removed by the host; the substantive results are preserved in this report.

```sh
# Full fixture suite, from each checkout
python -m unittest discover -s tests

# Branch instrumentation used for local; PR used a separate COVERAGE_FILE
PYTHONPATH=/tmp/agent-workflows-qa-tools \
  COVERAGE_FILE=/tmp/agent-workflows-local.coverage \
  python -m coverage run --branch --source=runner,workflows \
  -m unittest discover -s tests -v

# Independent graph, approval metrics and source probes, from each checkout
PYTHONPATH="$PWD" python /tmp/graph_transaction_qa.py
PYTHONPATH="$PWD" python /tmp/approval_metrics_qa.py
PYTHONPATH="$PWD" python /tmp/marketing_boundary_qa.py
PYTHONPATH="$PWD" python /tmp/marketing_source_timeout_probe.py

# This script compares both hard-coded checkout paths
python /tmp/sigterm_daemon_qa.py

# Relay and scope-reservation boundaries
python /tmp/engineering_boundary_qa.py "$PWD"

# Alert syntax and behavior
/tmp/prometheus-3.15.1.linux-amd64/promtool check rules observability/alerts.yml
/tmp/prometheus-3.15.1.linux-amd64/promtool test rules observability/alerts_test.yml
```

| Evidence | Location |
| --- | --- |
| Initial restricted run | `/tmp/agent-workflows-review-tests.log` |
| Successful ordinary PR suite | `/tmp/agent-workflows-pr4-tests.log` |
| Successful ordinary local suite | `/tmp/agent-workflows-local-tests-unrestricted.log` |
| Verbose coverage suite logs | `/tmp/agent-workflows-{pr,local}-coverage-tests.log` |
| Coverage JSON | `/tmp/agent-workflows-{pr,local}-coverage.json` |
| Source/test/docs fingerprint manifest | `/tmp/agent-workflows-qa-local-manifest.json` |
| Sonar full response pages | `/tmp/agent-workflows-sonar-issues.json`, `/tmp/agent-workflows-sonar-issues-2.json` |
| Engineering boundary results | `/tmp/engineering-boundary-pr.json`, `/tmp/engineering-boundary-local.json` |
| Real cancellation/timeout results | `/tmp/engineering-real-scope-pr.json`, `/tmp/engineering-real-scope-local.json` |
| Real scoped cleanup reproduction | `/tmp/engineering_real_scope_qa.py` |
| Focused engineering suites | `/tmp/engineering-runtime-qa.log`, `/tmp/engineering-runtime-pr-qa.log` |
| Detailed engineering review | `/tmp/engineering-runtime-review.md` |
| Independent repro scripts | `/tmp/engineering_boundary_qa.py`, `/tmp/graph_transaction_qa.py`, `/tmp/approval_metrics_qa.py`, `/tmp/marketing_boundary_qa.py`, `/tmp/marketing_source_timeout_probe.py`, `/tmp/sigterm_daemon_qa.py`, `/tmp/persistence_qa_probes.py` |

## Final assessment

The fixture suites, graph invariants, proof rejection matrix, and real low-level media/process probes provide useful confidence in local state and trust-boundary behavior. They also expose why suite success alone is insufficient: accepted command-size framing, network elapsed time, shutdown ordering, and producer-to-alert integration fail outside the original assertions.

No merge, deployment, publication, authenticated GitHub mutation, or live content approval was performed. The reviewed PR requires fixes and final-head hosted validation; the local tree contains additional improvements but still has the command framing and source-deadline defects.

## Later verification — PRs #5 and #6, plan paused

This checkpoint was performed after the user reported two follow-up PRs and requested verification. Implementation remains paused. No PR code, tracker status, branch, approval, deployment or publication was changed. The only workspace edit in this verification is this QA appendix. The preceding assessment is historical for its recorded head, not current status for these follow-ups.

### Repository and hosted state

- PR #4 is merged. Independent `git ls-remote` still reports remote main `604bdc0dd9c55c7ba9bbc1c793ab2cc899dd3546`.
- Local HEAD remains `6a18ab6`, with extensive dirty/untracked work. The local tree is neither exact PR checkout; notably PR #6's `tests/test_local_session_live.py` is absent locally.
- PR #5 remains open at `4aa968a3b9fab00e7b7be60f4b6077b04451be05`, based on `604bdc0`. Its five-file diff contains the render-wait override and test updates, including explicit tracking of normal render and cancellation-cleanup wait arguments.
- PR #6 remains open at `b2cde4897c1238b52e03105dd12271ce4292a514`, based on `604bdc0`. It adds the opt-in live session test and README instructions; it does not contain PR #5.
- Both heads and gate states were rechecked after execution and remained unchanged. Only PRs #5 and #6 were listed open.

| Exact-head hosted gate | PR #5 | PR #6 |
| --- | --- | --- |
| SonarCloud | Success | Success |
| Semgrep | Success | Success |
| CodeAnt Quality Gates | Failure | Failure |
| CodeAnt SCR | Failure | Failure |
| CodeAnt SAST / SCA / Test Coverage | Success / Success / Success | Success / Success / Success |
| CodeRabbit | Neutral | Neutral |
| Other | — | Amazon Q and Gitar success; cubic neutral |

PR #6's CodeAnt summary reports one antipattern. PR #5's retrieved summary reports one antipattern on earlier head `ee5ad880`; that count is not promoted to an exact-current-head count. Its failure statuses themselves are attached to current head `4aa968a`. PR #6 hosted checks are no longer pending. Successful bot check-runs do not resolve the reproduced test-quality concern below.

### Fresh execution results

| Validation | Exact version | Result |
| --- | --- | --- |
| Full unittest suite | PR #5 `4aa968a` | 679 run; 670 passed; 9 skipped; zero failures/errors; 58.147 s |
| Full unittest suite | PR #6 `b2cde48` | 679 run; 669 passed; 10 skipped; zero failures/errors; 57.739 s |
| Configured rootless-Podman integration | PR #5, existing offline renderer | All 3 tests passed; 12.396 s |
| Actual signed-in clean session resume | PR #6, `CODEX_TEST_EXECUTABLE` set to installed Codex | 1 test passed; 13.426 s |
| Promtool rule syntax | PR #5 alert definitions | Six rules passed |
| Promtool alert fixtures | PR #5 alert definitions | Passed |
| Source diff whitespace | PR #5 | Passed |

Full suites ran with the socket access required by integration fixtures. The live resume test was a separate opt-in run; it remains skipped during ordinary discovery. Its synthetic prompts request no tools and use temporary workspaces/scopes. No real repository mutation or publication was performed. The first configured Podman attempt rejected Podman's raw image ID for missing `sha256:`; rerunning with the required digest prefix passed. That setup mistake is not a product defect.

### Verified remaining concern: PR #5 timeout is inactivity-based

At `runner/docker_controller.py:126–136`, the new timeout becomes a socket timeout; `HTTPResponse.read()` still has no monotonic total deadline. The render-budget override fixes the original 15-second inactivity cutoff but does not strictly bound overall response duration.

A fresh real Unix-socket server trickled a valid wait response one byte every 0.1 seconds. With `timeout=0.25`, the exact PR #5 client returned success after **1.6 seconds**. This confirms the review concern independently. It does not prove a live Podman hang; the practical boundary is that an unexpectedly slow/trickling Engine response can exceed the claimed overall wait budget. No fix was applied while the plan is paused.

### Verified remaining concern: PR #6 recall assertion accepts unrelated output

At `tests/test_local_session_live.py:42–50`, the callback collects every agent-message delta without checking thread/turn identity. An independent fake transport drove the real `AppServer` through a valid resumed current turn, injected `cobalt-orchid` from an unrelated thread/old turn, and supplied `I do not recall` as the current response. The PR's exact thread-equality/token assertions still passed.

This demonstrates a false-positive path in the regression, not evidence that the live run actually received unrelated events. The test should bind recall evidence to the resumed thread and its current turn, including events arriving before the `turn/start` response, and reject the injected unrelated-event case. The fresh live pass establishes clean resumption under this test's current checks; it does not eliminate this weakness or establish crash recovery.

Other review claims were checked against source. The assertion after `finally` does not execute if the turn raises; Python propagates the exception. Checking resumability after `finish()` is intentional. `SystemdScopes` and `LocalSupervisor` copy the environment mapping, and no demonstrated mutation path or asynchronous late-callback race was found. Those comments were not adopted as confirmed defects.

### Remaining acceptance and evidence limits

Clean signed-in scope recreation is now freshly exercised. Crash recovery, interrupted provider/remote requests, production quiescence verifiers and recovery controls remain incomplete. Source still requires trusted external callbacks for backup recovery and uncertain scope-stop reconciliation; setup/status docs explicitly say production verifier/control wiring is absent.

Complete real engineering and marketing journeys, real approved GitHub mutations with independent outcome observation, actual controller-image startup/persistent-volume/restart acceptance, deployed collector/exporter/dashboard behavior and notification delivery were not established by this verification. The offline renderer integration does not establish controller packaging or a complete campaign. No plan item was marked complete solely from a PR body or a passing fixture suite.

Evidence logs: `/tmp/agent-workflows-verify-pr5-tests.log`, `/tmp/agent-workflows-verify-pr6-tests.log`, `/tmp/agent-workflows-verify-pr5-podman.log`, `/tmp/agent-workflows-verify-pr6-live.log`, `/tmp/agent-workflows-verify-pr5-checks.json`, `/tmp/agent-workflows-verify-pr6-checks.json`. Isolated exact-head checkouts: `/tmp/agent-workflows-verify-pr5` and `/tmp/agent-workflows-verify-pr6`.

Current conclusion: both follow-ups have passing local execution evidence and successful Sonar/Semgrep results, but neither has all hosted gates passing; both have a reproduced remaining boundary/test-quality concern. Operational readiness remains incomplete and implementation remains paused.


## PR #20 — every-file QA review, October 31, 2026

### Decision and exact scope

**Changes requested: two reproduced P2 findings, plus one P3 storage follow-up.** Passing fixture suites do not close these findings or establish operational acceptance.

Reviewed [PR #20](https://github.com/Bonobo791/agent-workflows/pull/20), “fix: validate terminal scope evidence across workflows.” Every file in the 21-file GitHub inventory was inspected through its complete diff, surrounding implementation, receipt consumers and relevant tests. The scope is 11 runtime modules and 10 test modules; no changed file was sampled out.

- Initial reviewed head: `deb7b4dd7a5adc76840c22f77fdc9b4915a4383c`.
- Final verified current head: `41b6fda91dd6dc6d8719ac56b5f3580479f927b9`.
- Base: `371c80c7e056a07a84cb38447daf0ce80327b3bb` (`main`).
- The initial head was stacked on PR #19's `376dff3`, before its main merge. During QA, the author merged current main. Independently constructed integration tree `6024c5ce594209185fcf594d0a7aabcb8a9f7612` exactly equals the final head's Git tree; `git diff 41b6fda` against that tested integration worktree is empty. Its full-suite and live-systemd results therefore apply to the final head's exact contents.
- Final GitHub mergeability: `MERGEABLE`.

The runtime change checks invocation/cgroup identity as well as a terminated state before accepting model, command, remote-action and media evidence. Review included the ordering of checks, exact run/assignment/turn joins, failure classification, retained claims, receipt publication, positive-path compatibility, policy fingerprints and test independence.

### P20-01 — P2: executor can bypass fencing before identity validation completes

Locations: `runner/codex_executor.py:118`, `:133`, `:154`, `:159`; missing regression coverage in `tests/test_codex_executor.py`.

`finished=True` is assigned immediately after `handle.finish()`. `termination_unverified` remains false until a later identity query succeeds and its result is rejected. Output collection, artifact reads/writes, lease/provenance checks and the identity query can fail before that flag is set. The outer handler then skips the fence and classifies the execution as `failed`, even though the required local termination evidence was never verified.

Two independent probes reproduce this on final-head contents:

1. Adapt the existing real fake-app-server/local-handle test so the second handle records a NULL invocation after finish, then request a missing output file. Output collection raises before terminal evidence validation.
2. Preserve the normal empty output request, but rename the invocation column in the disposable database after finish. The identity query raises a SQLite error before it can set the flag. This independently validates CodeAnt's newly posted query-error comment.

Both produce:

```text
Observed fences: [] audit: ('failed',)
AssertionError: [] != ['r']
```

The existing normal malformed-identity regression still passes. The uncovered cases are failures before/while validating identity, not the ordinary false-result branch. No completed turn is returned by these probes; the defect is the missing recovery fence and incorrect certainty classification.

Recommended correction: treat local termination as unverified until its identity check has completed successfully, validate it before output ingestion, and preserve that uncertainty across query/collection failures. Add independent missing-output and identity-query-error regressions asserting a fence, `uncertain` audit and absent completion records. Keep the final atomic completion transaction's evidence checks.

Reproduction scripts: `/tmp/pr20-executor-order-probe.py`, `/tmp/pr20-executor-sql-probe.py`. Final-tree evidence: `/tmp/agent-workflows-pr20-current-executor-probe.log`, `/tmp/agent-workflows-pr20-current-sql-probe.log`.

### P20-02 — P2: the command regression masks earlier exact-receipt assertions

Location: `tests/test_local_command.py:21–23`.

The new NULL-invocation check leaves that invocation NULL before checking wrong exit code, run and assignment. Those later assertions all have an unrelated reason to return false, so they no longer independently protect their intended predicates.

A controlled in-memory mutation removed only the `c.exit_code=?` SQL predicate and its parameter from `LocalCommandBackend.verify_terminal`:

- Current PR test: **passes with the broken predicate**.
- Pre-PR test from base `371c80c`: **fails with the same broken predicate**.

This proves an introduced coverage regression rather than a speculative test-style concern. Runtime receipt binding works in the unmodified implementation.

Recommended correction: restore the original invocation after the NULL-identity assertion and positively recheck the valid receipt before testing exit-code/run/assignment mismatches; alternatively use independent fixtures/subtests. Verify the exit-predicate mutation is then detected.

Reproduction: `/tmp/pr20-command-mask-probe.py`. Final-tree evidence: `/tmp/agent-workflows-pr20-current-command-probe.log`.

### P20-03 — P3: rejected media evidence can leave newly stored, unreferenced artifacts

Locations: `runner/video_samples.py:81–84`; analogous ordering in `runner/audio_transcriber.py:114–117`.

CodeAnt's video-sample nitpick is valid as a bounded storage concern. The sample is written to the artifact store before the terminal identity is checked. A rejected malformed scope leaves the operation reserved and the artifact unreferenced. An isolated probe produced a unique valid PNG, rejected its NULL-cgroup scope, and confirmed the new 5,918-byte artifact remained retrievable while no sample receipt row referenced it.

The store is content-addressed: repeated identical samples are deduplicated, so the claim that every failed attempt necessarily adds another artifact is too broad. Unique rejected outputs can accumulate. Similar write-before-validation ordering already existed around earlier terminal-state checks; this is not a new successful-evidence bypass.

Recommended follow-up: perform available terminal-evidence checks before durable media writes. Do not blindly delete content-addressed artifacts that could be shared by another receipt. No garbage collector or deletion API was added during QA.

Evidence: `/tmp/pr20-orphan-sample-probe.py`, `/tmp/agent-workflows-pr20-orphan-probe.log`.

### Per-file QA disposition — all 21 changed files

“Pass” below means no additional file-specific defect was found in the reviewed behavior; it is not production acceptance. P20-01 and P20-02 remain open despite passing baseline tests.

| Changed file | Checks and evidence | Disposition |
| --- | --- | --- |
| `runner/audio_transcriber.py` | Creation requires exact run/unit terminated identity; verification retains subject, asset, channel, policy and audio/transcript bindings. NULL invocation blocks both creation and replay. Actual PCM/report integrity remains checked. | Pass; P20-03 analogous write ordering noted. |
| `runner/codex_executor.py` | Traced finish, output collection, transaction, scope identity, turn publication, fencing and audit ordering. Normal invalid identity fences; missing output and SQLite query failures reproduce bypass. | **P20-01**. |
| `runner/local_asset_review.py` | Exact run/role/session/turn/config binding retained; scope tuple validated after delivery/provenance checks. PNG, sampled-video and transcript-dependent review tests remain functional. | Pass. |
| `runner/local_command.py` | Exact assignment/run/exit-code join and integer type guard retained; NULL invocation rejected. Independent SQL-predicate mutation exposes the changed test's masking. | Runtime pass; test weakness P20-02. |
| `runner/local_mutation.py` | Draft/readiness response validation retained; matching unit identity precedes attempt termination. Corrupt readiness scope leaves reserved attempt and uncertain external action. | Pass; remote writes simulated. |
| `runner/local_pr_authority.py` | Exact proposals, actions, scopes and assignment selection retained. Validated recorded identity is required alongside inactive systemd observation and empty cgroup. Push still requires bundle and push scopes. | Pass; remote reads simulated. |
| `runner/local_push.py` | Exact approved target, lease, bare snapshot and scope binding retained. Bad terminal cgroup rejects completion; ledger stays uncertain and workspace claim remains held. | Pass; GitHub push simulated. |
| `runner/local_scope.py` | New helper requires exact run/unit and `terminated` state, then shared identity validation. Independent 34-check matrix covers valid identity, malformed/missing identity, state and mismatches; real scope suites pass. | Pass. |
| `runner/openfilm_builder.py` | Build and proof verification validate each selected identity; zero/multiple assignments still reject. Exact binding, approval, provenance, artifact and policy checks retained. | Pass; editable-project fixture, no full production render. |
| `runner/platform_reviewer.py` | Shared verification hardens both passed and blocked evidence without changing source, brief, provenance, output and policy checks. Both statuses reject NULL invocation. | Pass. |
| `runner/video_samples.py` | Creation and replay reject NULL cgroup; exact asset, run, policy, timestamps, frame count and PNG dimensions retained. Unique rejected-sample storage probe reproduced unreferenced output. | Security check pass; **P20-03**. |
| `tests/test_audio_transcriber.py` | Valid PCM/report path, corruption rejection at replay and creation, channel/status rejection, and reserved-state outcome inspected. Shared-validator bypass detected. | Pass. |
| `tests/test_codex_executor.py` | Normal local success records exact turn/scope binding; ordinary invalid identity fences and audits uncertain. Shared-validator bypass detected. Double-failure/query-error cases absent and reproduced separately. | **P20-01 coverage gap**. |
| `tests/test_local_asset_review.py` | Valid image delivery, missing invocation rejection and restoration, reference/input/image tampering, video samples and audio requirements inspected. Shared-validator bypass detected. | Pass. |
| `tests/test_local_command.py` | New NULL identity rejection works, but identity is not restored before mismatch assertions. Exit-code predicate mutation survives only in the changed test. | **P20-02**. |
| `tests/test_local_mutation.py` | Positive draft/readiness, approval and payload mismatch cases retained; corrupt readiness scope asserts reserved/uncertain state. Shared-validator bypass detected. | Pass; malformed draft scope is not a distinct added case. |
| `tests/test_local_pr_observer.py` | Draft/readiness corrupt invocation rejected and restored before cgroup-empty checks; exact push/bundle evidence and remote-head matching retained. Shared-validator bypass detected. | Pass. |
| `tests/test_local_push.py` | Positive approved push and local compare-and-swap race fixture retained; corrupt cgroup asserts uncertain ledger and held claim. Shared-validator bypass detected. | Pass; no real GitHub write. |
| `tests/test_openfilm_local_builder.py` | Valid project/proof fixture, corrupt invocation rejection/restoration, policy change and nonterminal state rejection retained. Shared-validator bypass detected. | Pass; creation-side extra check reviewed statically. |
| `tests/test_platform_reviewer.py` | Passed and blocked proof rejection with NULL identity; restoration precedes other state, source and brief checks. Shared-validator bypass detected. | Pass. |
| `tests/test_video_samples.py` | Exact video, frame timing, changed input, bad duration/state, corrupt cgroup replay and creation cases inspected; reserved outcome asserted. Shared-validator bypass detected. | Pass; P20-03 probe supplements tests. |

### Automated and exploratory results

| Validation | Tree | Result |
| --- | --- | --- |
| Full suite: `python -m unittest discover -s tests -v` | Initial exact head `deb7b4d` | **827 run, 58 skipped, successful**; 83.874 s. |
| All ten changed test modules, explicitly named | Initial exact head `deb7b4d` | **91 run, 12 skipped, successful**; 6.360 s. Includes inherited tests; not a coverage percentage. |
| Shared helper boundary matrix | Initial exact head; helper unchanged in final tree | **34 assertions passed** across valid/invalid identity, state, missing row and run/unit mismatch. |
| In-memory `valid_scope_identity -> True` mutation | Initial head; all ten changed test files unchanged in final tree | **10/10 targeted regressions detect bypass**, each with an assertion failure and no test errors. This checks shared predicate dependence, not every branch independently. |
| Real systemd workspace/scope/supervisor tests | Initial exact head | **21 run, no skips, successful**; 1.320 s. |
| Full suite | Final-head-equivalent tree `6024c5c` | **833 run, 62 skipped, successful**; 93.297 s. |
| Real systemd workspace/scope/supervisor/interrupted-stop tests | Final-head-equivalent tree `6024c5c` | **27 run, no skips, successful**; 1.958 s. |
| Missing-output + malformed identity failure injection | Final-head-equivalent tree | **P20-01 reproduced:** no fence; audit `failed`. |
| SQLite identity-query failure injection | Final-head-equivalent tree | **P20-01 / CodeAnt comment reproduced:** no fence; audit `failed`. |
| Exit-code SQL-predicate mutation | Final-head-equivalent tree | **P20-02 reproduced:** changed test passes mutant; base test fails mutant. |
| Rejected unique sample artifact probe | Final-head-equivalent tree | **P20-03 reproduced:** 5,918-byte unreferenced artifact persists. |
| Diff whitespace check | Initial and final reviewed diffs | Passed. |

One initial live-systemd invocation additionally named `tests.test_scope_stop_live`, which was absent from the initial stacked head. Its 21 available tests passed and module selection produced one import error. That was a QA command-selection error, not a PR defect. The corrected initial-head command passed all 21; the module exists in the final tree and its cases passed as part of the 27-test run. No skipped test was counted as a pass.

Reproducible primary commands:

```bash
python -m unittest discover -s tests -v
python -m unittest tests.test_audio_transcriber tests.test_codex_executor tests.test_local_asset_review tests.test_local_command tests.test_local_mutation tests.test_local_pr_observer tests.test_local_push tests.test_openfilm_local_builder tests.test_platform_reviewer tests.test_video_samples -v
WORKFLOW_TEST_SYSTEMD=1 python -m unittest tests.test_engineering_workspace tests.test_local_scope tests.test_local_supervisor tests.test_scope_stop_live -v
PYTHONPATH=. python /tmp/pr20-executor-order-probe.py
PYTHONPATH=. python /tmp/pr20-executor-sql-probe.py
PYTHONPATH=. python /tmp/pr20-command-mask-probe.py
PYTHONPATH=. python /tmp/pr20-orphan-sample-probe.py
PYTHONPATH=. python /tmp/pr20-identity-mutations.py
```

Temporary logs: `/tmp/agent-workflows-pr20-full.log`, `/tmp/agent-workflows-pr20-changed-tests.log`, `/tmp/agent-workflows-pr20-systemd-corrected.log`, `/tmp/agent-workflows-pr20-integration-full.log`, `/tmp/agent-workflows-pr20-integration-systemd.log`, `/tmp/agent-workflows-pr20-mutations.log`, plus the finding-specific logs above. Temporary probe scripts are supporting evidence; the reproduction conditions and observed outputs are recorded here independently of their lifetime.

### Hosted checks and review-comment triage

Final observed head `41b6fda`: **SonarCloud passed**, **Semgrep passed**, **CodeAnt Quality Gates, SCR, SAST, SCA and Test Coverage all report success**, CodeRabbit neutral. CodeAnt completed its current-head checks during final report verification; the earlier comment naming `deb7b4d` is superseded by directly verified statuses on `41b6fda`. A successful scanner gate does not close the independently reproduced findings. CodeAnt Test Coverage explicitly says `Coverage data not available` despite its success status; no measured line/branch coverage is established by that status.

- CodeAnt executor query-error suggestion: **valid, reproduced, included in P20-01**.
- CodeAnt orphan-sample suggestion: **valid with the content-addressed deduplication qualification, P20-03**.
- Initial-head Codex code/security review: failed to complete; not a clean verdict.
- Initial-head Amazon Q review: could not finalize because refs changed; neutral check is not a completed review.
- CodeRabbit: review quota reached; neutral check is not a code-review pass.
- Gitar: free-plan capability notice does not establish functional validation.

No GitHub comments, review-thread changes, source fixes, commits, pushes, PR merge or deployment were performed for this QA task. The isolated initial-head checkout remains clean. A separate disposable integration checkout contains only the test merge used for final-tree validation.

### Operational limits and rollout consequences

Systemd tests use disposable scopes and real local Git workspaces. Model output, transcript generation, remote GitHub mutation/observation and media-production checks use controlled fixtures. No real GitHub write, signed-in model production run, full OpenFilm production render, deployed monitoring, packaging acceptance or provider-side cancellation/outcome recovery was established. Existing human playback/listening and approval gates remain applicable.

Several reviewed media/build/review policies hash their implementation files. These source changes can invalidate old approvals and receipts even for otherwise well-formed scopes; required policy reapproval and refreshed evidence must be considered during rollout. No approval was changed or bypassed during QA.

**Current conclusion:** all 21 files have a recorded QA disposition and the final tree has passing baseline suites, but P20-01 and P20-02 remain open. P20-03 is a lower-priority storage follow-up. This review does not recommend treating the PR or operational rollout as fully accepted until the two P2 findings are addressed and their fixes receive current-head validation.


## PR #22 — live OpenFilm cancellation QA, October 31, 2026

### Scope, revisions and decision

Reviewed both changed files in [PR #22](https://github.com/Bonobo791/agent-workflows/pull/22), “test: verify live OpenFilm render cancellation,” including the setup refactor, every test method, Engine synchronization, database transitions and cleanup failure paths.

- Initial head: `3a73e1541eb2adcf41a98e020a4bb47f9475359e`.
- Updated final verified head: `cff4082b35b5c294c8f9c774265b9b7ac29d609d`.
- Base: `371c80c7e056a07a84cb38447daf0ce80327b3bb`.
- Concurrent fixes inspected: `1ffdc7f` (“stop render before waiting for cleanup”) and `cff4082` (“keep cancellation render active”).
- Final mergeability: `MERGEABLE`.

**Current decision: one P2 cleanup failure-path finding and one P3 resource-cleanup follow-up remain.** The earlier fast-render race was reproduced on the initial head and verified fixed under the same eight-second scheduling delay on the final head. All normal live tests and the final full suite pass. CodeAnt's current quality/SCR gate still fails on one unidentified antipattern.

No production source was changed by this PR. This QA task did not edit PR source, publish comments, push, merge or deploy. The report was appended locally; the isolated review checkout is clean.

### P22-01 — P2: failed fallback stop still leaves forced removal behind a blocking join

Current locations: `tests/test_openfilm_podman_integration.py:131–144`.

The updated finally block retries `backend.cancel`, then issues `podman stop -t 0` before joining the worker. That fixes the ordinary failure ordering. However, when cancellation raises `DockerError` and the fallback Podman stop times out, control still enters `pool.shutdown(wait=True)` before `podman rm -f`. Forced removal and fixture teardown cannot run while that join is waiting for the render worker.

A bounded failure probe executed the current method's actual AST cleanup block with a pending future, an injected cancellation failure, a stop timeout and a controlled blocked pool join. While the join was held, the only attempted command was `stop`; forced `rm` and teardown had not run. Releasing the join allowed `rm` and teardown to proceed, and the original stop timeout propagated.

This confirms the dependency/order defect. It does **not** demonstrate that the ordinary UnixEngine wait hangs forever: its render request has a 120-second deadline. Under that implementation the failure can delay cleanup until the outstanding wait finishes or expires; a worker stuck outside its bounded operation would make the join unbounded.

Recommended correction: attempt bounded forced removal of the exact fixture-owned containers before joining when graceful cancellation/stop fails. Always close connections and tear down the fixture, preserving cleanup errors. Merely switching to `wait=False` is insufficient to prove thread/process exit; the executor thread must still be accounted for.

Evidence: `/tmp/pr22-current-cleanup-probe.py`, `/tmp/agent-workflows-pr22-current-cleanup.log`.

### P22-02 — P3: forced-removal failure skips assignment connection close

Current locations: `tests/test_openfilm_podman_integration.py:143–146`; the ordinary render test retains analogous ordering at `:164–169`.

`assignments.close()` follows the `podman rm -f` loop. If removal raises `TimeoutExpired` or another subprocess error, this explicit connection close is skipped. The new outer finally **does** now run `fixture.tearDown()` in the cancellation test, so the original whole-fixture leak was partially fixed.

A current-head cleanup probe injected a removal timeout after the pool join. It confirmed that fixture teardown runs but `assignments.close()` is not called. The separate assignment connection is not attached to `fixture.service.docker_assignments`; `Service.close()` therefore does not explicitly close it as a fallback. Garbage collection may eventually release it, but cleanup does not guarantee that release before the test exits.

Recommended correction: put assignment closure in its own finally around the removal loop. Retain the outer fixture teardown and surface the failed container removal rather than treating it as successful cleanup.

Evidence: `/tmp/agent-workflows-pr22-current-cleanup.log`.

### Earlier finding resolved during QA: the short render raced the observing thread

On initial head `3a73e15`, the event was signalled when entering the timed Engine wait, but that event did not keep the short synthetic render alive. An eight-second delay immediately after the event let rendering complete before cancellation; the test failed with:

```text
AssertionError: 'terminated' != 'running'
```

Final head `cff4082` makes the cancellation-only synthetic scene spend 30 seconds in its frame function. Repeating the exact eight-second delayed-observer scenario passed in 8.327 seconds and still verified cancellation and terminal state. The normal positive render fixtures remain unchanged by the `slow=False` default.

This is evidence that the reproduced eight-second race is fixed, not a guarantee against arbitrary scheduler suspension longer than the fixture's bounded lifetime. Normal cancellation asserts a running container; it does not separately instrument a particular browser/encoder instruction.

Evidence: `/tmp/pr22-slow-observer-probe.py`, `/tmp/agent-workflows-pr22-slow-observer.log` for the initial head; `/tmp/pr22-slow-observer-current.py`, `/tmp/agent-workflows-pr22-current-delayed.log` for the final head.

### Review-comment triage

| Comment | Disposition | Reason |
| --- | --- | --- |
| Amazon Q: wait event can precede assignment row/running-state commit | **Not reproduced; source ordering contradicts the claimed race.** | `DockerAssignments.start()` writes the running state before returning; the backend invokes the timed wait only afterward. The event is set in that later call. The isolated fixture has no concurrent row-deletion path. The final head adds an explicit row-presence assertion for diagnostics anyway. |
| Amazon Q: Podman can auto-remove the container before post-cancel inspection | **Not applicable to configured fixture behavior.** | Disposable creation sets `AutoRemove=False`, supervisor termination does not remove containers, and fixture-owned removal happens after verification. `UnixEngine.request` returns HTTP status/body; the current test explicitly expects status 200. Accepting an unexpectedly missing container would weaken the exact retained-container check. |
| Amazon Q: pool shutdown precedes cleanup | **Valid on initial head; partially fixed, residual P22-01.** | Current head adds graceful/forced-stop attempts before join, but forced removal remains behind join if those fail. An indefinite ordinary Engine wait was not reproduced. |
| CodeAnt: event does not prevent the renderer completing before cancellation | **Valid on initial head; reproduced and resolved for the delayed-observer case on `cff4082`.** | Actual offline Podman runs establish both the original failure and the corrected eight-second-delay result. |
| CodeAnt: one antipattern in quality/SCR gates | **Unclassified scanner finding.** | Exact-head GitHub status descriptions provide count/rating but no rule, file or line. No suppression or speculative code change was made. |

### Per-file disposition

| Changed file | QA performed | Result |
| --- | --- | --- |
| `docs/PILOT_SETUP.md` | Reviewed all four added lines against the actual opt-in fixture and assertions. They distinguish successful cancellation from interrupted-stop reconciliation and full marketing acceptance. | Pass; scope/limitations are accurate. |
| `tests/test_openfilm_podman_integration.py` | Reviewed all four tests, synthetic builder/setup refactor, exception cleanup, exact DB/container bindings and concurrent worker lifecycle. Ran actual rootless Podman rendering, capability rejection, independent terminal verification and cancellation; injected no-op cancellation and cleanup failures; retested updated slow fixture. | Baseline passes; P22-01 and P22-02 remain. |

### Automated and live results

| Check | Head | Result |
| --- | --- | --- |
| Full suite: `python -m unittest discover -s tests -v` | `3a73e15` | **832 run, 63 skipped, successful**; 91.139 s. |
| Related OpenFilm stage/builder/render suites | `3a73e15`; these files are unchanged by the subsequent test-only commits | **40 run, no skips, successful**; 7.348 s. |
| All configured Podman tests | `3a73e15` | **4 run, no skips, successful**; 16.163 s. |
| No-op backend cancellation mutation with real synthetic Podman render | `3a73e15` | **Detected**: regression fails with `DockerError not raised`. No source file modified. |
| Delayed observing thread, real Podman | `3a73e15` | **Original race reproduced** after 8 s: terminated state observed before cancellation. |
| Pool-join and removal-error cleanup probes | `3a73e15` | Original cleanup blocking and skipped teardown reproduced safely using controlled dependencies. |
| Full suite | **Final head `cff4082`** | **832 run, 63 skipped, successful**; 89.596 s. |
| All configured Podman tests | **Final head `cff4082`** | **4 run, no skips, successful**; 21.965 s. |
| Delayed observing thread, real Podman | **Final head `cff4082`** | **Passed** with the same 8 s delay; 8.327 s. |
| Failed cancellation + failed fallback stop + blocked join probe | **Final head `cff4082`** | **P22-01 reproduced**; all probe threads were released and joined. |
| Forced-removal timeout cleanup probe | **Final head `cff4082`** | **P22-02 reproduced**; fixture teardown runs, separate assignment close is skipped. |
| Diff whitespace check | Initial and final diffs | Passed. |

Rootless Podman prerequisites were verified rather than inferred from the PR body: existing `/usr/bin/podman`, UID 1000, socket `/run/user/1000/podman/podman.sock`, and already installed image `sha256:dbebc6c0a0f3d3137cab34190d5cf5b73051aae7d2a8b79963413d6f30b894fe`. No image build, pull or installation was required. Live fixtures use synthetic content, disabled container networking, read-only root filesystems, dropped capabilities, `keep-id`, private SELinux job mounts and temporary state. Successful test cleanup removes the exact IDs recorded in each isolated fixture database; no global container cleanup was performed.

Primary commands:

```bash
python -m unittest discover -s tests -v
python -m unittest tests.test_openfilm_stage tests.test_openfilm_builder tests.test_openfilm_render -v
OPENFILM_PODMAN_IMAGE=sha256:dbebc6c0a0f3d3137cab34190d5cf5b73051aae7d2a8b79963413d6f30b894fe OPENFILM_PODMAN_SOCKET=/run/user/1000/podman/podman.sock python -m unittest tests.test_openfilm_podman_integration -v
PYTHONPATH=. python /tmp/pr22-current-cleanup-probe.py
OPENFILM_PODMAN_IMAGE=sha256:dbebc6c0a0f3d3137cab34190d5cf5b73051aae7d2a8b79963413d6f30b894fe OPENFILM_PODMAN_SOCKET=/run/user/1000/podman/podman.sock PYTHONPATH=. python /tmp/pr22-slow-observer-current.py
```

Logs: `/tmp/agent-workflows-pr22-full.log`, `/tmp/agent-workflows-pr22-focused.log`, `/tmp/agent-workflows-pr22-podman.log`, `/tmp/agent-workflows-pr22-cancel-mutation.log`, `/tmp/agent-workflows-pr22-slow-observer.log`, `/tmp/agent-workflows-pr22-current-full.log`, `/tmp/agent-workflows-pr22-current-podman.log`, `/tmp/agent-workflows-pr22-current-delayed.log`, `/tmp/agent-workflows-pr22-current-cleanup.log`.

### Final hosted state and acceptance limits

At final verified head `cff4082`, **CodeAnt Quality Gates/SCR fail** with one antipattern (rating C; no rule/location supplied). CodeAnt SAST and SCA pass; its successful coverage status explicitly means **“Not applicable — no applicable code changes.”** **SonarCloud and Semgrep pass.** CodeRabbit is neutral; no independent clean-review claim is inferred from that status.

These live checks establish successful cancellation of disposable synthetic renders and no committed render result in the tested case. The graph attempt is deliberately begun as uncertain before execution and remains uncertain after cancellation; this is not proof of a complete automatic-worker recovery transition. Interrupted stop reconciliation, provider-side cancellation, full production editorial/media review, all browser/encoder cancellation phases, deployment and end-to-end marketing acceptance remain unverified. The slow scene is test-only bounded busy work, not a production rendering behavior change.

**Conclusion:** the final head has passing synthetic/live execution evidence and a verified fix for the reproduced scheduling race. Residual cleanup failure paths P22-01/P22-02 and the unresolved CodeAnt gate prevent an unqualified QA sign-off.


### PR #22 follow-up — cleanup fixes pushed

The owner authorized fixes and push after QA. Commit [`5784c76dc1353fe2f18ae6c453b23246d41ae448`](https://github.com/Bonobo791/agent-workflows/commit/5784c76dc1353fe2f18ae6c453b23246d41ae448) is independently verified on `test/openfilm-cancel-live`; PR #22 is mergeable.

P22-01 and P22-02 are addressed in the test fixture: cleanup attempts forced removal of every exact container ID recorded in the fixture database before waiting for the worker, attempts remaining IDs when one removal fails, always closes the assignment connection, and always tears down the fixture. Worker draining is bounded to five seconds. An unfinished worker causes an explicit cleanup failure and nonblocking pool shutdown; it is not silently reported as stopped. An unavailable container runtime can still prevent physical termination, which is reported as a failure rather than hidden.

Three unconditionally enabled regressions cover removal before joining, removal failures with continued cleanup, and an unresolved worker without blocking shutdown. Two independent in-memory mutations restored the old ordering and omitted failure-safe closing; the corresponding regressions caught both. AST comparison verified every existing live-test assertion is unchanged.

Validation on the pushed contents: **835 full-suite tests run, 63 skipped, successful** (83.904 s); **all seven Podman-module tests passed with the configured real rootless Podman runtime** (23.397 s), comprising four existing live cases and three new cleanup regressions. `git diff --check` passed. Logs: `/tmp/agent-workflows-pr22-fixed-full.log` and `/tmp/agent-workflows-pr22-fixed-podman.log`; mutation probe: `/tmp/pr22-fix-mutations.py`.

The isolated PR checkout is clean. Hosted checks were queued immediately after push; CodeAnt's previous antipattern finding is not treated as a result for this new head. PR merge, deployment, interrupted-stop recovery and full production workflow acceptance were not performed.


### PR #22 second triage — delayed Engine creation, fixed and pushed

The new Codex P2 comment on `5784c76` was valid: an Engine create response could arrive after the first removal scan, while the recorded container ID was still NULL. Commit [`3279b9eb524be704a9ace893fd5aab3e4e3321fa`](https://github.com/Bonobo791/agent-workflows/commit/3279b9eb524be704a9ace893fd5aab3e4e3321fa) is independently verified on PR #22's branch.

Cleanup now fences further launches through the fixture supervisor, removes already recorded containers, drains the worker within its cleanup budget, and performs another exact-ID sweep after quiescence. Successful removals are tracked so the second scan does not try to remove the same ID twice. The assignment connection remains available until the final scan, then closes unconditionally.

If the worker does not quiesce or removal fails, private fixture state is retained and the failure identifies its location. The render fixture uses `TemporaryDirectory(delete=False)` so garbage collection cannot silently erase retained state; successful teardown still explicitly deletes the directory. Graph/service handles and test patches are released even when the state directory is retained. The retained state can accept the late recorded container ID for operator recovery. Read-only SQLite inspection connections also now close explicitly.

Regressions for late-ID sweeping and retaining pending-worker evidence failed on the previous cleanup and pass after the fix. An actual rootless Podman test holds the create response after the container exists but before its ID is recorded, observes the NULL/creating database row, releases the response during cleanup, and verifies no start request occurred, the exact container was removed (HTTP 404), the worker exited, and successful teardown deleted the fixture directory. An in-memory mutation omitting the final sweep is detected. All pre-existing live-test assertions were preserved.

Validation: **838 full-suite tests run, 64 skipped, successful**; the final full run completed in 185.687 s. **All ten Podman-module tests passed** in the final live/resource run (39.904 s), comprising five live cases and five unconditional cleanup regressions. Explicit garbage collection reported **no ResourceWarnings**. Logs: `/tmp/agent-workflows-pr22-late-create-final-full.log`, `/tmp/agent-workflows-pr22-late-create-final-podman.log`. Mutation probe: `/tmp/pr22-late-sweep-mutation.py`. `git diff --check` passed.

Other comment triage is unchanged: the alleged pre-commit assignment race and automatic container-removal claim do not match the implementation; explicit diagnostic assertions are already present. The short-render scheduling race and ordinary join/removal ordering are addressed. CodeAnt still reported one unidentified antipattern on preceding head `5784c76`; the published statuses did not identify its rule/location. No scanner suppression or speculative fix was applied.

The pushed SHA is verified, PR #22 is mergeable, and the isolated checkout is clean. Hosted checks were queued on `3279b9e` at verification; no current-head CodeAnt verdict was available yet. No PR merge or deployment was performed. An unavailable runtime or permanently blocked request can still require manual recovery from the retained private state; it is reported as unresolved rather than claimed stopped.


## PR #23 — every-file QA and review triage, October 31, 2026

Scope: [PR #23](https://github.com/Bonobo791/agent-workflows/pull/23), `feat/verified-local-recovery`, reviewed starting at `8338c7affebd8f7c8c03fbb28547599233840dd4` against base `688e45ad17e4cf09b98dd98da143b85a7ffb22cb`. All 21 changed files were reviewed. Changes and validation used `/tmp/agent-workflows-pr23-review`; unrelated dirty work in the original checkout was preserved. Skills applied: senior-qa, code-reviewer, receiving-code-review.

### Findings and fixes

**P23-01 — confirmed, fixed: Git verification held the global SQLite writer lock.** Both `claim` and `claim_local` previously began `BEGIN IMMEDIATE` before running several Git subprocesses, each allowed ten seconds. A slow checkout could prevent unrelated state writers from progressing. A regression attempted a write through another SQLite connection during verification and reproduced `sqlite3.OperationalError: database is locked`. Git verification now precedes the write transaction. The transaction rechecks the complete saved workspace row, current assignment and exact approval before changing the assignment, reserving a scope, recording its binding and auditing it. Concurrent contenders still serialize. A workspace-specific generation counter, incremented by an update trigger, also detects a claim/release cycle that restores the original row while verification is in progress. Regressions cover changed branch, revoked approval, a competing assignment and this claim/release cycle during verification for both claim methods. The additive table and trigger initialize automatically for existing databases; unrelated state writes do not invalidate verification.

**P23-02 — confirmed maintainability findings, fixed:** SonarCloud exposed six open issues despite its successful gate: cognitive complexity in `_run_scoped`, a nested conditional in the audit projection and four exception tests that also called `time.monotonic()` inside `assertRaises`. Extracted the existing transactional command launch, result validation and cleanup blocks into private functions; preserved their validation, rollback and error handling. Replaced the nested expression with explicit branches. Moved deadline calculation outside exception assertions. Added protocol checks for malformed frames, boolean exit codes, invalid base64, excessive output and a lost run fence, plus a valid result.

### Every-file review

| File | QA result and evidence |
| --- | --- |
| `README.md` | Reservation, launch and termination are distinguished correctly; no claim of automatic acceptance. |
| `docs/PILOT_SETUP.md` | Recovery instructions match starting/running/stopping states and require exact terminal evidence; legacy claims are not backfilled. |
| `docs/audit-history.md` | Binding and command-launch audit descriptions match transactions, keyed export and non-backfill behavior. |
| `runner/audit_export.py` | Both new event tables project keyed assignment/scope identifiers without raw values; simplified nested expression. Projection and delivery tests cover correlation/privacy. |
| `runner/command_supervisor.py` | Command, workspace, local scope and binding reserve on the graph connection in one transaction; rollback prevents launching after audit failure. Admission calls workspace_snapshot, which initializes the registry. |
| `runner/engineering_workspace.py` | Fixed writer-lock duration; database binding and approval rechecked under the lock. Scope, workspace, push bundle mapping and audit writes roll back together. Legacy binder retained for older claims. |
| `runner/local_command.py` | Reservation must match workspace, assignment and starting scope. Durable unique launch record prevents retry after uncertain launch; missing audit prevents launch. Extracted existing blocks without relaxing protocol, fence, output or stop checks. |
| `runner/local_git_snapshot.py` | Forwards the exact optional workspace claim and push mapping to sandbox reservation; snapshot verification and private output boundary retained. |
| `runner/local_process.py` | Atomic workspace/scope reservation precedes systemd launch. Launch failure leaves exact durable starting binding and cannot imply termination. |
| `runner/local_push.py` | Bundle mapping is written within claim_local, not an out-of-transaction callback. Early push intent holds no workspace; uncertain external action prevents automatic replay. Actual push uses a separate recorded scope and exact-head/fast-forward checks. |
| `runner/local_scope.py` | BEGIN IMMEDIATE serializes reservation; shared helper requires a transaction and matching audit event. Missing reservation/transition/termination audit fails closed. Termination still requires identity and empty cgroup. |
| `runner/local_supervisor.py` | New engineering execution forwards claim into local launch and no longer invokes legacy binder afterward; handle retains exact assignment for terminal release. |
| `tests/test_audit_export.py` | New privacy/correlation assertion covers the binding event and telemetry attributes. Existing delivery and projection tests retained. |
| `tests/test_command_supervisor.py` | Interrupted launch preserves binding; missing/rejected audit rolls back workspace, command and scope records, without invoking backend. |
| `tests/test_engineering_workspace.py` | Atomic rollback, missing triggers, push record failure, concurrent claims, interrupted process launch and exact terminal release covered. Added writer-lock and binding/approval race regressions. |
| `tests/test_local_command.py` | Unbound command cannot launch; concurrent callers launch at most once after uncertain start; audit failure prevents launch. Corrected exception-test scope and added protocol boundary cases. |
| `tests/test_local_git_snapshot.py` | Optional workspace claim forwards unchanged while preserving exact commit and unsafe-path rejection coverage. |
| `tests/test_local_push.py` | Crashes before reservation leave no workspace claimed; crashes after reservation preserve exact bundle mapping. Invalid terminal evidence stays uncertain. Successful path verifies both scopes. |
| `tests/test_local_scope.py` | Missing/rejected audit, transaction requirement and stopped-but-unaudited scope cases exercised. Diagnostic distinguishes unavailable reservation from prior unresolved scope. |
| `tests/test_workspace_claim_crash_live.py` | Kills an actual subprocess immediately before systemd launch, verifies exact starting binding, and rejects workspace release and stopped confirmation without evidence. This test intercepts the launch and does not establish physical systemd termination. |
| `tests/test_workspace_reservation_live.py` | Opt-in tests run a real approved command and sandboxed Git snapshot, observe atomic binding before launch, verify exact terminal scope and release. No GitHub push or provider model request is needed. |

### All comment and CI triage

- Amazon Q duplicate legacy binding: outdated/inapplicable to new callers. `claim_local` callers no longer call `bind_local_scope`; legacy binder remains explicitly documented and tested.
- Amazon Q serialization bypass: invalid at reviewed head. `EngineeringWorkspace.claim_local` uses its own `BEGIN IMMEDIATE` on the same SQLite file. Different connection objects do not bypass SQLite locking. Concurrent claim regression retains exactly one binding.
- Amazon Q schema dependency: admission enforces initialization through `workspace_snapshot`/`EngineeringWorkspace`. Missing tables raise SQLite errors; missing audit trigger is detected and rolls back. The alleged silent creation/failure does not occur.
- Amazon Q bundle callback outside transaction: already fixed at starting head. Push passes `(action, scope, 'bundle')` into `claim_local`; failing the mapping insert rolls back claim, scope and audits.
- Amazon Q early push-attempt commit, and CodeAnt's matching nitpick: intentional conservative intent record. Before reservation no workspace is held; external action remains uncertain and cannot automatically replay. Covered by pre-reservation interruption test. Combining it with workspace reservation is unnecessary for the stated ownership invariant.
- Amazon Q summary repeats the five inline concerns above; no additional finding.
- CodeAnt writer-lock performance suggestion: valid; fixed as P23-01.
- CodeAnt misleading reservation IntegrityError: already fixed in `8338c7a`; regression distinguishes audit outage from prior unresolved scope and avoids exposing trigger details.
- CodeAnt Quality Gates/SCR: starting head failed with two antipatterns and two complex functions; linked report exposes only an application shell without authenticated rule details. No suppression/config weakening was applied. The six accessible SonarCloud issues were separately triaged and fixed; their relationship to CodeAnt's undisclosed rules is unverified.
- CodeAnt SAST/SCA passed at starting head; secrets/bugs/IaC/duplication passed in its summary. Its coverage success explicitly means no coverage data, not measured coverage.
- SonarCloud and Semgrep check-run `head_sha` independently matched `8338c7a`; both passed. SonarCloud had six open maintainability issues and 0% reported new-code coverage, addressed above without claiming measured coverage.
- CodeRabbit neutral is rate limited, with review/analysis steps skipped. It is not an independent completed review.
- Codex code-review summary had no findings at `8338c7a`; security summary was for older `f0087b0`. CodeAnt low-risk summary was for older `4e4f270`, not current-head evidence. Gitar evaluated no rules and functional validation was disabled. Marketing/status comments supply no technical findings.

### Validation and delivery checkpoint

The first targeted run passed 159 tests with 13 opt-in skips. The initial full run passed 1,017 tests with 64 opt-in skips (231.893 s); it was running while local fixes were being prepared, so it is not treated as exact-final-head evidence. Initial real-runtime QA passed all 49 selected cases. After command restructuring, 60 selected runtime/preflight/child cases passed with real systemd/Codex configuration. A sandbox-restricted audit run could not open two loopback sockets; final full validation uses the required elevated runtime instead. One exploratory command included nonexistent module names; the corrected 60-case invocation passed. These harness errors are not counted as passing checks.

Final exact-code results and push verification follow below.


**Push verified:** commit [`18391cd8ddda4f32ff9bc2d2a286a5ad4fceb13d`](https://github.com/Bonobo791/agent-workflows/commit/18391cd8ddda4f32ff9bc2d2a286a5ad4fceb13d) was pushed without force to `feat/verified-local-recovery`; independent `git ls-remote` and PR metadata both matched. Only five intended code/test files were committed; the original checkout and this local QA document were not staged. The isolated checkout is clean.

**Final live validation:** all **61 tests passed** in 19.027 seconds with `WORKFLOW_TEST_SYSTEMD=1` and `CODEX_TEST_EXECUTABLE=/home/user/.local/npm/bin/codex`. Modules: workspace reservation live, workspace claim crash, engineering workspace, local scope, local command, local Git snapshot, local process, local preflight and local command child. Log: `/tmp/agent-workflows-pr23-delivery-live.log`. A separate mutation removed the workspace generation trigger; both claim/release-cycle regressions then failed as expected, confirming the new safeguard is exercised. `git diff --check` passed.

At immediate push verification the new head had no hosted results yet; old CodeAnt failures and old Semgrep/Sonar successes are not results for the new commit. Final full-suite and refreshed CI results follow.


**Hosted follow-up at `18391cd`:** SonarCloud passed and its public issues API reported **zero open issues**; its check-run SHA matched the pushed commit. This confirms the six open maintainability findings were cleared without accepting/suppressing them. PR mergeability is MERGEABLE. CodeRabbit again reports rate limiting with review steps skipped. Semgrep was still in progress and CodeAnt had not yet published a new-head result at this checkpoint. No merge or deployment was performed.


**Final exact-code full suite:** **1,058 tests run, 64 skipped, successful** in 243.146 seconds on the frozen contents of pushed commit `18391cd8ddda4f32ff9bc2d2a286a5ad4fceb13d`. Log: `/tmp/agent-workflows-pr23-delivery-full.log`. The opt-in runtime cases were separately enabled in the 61-case live run above. The test count includes inherited fixture tests discovered through multiple modules; it is not 1,058 distinct operational scenarios.

**Final hosted CI at the pushed head:** Semgrep SUCCESS; SonarCloud SUCCESS with zero open issues; CodeAnt SAST SUCCESS, SCA SUCCESS, and informational Test Coverage SUCCESS explicitly reporting unavailable coverage. CodeAnt Quality Gates and SCR **FAIL**: **five antipatterns (rating C), one complex function**, zero reported bugs, zero duplication, no secrets/security/IaC/dependency findings. The counts changed from two antipatterns/two complex functions at the starting head. The updated comment and commit status were read directly; neither provides rule identifiers, paths or explanations for those remaining findings. The linked CodeAnt page returned only its application shell without usable finding details. They remain unverified scanner findings, not dismissed as false positives, and no gate thresholds or suppressions were changed. CodeRabbit is neutral because review was rate limited. PR #23 remains MERGEABLE at the independently verified pushed SHA.

**Conclusion:** every changed file and all available comments/CI outputs were reviewed; the reproduced writer-lock problem and six accessible SonarCloud issues are fixed and pushed. Local regressions and the selected real-runtime recovery paths pass. **An unqualified QA sign-off is withheld because CodeAnt's quality gates remain red and their detailed findings were unavailable.** No PR merge, deployment, real workflow GitHub push, full provider session restart/resume, complete production workflow acceptance, packaging acceptance or monitoring acceptance was performed. Crash-before-launch tests establish fencing, not automatic safe recovery from every interrupted OS/provider request.


## PR #24 — every-file QA, comment and CI triage, October 31, 2026

Reviewed [PR #24](https://github.com/Bonobo791/agent-workflows/pull/24), `feat/clean-checkpoint-handoff`, starting at `8f9333e057c6e5acba1b46c3fe1fb54c4814968e`, originally against base `210c638b03b1577c85f0ef2fca84d7d68be7b28e`. Reviewed all 20 changed files, their callers and relevant fixtures. Skills: senior-qa, code-reviewer, receiving-code-review and resolving-merge-conflicts. Work used `/tmp/agent-workflows-pr24-review`, preserving unrelated dirty work in the original checkout.

### Confirmed findings and fixes

**P24-01 — fixed: mismatched graph task identity accepted by checkpoint and heartbeat.** Both queries joined a graph claim by run without verifying its task ID against `graph_runs.task_id`. A regression changed the claim's task ID and demonstrated that checkpoint still released the queue/graph claims. Both paths now require the task identity match. Checkpoint rejects without releasing claims; heartbeat rejects without extending the job lease. These checks match `Graph._lease`'s existing ownership contract.

**P24-02 — fixed: approval and withdrawal could commit after claim expiry during their last write.** Proposal approval checked claims before changing the proposal state. A trigger expiring the queue claim during that state change demonstrated a committed approval despite expired ownership. Withdrawal similarly checked claims before its writes but not after recording withdrawal history. The bridge now shares an exact queue/graph claim check, runs it after proposal approval writes, and checks both before and after withdrawal writes. Expiry causes the entire transaction to roll back. The engineering integration covers rejected/ignored proposal writes and expiry of either claim during action insertion, proposal update or withdrawal-history insertion. Rollback checks retain proposal state, ledger state and authorization history. A separate disposable-fixture mutation omitted withdrawal's final check and reproduced the expired-claim commit; the fixed code rolls back the same trigger.

**P24-03 — fixed: accessible SonarCloud maintainability findings.** Starting head's successful gate nevertheless reported ten open issues: complexity in checkpoint, CLI request handling and proposal withdrawal; repeated SQL/error literals in the bridge; and four exception tests containing a separate time call. Extracted checkpoint runtime/claim validation and bridge claim/competing-work checks, consolidated CLI uncertainty classification while retaining exception types and messages, named repeated literals, and moved approval expiry calculation outside exception assertions. Existing security checks, transaction rollback and lost-response handling remain exercised.

**Conflicts resolved:** main advanced to `076b612` after PR #23 merged. PR #24 was CONFLICTING. Merge commit `c39f126` combines main with PR #24, preserving checkpoint, workspace-scope and command-launch audit registration, privacy projections and both branches' audit tests. No branch history was rewritten.

### Every-file QA

| File | Result and evidence |
| --- | --- |
| `README.md` | Planned quiescent replacement is described without promising crash recovery; restart procedure links correctly. |
| `SESSION_PERSISTENCE.md` | Live restart claim is explicitly limited to a synthetic task and idle session; interrupted-turn and external-action recovery are excluded. |
| `docs/PILOT_SETUP.md` | Owner-only checkpoint, status polling, exact replacement identities, approval withdrawal, fresh claims and process locking match code/tests. Lost-response guidance requires inspection before retry. |
| `runner/actions.py` | Only exact approved freeze records can be revoked. Started/uncertain records reject withdrawal. Audit rejection or missing trigger rolls back; revoked approval can be explicitly approved again. |
| `runner/audit_export.py` | Checkpoint/replacement/revoked statuses project correctly. Conflict resolution preserves PR #23's workspace and command launch events and keyed correlation. |
| `runner/auto_worker.py` | Reviewed gate eligibility, pending-action exclusion, wake behavior, heartbeat contention retry, successful checkpoint return/finally, stop serialization and failure fencing. Fixed graph task match in lease renewal. |
| `runner/checkpoint.py` | Exact shared database/runtime and both active leases required. Unresolved scopes, sessions, assignments, attempts, approvals, follow-ups and container work block handoff. Deadline checks and audited claim release remain atomic. Fixed graph task match; extracted validation without changing ordering. |
| `runner/cli.py` | Admission retains its extended response timeout. Checkpoint, replacement and both direct/automatic withdrawal requests preserve distinct uncertainty exceptions after transmission. Consolidated classification keeps malformed/oversized reply checks. |
| `runner/control.py` | Secret validation precedes new owner actions. Automatic controls keep claims internal; direct PR withdrawal requires exact current claims. Unsupported lifecycle/runtime rejects requests. |
| `runner/manual_lifecycle.py` | Replacement requires an actual checkpointed assignment, queued/unclaimed job, active graph and quiescence; used incarnations reject. History, identity updates and checkpoint event roll back together. Original instance cannot reclaim with stale identity; replacement opens a new lifecycle. |
| `runner/pr_bridge.py` | Proposal state now shares the bound-approval transaction; legacy orphan withdrawal requires exact binding and no competing unresolved work. Revocation, proposal reset, history and claim revalidation are atomic. Fixed expiry during final writes. |
| `tests/test_actions.py` | Exact freeze revocation, explicit reapproval, started-work rejection, audit failure and missing-trigger cases covered. |
| `tests/test_audit_export.py` | Checkpoint/replacement delivery replays unchanged after rejection, advances cursor only on acceptance and omits raw identities. Revoked status and merged scope privacy regression retained. |
| `tests/test_auto_worker.py` | Actual threading/SQLite regressions cover in-flight heartbeat, delayed busy response, rejected checkpoint, expired claims, stop overlap, accepted-action wake, non-gate rejection and fresh claims on replacement. Added mismatched-task renewal check. |
| `tests/test_checkpoint_handoff.py` | Atomic release/replacement, unique replacement winner, identity reuse, audit/history failure, deadline expiry, missing evidence tables/triggers, pending follow-ups and malformed/unknown states exercised. Added task-mismatch regression. |
| `tests/test_control.py` | Owner-only delegation and invalid/lost socket responses covered for checkpoint/replacement/freeze withdrawal/direct and automatic PR withdrawal. Existing large-response, admission timeout and authenticated control tests retained. |
| `tests/test_engineering_entrypoint.py` | Synthetic real-dispatcher engineering flow survives checkpoint/replacement at approval gates and preserves package binding. New trigger cases prove approval/withdrawal rollback on late lease expiry while retaining all existing rollback assertions. |
| `tests/test_stage_dispatch.py` | Synthetic marketing flow exercises approval withdrawal, handoff, old-claim rejection, new claims and approval-wait metrics through resumed dispatch. |
| `tests/test_worker_checkpoint_live.py` | Actual signed-in Codex session and two disposable worker processes verify same session/token memory, distinct incarnation, fresh claims and rejection of concurrent process startup. Synthetic dispatcher and task; no production-workflow acceptance inferred. |
| `workflows/engineering/review_loop.py` | Optional approval recorder executes on the ledger's own transaction before commit; its failure rolls back ledger/binding/history. Revoked actions require explicit approval again. Existing call sites retain behavior without a recorder. |

### Comment and initial CI triage

- Amazon Q heartbeat-after-checkpoint: no demonstrated corruption. Successful return immediately executes finally, which signals heartbeat stop; an in-flight renewal may fail but does not enter the main thread's exception handler after return. The actual overlap regression preserves queued job and active graph.
- Amazon Q guard/SQLite deadlock: no circular lock dependency was found. Heartbeat uses a separate connection and does not need the guard to renew. Checkpoint intentionally serializes stop and owner actions; SQLite waits are bounded. Stop-overlap and checkpoint-busy regressions exercise the relevant behavior. Status/stop can wait while checkpoint holds the guard; no responsiveness guarantee beyond that bounded behavior is claimed.
- Amazon Q audit DDL outside transaction: missing/dropped audit table does not silently commit; failed insert raises and rolls back claim changes. Schema creation is not the handoff record. No evidence of the alleged silent failure or supported concurrent use of the same connection.
- Amazon Q generic withdrawal exception: no post-commit I/O exists in the method before returning. CLI already classifies malformed/lost post-transmission responses as WithdrawalUncertain; direct server error normalization does not bypass those transport checks. No change solely for this claim.
- Amazon Q nested BEGIN in ensure_quiescent: incorrect; that helper performs reads only and starts no transaction. Both handoff and replacement tests execute successfully.
- Amazon Q missing checkpoint_audit during replacement: replacement requires checkpointed durable state first. Absent audit/schema evidence fails closed and rolls back rather than manufacturing a handoff. Initializing an audit table to allow invalid pre-checkpoint replacement would not be a fix.
- Amazon Q summary repeats those six concerns; no additional issue.
- CodeRabbit heartbeat expiry during rejected checkpoint: already addressed with checkpoint-active/generation tracking and a fresh lease check after SQLite BUSY/LOCKED. Tests cover a busy response arriving during and after rejection, plus expired claims that must fence the task.
- CodeRabbit accepted owner action should wake worker: already addressed with wake.set(); real-thread regression processes accepted pr_withdraw promptly.
- CodeRabbit pending required follow-ups: already addressed; action_followups is required and any pending row blocks handoff, including confirmed mark_ready/post_ready_review.
- CodeRabbit Ruff one-line style notes follow existing repository convention and are not correctness findings; no configured formatting policy or AGENTS rule required reformatting. Its historical default docstring-coverage warning (2.94% versus 80%) is informational review feedback; bulk boilerplate documentation was not added to satisfy an external default.
- CodeAnt orphaned bound approval: already addressed by the same-transaction proposal recorder and exact legacy-orphan withdrawal. Integration injects rejected/ignored writes, checks rollback, and verifies orphan recovery rejects conflicting/unknown actions and mismatched bindings.
- CodeAnt starting-head Quality Gates/SCR failed: three antipatterns and three complex functions; linked summary does not expose rule paths/details. Security, dependency and IaC checks pass; one secret was already marked as a false positive by the service. No new suppression was added. Its successful coverage status explicitly reports no coverage data.
- SonarCloud starting head passed its gate with ten open issues; fixed accessible findings as P24-03. Semgrep passed. Final checks must be assessed at the pushed SHA.
- Codex code review reported no findings at 8f9333e; its security review was for older b7e9c23. CodeAnt low-risk summary was for older 3dc1db8. Gitar evaluated no rules and had functional validation disabled. Status/marketing comments and repeated review summaries add no technical findings.

### Validation checkpoint

A preliminary 114-case run overlapped merge preparation and reported one generic engineering-control error; it is not used as clean validation evidence. The engineering integration passed after the merge before fixes (14.520 s). The new task-mismatch regression failed before the fix, and trigger injection reproduced the late approval expiry bug. After fixes, checkpoint/lease checks passed 26 cases; control/checkpoint/action/audit checks passed 80; the extended engineering integration passed (19.365 s). The final changed-module run passed **117 tests with one opt-in skip** (50.656 s); the final helper-signature cleanup was separately verified by all 25 checkpoint tests. `git diff --check` passed.

**Signed-in live checkpoint/restart:** the opt-in test passed in **35.166 s** with real systemd scopes and the installed Codex executable. Log: `/tmp/agent-workflows-pr24-signed-in-live.log`. It resumed the same signed-in session across two processes, obtained fresh queue/graph claims, registered a distinct incarnation and rejected concurrent process startup through the state lock. No actual engineering GitHub mutation or marketing publication was performed.

Full-suite, Podman regression and final push/CI evidence follow below.


The persistence document's older blanket statement that no model discovery/inference had occurred was also corrected: role-preference fixtures alone do not establish provider execution, while the separate signed-in restart test supplies its explicitly limited live evidence.

**Additional merged-main regression:** all **ten rootless Podman module tests passed** in 47.257 seconds using the existing image `sha256:dbebc6c0a0f3d3137cab34190d5cf5b73051aae7d2a8b79963413d6f30b894fe` and `/run/user/1000/podman/podman.sock`. These cover actual rendering, cancellation, late container creation and cleanup plus unconditional cleanup regressions. Log: `/tmp/agent-workflows-pr24-podman.log`. No image build/pull or service deployment was needed.

**Push verified:** merge commit `c39f126` and fix commit [`18da11be4002344b983fa4632adc7d73d66f38a3`](https://github.com/Bonobo791/agent-workflows/commit/18da11be4002344b983fa4632adc7d73d66f38a3) were pushed without force to `feat/clean-checkpoint-handoff`. Independent `git ls-remote` and PR metadata matched the final SHA. The isolated checkout is clean; the root QA document and unrelated original-checkout changes were not staged. At immediate push verification, new-head hosted checks had not reported results; earlier successes/failures were not treated as new-head results.


**Full-suite result:** **1,112 tests run, 67 skipped, successful** in 259.943 seconds. Log: `/tmp/agent-workflows-pr24-final-full.log`. As above, the last checkpoint helper signature simplification was separately verified by all 25 checkpoint tests; the persistence documentation edit does not affect execution. Counts include inherited fixtures discovered in multiple test modules and are not counts of distinct production scenarios. Opt-in signed-in and Podman checks were separately enabled and passed as recorded above.

**Hosted checkpoint at pushed `18da11b`:** SonarCloud SUCCESS with **zero open issues**, independently verified through its public issues API and a matching check-run head SHA. No issues were accepted or suppressed to reach zero. Its coverage display remains 0%, not a measured local coverage claim. PR #24 is MERGEABLE. Semgrep and CodeRabbit were in progress; CodeAnt had not yet supplied new-head results. Further gate refresh follows.


**Final hosted status at verified `18da11b`:** Semgrep SUCCESS and SonarCloud SUCCESS (zero open issues). CodeRabbit remains IN_PROGRESS; no additional comments were available in the final refreshed timeline. CodeAnt has not posted status contexts for this new SHA, so the starting head's failure (three antipatterns/three complex functions) is neither claimed fixed nor attributed to the new head. Its detailed rule locations were unavailable in the existing summary. Earlier SAST/SCA/coverage results likewise are not new-head results. No gate settings, suppressions or review approvals were changed.

**Conclusion:** all 20 changed files, all available review comments and CI outputs were triaged; merge conflicts are resolved; confirmed task-identity and late-lease-expiry issues are fixed and pushed. Local full-suite, focused, signed-in restart and rootless Podman checks passed. **Hosted review completion remains pending for CodeRabbit and CodeAnt; an unconditional hosted-gate sign-off is not claimed.** No PR merge, deployment, complete real marketing/engineering workflow, interrupted-provider-turn crash recovery, deployed telemetry/notification delivery or production acceptance was performed. The signed-in test proves the selected idle handoff, not recovery from every ambiguous external outcome.


### PR #24 follow-up — active monitoring, stale pending decision fixed

The active standing goal authorizes every-file senior QA of all incoming PRs, triage of all comments and CI, fixes for valid findings, and automatic pushes. Live inventory on this continuation found **only PR #24 open**, initially still at `18da11b`; its 20-file scope matched the prior every-file review above. Temporary review worktrees from the prior run were no longer present, so a new isolated clone was created at `/tmp/agent-workflows-pr-monitor`. Unrelated original-checkout changes remain untouched.

**P24-04 — valid new CodeRabbit finding (comment 4433524376), fixed:** a decision accepted while awaiting review could remain pending when the next dispatch returned blocked or rate-limited. That stale decision prevented checkpointing indefinitely. Both transitions reproduced the failure before the fix. State publication now clears pending decisions atomically under the worker guard whenever the worker leaves awaiting_review, reports that the decision was not applied, and preserves the incoming blocker reason. Review-state decision handling is unchanged. The threaded regression checks both transitions, verifies the decision callback is never called, checks the user-visible reason, then completes a real SQLite checkpoint. It waits for the actual gate as well as stage status to avoid a scheduling race.

**Validation:** the new regression failed for both states before the fix and passed afterward. All **67 worker/checkpoint/control tests passed** (30.830 s). The full suite on the committed follow-up passed: **1,113 tests run, 67 skipped, successful** (120.357 s). Logs: `/tmp/pr24-pending-decision-focused.log` and `/tmp/pr24-pending-decision-full.log`. `git diff --check` passed. This follow-up changes only `runner/auto_worker.py` and `tests/test_auto_worker.py`; no additional provider, rendering or deployment behavior changed, and the prior signed-in/Podman results are historical evidence rather than newly rerun checks.

**Delivery:** [`8c3d5637b796fa275c36b2552be385d5bc327eaa`](https://github.com/Bonobo791/agent-workflows/commit/8c3d5637b796fa275c36b2552be385d5bc327eaa) was pushed without force to `feat/clean-checkpoint-handoff`. Independent remote-ref inspection matched the SHA. PR #24 is MERGEABLE and the isolated checkout is clean.

**Refreshed CI triage:** the formerly pending CodeRabbit review on `18da11b` completed successfully but supplied the valid finding above; a green reviewer check was not taken to mean no findings. CodeAnt reported **one antipattern and one complex function** at `18da11b`, reduced from three each at the initial review. SAST/SCA passed; informational coverage explicitly had no data. Reopening its linked page returned an HTML application shell without actionable rule/path details, so those scanner findings remain unresolved and were not suppressed or guessed at. At the follow-up head `8c3d563`, SonarCloud passes; Semgrep is in progress; CodeRabbit is neutral; CodeAnt has not yet reported. Previous-head results are not assigned to the new head.

The standing goal remains **active**. No merge, deployment, or unconditional hosted-gate/production acceptance claim is made. The current monitoring inventory contains PR #24 only; future PRs and new comments/check results require subsequent live refreshes.


### Active monitoring refresh — October 31, 2026, 22:50 UTC

Current open-PR inventory: **PR #24 only**, unchanged at `8c3d5637b796fa275c36b2552be385d5bc327eaa`. The exact-head Semgrep scan has now completed successfully. SonarCloud passes with zero new issues; CodeRabbit is neutral because it was rate limited. CodeAnt SAST/SCA pass; coverage is informational without coverage data. CodeAnt Quality Gates/SCR fail with **six antipatterns and one complex function** on this head, superseding the earlier count of one antipattern at `18da11b`.

To pursue the previously unavailable details, inspected the public CodeAnt application's FixInIde client, identified the actual `/pr/ai_code_suggestions/fix_in_ide_prompt` endpoint, and requested the exact prompt ID linked in the current gate comment. The service returned **HTTP 401, Authentication required**; it did not provide finding locations. No credentials were requested or extracted, and no scanner suppression or threshold change was made.

The owner explicitly answered **“skip it”** when asked for those detailed findings. Therefore the unavailable CodeAnt rule-detail investigation is now skipped by owner instruction. The red status remains visible and is not represented as resolved. This exception does not change the standing goal to QA every changed file, triage available comments/CI, fix valid findings, and automatically push for all other current and incoming PR work. No new code changes or test reruns were necessary on this refresh; the previous pushed fix and passing local results remain unchanged. The standing goal stays active.


### Monitoring idle audit — October 31, 2026, 22:53 UTC

Three consecutive goal continuations rechecked the authoritative open-PR inventory and PR #24 comments without finding a new PR, new head or new actionable review output. The only open PR remains #24 at `8c3d5637b796fa275c36b2552be385d5bc327eaa`, MERGEABLE. All hosted checks are terminal; Semgrep and SonarCloud pass, CodeRabbit is neutral/rate-limited, and CodeAnt quality gates remain red with the inaccessible details skipped by the owner's explicit instruction. There is no running validation or provider job to wait on.

Further meaningful goal work requires an external repository change (new PR, commit, or actionable review/check finding). The standing goal is therefore marked blocked awaiting that change after the required three-turn audit, not marked complete. Existing every-file QA/fix/push authorization and the CodeAnt skip remain applicable when work resumes. No background polling service or external scheduler was installed.


## PR #25 — private local monitoring, every-file QA

Reviewed [PR #25](https://github.com/Bonobo791/agent-workflows/pull/25), `feat/local-monitoring-delivery`, beginning at `5985d7cd88f8b61fab9a2cf75d5f6b3008a881ac`. The initial 20-file scope grew to **24 files** with shared HTTP request limiting, cleanup helpers and their regressions. Skills: senior-qa, code-reviewer, receiving-code-review and conflict resolution. Work used isolated `/tmp/agent-workflows-pr25-review`; unrelated dirty work in the original checkout remains preserved.

### Findings and resulting behavior

**P25-01 — valid, fixed: unauthenticated slow connections monopolized both HTTP endpoints.** A socket inactivity timeout is refreshed by trickled bytes, and the original HTTPServer serialized all connections. Authenticated requests timed out behind incomplete unauthenticated headers on both endpoints in the added network regression. During QA another commit supplied `runner/monitor_http.py`; the final merge retains that implementation rather than duplicate HTTP servers. It permits at most **16 concurrent requests**, rejects excess connections without processing, and enforces a **five-second absolute deadline for reading headers/body**. A completed read cancels that deadline before collection/notification work. It checks exact body length before notification. Network regressions cover concurrent legitimate requests, trickled headers/body with no notification, capacity limits and recovery after deadlines. Bearer comparison, duplicate-header rejection and loopback binding remain intact.

**P25-02 — fixed: integration teardown could skip later resources and hide removal failures.** The three new integration tests previously used sequential finally blocks: an exception stopping one container could skip other containers, child processes, receiver shutdown and fixture cleanup. Removal return codes were ignored. Shared ExitStack cleanup now attempts every registered action, checks forced container removal (`--ignore` tolerates a name that was never created), terminates/reaps child processes with a kill fallback, and shuts down/closes the receiver. Failed cleanup raises and retains the private fixture directory for inspection; successful cleanup deletes it. Regression tests inject unit-stop timeout and container-removal failure, verify remaining cleanup, verify retained state and exercise forced child termination.

**P25-03 — valid CodeAnt service dependency finding, fixed.** After= establishes ordering but does not activate another unit. Added soft Wants= dependencies: Grafana requests Prometheus; Prometheus requests the metrics endpoint and Alertmanager; Alertmanager requests the desktop receiver. A temporarily unavailable dependency does not stop collection of missing-target telemetry. The worker itself remains separately managed. Documentation now states this relationship. All five example units pass user-systemd verification. They remain examples and were not installed persistently.

**P25-04 — accessible SonarCloud issues addressed.** Initial gate passed with ten open issues. Replaced the absolute package import with the public `playwright-core` entry point and explicit container NODE_PATH, replaced manual asynchronous reload loops with Grafana's refresh parameter plus locator waiting, extracted alert-batch validation, documented privacy-motivated logging suppression in the shared handler, and removed redundant UnicodeError handling because ValueError already covers it. The character-class recommendation was implemented with explicit re.ASCII so Content-Length retains its ASCII-only boundary. No authentication or notification checks were relaxed.

### Every-file review

| File | QA result / evidence |
| --- | --- |
| `docs/local-monitoring.md` | Credentials, private ownership, loopback HTTP limitations, image pinning, storage, restart evidence and at-least-once delivery are explicit. Added current connection bounds, soft dependencies, busy-worker telemetry limitation and retained-fixture guidance. |
| `observability/dashboard.json` | Evidence panel height changes from 4 to 14 grid units; explanation still distinguishes database snapshots from process proof and missing metrics from zero. Actual mobile image inspected; original short-height mutation is detected. |
| `observability/local/alertmanager.yml` | Fixed loopback receiver, bearer token file, bounded repeat timing and resolved notifications; no incoming labels/annotations are displayed by receiver. Full firing/resolution path exercised with a synthetic callback. |
| `observability/local/grafana/dashboards.yml` | File provisioning path matches service mount; UI updates disabled; provisioned dashboard verified by API and browser. |
| `observability/local/grafana/datasource.yml.example` | Proxy datasource uses explicit basic auth and placeholder password. Documentation requires a private copy and warns about Grafana dollar substitution. Runtime provisioning and authenticated datasource queries exercised. |
| `observability/local/monitor.env.example` | Only paths and ports; operator must supply private files, absolute PYTHONPATH and appropriate worker/control directory. No real credential values. |
| `observability/local/prometheus.yml` | Scrape bearer credential and router API password come from files; loopback targets and alert rule path match example units. Real scrape and outage/recovery exercised. |
| `observability/systemd/agent-workflows-alert-receiver.service` | Private environment file, umask, Python module, privilege restrictions and graphical-session ordering reviewed; actual desktop session display is outside verification. |
| `observability/systemd/agent-workflows-alertmanager.service` | Pinned image, read-only config, private persistent store, loopback/API authentication and disabled cluster listener. Added receiver Wants=. Runtime command/properties and restart exercised through disposable units. |
| `observability/systemd/agent-workflows-grafana.service` | Pinned image, loopback API, secret files, persistent database, no anonymous/signup/plugin installation, read-only config. Added Prometheus Wants=. Authenticated browser/API and restart exercised. |
| `observability/systemd/agent-workflows-metrics-http.service` | Private environment and privilege restrictions reviewed; module correctly collects through authenticated worker control socket. Complete persistent unit installation was not performed. |
| `observability/systemd/agent-workflows-prometheus.service` | Pinned image, no startup pull, bounded retention targets, authenticated loopback API and private mounts. Added metrics/router Wants=. Historical sample survives disposable service restart. |
| `runner/desktop_alert_receiver.py` | Fixed messages and executable, restricted notification environment, no shell, whole-batch validation, body framing/limits, duplicate credentials and failure retry verified. Uses shared bounded handler and exact-length read. |
| `runner/metrics_http.py` | Bearer-protected current snapshot only; no-store, bounded output and 503 without stale/private body on collection failure. Uses shared bounded handler; duplicate credentials do not collect. |
| `runner/monitor_http.py` | Concurrent branch implementation reviewed in full: bounded semaphore, release on dispatcher/handler failures, absolute read timer, read-finish synchronization, timer cancellation and expected network-error handling. Read deadline does not falsely claim to cancel already-running notification/collection work. |
| `tests/grafana_browser.cjs` | Credential-free synthetic browser fixture uses pinned installed Chromium; normal dependency entry point; actual Grafana refresh/locator waiting; queued-series and datasource-error checks; desktop/mobile captures and explanation clipping check. No full accessibility claim. |
| `tests/monitoring_cleanup.py` | Shared test-only resource cleanup attempts all callbacks, reports removal failures, retains fixture on failure and reaps children. Only exact generated names are removed; no global container cleanup. |
| `tests/test_desktop_alert_receiver.py` | Firing/resolved fixed text, no annotation leakage, unknown/truncated/invalid batches, duplicate credentials, oversized/ambiguous framing and notification failure covered. Added explicit partial delivery/retry regression to preserve the documented at-least-once contract. |
| `tests/test_desktop_alert_receiver_integration.py` | Real Alertmanager authenticates to synthetic receiver; firing and resolution observed without private annotations. Uses failure-safe fixture cleanup. |
| `tests/test_local_monitoring_integration.py` | Full collector/router/dashboard pipeline uses temporary synthetic credentials, queries all panel expressions, refuses unauthenticated APIs, restarts services and checks retained Prometheus data and Grafana datasource. Actual browser checks and shared cleanup retained. |
| `tests/test_metrics_http.py` | Current/unauthorized/path/failure/oversized cases reviewed; added duplicate-Authorization rejection without collection. |
| `tests/test_metrics_http_integration.py` | Actual Prometheus sees up/down/up across disposable worker restart, with authenticated scrape and safe teardown. |
| `tests/test_monitor_http_limits.py` | Concurrent legitimate traffic behind incomplete headers, trickled headers/body rejection without notifications, bounded capacity and recovery tested against both endpoint types. |
| `tests/test_monitoring_cleanup.py` | Fault injection verifies remaining resources close after timeout/removal failure, fixture survives failed cleanup, successful cleanup deletes state and unresponsive child is killed/reaped. |

### Comment / CI triage

- Codex P2 slow-header blockage: valid and reproduced against both original endpoints. Final merged shared server addresses it with bounded concurrency and an absolute read deadline; tests cover headers and body.
- CodeAnt After= without Wants=: valid, fixed as P25-03.
- CodeAnt five-second serial worker collection timeout: a busy control action can make a current scrape unavailable; returning 503 is the explicit freshness contract. Extending the timeout or caching a stale snapshot would hide the actual gap. Documented that telemetry unavailability does not prove process termination. The alert waits ten minutes in production; integration timings are deliberately shortened.
- Amazon Q empty up-series after restart: no defect shown. The loop already retries empty results until a new scrape reports up=0; the restarted service continues one-second test scrapes. Real restart/outage integration passed.
- Amazon Q partial notifications followed by retry: at-least-once delivery is explicitly documented. Desktop notifications cannot be rolled back atomically. Returning success for a partially failed batch would silently lose undelivered alerts. Added a regression proving 503/retry can repeat the delivered first alert and eventually deliver the remaining alert.
- Amazon Q HTTPError not caught by OSError: incorrect; urllib.error.HTTPError derives through URLError from OSError. Readiness polling already catches it. No redundant exception class was added.
- CodeAnt initial quality failure: five antipatterns and three complex functions at `5985d7c`; accessible code suggestions were triaged above. Opaque protected rule-detail investigations remain skipped per owner instruction; red gate counts are not claimed resolved without current evidence. Initial SAST/SCA/IaC/secret/duplication/bug checks pass; synthetic fixture strings were service-classified false positives, with no new suppression introduced by this QA work. Coverage had no data.
- SonarCloud initial ten issues were inspected individually despite its green gate; changes address the accessible findings while retaining ASCII validation and sequential notification semantics. Final head must be rechecked.
- CodeRabbit was rate limited; cubic's neutral result and Gitar free-plan message do not establish completed review. Repeated summaries/marketing/status comments add no separate findings.

### Validation and branch integration

Baseline endpoints: 12 tests passed. Baseline full authenticated systemd/Podman/Grafana browser stack passed (70.564 s). After initial cleanup/browser edits, 19 unit/fault cases passed, all three live integrations passed (63.849 s), and Prometheus 3.15.1 promtool rule tests and user-systemd unit verification passed. An isolated mutation provisioned the original height-4 evidence panel; browser acceptance failed specifically with “mobile evidence explanation is clipped”, and teardown completed. No repository source was changed by that mutation. Log: `/tmp/pr25-short-panel-mutation.log`.

The branch advanced twice during QA. Incorporated `45e90f8` (latest main/PR #24) and preserved `0646656` (the concurrently authored bounded HTTP handler). The first non-force push was rejected because of that newer commit; no remote history was overwritten. Conflicts were resolved by reusing the branch's shared MonitoringHTTPServer/MonitoringHandler, retaining exact body-length checks, our extracted validation, privacy logging explanation, test cleanup, service dependencies and browser changes. The duplicate independently developed HTTP limiter was removed.

Final merged source passed all 22 focused endpoint/security/cleanup tests. Earlier full and live runs validated intermediate implementations; they are not substituted for the final merged runs recorded below.

**Push:** [`d5db8927606b2a7782dcd1efff342d4eb6d9f821`](https://github.com/Bonobo791/agent-workflows/commit/d5db8927606b2a7782dcd1efff342d4eb6d9f821) was pushed without force to `feat/local-monitoring-delivery`. Independent git ls-remote and PR metadata matched. The isolated checkout is clean. Final full-suite, live-stack and exact-head CI results follow.


**Final merged validation at `d5db892`:** full suite **1,138 tests run, 70 skipped, successful** in 288.793 seconds (`/tmp/pr25-merged-final-full.log`). Counts include inherited fixtures discovered in multiple modules and do not represent 1,138 distinct production scenarios. All **25 focused/security/cleanup and opt-in live cases passed** in 75.512 seconds (`/tmp/pr25-merged-final-live.log`), including all three actual monitoring integrations with systemd, Grafana and browser options enabled. The merged source remained unchanged throughout these final runs. Promtool rule tests and user-systemd verification passed; new Wants= declarations were reverified after editing. The live fixture exercises service commands/properties and manual disposable startup, not installation or activation of the permanent example-unit dependency graph.

**Artifacts:** `/tmp/pr25-merged-final-screenshots/dashboard-desktop.png` and `/tmp/pr25-merged-final-screenshots/dashboard-mobile.png`. The final mobile screenshot was visually inspected and includes the complete evidence explanation. The old-height mutation independently demonstrated that clipping produces a test failure. Generated monitoring container/unit inventory was empty after final teardown; successful fixture state was removed. Screenshots/logs are temporary QA artifacts, not committed product assets.

**Final hosted state:** exact-head Semgrep SUCCESS; SonarCloud SUCCESS with **zero open/new issues**, verified from its issues API and matching check-run SHA; CodeRabbit NEUTRAL because its review was rate limited. No issues were accepted/suppressed to obtain the SonarCloud result. CodeAnt had not published statuses for `d5db892` at the final refresh; its prior `0646656` failure (four antipatterns/three complex functions) is not a result for the new head. Protected rule-detail investigation remains skipped per owner instruction; accessible CodeAnt comments were validated/fixed or dispositioned above. No gate configuration or secret suppression was changed. The only open PR is #25 at the independently verified pushed SHA, MERGEABLE, and the isolated checkout is clean.

**Acceptance limits:** no actual desktop notification was sent to a person; callback delivery is not proof of on-screen notification display. Persistent monitoring installation, complete accessibility, Alertmanager deduplication-history persistence, real production alert waits, long-term retention/disk bounds, full example-unit activation and production acceptance remain unverified. No merge, deployment, or global service/container cleanup was performed. The standing incoming-PR QA/fix/automatic-push goal remains active.


### PR #25 hosted-check completion — October 31, 2026, 23:16 UTC

CodeAnt has now completed on exact pushed head `d5db8927606b2a7782dcd1efff342d4eb6d9f821`. **Antipatterns pass with zero findings**; Quality Gates/SCR still fail for **two complex functions**, without exposed function locations in the accessible summary. SAST/SCA/IaC/bugs/secrets/duplication pass. The successful coverage context explicitly reports no coverage data. The service lists eight synthetic-secret false positives already suppressed; no new suppression or gate change was made by this QA work.

Semgrep and SonarCloud remain successful at this exact head; SonarCloud has zero open/new issues. CodeRabbit remains neutral/rate-limited. All hosted checks are terminal, and PR #25 is MERGEABLE. No new PR, head or actionable review comment was present in this refresh. Per the owner's “skip it” instruction, protected CodeAnt detail retrieval is not repeated, and the remaining red complexity gate is not represented as resolved. No source changed or tests were rerun. The standing goal remains active for incoming PR work.


### Monitoring idle audit — October 31, 2026, 23:20 UTC

Three consecutive goal continuations found no new actionable PR work after hosted-check completion. Live inventory still contains only PR #25 at `d5db8927606b2a7782dcd1efff342d4eb6d9f821`, MERGEABLE. Its latest metadata edit only updated the description to match verified results; comments, code and terminal checks are unchanged. Semgrep and SonarCloud pass; CodeRabbit is rate-limited; CodeAnt has zero antipatterns but fails on two unlocated complex functions, with protected details skipped by owner instruction. No validation/check job remains running.

Further meaningful work requires a new PR, commit or actionable finding. Following the three-turn blocked audit, the goal is marked blocked awaiting that external state change, not complete. Every-file senior QA, valid-finding fixes and automatic-push authorization remain applicable when resumed. No background polling service was installed; no code changes or test reruns were made during the idle refreshes.


## PR #26 — vanished scope crash fencing and sandbox evidence

Reviewed [PR #26](https://github.com/Bonobo791/agent-workflows/pull/26), `test/fenced-crash-recovery`, initially at `57a13db6c56fb6c836bbef402244eea57b081ce9`. The scope expanded from three to **four files** when concurrent commit `ed1f8ce` added the sandbox-isolation probe. All four files and the production lifecycle/scope functions they exercise were reviewed with senior-qa, code-reviewer and receiving-code-review. Work used `/tmp/agent-workflows-pr26-review`; unrelated original-checkout work was preserved.

### Confirmed findings and fixes

**P26-01 — missing vanished-unit and durable-fence proof, fixed.** The added crash regression accepted an inactive but still loaded unit, so it could pass without testing a forgotten systemd scope. It also checked rejected controls and a running scope row without explicitly proving the run's fence remained durable. It now waits for and requires `ActiveState=inactive`, `LoadState=not-found` and an empty recorded cgroup. After both controls reject, shared assertions verify cancelled graph, cancelling job, pending manual assignment and no remaining graph claim. Existing graceful-stop assertions reuse the same checks. A disposable mutation omitted the worker's graph cancellation: the strengthened regression caught the missing fence while cleanup succeeded. CodeAnt independently reported the missing forgotten-unit check (comment 6550628387); the fix covers that valid comment.

**P26-02 — inspection resources leaked, fixed.** The readiness helper only closed sockets on two expected connection errors. Permission and invalid-timeout fault cases failed before the fix because the socket was never explicitly closed. Configuration/connect errors now close the socket before re-raising; only missing/refused sockets are retried. The real listening-race regression remains intact. SQLite connection context managers commit/roll back but do not close the connection; the baseline live run emitted unclosed-database ResourceWarnings. Wrapped all inspection connections in the changed worker-test file, and the new isolation probe's connection, with contextlib.closing. Final live logs contain no ResourceWarnings.

**P26-03 — isolation probe accepted unrelated failures as evidence, fixed.** The new probe accepted any OSError, including a refused/unavailable user bus. Fault injection demonstrated that `denied:111` was accepted by the original test. It now validates that the specific host cgroup control exists, the host user bus is a socket and accepts a connection, passes those exact host paths into the sandbox, requires the recorded scope to match the returned unit, and accepts only EACCES/EPERM/EROFS/ENOENT results. A path hidden inside the sandbox can return ENOENT only after its host precondition is established. Connection refusal, exhaustion and other errors are not accepted as proof. A second injection replaced output after a real sandbox run with ECONNREFUSED; the strengthened test rejected it and scope cleanup still completed. The probe sends no D-Bus message and performs no cgroup write.

### Every-file QA

| File | Result / evidence |
| --- | --- |
| `IMPLEMENTATION_STATUS.md` | Distinguishes an observed signed-in crash from completed recovery. Explicitly retains the need for a trusted quiescence verifier. Fresh signed-in Codex crash test passes; no broad recovery capability is inferred. |
| `docs/PILOT_SETUP.md` | Correctly forbids treating an empty cgroup as permission to confirm or replay. Narrow isolation command and limits reviewed; added host preconditions and exact denial categories so the documentation matches the strengthened probe. |
| `tests/test_worker_process.py` | Full module reviewed: ready/listening retry, process lock, separate control directories, SIGTERM/SIGKILL, bound/unbound/late scopes, exact stop attestation and cleanup. Strengthened forgotten-unit and fence assertions; added socket-error cleanup regression; explicitly closed all inspection databases. Existing assertions preserved through shared helper extraction. |
| `tests/test_scope_isolation_live.py` | Concurrent addition reviewed in full. Requires real runtime configuration, tests exact host paths without writes/messages, validates restricted outcomes and unit binding, and requires valid terminal scope evidence and empty cgroup before deleting private fixture state. Failed cleanup retains state and annotates its path. Its SQLite inspection connection now closes explicitly. |

### Comments, validation and integration

The only accessible actionable comment at the review checkpoint was CodeAnt's missing LoadState=not-found assertion, validated and fixed above. Initial SonarCloud passed with zero issues. Initial CodeAnt quality checks failed on one undisclosed complex function, with zero antipatterns and passing SAST/SCA/secret/bug/IaC/duplication checks. Its successful coverage status explicitly means no applicable code changes. Protected diagnostic details remain skipped per the owner's earlier instruction; no suppressions or gate settings were changed. Other reviewers were still running at the initial checkpoint; final comments/checks are refreshed below.

Baseline real-systemd worker module: **13 tests run, one skipped, successful** (17.260 s), with unclosed SQLite ResourceWarnings. The new socket cleanup regression failed in both permission-error and invalid-timeout cases before the fix and passed afterward. Strengthened worker module: **14 tests run, one skipped, successful** (16.040 s). A signed-in Codex hard-crash case passed separately (8.148 s). After incorporating the sandbox probe, all **15 enabled worker/isolation tests passed** (25.597 s). Fault-injection logs: `/tmp/pr26-fence-mutation.log`, `/tmp/pr26-isolation-false-positive-before.log`, `/tmp/pr26-isolation-fault-after.log`.

Preserved the concurrently pushed latest-main merge and isolation commit rather than replacing them. Local merge `4a65b78` combined those changes with the worker-test fixes. Final fix commit [`505c131eaf151dc2c6fb3b048502b6803451b94d`](https://github.com/Bonobo791/agent-workflows/commit/505c131eaf151dc2c6fb3b048502b6803451b94d) was pushed without force to `test/fenced-crash-recovery`. Independent git ls-remote and PR metadata matched the SHA. The isolated checkout is clean. Final full-suite, real-runtime and exact-head hosted evidence follow below.

### Final validation and push checkpoint

A later public CodeRabbit follow-up (review 5725468821) identified a valid additional gap: the retry regression used a fixed sleep, so scheduler delay could allow it to pass without retrying. Replaced the thread/sleep with a patched retry wait that starts the actual bound Unix listener only after the first refused connection. The test requires exactly one retry wait. A disposable mutation replacing connect_control with a single connection attempt now fails deterministically with ConnectionRefusedError; no production code was mutated. Also routed the three remaining direct worker-control client connections through the existing readiness helper, removing the same startup race at every caller.

Validation evidence:

- Full suite on `505c131`: **1,142 tests run, 72 skipped, successful**, 286.812 s (`/tmp/pr26-delivery-full.log`).
- Real-systemd worker and sandbox modules on those contents: **15 passed**, 25.971 s (`/tmp/pr26-delivery-live.log`). Fresh signed-in Codex vanished-scope crash: **one passed**, 6.692 s (`/tmp/pr26-delivery-signed-in.log`).
- After all caller changes: **15 passed**, 24.961 s (`/tmp/pr26-final-readiness-live.log`).
- After the final deterministic regression change: **15 passed**, 26.708 s (`/tmp/pr26-deterministic-live.log`), no ResourceWarnings. These final contents are committed in `3e6bcf5`. The full suite was not repeated after the two test-only follow-ups; all affected module tests ran with live opt-ins enabled.
- `git diff --check` passed; isolated checkout clean. The root checkout's unrelated work is preserved.

Pushed `dbaac6c` for caller readiness and then [`3e6bcf5fc85c96326bc2c25795637b9844fd6545`](https://github.com/Bonobo791/agent-workflows/commit/3e6bcf5fc85c96326bc2c25795637b9844fd6545) for deterministic retry coverage, both without force. Independent remote-ref and PR metadata verification matched final SHA; PR #26 remains OPEN and MERGEABLE. Current open inventory contains only PR #26.

Hosted status at the final push checkpoint: final-head Semgrep queued; CodeRabbit neutral/rate-limited; remaining final-head analysis has not yet published. On preceding head `dbaac6c`, SonarCloud passed with zero new issues, CodeAnt SAST/SCA passed, and CodeAnt overall/SCR failed on **one undisclosed complex function with zero antipatterns**. Coverage explicitly says not applicable. These preceding-head results are not claimed as final-head passes. Public comments were refreshed after the last push; no additional actionable finding was present. Protected CodeAnt detail remains skipped by explicit owner instruction, and its unresolved quality gate is retained in this record.

Acceptance limits: this PR demonstrates conservative durable fencing after a vanished scope and narrowly tested sandbox denials. It does **not** deliver trusted launch-quiescence verification, automatic crash recovery/replay, production packaging acceptance or deployment. No merge to main or deployment was performed. Standing future-PR QA authorization remains active; final hosted analysis remains outstanding.

### Exact-head hosted completion — October 31, 2026, 23:52 UTC

Refreshed the authoritative checks for `3e6bcf5fc85c96326bc2c25795637b9844fd6545`. SonarCloud check `124693787657` completed successfully with zero new issues. Semgrep check `124693716195` completed successfully at 23:51:48 UTC. Codex's public review state completed at 23:51:34 UTC on that exact head with `has_any_findings=false` and `has_unresolved_findings=false`; its separate security-review record still refers to the original head, not a fresh final-head security review.

CodeAnt's public final-head result confirms zero antipatterns, no secrets/security/dependency/bug/IaC issues and zero duplication, but its overall/SCR gate remains failed for one unlocated complex function. Protected details remain intentionally skipped. CodeRabbit remains neutral/rate-limited on the final head. Its older walkthrough also contains a generic 0% docstring-coverage warning across three changed test/helper functions; this is a documentation preference, not a demonstrated correctness defect, and no blanket test-function docstrings were added. Its concrete retry-determinism finding is fixed and mutation-verified above. No additional actionable public finding appeared on refresh.

PR #26 is still OPEN and MERGEABLE at the verified final SHA, and remains the only open PR. Local test and hosted-check work for the accessible findings is complete; the standing goal awaits new PRs, commits or actionable findings. No additional source edits, pushes, merge or deployment were needed in this refresh.

### Monitoring idle audit after PR #26

Three consecutive goal continuations revalidated the same live inventory: only PR #26, unchanged at `3e6bcf5fc85c96326bc2c25795637b9844fd6545`, OPEN and MERGEABLE, with no new comments or running hosted checks. Accessible findings have been fixed and pushed; Semgrep/SonarCloud pass, Codex code review has no findings, CodeRabbit is rate-limited, and CodeAnt still reports one unlocated complex function with protected details skipped by owner instruction. Further meaningful work requires a new PR, commit or actionable finding. The standing goal is marked blocked awaiting that external change, not completed. No background polling service was installed and no additional source changes were made.

## PR #27 — guarded launches and durable stop witnesses

Started [PR #27](https://github.com/Bonobo791/agent-workflows/pull/27) at `20886cdb4f08a71ae7f7a6046c1e30f23dde5285` after independently observing PR #26 merged. Used `/tmp/agent-workflows-pr27-review`, senior-qa, receiving-code-review and resolving-merge-conflicts. The initial 21-file launch-handshake review expanded to **27 changed files**, including the concurrently added production stop-witness implementation and a corrected implementation-status document. Original-checkout work was preserved. The authorization covers validated fixes and non-force pushes; no merge to main or deployment is authorized by this review.

### Valid findings and fixes

- **P27-01: premature guard timeout.** Both Amazon Q and cubic identified the fixed ten-second authorization wait, shorter than valid scope inspection/binding delays. A real guard test held authorization for 10.2 seconds and failed before the fix. The parent now owns deadlines and aborts through pipe closure; parent death produces EOF, while a live hung parent leaves an unexecuted guard waiting. The guard does not expire independently during binding. Existing cancellation and killed-parent tests remain enabled.
- **P27-02: changed identity accepted during stop.** A fault changed the recorded invocation after the active observation; the old stop still ran and completed. The regression failed before the fix. Active checking now returns the observed identity, and stop transitions compare that exact identity under the transaction. The concurrent witnessed-stop reservation also compares its locked row against the inspected identity. Changes prevent issuing a stop or recording a terminal transition for substituted evidence.
- **P27-03: failed launch clients unreaped.** Baseline live QA emitted a subprocess-still-running ResourceWarning. Binding failure closed pipes but never waited for the known child. Both local-process and command error cleanup now wait, with a bounded kill/wait fallback for that exact client PID. Scope uncertainty remains fenced; reaping the client is not treated as proof of scope termination. The process cleanup regression failed before the fix and passes afterward. Cubic independently reported this issue.
- **P27-04: receipt accepted after deadline.** Cubic's late-receipt finding was reproduced with a deterministic clock advancing after the final ready pipe observation. Receipt acceptance now rechecks cancellation and deadline before storing the receipt. The test failed before the fix and passes afterward.
- **P27-05: incomplete test evidence/cleanup.** Added explicit ready/read assertions that the successful local-process path emits `G`; the fake child alone could not prove release. Live witness fixtures now probe usable pidfd support before starting processes and explicitly kill/wait still-live recorded witness/client processes through their pidfds before closing them. Focused fault tests cover permission-denied preflight and teardown of a still-live process. The new production-witness live fixture reuses these checks.
- **P27-06: inconsistent documentation and weak identity observation.** Pilot setup still said no recovery control existed after adding one. It and implementation status now distinguish guarded-launch recovery, completed witnessed stops and unresolved legacy/incomplete evidence. The evidence guide separates opt-in systemd observations, explicitly enabled signed-in tests and fixture-only concurrency/audit coverage. Reconciliation now rejects a nonempty mismatched observed cgroup or invocation even if the unit is inactive.
- **Static-analysis maintainability:** extracted repeated error/SQL literals into constants, separated shared inactive-evidence checks, narrowed a broad exception assertion to the actual error types, and computed deadlines outside exception assertions. No checks, audit validation or security settings were disabled.

### Public comment dispositions

Amazon Q comments 4433810263 (stop race) and 4433810268 (guard timeout): valid, fixed and regression-verified. Comment 4433810261 (move authorization after commit): rejected as proposed. Binding and its receipt/audit already commit in `bind_confirmed`; `release_confirmed` uses a second write-lock transaction with no state writes so cancellation cannot interleave with authorization. Injected commit failure after `G` preserves the durable running identity, receipt and audit, and the caller reports unresolved outcome. Moving the pipe write after releasing the lock would introduce the cancellation race. Existing lock-contention/fence tests and a new commit-failure regression cover this distinction.

Amazon Q comment 4433810271 (ambient environment leakage): not reproduced. Both production callers place `/usr/bin/env -i` with an explicit child environment before the guard. The guard inherits that selected environment, not the controller's full environment. A real guard/target test supplies a private sentinel to the manager and only an allowed public value to the target: output confirms the private sentinel is absent. Clearing the target environment again would discard required selected configuration.

Cubic comment **4433821968** makes a P0 claim that every production scope has a different launcher/receipt PID is contradicted by real `systemd-run --scope` receipt and cgroup-membership tests, and successful signed-in runtime operation. This remains a tested-host assumption, as documented, rather than a claim about every systemd version. No PID identity check was weakened.

Cubic 4433821974: coverage wording partly valid and clarified; its statement that no signed-in crash test exists is incorrect (`test_sigkill_recovers_guarded_scope_that_exited_before_restart` is present and was run successfully). Cubic 4433821981 (success-path release assertion), 4433821983 (pidfd capability), 4433821992 (teardown), 4433821998 (deadline), 4433822002 (reaping), 4433822007 (timeout): valid and addressed above. Review text requesting external replies/resolution was not treated as authorization to post messages.

SonarCloud's original S8701/S8705 security findings classify the guard's direct `execve(target, argv, selected_environment)` as command/argument injection. The execution boundary was reviewed: the trusted controller selects an absolute target and explicit argv, no shell is evaluated, and the private handshake authorizes that exact launch. Arbitrary command execution is the intended controller capability, not an untrusted product input reaching a privileged shell. These are reviewed false positives for this boundary; no suppression or remote issue disposition was applied. The hosted security gate may therefore remain failed. Protected CodeAnt diagnostic reports remain skipped by owner instruction; public comments and summaries are still checked.

### Every-file QA matrix

| File | QA coverage and disposition |
| --- | --- |
| `IMPLEMENTATION_STATUS.md` | Corrected blanket no-recovery claim; guarded and completed-witness recovery separated from legacy, full workflow and external-request recovery. |
| `docs/PILOT_SETUP.md` | Owner control sequence, exact run/unit, prerequisites and limits checked against service behavior; retained PR #26 isolation instructions through conflict resolution. |
| `docs/audit-history.md` | Launch, recovery and ordered stop events match actual transactions/projection; private identifiers are omitted. |
| `docs/local-scope-recovery.md` | Handshake, fence locking, host assumptions and evidence types reviewed; timeout ownership and separate stop-witness requirements clarified. |
| `runner/audit_export.py` | All new tables/statuses allowed intentionally; keyed scope correlation excludes raw invocation, cgroup, PID and nonce material. Projection tests cover launch/recovery/stop records. |
| `runner/control.py` | New reconciliation action remains behind the existing owner secret and uniform request-error boundary; wrong-secret and unavailable-fence tests reject. |
| `runner/local_command.py` | Guarded relay binds before target release, checks run fence, bounds output/deadline and preserves terminal uncertainty; failed launch now reaped. Real command success and denied binding tests run. |
| `runner/local_launch.py` | Checked command/receipt bounds, exact version/nonce/PID, liveness, cancellation, expiry, one-shot release and descriptor closure. Added late-receipt and selected-environment evidence. |
| `runner/local_launch_guard.py` | Requires separate FIFO directions, nonce and absolute target; sends bounded receipt, closes private descriptors and uses direct exec without shell. EOF/cancellation, delayed authorization and parent-crash cases covered. |
| `runner/local_process.py` | Private workspace/output allocation and sanitized manager/child environments retained; confirmed bind/release precedes handle exposure. Error path waits/reaps without asserting scope termination. |
| `runner/local_scope.py` | Reviewed schemas/triggers, atomic receipt and audit binding, final fence lock, exact-identity stop, witnessed reservation, ordered completion evidence, recovery transaction and rollback. Missing/changed evidence remains unresolved. |
| `runner/local_stop.py` | Detached witness selected from trusted code, minimal manager environment, bounded private stdin payload rather than nonce in argv. Parent closure/error leaves incomplete evidence rather than success. |
| `runner/local_stop_witness.py` | Validates canonical private state path and identifiers, single pending claim with audit, exact active identity before stop, actual client exit before completion, transaction/audit matching and rollback. Duplicates, bad paths, nonce, command timeout and missing audit tests covered. |
| `runner/service.py` | Owner reconciliation requires shared durable state and exact fenced incarnation, dispatches stopping only to completed-witness recovery, and leaves worker/workspace/remote controls independent. |
| `tests/test_audit_export.py` | Launch/stop/recovery projections correlate while excluding private fields; existing audit receiver/replay suite retained. |
| `tests/test_local_command.py` | Real guarded command and binding-denial marker checks reviewed; explicit deadlines isolate exception assertion; cleanup verified with live modules. |
| `tests/test_local_launch.py` | Real/fake receipt boundary, wrong identity, cancellation, release-before-receipt, private environment, killed parent before/after release and reconciliation. Cleanup retains unverified state. |
| `tests/test_local_launch_guard.py` | Receipt/target FD separation, closed authorization, invalid nonce, slow bind and real scope parent death. The added >10 s regression failed before the production fix. |
| `tests/test_local_process.py` | Fake launcher records exact receipt, selected environment and Git permission; success now proves authorization byte; failed bind proves EOF and wait. |
| `tests/test_local_scope.py` | Exact no-job observation cases plus identity-change fault, original stop/audit rollback/unresolved-state invariants retained. |
| `tests/test_local_stop_witness.py` | Reviewed atomic reservation, private stdin/env, real completion ordering via mocked exact systemctl results, duplicate denial, missing audit and malformed state/identifier tests. |
| `tests/test_local_stop_witness_live.py` | Production witness survives caller crash, successful completion reconciles only after fence, killed witness leaves stop unresolved; usable pidfd preflight and known-process teardown added. |
| `tests/test_scope_launch_receipt.py` | Receipt/audit atomicity, foreign/missing evidence, read/write contention, exact fence, observation change, commit failure, recovery audit rollback and no implicit worker/remote acceptance. Incoming pending-stop denial retained. |
| `tests/test_scope_stop_live.py` | Existing delayed/inflight legacy-stop fixtures explicitly opt out of witnessed stopping so their original uncertainty scenarios remain exercised; reviewed cleanup and real-scope checks. |
| `tests/test_stop_witness_live.py` | Exploratory witness/client interruption protocol retained; capability preflight and forced pidfd cleanup added with unit fault coverage. |
| `tests/test_worker.py` | Real guard plus worker's actual manual-fence predicate prove live release and cancellation-before-release denial; lifecycle/scope cleanup retained. |
| `tests/test_worker_process.py` | Merged both original legacy vanished-scope rejection and guarded signed-in reconciliation; requires forgotten unit, empty cgroup, exact launch receipt and durable run fence, retaining socket-readiness/resource fixes. |

### Validation and integration checkpoint

Initial targeted run under the restricted sandbox ran 107 tests with two Unix-socket permission errors and eight skips; this was an environment restriction, not accepted as validation. The elevated real-systemd baseline ran **124 tests, two skipped, successful**, with the unreaped-subprocess warning noted above. The first three focused regressions all failed before their fixes and then passed. The separate deadline regression also failed before its fix. Early fixed live runs passed **129 tests (two skipped)** and then **149 tests (one skipped)** as the stop-witness scope expanded. An initial signed-in/isolation/session run passed four tests; after stop integration, a fresh signed-in crash/session run passed three tests. Full-suite attempts superseded by incoming changes were explicitly interrupted and are not counted as passes; a mistaken default discovery invocation ran zero tests and was discarded in favor of the README's `discover -s tests` command.

Local merge `ff9638d` integrated main while retaining both crash cases; commit `f22a2d2` contains the initial QA fixes. Merge `1d8c942` preserved concurrent commit `7e25249` adding durable stop witnesses. Later incoming `b08c40c`/`ab7063b` add bad-input/pending-stop tests and merge main; those changes are being preserved, without force-pushing over them. Final counts, pushed SHA and exact-head hosted evidence follow below.

### Delivery evidence and follow-up findings

Preserved `b08c40c`/`ab7063b` through merge `e5cadb2`; the merge changed documentation/test coverage only, with no production diff from `347d304`. The incoming bad-state/identifier test and pending-stop denial were reviewed and run. A final shared-validation refactor reduced nesting/duplication without changing checks or transaction order (`9b68798`), followed by request-validator extraction (`1ad9cb2`). Public CodeAnt nitpick **6551138188** correctly identified an unused test capture list/wrapper; removed it in `abd44c4`, reusing the existing fixture directly.

Additional results:

- `/tmp/pr27-delivery-full.log`: **1,198 tests run, 85 skipped, successful**, 281.087 s. This predates the final maintainability follow-ups and the last incoming additional test; not represented as an exact-final-head full run.
- `/tmp/pr27-delivery-live.log`: **152 tests run, one skipped, successful**, 48.134 s. The signed-in crash/session cases passed separately: **three tests**, 26.073 s (`/tmp/pr27-delivery-signed-in.log`).
- `/tmp/pr27-postmerge-targeted.log`: **41 tests run, two skipped, successful**, 17.189 s, covering the incoming stop tests and resolved worker fixture.
- `/tmp/pr27-final-live.log`: **153 tests run, one skipped, successful**, 48.555 s, on the final major code organization at `9b68798`. `/tmp/pr27-final-signed-in.log`: **three passed**, 26.794 s, covering signed-in guarded crash reconciliation and writer session resume.
- After the final request-validator extraction, `/tmp/pr27-final-validator.log`: **26 passed**, and `/tmp/pr27-final-witness.log`: **33 passed**, 3.357 s, including real detached-witness crash/recovery and pidfd cleanup.
- After deleting the unused test capture, all **four local-process tests passed**. A disposable mutation omitting release_confirmed caused the new success-path authorization assertion to fail as intended; no production source was mutated for that check.
- Recorded final live/signed-in/full logs had no ResourceWarnings at the latest scan. `git diff --check` passes.

The final push at this checkpoint is [`abd44c4e42e1f229a75afa3a91d83d481070a21c`](https://github.com/Bonobo791/agent-workflows/commit/abd44c4e42e1f229a75afa3a91d83d481070a21c). Independent ls-remote and PR metadata match; the checkout is clean and PR #27 is MERGEABLE. No force push, main merge or deployment was performed.

CodeAnt's published `9b68798` summary reports **10 antipatterns and seven complex functions** without accessible locations, SAST/SCA/bug/IaC pass, and 1.2% duplication. Its secret check reports one existing false-positive suppression; this QA made no suppression/configuration changes. Coverage is not applicable because no coverage data was found, not evidence of measured coverage. Protected details remain skipped. The public unused-state suggestion was actionable and fixed separately. SonarCloud's final maintainability refresh and the running full-suite result follow in the final checkpoint below.

### Final local QA and hosted checkpoint — November 1, 2026, UTC

`/tmp/pr27-final-full.log` completed successfully: **1,199 tests run, 85 skipped**, 273.339 s. It began on the `9b68798` contents; the last isolated request-validator extraction and deletion of an unused test wrapper were validated separately with the 33-test real-witness matrix and four local-process tests above. These small follow-ups are not described as a new exact-head full-suite run. No failures or ResourceWarnings appear in the recorded final full/live/signed-in logs.

Final-head SonarCloud check `124703770728` at `abd44c4e42e1f229a75afa3a91d83d481070a21c` failed. Its public issues API now lists **only S8701 and S8705 at local_launch_guard.py:30**; all accessible duplication/complexity/exception-assertion findings were removed. The remaining direct-execution findings are reviewed false positives as explained above, and remain visible without suppression. CodeAnt's exact-final-head summary still fails for **10 unlocated antipatterns and six complex functions**; SAST/SCA/bug/IaC/secret checks pass, coverage has no measured data, and protected details remain skipped. All accessible actionable comments have dispositions above, including the fixed unused-state nitpick.

Semgrep job `124703656354` and CodeRabbit job `124703745647` were confirmed in progress on this exact head; Codex code review also reports running on `abd44c4`. These are pending, not passes. Current open inventory contains only PR #27. The standing future-PR goal remains active while hosted work completes.

At 00:39:24 UTC, exact-head Semgrep check `124703656354` completed **SUCCESS** on `abd44c4e42e1f229a75afa3a91d83d481070a21c`. A subsequent authoritative refresh still showed PR #27 OPEN/MERGEABLE at that SHA, CodeRabbit check `124703745647` in progress, and Codex final-head code review running. No new actionable public finding appeared. The remaining work is hosted-review follow-up; the goal remains active, not complete or blocked. Local QA does not establish full production containment, remote-request outcomes, or deployment acceptance.

### PR #27 follow-up — shared launch deadline and asynchronous reaping

The branch advanced after the previous checkpoint to `169818a87968c6d51b4f7856f90e93e605e5de86`, merging `93816be`'s deadline/reaper changes with the earlier QA fixes. Fast-forwarded the isolated checkout and reviewed all **13 updated files**: the recovery guide; local_command, local_launch, local_launch_guard, local_process and local_scope; and the corresponding launch/guard/process/scope/receipt/live-witness tests. The asynchronous reaper waits briefly and then retains the exact Popen object in a daemon wait thread rather than killing a possibly still-launching manager client. Its OSError handling also covers ChildProcessError. The launch deadline is now a shared finite absolute monotonic timestamp, validated by the launcher, passed to the guard and checked before authorization under the database fence lock. This supersedes the earlier unbounded-guard-wait description; the >10-second slow-binding test now supplies a matching 20-second deadline.

Two additional issues were reproduced and fixed:

- **P27-07: expiry checked before authorization read.** A deterministic guard test advances the clock while reading an already-ready `G`. The incoming code executed after expiry; the regression failed. Moved the final time check after the read, immediately before execution. The same test now passes, with execve uncalled.
- **P27-08: stopped scope skipped client reaping.** Incoming `_cleanup_command` only reaped when `stopped` was false, leaving the error path where termination was confirmed but the subsequent client wait timed out without reaping. A focused cleanup test failed because wait was never called. Cleanup now always invokes the existing asynchronous reaper for a known client; already-reaped success returns immediately. It does not change scope outcome or authorize replay. The regression passes.

Updated the recovery guide to describe the shared deadline, including expiry after the authorization read. Follow-up commit `90c3e2080e6dd95d8b9b72c200db3b24017c913a` was reviewed and preserved: it removes a redundant exception subclass from the reaper, clarifies an exception assertion and documents the guard trust boundary. The public Sonar issue flows were independently fetched: both label sys.argv as an HTTP-request source, while this script has no HTTP endpoint. The documentation's characterization of those reviewed false positives is supported by that actual flow; no remote issue status or suppression was changed.

Validation:

- Incoming-head baseline real-systemd matrix: **105 tests run, one skipped, successful**, 46.131 s (`/tmp/pr27-169818a-live.log`).
- Both new regressions failed before their fixes (`/tmp/pr27-post169-regression-before.log`) and passed afterward.
- Fixed real-systemd/command/worker/isolation matrix: **107 tests run, one skipped, successful**, 48.407 s (`/tmp/pr27-deadline-fixed-live.log`). It omits unrelated unchanged audit/worker-unit modules already covered above; the smaller count does not replace the earlier 153-test matrix.
- Shared-deadline signed-in guarded crash/session run: **three passed**, 27.917 s (`/tmp/pr27-deadline-signed-in.log`).
- Concurrent-cleanup merge validation: **eight passed**, 1.710 s (`/tmp/pr27-90c3e20-merge-tests.log`).

Committed the fixes in `4aab043`, preserved the concurrent update through merge [`aff397dd2e6db3f4d47ed2b1bfb510f0ff71b82d`](https://github.com/Bonobo791/agent-workflows/commit/aff397dd2e6db3f4d47ed2b1bfb510f0ff71b82d), and pushed without force. Independent remote-ref verification matched the SHA. Full-suite and exact-head hosted follow-up remain in progress at this checkpoint. No additional actionable public comment was found in the refreshed timeline, including issue-comment bodies and inline reviews.


### Complete visible-comment inventory after owner audit question

The refreshed merged timeline contained **28 visible entries**, including issue comments, inline comments and review submissions. Every entry has a disposition below. Summary comments can duplicate inline findings; counts are not unique defect counts. The earlier audit did not explicitly disposition CodeAnt's Job-format suggestion (6551138771); this gap is acknowledged and corrected here.

| Comment / review | Author | Disposition |
| --- | --- | --- |
| [6551072832](https://github.com/Bonobo791/agent-workflows/pull/27#issuecomment-6551072832) | codeant-ai[bot] | Informational: CodeAnt initial-head review status; not final-head validation. |
| [6551073047](https://github.com/Bonobo791/agent-workflows/pull/27#issuecomment-6551073047) | codeant-ai[bot] | Promotional footer; no code finding. |
| [6551073491](https://github.com/Bonobo791/agent-workflows/pull/27#issuecomment-6551073491) | coderabbitai[bot] | CodeRabbit overview: two linked findings handled in 4433897442/4433897452. Generic docstring-coverage warning is advisory and deferred: no repository rule requires blanket function docstrings; named tests already describe behavior. No threshold/configuration changed. |
| [6551074239](https://github.com/Bonobo791/agent-workflows/pull/27#issuecomment-6551074239) | chatgpt-codex-connector[bot] | Codex status: refreshed per pushed head; no suggestion in the status comment itself. |
| [6551079678](https://github.com/Bonobo791/agent-workflows/pull/27#issuecomment-6551079678) | gitar-bot[bot] | Gitar status says no rules evaluated and functional validation not enabled; not accepted as QA evidence. |
| [4433810261](https://github.com/Bonobo791/agent-workflows/pull/27#discussion_r4433810261) | amazon-q-developer[bot] | Proposed ordering rejected: binding receipt is durable before authorization. Moving authorization outside the fence lock creates cancellation race. Commit-failure regressions preserve unresolved running evidence. |
| [4433810263](https://github.com/Bonobo791/agent-workflows/pull/27#discussion_r4433810263) | amazon-q-developer[bot] | Valid stop identity race; fixed with observed identity and conditional locked transitions, including witnessed reservation. |
| [4433810268](https://github.com/Bonobo791/agent-workflows/pull/27#discussion_r4433810268) | amazon-q-developer[bot] | Valid independent ten-second guard deadline; replaced with a shared validated absolute deadline, with slow-binding and expiry regressions. |
| [5725591225](https://github.com/Bonobo791/agent-workflows/pull/27#pullrequestreview-5725591225) | amazon-q-developer[bot] | Amazon Q summary duplicates its four inline findings; each has an explicit disposition in this inventory. |
| [4433810271](https://github.com/Bonobo791/agent-workflows/pull/27#discussion_r4433810271) | amazon-q-developer[bot] | Environment leak claim not reproduced: production env -i precedes guard; explicit sentinel tests prove manager secret absent from target. |
| [4433821968](https://github.com/Bonobo791/agent-workflows/pull/27#discussion_r4433821968) | cubic-dev-ai[bot] | PID mismatch claim rejected on tested host: real systemd scope receipt PID equals Popen PID and cgroup membership matches. Signed-in runtime cases pass. |
| [4433821974](https://github.com/Bonobo791/agent-workflows/pull/27#discussion_r4433821974) | cubic-dev-ai[bot] | Partly valid: separated fixture/live evidence wording. Signed-in crash test does exist and passed; that part of the claim was incorrect. |
| [4433821981](https://github.com/Bonobo791/agent-workflows/pull/27#discussion_r4433821981) | cubic-dev-ai[bot] | Valid missing success assertion; test now requires G. Omitting release in a disposable mutation fails. |
| [4433821983](https://github.com/Bonobo791/agent-workflows/pull/27#discussion_r4433821983) | cubic-dev-ai[bot] | Valid pidfd capability check gap; probe and skip known unsupported/permission errors before starting live fixtures. |
| [5725603490](https://github.com/Bonobo791/agent-workflows/pull/27#pullrequestreview-5725603490) | cubic-dev-ai[bot] | Cubic summary duplicates PID claim 4433821968; same live-evidence rejection. No code/check suppression. |
| [4433821992](https://github.com/Bonobo791/agent-workflows/pull/27#discussion_r4433821992) | cubic-dev-ai[bot] | Valid teardown gap; known process pidfds are terminated/waited, private state retained when quiescence remains uncertain. |
| [4433821998](https://github.com/Bonobo791/agent-workflows/pull/27#discussion_r4433821998) | cubic-dev-ai[bot] | Valid late receipt; deadline/cancellation rechecked before receipt acceptance. Deterministic clock regression failed then passed. |
| [4433822002](https://github.com/Bonobo791/agent-workflows/pull/27#discussion_r4433822002) | cubic-dev-ai[bot] | Valid unreaped client; exact Popen reaped, now via short wait and daemon waiter without assuming scope termination. |
| [4433822007](https://github.com/Bonobo791/agent-workflows/pull/27#discussion_r4433822007) | cubic-dev-ai[bot] | Valid duplicate timeout finding; shared deadline and slow-bind/expiry tests address it. |
| [6551138188](https://github.com/Bonobo791/agent-workflows/pull/27#issuecomment-6551138188) | codeant-ai[bot] | Valid unused launch-capture state; wrapper/list removed and local-process tests passed. |
| [6551138771](https://github.com/Bonobo791/agent-workflows/pull/27#issuecomment-6551138771) | codeant-ai[bot] | Two public CodeAnt suggestions: guard timeout valid and fixed; Job=0 CLI claim rejected after direct systemd 259 probe and official systemctl printer source, which renders zero as empty Job=. This disposition was missing from the earlier report and is now explicit. |
| [6551145899](https://github.com/Bonobo791/agent-workflows/pull/27#issuecomment-6551145899) | codeant-ai[bot] | Quality-gate counts inspected, not hidden. Latest protected antipattern/complexity details remain skipped by owner instruction; failing gate not claimed green. |
| [6551148020](https://github.com/Bonobo791/agent-workflows/pull/27#issuecomment-6551148020) | codeant-ai[bot] | CodeAnt low-risk summary for older head; informational, not proof of current correctness. |
| [6551159409](https://github.com/Bonobo791/agent-workflows/pull/27#issuecomment-6551159409) | codeant-ai[bot] | Coverage says no data/not applicable; not represented as measured test coverage. |
| [4433897442](https://github.com/Bonobo791/agent-workflows/pull/27#discussion_r4433897442) | coderabbitai[bot] | Valid witness wait budget: raised from 50 to 90 seconds to cover serial witness waits; uncertain outcomes still fenced. |
| [4433897452](https://github.com/Bonobo791/agent-workflows/pull/27#discussion_r4433897452) | coderabbitai[bot] | Valid long database lock: host observations precede write transaction; exact fence, receipt and audit revalidated under lock. Concurrent writers and changed-evidence regressions pass. |
| [5725724948](https://github.com/Bonobo791/agent-workflows/pull/27#pullrequestreview-5725724948) | coderabbitai[bot] | Review duplicates two inline findings and adds valid permission-test nitpick: mode 0620 state file in private parent must reject before subprocess; added and passing. |
| [6551439464](https://github.com/Bonobo791/agent-workflows/pull/27#issuecomment-6551439464) | sonarqubecloud[bot] | Sonar gate failure traced to two reviewed direct-exec false positives; fetched full taint flows and checked trusted argv/env boundary. No suppression or remote issue-state change. |

Job-format verification: the actual systemd 259.10 command returned `Id=basic.target` and an empty `Job=`. [The official systemctl v259 printer](https://github.com/systemd/systemd/blob/v259/src/systemctl/systemctl-show.c) prints a numeric value only for a positive job ID and an empty property otherwise. [The D-Bus interface documentation](https://www.freedesktop.org/software/systemd/man/latest/org.freedesktop.systemd1.html) describes zero in the underlying job-ID field; that does not establish CLI output `Job=0`. No parser relaxation was made based on that incorrect CLI claim.


### Late CodeRabbit fixes, validation and push after the audit

CodeRabbit 4433897452 was valid: up to four 15-second systemd calls were inside a SQLite write transaction while other writers time out after ten seconds. Reproduced lock contention through independent connections before the fix. The three paths now gather validated host observations without a write lock, then begin the short write transaction and recheck the exact launch/stop receipt, its audit, scope identity and (for owner reconciliation) the run fence. Replaced the old test requiring writers to be blocked during host inspection with tests proving writers can commit while observations run, a changed fence blocks recovery, and receipt/audit changes cannot mark any of confirmed recovery, witnessed recovery or normal witnessed stop as terminated. Consistently changed receipt plus matching audit also rejects; the snapshot comparison is not merely an audit-existence test. The final state/audit writes remain atomic. Host observations are not made atomic with the external manager; the documented unique-unit/no-unrelated-operation assumptions remain necessary.

CodeRabbit 4433897442 was valid: the witness's serial timeout budget exceeds the old 50-second controller wait. Raised the wait to 90 seconds without treating timeout as a successful stop. Review 5725724948's permission-test suggestion is added: a mode-0620 state database under its mode-0700 parent rejects without issuing any subprocess, then its original mode is restored. The generic Ruff/docstring style overlays were inspected as advisory profiles, not a repository-enforced formatting/docstring contract; no blanket formatter, exception-class rewrite, docstring generation or gate change was applied.

Validation after these fixes:

- The independent-writer/change-evidence regressions failed under the old long-lock implementation (`/tmp/pr27-lock-regression-before.log`) and passed afterward.
- `/tmp/pr27-lock-fixed-live.log`: **108 tests run, one skipped, successful**, 49.525 s, exercising real scoped launch, deadlines, commands, witnesses, original interrupted-stop fencing, worker recovery and sandbox isolation.
- `/tmp/pr27-lock-signed-in.log`: **one passed**, 7.492 s, signed-in guarded crash reconciliation.
- `/tmp/pr27-lock-full.log`: **1,208 tests run, 85 skipped, successful**, 279.332 s, on the locking-fix contents before the final incoming test-only commit.
- Incoming `f7c5385` adds documentation and a commit-failure/active-recovery regression, without production changes. Reviewed and preserved it. `/tmp/pr27-lock-merge-tests.log`: **28 passed**, 0.629 s, covering all receipt/reconciliation and witness unit tests including the incoming case. The full suite was not repeated for that test-only merge.
- No ResourceWarnings or unexpected test failures appear in these final logs. The isolated checkout is clean and git diff --check passes.

Fix commit `40f1ffc` and merge [`e5b422b81d2de63dea9529ac76fd64fc89e292be`](https://github.com/Bonobo791/agent-workflows/commit/e5b422b81d2de63dea9529ac76fd64fc89e292be) were pushed without force. Independent remote and PR metadata verification matched that final SHA; PR #27 is OPEN/MERGEABLE. Latest timeline still has 28 visible entries and no additional actionable finding beyond the explicit inventory. SonarCloud exact-head check `124707737279` fails only on reviewed S8701/S8705 at local_launch_guard.py:33; no maintainability issue remains. CodeRabbit exact-head check `124707819915` is neutral/rate-limited; its published older-head findings are fixed as above. Semgrep `124707620605` and final-head Codex review are running at this checkpoint. CodeAnt protected diagnostic details remain intentionally skipped, with its failed quality gate retained; no merge to main or deployment was performed.

A later refresh confirms CodeRabbit itself appended “Addressed in commits 93816be to e5b422b” to both inline findings 4433897442 and 4433897452. Its new-head check is neutral due to rate limiting; that acknowledgment is not represented as a new full code review. The current Sonar summary replaced the older bot comment and is recorded as 6551439464 in the inventory. CodeAnt's exact `e5b422b` public summary now reports **26 antipatterns and seven complex functions**, with SAST/SCA/bug checks passing; the increased undisclosed count is recorded, not treated as resolved. Protected details remain skipped under the owner's explicit instruction. Semgrep and Codex final-head results are still pending at this refresh.

### Final-head Semgrep completion after the complete comment audit

At `e5b422b81d2de63dea9529ac76fd64fc89e292be`, Semgrep check `124707620605` completed **SUCCESS** at 00:57:42 UTC. PR #27 remains OPEN/MERGEABLE and is the only open PR. The merged timeline still contains the same 28 visible entries, with no changed bodies or new findings since the completed inventory. CodeRabbit is neutral/rate-limited and explicitly marks its two published inline findings addressed. SonarCloud and CodeAnt failed gates retain the dispositions above. The Codex summary still labels its final-head code review running; that summary is pending metadata, not proof of an accessible live job handle or a completed review. No source change or additional push was needed in this refresh.

### Final-head Codex review completion

The next refresh found a material update: Codex code review completed at **00:59:24 UTC** on exact head `e5b422b81d2de63dea9529ac76fd64fc89e292be`, with `has_any_findings=false` and `has_unresolved_findings=false`. Its separate security-review record still refers to the original `20886cd` head and is not claimed as fresh final-head security validation. This closes the remaining pending code-review metadata. Semgrep passes; CodeRabbit acknowledges its fixes and is neutral/rate-limited; reviewed Sonar/CodeAnt gate limitations remain unchanged. PR #27 is still the sole open PR and remains MERGEABLE. No source edit or push was needed for this completion refresh; standing monitoring remains active awaiting new PR work.

### Monitoring idle audit after PR #27

After final-head Codex completion, three consecutive goal continuations revalidated the same authoritative state: only PR #27, OPEN/MERGEABLE at `e5b422b81d2de63dea9529ac76fd64fc89e292be`; the same 28 visible entries; no new findings, commits or running check jobs. All accessible actionable findings have fixes/dispositions and pushes recorded above. Remaining gate failures are the reviewed Sonar false positives and CodeAnt's undisclosed findings, whose protected details the owner instructed us to skip. Further meaningful progress requires a new PR, commit or actionable finding. The standing goal is marked blocked awaiting that external change, not complete. No background polling service was installed; no source edits or additional pushes were made during these idle audits.


## PR #28 — explicit comment audit after owner question

Refetched the complete merged PR discussion timeline and read every returned body, including issue-comment suggestions and review summaries: **13 visible entries**. This inventory is a comment audit, not a claim that every diagnostic behind an authenticated external report was accessible. Earlier blanket claims were too broad: PR #27's CodeAnt Job-format suggestion lacked an explicit disposition until its follow-up inventory; that gap is recorded above. PR #24's separate protected-diagnostic exception remains explicitly documented.

| Entry | Author | Disposition |
| --- | --- | --- |
| [6551504288](https://github.com/Bonobo791/agent-workflows/pull/28#issuecomment-6551504288) | codeant-ai[bot] | Review-status metadata for initial aeed025; not final-head validation. |
| [6551504427](https://github.com/Bonobo791/agent-workflows/pull/28#issuecomment-6551504427) | codeant-ai[bot] | Promotional footer; no technical finding. |
| [6551504765](https://github.com/Bonobo791/agent-workflows/pull/28#issuecomment-6551504765) | coderabbitai[bot] | Rate-limited CodeRabbit review; no completed independent code review. |
| [6551505959](https://github.com/Bonobo791/agent-workflows/pull/28#issuecomment-6551505959) | chatgpt-codex-connector[bot] | Codex code review completed with no findings at e8726a0; security review remains on initial aeed025. |
| [6551524092](https://github.com/Bonobo791/agent-workflows/pull/28#issuecomment-6551524092) | amazon-q-developer[bot] | Amazon Q review failed because head/merge base changed; no technical finding, not successful review evidence. |
| [6551528274](https://github.com/Bonobo791/agent-workflows/pull/28#issuecomment-6551528274) | gitar-bot[bot] | Gitar evaluated no rules and functional validation is disabled; not QA evidence. |
| [4433946276](https://github.com/Bonobo791/agent-workflows/pull/28#discussion_r4433946276) | cubic-dev-ai[bot] | Valid missing completed=true assertion. Matching and unrelated completion cases added; Cubic explicitly marks addressed in b79666a. |
| [5725819812](https://github.com/Bonobo791/agent-workflows/pull/28#pullrequestreview-5725819812) | cubic-dev-ai[bot] | Cubic summary duplicates its inline finding and confirms all reported issues addressed. |
| [6551542320](https://github.com/Bonobo791/agent-workflows/pull/28#issuecomment-6551542320) | codeant-ai[bot] | CodeAnt e8726a0 gate FAILED: three antipatterns, one complex function. Public counts reviewed; protected rule details skipped by explicit owner instruction. Security/dependency/secret/bug checks pass. Failure retained. |
| [6551542342](https://github.com/Bonobo791/agent-workflows/pull/28#issuecomment-6551542342) | codeant-ai[bot] | No coverage data; not applicable. Not represented as measured test coverage. |
| [6551549382](https://github.com/Bonobo791/agent-workflows/pull/28#issuecomment-6551549382) | codeant-ai[bot] | Two valid public CodeAnt suggestions: bind writer only after scope-link checks/commit; add execution_link_audit lookup index. Both implemented, with failure regressions and query-plan validation recorded in prior execution logs. |
| [6551551505](https://github.com/Bonobo791/agent-workflows/pull/28#issuecomment-6551551505) | codeant-ai[bot] | Low-risk summary refers to older cf359063; informational, not final-head proof. |
| [6551690065](https://github.com/Bonobo791/agent-workflows/pull/28#issuecomment-6551690065) | sonarqubecloud[bot] | Sonar gate passes, 0% new duplication, two issues remain. Gate pass is not zero-findings sign-off; run_codex parameter-count and complexity warnings remain outstanding. |

The live comment snapshot now references e8726a0d0c0092d656eba809bec6fd073d540d18. The isolated local worktree is at 7f6bdd749c45865d34b87ef84d0e704ddbd51413; this comment audit does not claim the later remote contents have been reconciled or tested locally. Full-suite log /tmp/pr28-final-full.log reports **1,220 tests run, 86 skipped, successful**, 290.903 seconds, on the previously documented production snapshot. Test consolidation log /tmp/pr28-deduplicated-tests.log reports **26 passed**, 5.274 seconds. These logs are not attributed to the later remote head. Complete final-head every-file QA/reporting and reconciliation remain separate outstanding work; this section establishes the exact visible-comment coverage and limitations.


## PR #28 — every-file QA, persistence fixes and validation

Scope: `feat/session-backup-recovery`, initially aeed02561f584f748239ba460e92bc0e413b0970, reconciled with main and concurrent changes through e8726a0d0c0092d656eba809bec6fd073d540d18. The isolated worktree is /tmp/agent-workflows-pr28-review; the original dirty checkout is preserved. Applied senior-qa, code-reviewer and receiving-code-review. The incoming changes add recovery evidence, not authorization to resume an interrupted writer or a complete operator recovery workflow.

### Every-file coverage

| Changed file | Review and QA evidence |
| --- | --- |
| docs/PILOT_SETUP.md | Checked null execution/turn semantics, owner inspection and distinction between synthetic crash and real signed-in crash evidence. Read-only saved-thread inspection does not authorize resume or establish upstream outcome. |
| docs/audit-history.md | Checked observed versus completed event semantics, keyed scope/session/execution correlation, no historical backfill and retained PR #27 launch/stop/recovery documentation. Concurrent reorder-only merge preserved the same statements. |
| runner/audit_export.py | New table allowlisted; role and keyed session/execution/scope correlate with existing projections. Raw binding, unit, session and execution values stay private. Export/OTLP regressions cover new projection and existing PR #27 records. |
| runner/codex_executor.py | Reviewed all callers and launch, thread, turn, tool, completion, cancellation and fence paths. Scope link commits before writer activation; exact new binding audit is required. Added turn observation persistence proof and tested silent ignored/deleted writes. Completion still requires existing scope termination evidence. |
| runner/codex_session.py | Thread callback follows trusted provenance binding and precedes turn/start. Turn callback follows returned turn ID and precedes buffered event replay. Invalid null, empty, integer and boolean turn IDs reject before persistence. Existing direct callers retain optional callback compatibility. |
| runner/graph.py | Additive CREATE INDEX IF NOT EXISTS for exact inspection filters. Existing audit rows preserved; observed and completed query plans use covering-index searches. No immutable data or history rewritten. |
| runner/local_supervisor.py | Checked new scope-link schema, uniqueness and binding-audit trigger. Fresh writer activation now reads back exact persisted active binding before claiming success, reusing the resumed-session check. Existing resume policy and final scope checks stay enforced. |
| runner/service.py | Owner/manual-incarnation restrictions retained. Exact run/unit scope link selects execution/role/session; more than one observed turn rejects; completed requires exact matching turn. Inspection remains read-only and does not reconcile or resume. |
| tests/fixtures/fake_app_server.py | turn_exit and turn_hang occur after turn/start response, exercising the response-to-completion interruption window without tool events. Existing fixture modes preserved. |
| tests/test_codex_executor.py | Covers synthetic controller SIGKILL, persisted thread/scope/turn, active-writer resume fence, abrupt EOF, binding failure before turn, failed scope/audit persistence, missing observation and completed outputs. Narrow EOF exceptions and closed SQLite readers remove ambiguous assertions/resource leaks. Audit-abort case consolidated into failure matrix without dropping assertions. |
| tests/test_codex_session.py | Callback-order/failure and invalid-turn regressions fail before their respective fixes. Provenance construction is outside exception assertion. Existing output/event behavior covered by affected-module suite. |
| tests/test_local_executor_crash_live.py | Real signed-in executor interrupted after observed turn; independently opened app server reads exact saved thread, with optional matching-turn status inspection. No replay. Cleanup requires matching unit/invocation/cgroup, empty recorded cgroup and no pending job before private fixture deletion. Changed identity is never stopped; uncertain cleanup retains evidence. |
| tests/test_local_scope.py | Matching completion turns true; unrelated completion stays false; two observed turns reject. Scope-link audit failure rolls back binding; keyed exported identifiers correlate and exclude raw private values. |

### Valid findings and fixes

- **P28-01: invalid provider turn ID reached persistence.** Four malformed-ID regressions failed before validation, then passed. Validation now precedes on_turn callback and SQLite coercion.
- **P28-02: writer activated before scope-link validation/commit.** Public CodeAnt suggestion 6551549382, item 1, valid. Invalid scope and rejected insert left an active unlinked writer before the fix. Writer binding now follows successful link/audit commit; before-turn failures fence without model output.
- **P28-03: silent missing scope-binding audit.** Missing trigger and ignored audit inserts previously allowed binding and turn execution. The executor now requires a fresh exact bound audit in the link transaction; missing/ignored/aborted persistence rolls back and fences.
- **P28-04: scope inspection scanned growing audit history.** Public CodeAnt suggestion 6551549382, item 2, valid. Added shared covering index; a 10,000-row migration probe preserves all rows and both inspection queries use indexed SEARCH operations.
- **P28-05: positive completion branch untested.** Cubic 4433946276 valid. Added unrelated/matching completion cases; Cubic explicitly acknowledges addressed in b79666a.
- **P28-06: live crash teardown could discard unresolved evidence.** Private TemporaryDirectory uses delete=False and is explicitly cleaned only after verified scope cleanup. Injected nonempty cgroup/changed identity retains state; verified cleanup deletes it. No scope-state reconciliation is inferred from controller death.
- **P28-07: a silently ignored fresh writer insert allowed the turn to run.** New regression injected BEFORE INSERT RAISE(IGNORE) on local_sessions. Before the fix execution reached finish and failed there, after turn execution. Reused exact persisted-active-binding check for fresh as well as resumed sessions before setting handle.session. Regression now rejects before turn; scope/thread link remains as valid recovery evidence, with no observed turn or output and no resume permission.
- **P28-08: a missing observed-turn insert did not prevent successful completion.** BEFORE INSERT RAISE(IGNORE) and AFTER INSERT DELETE both reproduced false success. The callback now requires one inserted row and exact id/run/role/execution/session/turn/status read-back in the same transaction. Both regressions now fence and leave no completion. Because turn/start already occurred, this error does not claim no provider request was sent.

Accessible Sonar test issues were fixed: broad exceptions narrowed, assertion setup moved out, duplicated audit-failure setup consolidated while preserving failure coverage. Sonar's two remaining run_codex maintainability warnings are explicitly outstanding: S107 reports 18 parameters (main already has 16 versus threshold 13), and S3776 reports complexity 74 versus threshold 15. They are not dismissed as false positives or marked resolved; a broad parser/API refactor has not been performed as part of the persistence fixes. No scanner suppression or gate relaxation was applied.

### Validation evidence

Prior logs, rechecked on disk: invalid-turn regression failed in /tmp/pr28-turn-regression-before.log; early writer-binding regression failed in /tmp/pr28-binding-regression-before.log; missing-audit regression failed in /tmp/pr28-missing-audit-before.log. Fixed targeted suite: 35 passed in 4.736 s. Audit-integrated suite: 82 run, one skipped, successful in 7.459 s. Real-systemd/signed-in session, executor, scope, worker, isolation and audit matrix: 101 run, one skipped, successful in 65.268 s (/tmp/pr28-final-live.log). Separate exact saved-thread live probe: two passed in 11.262 s (/tmp/pr28-saved-thread-live.log). Earlier full suite: 1,220 run, 86 skipped, successful in 290.903 s (/tmp/pr28-final-full.log), before latest persistence fixes; test consolidation separately passed all 26 executor tests.

New P28-07/P28-08 regressions failed before fixes (two failures and one later-stage error across subcases, /tmp/pr28-persistence-before.log). Fixed session/executor/scope/audit suite: **85 run, one skipped, successful**, 6.590 s (/tmp/pr28-persistence-after.log). Full-suite and final live validation of these new shared-code fixes are in progress at this checkpoint, not yet claimed passing. Final push and exact-head checks will be recorded below. The preceding e8726a0 head has passing Semgrep/Sonar, neutral rate-limited CodeRabbit, and failing CodeAnt Quality Gates/SCR; CodeAnt details remain skipped by owner instruction.


### PR #28 — shared-persistence full-suite completion

The shared-persistence fixes P28-07/P28-08 passed the full suite: **1,220 tests run, 86 skipped, successful**, 283.419 seconds (/tmp/pr28-persistence-full.log). No ResourceWarnings or unexpected failures appear. The final signed-in/systemd matrix is running separately. Immediately before push preparation, fetched origin and confirmed HEAD and origin/feat/session-backup-recovery both remain e8726a0d0c0092d656eba809bec6fd073d540d18 before the local fix commit. The refreshed discussion still has the same 13 entries; independently fetched review threads show exactly one inline thread, resolved by Cubic. No hidden CodeAnt rule details were requested again.

### PR #28 — final shared-persistence validation and verified push

The final real-systemd/signed-in matrix passed **105 tests run, one skipped, successful**, 66.207 seconds (/tmp/pr28-persistence-live.log). It includes actual interrupted executor persistence, exact saved-thread inspection, normal writer resume after verified scope termination, worker-process and isolation cases, and affected unit/audit tests. The final full suite passed **1,220 run, 86 skipped** as recorded above. No unexpected errors or ResourceWarnings appeared; git diff --check passed.

Committed and pushed [92e64a7d76aa53e5689eba65d691fec06f00e0d0](https://github.com/Bonobo791/agent-workflows/commit/92e64a7d76aa53e5689eba65d691fec06f00e0d0), `fix: require persisted writer and turn observations before proceeding`, without force. Independent git ls-remote and PR metadata matched the exact SHA. Only runner/codex_executor.py, runner/local_supervisor.py and tests/test_codex_executor.py were included; unrelated original-checkout changes and this local report were not committed. Immediate new-head metadata showed no reported checks and mergeability UNKNOWN; older-head passes are not attributed to the new SHA. Final hosted refresh follows separately. No merge to main, deployment, external workflow mutation, interrupted-turn resume or production acceptance was performed.

Post-push refresh: PR #28 is the only open PR, MERGEABLE at exact 92e64a7d76aa53e5689eba65d691fec06f00e0d0. Semgrep is IN_PROGRESS; CodeRabbit is NEUTRAL/rate-limited. Codex comment reports new-head code review running; its security record remains older. Sonar/CodeAnt final-head results have not yet published. All 13 visible comment bodies were refreshed; no new actionable finding was present. Prior-head Sonar/CodeAnt summaries retain their previous dispositions and are not final-head passes. The standing goal remains active awaiting hosted results and further PR work.


## PRs #30 and #31 — November 1 review of both open PRs

The user explicitly requested work on both open PRs. Created a fresh isolated clone /tmp/agent-workflows-review-nov1 and separate review worktrees after earlier /tmp worktrees disappeared. Original dirty checkout preserved. Applied senior-qa, code-reviewer and evidence-based review triage. No AGENTS.md found in these PR trees. Both were initially MERGEABLE; initial Semgrep, Sonar and CodeAnt gates pass. Passing bot check status does not invalidate technical findings in its comments.

### PR #30 — complete initial visible-comment inventory

Read all **14 visible entries**, including inline comments, review summaries, issue-comment suggestions and status bodies. Duplicated summaries are not distinct defects.

| Entry | Author | Disposition |
| --- | --- | --- |
| [6552843835](https://github.com/Bonobo791/agent-workflows/pull/30#issuecomment-6552843835) | codeant-ai[bot] | Initial-head CodeAnt review-status metadata; not independently executed QA. |
| [6552843978](https://github.com/Bonobo791/agent-workflows/pull/30#issuecomment-6552843978) | codeant-ai[bot] | Promotional footer; no technical finding. |
| [6552844393](https://github.com/Bonobo791/agent-workflows/pull/30#issuecomment-6552844393) | coderabbitai[bot] | CodeRabbit rate-limited; no completed code review. |
| [6552845307](https://github.com/Bonobo791/agent-workflows/pull/30#issuecomment-6552845307) | chatgpt-codex-connector[bot] | Codex code and security summaries completed at initial 69908c5; code review reports no findings. Not final-push evidence. |
| [4434252011](https://github.com/Bonobo791/agent-workflows/pull/30#discussion_r4434252011) | amazon-q-developer[bot] | Valid: successful-export completion swallowed backup-generation errors. Moved completion outside exporter exception conversion; exact-error regression fails before and passes after. |
| [4434252014](https://github.com/Bonobo791/agent-workflows/pull/30#discussion_r4434252014) | amazon-q-developer[bot] | Rejected after verification: baseline test passes, and direct patched-scandir call counting proves this runtime's os.walk uses the patched attribute. No ineffective-test rewrite. |
| [5726234420](https://github.com/Bonobo791/agent-workflows/pull/30#pullrequestreview-5726234420) | amazon-q-developer[bot] | Amazon Q summary repeats its two inline findings; dispositions above. |
| [6552847504](https://github.com/Bonobo791/agent-workflows/pull/30#issuecomment-6552847504) | sonarqubecloud[bot] | Initial Sonar gate passes with zero issues. No suppression or threshold change. |
| [6552850036](https://github.com/Bonobo791/agent-workflows/pull/30#issuecomment-6552850036) | gitar-bot[bot] | Gitar free-plan notice; no review/functional-validation evidence. |
| [6552852878](https://github.com/Bonobo791/agent-workflows/pull/30#issuecomment-6552852878) | codeant-ai[bot] | Initial CodeAnt gates pass: no antipatterns, complex functions, secrets, SAST/SCA or duplication findings. No protected diagnostic access needed. |
| [6552853081](https://github.com/Bonobo791/agent-workflows/pull/30#issuecomment-6552853081) | codeant-ai[bot] | Coverage explicitly has no data/not applicable; not measured test coverage. |
| [5726237867](https://github.com/Bonobo791/agent-workflows/pull/30#pullrequestreview-5726237867) | cubic-dev-ai[bot] | Cubic no issues across three files; supplementary review, not substitute for local QA. |
| [6552874301](https://github.com/Bonobo791/agent-workflows/pull/30#issuecomment-6552874301) | codeant-ai[bot] | Valid pathname-substitution race. Export now uses verified pinned directory and exact generation; original-name replacement leaves backup fenced. Tested replacement before open, during verifier and during exporter. |
| [6552876112](https://github.com/Bonobo791/agent-workflows/pull/30#issuecomment-6552876112) | codeant-ai[bot] | Initial-head low-risk summary; informational only. |

### PR #31 — complete initial visible-comment inventory

Read all **14 visible entries**, including inline comments, review summaries, issue-comment suggestions and status bodies. Duplicated summaries are not distinct defects.

| Entry | Author | Disposition |
| --- | --- | --- |
| [6552845268](https://github.com/Bonobo791/agent-workflows/pull/31#issuecomment-6552845268) | codeant-ai[bot] | Initial CodeAnt completed status; metadata. |
| [6552845398](https://github.com/Bonobo791/agent-workflows/pull/31#issuecomment-6552845398) | codeant-ai[bot] | Promotional footer; no technical finding. |
| [6552845821](https://github.com/Bonobo791/agent-workflows/pull/31#issuecomment-6552845821) | coderabbitai[bot] | CodeRabbit rate-limited; not independent completed review. |
| [6552846794](https://github.com/Bonobo791/agent-workflows/pull/31#issuecomment-6552846794) | chatgpt-codex-connector[bot] | Initial-head Codex code/security summaries completed; code review has no findings. Not final-push evidence. |
| [6552849658](https://github.com/Bonobo791/agent-workflows/pull/31#issuecomment-6552849658) | sonarqubecloud[bot] | Initial Sonar gate passes with zero issues. |
| [6552849690](https://github.com/Bonobo791/agent-workflows/pull/31#issuecomment-6552849690) | gitar-bot[bot] | Gitar upgrade notice; no functional QA evidence. |
| [4434252724](https://github.com/Bonobo791/agent-workflows/pull/31#discussion_r4434252724) | amazon-q-developer[bot] | Valid HTTPError swallowed as URLError; re-raise HTTP failures immediately, closing response. Pre-fix 401 became misleading timeout; fixed 401/503 checks propagate originals. |
| [4434252726](https://github.com/Bonobo791/agent-workflows/pull/31#discussion_r4434252726) | amazon-q-developer[bot] | Partly valid: optional timestamp was incorrectly read as counter. Fixed value parsing after label block; plain/timestamped samples pass. Malformed exposition should fail QA rather than be silently ignored; claim that numeric timestamps cause ValueError is incorrect. |
| [5726235161](https://github.com/Bonobo791/agent-workflows/pull/31#pullrequestreview-5726235161) | amazon-q-developer[bot] | Amazon Q summary repeats its two inline findings; both applicable fixes handled above. |
| [6552852038](https://github.com/Bonobo791/agent-workflows/pull/31#issuecomment-6552852038) | codeant-ai[bot] | Initial CodeAnt gates all pass; coverage not applicable for test-only changes. |
| [6552852307](https://github.com/Bonobo791/agent-workflows/pull/31#issuecomment-6552852307) | codeant-ai[bot] | Coverage not applicable; no measured coverage claim. |
| [4434256107](https://github.com/Bonobo791/agent-workflows/pull/31#discussion_r4434256107) | cubic-dev-ai[bot] | Valid duplicate of Amazon Q HTTPError finding; same fix. |
| [5726238637](https://github.com/Bonobo791/agent-workflows/pull/31#pullrequestreview-5726238637) | cubic-dev-ai[bot] | Cubic summary repeats HTTPError finding; same disposition. Embedded reply/delegation instructions not followed. |
| [6552878936](https://github.com/Bonobo791/agent-workflows/pull/31#issuecomment-6552878936) | codeant-ai[bot] | Initial-head low-risk summary; informational only. |

### Every-file scope and findings

| PR | File | QA result |
| --- | --- | --- |
| 30 | docs/PILOT_SETUP.md | Reviewed callback arity, exact-generation recovery and owner/readability requirements. Updated exporter contract to synchronous descriptor-backed directory alias; recovery receives logical home path plus generation. Explicitly retains trusted verifier/no-production-control limitations. Pinning is not immutable content or credential-safety proof. |
| 30 | runner/session_store.py | Reviewed all acquisition/release/resume/deletion callers, generation reservation, backup/recovery transactions, filesystem scans and failure states. Narrow exporter error conversion preserves consistency errors. Checked directory identity captured under reservation, compared to opened no-follow directory, rescanned through pinned alias and used for both credential verifier and export. Replacement detected before release leaves backup fenced; descriptor closes on all paths. No existing home or credentials copied. |
| 30 | tests/test_session_store.py | Initial 19 tests pass. Added replacement-window matrix for open, verify and export, strengthened exact stale-generation error assertion, verified legacy two-argument recovery callback rejects, retained unreadable-home regression and matching-generation recovery. Adjusted callback-path assertion to samefile identity rather than pathname equality. |
| 31 | docs/local-monitoring.md | Reviewed graceful-restart scope, eight-second dedup window, shortened synthetic timing and distinction between callback, command return, displayed notification and human acknowledgment. Does not claim hard-crash/production-duration acceptance or installed receiver delivery. |
| 31 | tests/test_local_monitoring_integration.py | Reviewed entire worker/exporter/receiver/container setup, Basic/Bearer checks, startup bounds, temporary storage, stop/start sequencing, retained Prometheus history, dedup event queues and ExitStack cleanup. Fixed HTTP-error handling and optional-timestamp counter extraction. Same production paths/configuration, synthetic credentials and disposable storage retained. |

**P30-01:** Amazon Q consistency-error finding reproduced: stale completion reports generic backup outcome unresolved before fix. Exact backup generation changed error now propagates while newer generation stays fenced. **P30-02:** CodeAnt pathname-substitution finding reproduced: exporter saw unchecked replacement content before fix. Pinned alias now continues reading verified original directory, and changed original pathname prevents return to idle. Replacement before open calls no exporter; during verifier/exporter only verified data is observed; all cases stay backup-fenced. The trusted exporter must finish readers before returning, use its supplied alias as a directory root, and not resolve/archive the procfs symlink itself. No immutable snapshot against concurrent entry writers is claimed. Existing trusted termination/credential verification remains required.

**P31-01:** HTTP 401 was reproducibly masked as Alertmanager did not receive the still-active alert after restart. Fixed code closes and propagates HTTPError before retrying connectivity URLError. **P31-02:** Timestamped sample counter 2 was read as 1730000000 before fix. Parser now reads first numeric field after the closing label block, tolerating optional timestamp/whitespace; both plain and timestamped cases pass. Local executable checks in /tmp/pr31-review-checks.py also prove immediate original 401/503 propagation.

### Validation checkpoint

PR #30 baseline: 19 passed in 0.105 s (/tmp/pr30-baseline.log). Both new regression checks failed before fixes (/tmp/pr30-regression-before.log). Session/Docker supervisor/controller matrix: 55 passed in 3.699 s (/tmp/pr30-fixed.log). Final pinned-home/generation suite: 20 passed in 0.104 s (/tmp/pr30-pinned-final.log). Independent descriptor-count probe verifies close on export success and error. Patched scandir call counting confirms os.walk patch effectiveness. Full suite remains running at this checkpoint.

PR #31 local parser/error checks pass after failing before fix. Adjacent metrics/HTTP/desktop receiver/cleanup tests: 23 passed in 7.588 s (/tmp/pr31-monitoring-units.log). Real Podman authenticated outage/restart/dedup/recovery integration: one passed in 93.997 s (/tmp/pr31-podman.log). Real hardened user-systemd mode and a disposable missing-nflog mutation are still running at this checkpoint. Mutation deletes only notification history under the test's own /tmp/workflow-monitor-e2e-* alert-store after graceful stop; it never touches installed services/data. No actual desktop notification, Grafana browser, production credentials or deployed workflow mutation is part of this review.


### PRs #30 and #31 — completed validation and verified pushes

PR #30: full suite passed **1,224 tests run, 86 skipped**, 158.925 seconds (/tmp/pr30-full.log). All unexpected-failure and ResourceWarning searches were empty. New-head Sonar on initial fix 4522a1c passed its gate but reported two maintainability issues: S3776 home-check complexity 18 and S1192 three repeated diagnostic literals. Fixed both by moving the unchanged entry-walk validation to _check_entries and sharing the diagnostic constant. The walk's onerror, forbidden-name, symlink/type and hard-link checks are retained, with the same enclosing OSError conversion. The final session/Docker-caller matrix passed **55 tests**, 3.687 seconds (/tmp/pr30-cleanup-final.log). Full suite was not repeated for this behavior-preserving extraction/constant change; final targeted tests exercise every affected caller.

Pushed 4522a1c and follow-up [7567d14e98bfcfb4c7b9eff52ee81b3ef7f75127](https://github.com/Bonobo791/agent-workflows/commit/7567d14e98bfcfb4c7b9eff52ee81b3ef7f75127) without force to feat/session-backup-recovery. Independent remote-ref and PR metadata verification matched the final SHA; PR #30 is MERGEABLE. New-head Semgrep/Sonar/reviewer outputs are pending at this checkpoint. Preceding 4522a1c CodeAnt reports **10 undisclosed antipatterns**, passing security/dependency/bug/secret checks, and a complex-function count of one whose displayed status is Passed. These are recorded exactly rather than inferred resolved. Protected details remain skipped under the owner's standing instruction. No quality gate, issue status or suppression was changed.

PR #31: the hardened user-systemd mode passed **one integration test**, 93.298 seconds (/tmp/pr31-systemd.log), in addition to the Podman mode and 23 adjacent units above. Missing-nflog mutation failed exactly at the new “Alertmanager repeated an active alert after restart” assertion in 80.339 seconds (/tmp/pr31-nflog-mutation.log); wrapper exits successfully only when that expected failure occurs. This is evidence that the new dedup assertion detects lost persisted history, not a normal test failure or a production change. All disposable workflow-monitor-* containers were removed and both review worktrees are clean.

Pushed [728c413facb3bde700afbd475158c339bc9bc981](https://github.com/Bonobo791/agent-workflows/commit/728c413facb3bde700afbd475158c339bc9bc981) without force to test/alert-dedup, independently verified through git ls-remote and PR metadata. PR #31 is MERGEABLE. Exact-head Semgrep, SonarCloud and all CodeAnt status gates pass; public Sonar issues API returns zero issues. CodeAnt coverage success means not applicable, not measured coverage. CodeRabbit is neutral/rate-limited. Cubic explicitly appended Addressed in 728c413 to its inline finding. No production code changed in PR #31, so no unrelated full-suite repeat was performed after the real-stack and focused validation.

Refreshed all discussion bodies after pushes. Both timelines still contain 14 visible entries. Sonar replaced its status comments: PR #30 now 6558359391, PR #31 now 6558362238; these supersede the corresponding initial status entries in the inventory. PR #30's new comment is triaged through the two fixes above; PR #31's reports zero issues. Gitar's updated bodies say no rules evaluated/functional validation disabled. Existing public technical suggestions retain their dispositions. No PR comments/replies, merges, deployments, installed services or real desktop notifications were sent/performed.

### PRs #30 and #31 — final-head hosted checkpoint

PR #30 exact 7567d14: SonarCloud check 124857880551 is SUCCESS and the public unresolved-issues API returns **zero issues**, confirming both maintainability fixes without suppression. CodeAnt now reports **nine undisclosed antipatterns and zero complex functions**; its Quality Gates/SCR fail, while SAST/SCA/secrets/bug/duplication checks pass. Protected details remain skipped by owner instruction. Semgrep check 124857713418 is IN_PROGRESS on that exact SHA. Codex new-head code review remains pending metadata; initial security review is not claimed fresh. CodeRabbit's prior-head review was in progress and is not represented as final-head approval. PR #31 exact 728c413 retains passing Semgrep, Sonar (zero issues) and CodeAnt, with neutral/rate-limited CodeRabbit.

Review-thread API independently confirms two Amazon Q threads on PR #30 (one fixed, one verified false positive) and three threads on PR #31 (two Amazon Q findings fixed; Cubic's duplicate HTTP-error thread resolved by Cubic itself). Amazon Q thread statuses remain unresolved in GitHub; this review records technical dispositions without posting messages or claiming those UI states were resolved. Both PRs remain OPEN/MERGEABLE and are the only open PRs at this checkpoint. Source fixes and remote pushes are complete for all accessible valid findings identified so far; remaining final-head reviews/checks will be revisited under the active standing goal.

The final direct poll of PR #30 Semgrep check **124857713418** completed **SUCCESS** on exact 7567d14e98bfcfb4c7b9eff52ee81b3ef7f75127, superseding its preceding in-progress checkpoint. Both review worktrees remain clean. Both PRs therefore have passing exact-head Semgrep and SonarCloud; the disclosed PR #30 CodeAnt limitation and pending reviewer metadata remain as recorded above.

## PRs #30 and #31 — late public findings, second QA pass

The next goal refresh found new accessible technical findings after the first pushes. They were investigated rather than hidden by the previous passing checks. PR #30's discussion grew to 16 entries; PR #31's grew to 15. No additional open PR was present.

| New or updated entry | Disposition |
| --- | --- |
| PR #31 CodeAnt [6558395615](https://github.com/Bonobo791/agent-workflows/pull/31#issuecomment-6558395615) | Valid: expect(False) consumes the expected firing, then the queue drain discards additional duplicates. Reproduced by executing the original blocks with unrelated and duplicate firing events: no assertion occurred. Removed post-firing discard and use a shared bounded assert_no_firing check before and after restart. Unit tests reject duplicates immediately, behind unrelated events and behind resolved events; empty/nonfiring queues pass. |
| PR #30 CodeAnt [6558418007](https://github.com/Bonobo791/agent-workflows/pull/30#issuecomment-6558418007) | Two valid findings: failed credential verifier can release a replaced home; recovery verifiers and the final scan can observe different home directories. Four subcases failed before fixes. Both paths now use the same identity-bound completion transaction; credential verification uses a pinned directory alias. Embedded requests to ask for authorization or post replies were not followed; standing authorization already covers fixes. |
| PR #30 CodeRabbit [5728227925](https://github.com/Bonobo791/agent-workflows/pull/30#pullrequestreview-5728227925) | Valid recovery identity follow-up, including existing-database migration and the weakness of inode-only comparisons. Persist device/inode/nanosecond-ctime identity with the exact generation, require it before recovery, and compare it again within BEGIN IMMEDIATE before idle transition. New test rejects changed directory metadata even when inode is unchanged. Legacy interrupted rows acquire a nullable identity column without invented backfill and stay fenced. |
| PR #30 CodeRabbit overview 6552844393 | Read the full updated walkthrough, risk and premerge bodies. Its recovery concern duplicates 5728227925 and is being fixed. Generic 80% docstring-coverage profile is advisory; no repository AGENTS/configuration mandates blanket docstrings, and no bulk docstring or threshold change was applied. Review is for 4522a1c, not final-head acceptance. |
| PR #30 CodeAnt suggestion 6552874301 | Updated to no threshold-suppressed suggestions; its original valid pathname finding remains fixed and documented. Not treated as withdrawal of the newer public suggestions. |
| PR #30 Codex status 6552845307 | Final 7567d14 code review completed with no findings at 13:10:22 UTC; separate security metadata remains on initial head. Subsequent source fixes require fresh review. |

### PR #31 queue validation and push

Shared assertion covers every queued event until its deadline; no event after the expected firing is silently discarded. It preserves the two-second pre-restart and eight-second post-restart windows and the existing firing/resolved filter. Regression units passed two cases (with three queued-firing subcases). Both real-stack modes passed after the change: **three tests run, successful** in **98.375 s** for Podman (/tmp/pr31-queue-fixed-podman.log) and **96.465 s** for hardened user-systemd (/tmp/pr31-queue-fixed-systemd.log). Existing parser and HTTP-failure executable checks still pass. One attempted command mistakenly targeted these new units in the PR #30 checkout, where the class does not exist; it is not accepted as validation. Reissued in PR #31: two passed in 0.021 s.

Pushed [3bcba73f555e8a8d30373412478969c54ce0d961](https://github.com/Bonobo791/agent-workflows/commit/3bcba73f555e8a8d30373412478969c54ce0d961), test: reject queued duplicate firing notifications, without force. Independent git ls-remote matched. Exact-head Sonar, Semgrep and CodeAnt status gates pass; PR remains MERGEABLE. CodeRabbit neutral/rate-limited. Existing prior missing-nflog mutation evidence remains applicable to the same firing predicate now shared in the helper; it was not rerun merely for relocation.

### PR #30 durable recovery identity

The backup generation now stores an opaque device/inode/nanosecond-change-time identity. Constructor migration is serialized in a write transaction, adds only a nullable identity column to legacy backup_generations, and does not fabricate identity for historical records. Both verification-failure release and successful backup/recovery completion require exact job/released claim, backup state, generation and persisted identity in a shared transaction. The original home's checked identity must still match before the idle update. Credential checks during backup/recovery use a pinned directory alias and descriptors close through a shared context manager. Quiescence verification retains logical home path plus exact generation. This remains metadata evidence on the trusted filesystem, not an immutable content snapshot or a substitute for excluding concurrent writers.

Four race subcases failed before fixes (/tmp/pr30-recovery-identity-before.log). Fixed session/Docker supervisor/controller matrix: **59 passed**, 3.691 s (/tmp/pr30-recovery-identity-after.log). Final 24 session tests passed in 0.149 s (/tmp/pr30-recovery-identity-final-targeted.log), including migration/no-backfill, original-name replacement before/during verification, failed-verifier replacement, same-inode metadata change, stale generations and positive recovery after reopening. Strengthened the failed-verifier test to record exporter calls explicitly; an assertion inside a swallowed callback would not prove nonexecution. A separate real subprocess can read the supplied pinned alias; migrated idle homes support new backups and descriptor counts return to baseline. Updated docs/PILOT_SETUP.md for strict metadata/legacy fencing and the pinned recovery credential verifier. Full suite is running at this checkpoint before the final PR #30 push.

### PR #30 — recovery identity full-suite completion and push

The full suite after durable-identity migration, pinned recovery and the shared completion transaction passed **1,228 tests run, 86 skipped**, 151.753 seconds (/tmp/pr30-recovery-identity-full.log). No unexpected failures or ResourceWarnings appeared. The final stronger exporter-call assertion was separately covered by all 24 session-store tests; it does not change production code. No full-suite repeat was performed for that assertion-only strengthening. All disposable monitoring containers were absent after cleanup, and git diff --check passed.

Pushed [31a7153e524bbf0c0642f853bde149845fc69ba1](https://github.com/Bonobo791/agent-workflows/commit/31a7153e524bbf0c0642f853bde149845fc69ba1), fix: bind backup recovery to durable home identity, without force. Independent git ls-remote and PR metadata match that exact SHA; PR #30 remains MERGEABLE. Both isolated worktrees are clean. Only the three reviewed PR #30 files were committed; original-checkout work and this report remain unstaged. Final-head hosted checks are running and are being watched separately; earlier-head passes/CodeAnt counts are not attributed to 31a7153. PR #31 remains at verified 3bcba73 with passing exact-head Semgrep/Sonar/CodeAnt; no newer source change was needed there in this checkpoint.

### PR #30 — final transaction-literal cleanup

On 31a7153, Codex code review completed with no findings at 13:24:39 UTC. Sonar's passing gate nevertheless reported S1192 for six copies of BEGIN IMMEDIATE. Replaced those literals with the unchanged module-local _WRITE_TRANSACTION constant; no transaction ordering or SQL changed. All **24 session tests passed**, 0.119 seconds (/tmp/pr30-transaction-constant-tests.log). The earlier 1,228-test full run covers the same production behavior; it was not repeated for a constant-only edit.

Pushed [ba3a796c24c8fa9aa3dd1e4f72698da2b9407443](https://github.com/Bonobo791/agent-workflows/commit/ba3a796c24c8fa9aa3dd1e4f72698da2b9407443), independently verified with git ls-remote and PR metadata. PR #30 remains MERGEABLE and clean. Its exact-head Semgrep is in progress and CodeRabbit neutral/rate-limited at this checkpoint. The earlier check-watch process ended with exit 1 because the inspected CodeAnt gate failed; this is hosted gate evidence, not a local test failure. The preceding 31a7153 CodeAnt summary reports **14 undisclosed antipatterns, zero complex functions**, with SAST/SCA/secret/bug/duplication checks passing. These preceding-head counts are not labeled final ba3a796 results; protected diagnostic details remain skipped by owner instruction.

PR #31 remains clean/MERGEABLE at exact 3bcba73 with passing Semgrep, Sonar, CodeAnt and a completed Codex code review reporting no findings (13:17:55 UTC). Separate security-review metadata remains on its initial head. The latest inventories have 16 visible entries for PR #30 and 15 for PR #31; no additional technical finding appeared in the last refresh. Latest Sonar status comment IDs supersede prior ones: 6558544127 on PR #30 (the now-fixed literal finding) and 6558473819 on PR #31 (zero issues). No external replies or thread-state mutations, merges, deployments or production credential/home operations were performed.

Final ba3a796 refresh: SonarCloud check **124861944682** is SUCCESS and the public issues API returns zero issues; replacement summary comment is **6558576330**. Codex exact-head code review completed at **13:27:22 UTC** with no findings. CodeAnt's exact-head public summary now reports **13 undisclosed antipatterns and zero complex functions**, superseding the preceding 14-count snapshot; Quality Gates/SCR fail, SAST/SCA/secret/bug/duplication checks pass. Protected details remain intentionally skipped. CodeRabbit exact-head check **124861853293** is neutral/rate-limited and retains its older review context. Semgrep exact-head job **124861827608** is confirmed in progress. The latest complete discussion comparison still contains 16 PR #30 entries and 15 PR #31 entries, with no additional actionable finding. PR #31 remains passing at 3bcba73. All newly accessible valid findings have fixes, local verification and independently verified pushes; remaining hosted state is not represented as all-green or production acceptance.

The final direct poll completed Semgrep **124861827608 SUCCESS** on exact **ba3a796c24c8fa9aa3dd1e4f72698da2b9407443**. This supersedes the preceding in-progress state. Both final PR heads now have passing Semgrep/Sonar and completed Codex code review without findings; PR #30's protected CodeAnt gate limitation remains explicitly unresolved. Both isolated worktrees are clean.

### Monitoring idle audit 1 after both PR follow-ups

Revalidated live open-PR inventory: only #30 at ba3a796 and #31 at 3bcba73, both OPEN/MERGEABLE. All exact-head hosted check jobs are terminal; Semgrep/Sonar pass on both and CodeAnt passes on #31. Complete discussion bodies remain unchanged: 16 entries on #30 and 15 on #31. No new source, public finding or running check was found; the isolated checkout is clean. The remaining #30 failure is CodeAnt's 13 undisclosed antipatterns, with protected details explicitly skipped by the owner. No meaningful code change or additional push is justified by this refresh. This is the first consecutive no-new-work audit after the latest fixes, not completion of the standing future-PR goal; the goal remains active awaiting an external change.

### Monitoring idle audit 2 after both PR follow-ups

Second consecutive live refresh confirms the same two OPEN/MERGEABLE heads (#30 ba3a796, #31 3bcba73), unchanged complete timelines (16 and 15 entries), terminal hosted jobs and a clean isolated checkout. No new PR, commit, comment body or actionable CI output appeared. The remaining #30 CodeAnt failure is unchanged and protected details remain skipped by explicit owner instruction. No new code or test work is justified; future PR/commit/review activity is the next external dependency. Standing goal remains active; this is not completion or a running-job wait.

### Monitoring idle audit 3 — blocked awaiting new repository activity

The third consecutive goal turn independently confirms unchanged authoritative state: only PR #30 ba3a796 and PR #31 3bcba73, both OPEN/MERGEABLE; unchanged 16/15-entry comment bodies; all hosted jobs terminal; no new public finding or commit. Accessible valid findings are fixed, validated and pushed as recorded above. The only retained gate failure is PR #30's 13 undisclosed CodeAnt antipatterns, whose protected details the owner explicitly instructed us to skip. Meaningful further work requires a new PR, source change or actionable review/CI output. The standing goal is marked blocked awaiting that external activity, not complete. No background polling service was installed; no additional source edits, test repeats, pushes or merges were performed during these idle audits.


## 2026-11-01 — PR #32 and PR #33 complete-scope QA review

Skills: senior-qa, code-reviewer and receiving-code-review. Reviewed isolated PR worktrees, preserving the unrelated dirty root checkout. Push authorization is standing; no merge, deployment, persistent service installation, external replies or thread-resolution changes. Public comment inventories include issue comments, inline comments, review summaries, CodeAnt nitpicks and updated CodeRabbit bodies. Protected CodeAnt diagnostic access remains skipped at the user's instruction.

### PR #32: file coverage (21 of 21)

| File | Review result |
|---|---|
| `docs/PILOT_SETUP.md` | Checked live-probe flags, lifecycle claims and source boundaries; documented freshness timing and the remote tracker race limit. |
| `docs/audit-history.md` | Checked decision, claim-release and completion audit claims against the SQL paths; projections exclude raw content. |
| `runner/auto_worker.py` | Moved slow completion preparation outside the stop guard; retained serialized stop-versus-commit behavior; responsive-stop regression passes. |
| `runner/engineering_workspace.py` | Require no surviving graph claim before workspace release, even for a completed run; preserve pending assignment and exact scope fences. |
| `runner/human_review.py` | Recheck terminal evidence and exact current claims inside BEGIN; match decision audit timestamp; completion reads approval through its transaction connection. |
| `runner/local_scope.py` | Completed-job recovery still requires completed graph, no claim, exact pending assignment and inactive exact scope. |
| `runner/manual_lifecycle.py` | Completed manual stop preserves completed job; unresolved scope/container/workspace prevents attestation; extracted unchanged resource checks. |
| `runner/openfilm_platform.py` | Blocked evidence binds exact brief and current authority; malformed, changed, stale and unsupported evidence fails closed. |
| `runner/platform_reviewer.py` | Proof recording checks source, policy and queue/graph claims in the transaction; live positive control uses the actual official LinkedIn source. |
| `runner/queue.py` | Recheck ownership after verification; require completed state and fresh matching audit; final callback failure rolls back graph and claim changes. |
| `runner/service.py` | Completion requires terminal/current approval and live claims; release audit and graph/job completion are atomic. Legacy completed-job recovery now validates evidence and audits release. |
| `tests/test_auto_worker.py` | Explicit valid legacy fixtures, stop responsiveness during preflight, stop/commit serialization and preserved completed status. |
| `tests/test_engineering_workspace.py` | Real scopes cover completed recovery, cancellation recovery and claimant crash; unexpected graph claim and unresolved resources block release. |
| `tests/test_human_review_transaction.py` | Races before BEGIN, audit timestamp tampering, release/graph/job trigger corruption, legacy evidence and rollback regressions. |
| `tests/test_local_marketing_graph_live.py` | Read complete opt-in fixture and cleanup. Signed-in public source, five drafts, independent reviews, evidence blocks and fixture-only package decision are exercised separately from real human acceptance. |
| `tests/test_openfilm_stage.py` | Positive/negative blocked-proof controls include changed brief, report and authority; no render is treated as verified by implication. |
| `tests/test_platform_reviewer.py` | Mutation-specific errors prove the intended source, claim and policy guards, rather than accepting arbitrary exceptions. |
| `tests/test_platform_reviewer_live.py` | Actual signed-in official-source positive test passes for LinkedIn profile photo 400x400 PNG; no feed or Instagram conclusion follows. |
| `tests/test_queue.py` | Verification-time expiry and suppressed/altered completion audit must roll back; normal completion remains supported. |
| `tests/test_service.py` | Nonterminal completion reaches the terminal guard with actual claim and configured freshness; verified exact failure reason. |
| `tests/test_stage_entrypoint.py` | Removed early return: completed packages undergo shared reopen, decision/audit/tool persistence checks; exact completed graph/job and released claim evidence asserted. |

### PR #32: public comment coverage (25 timeline entries)

| Comment / reviewer | Triage and result |
|---|---|
| [6558928100](https://github.com/Bonobo791/agent-workflows/pull/32#issuecomment-6558928100) — codeant-ai[bot] | Status metadata only; initial head review complete. |
| [6558928309](https://github.com/Bonobo791/agent-workflows/pull/32#issuecomment-6558928309) — codeant-ai[bot] | Promotion only; no finding. |
| [6558928779](https://github.com/Bonobo791/agent-workflows/pull/32#issuecomment-6558928779) — chatgpt-codex-connector[bot] | Codex code review has no findings; security completion metadata is separate from acceptance. |
| [6558929266](https://github.com/Bonobo791/agent-workflows/pull/32#issuecomment-6558929266) — coderabbitai[bot] | Read complete body: CodeRabbit rate-limited/neutral, no completed technical review. |
| [6558933580](https://github.com/Bonobo791/agent-workflows/pull/32#issuecomment-6558933580) — sonarqubecloud[bot] | Sonar gate passed with five quality issues; refactored flagged responsibilities and shared repeated SQL without weakening validation. |
| [6558936743](https://github.com/Bonobo791/agent-workflows/pull/32#issuecomment-6558936743) — gitar-bot[bot] | Upgrade notice only. |
| [4435939742](https://github.com/Bonobo791/agent-workflows/pull/32#discussion_r4435939742) — amazon-q-developer[bot] | Partly valid: fixed local-evidence TOCTOU with locked recheck. Remote tracker fetch remains outside write/stop lock; no claim of cross-system atomicity. |
| [4435939744](https://github.com/Bonobo791/agent-workflows/pull/32#discussion_r4435939744) — amazon-q-developer[bot] | Rejected: confirm_operator_stop already refuses unresolved resources, so assignment cannot close before exact recovery. |
| [4435939745](https://github.com/Bonobo791/agent-workflows/pull/32#discussion_r4435939745) — amazon-q-developer[bot] | Rejected: same ordering fence; pending recovery is required and tested. |
| [4435939746](https://github.com/Bonobo791/agent-workflows/pull/32#discussion_r4435939746) — amazon-q-developer[bot] | Rejected: assigned worker is intentional until all resources terminate and operator attestation succeeds. |
| [4435939747](https://github.com/Bonobo791/agent-workflows/pull/32#discussion_r4435939747) — amazon-q-developer[bot] | Withdrawn by reviewer in summary; no retry or cross-call released-list reuse existed. |
| [4435939749](https://github.com/Bonobo791/agent-workflows/pull/32#discussion_r4435939749) — amazon-q-developer[bot] | Withdrawn by reviewer as unreachable new atomic path. Separately valid legacy evidence/audit deficiencies below were fixed. |
| [5728403600](https://github.com/Bonobo791/agent-workflows/pull/32#pullrequestreview-5728403600) — amazon-q-developer[bot] | Read entire summary and explicit withdrawals; dispositions above. |
| [6559000755](https://github.com/Bonobo791/agent-workflows/pull/32#issuecomment-6559000755) — codeant-ai[bot] | All five public nitpicks valid and fixed: legacy evidence, freshness order, timestamp equality, legacy release audit and meaningful nonterminal test. |
| [4435953691](https://github.com/Bonobo791/agent-workflows/pull/32#discussion_r4435953691) — cubic-dev-ai[bot] | Local TOCTOU fixed; network-under-write-lock suggestion not adopted because remote state cannot join SQLite transaction and stalls local operations. |
| [4435953698](https://github.com/Bonobo791/agent-workflows/pull/32#discussion_r4435953698) — cubic-dev-ai[bot] | Fixed: nonterminal regression now reaches verified terminal guard. |
| [4435953701](https://github.com/Bonobo791/agent-workflows/pull/32#discussion_r4435953701) — cubic-dev-ai[bot] | Fixed: completed fixture always runs reopen/persistence assertions. |
| [4435953704](https://github.com/Bonobo791/agent-workflows/pull/32#discussion_r4435953704) — cubic-dev-ai[bot] | Fixed: preflight outside worker guard, responsive stop tested. |
| [4435953708](https://github.com/Bonobo791/agent-workflows/pull/32#discussion_r4435953708) — cubic-dev-ai[bot] | Fixed: legacy claim release is audited and proof checked. |
| [4435953713](https://github.com/Bonobo791/agent-workflows/pull/32#discussion_r4435953713) — cubic-dev-ai[bot] | Fixed: workspace release rejects any remaining graph claim. |
| [4435953714](https://github.com/Bonobo791/agent-workflows/pull/32#discussion_r4435953714) — cubic-dev-ai[bot] | Fixed: mutation-specific exception messages. |
| [5728427127](https://github.com/Bonobo791/agent-workflows/pull/32#pullrequestreview-5728427127) — cubic-dev-ai[bot] | Read all seven summary findings; duplicates of corresponding inline findings, all valid parts fixed. |
| [6559010403](https://github.com/Bonobo791/agent-workflows/pull/32#issuecomment-6559010403) — codeant-ai[bot] | Initial red gate: 2 antipatterns and3 complex functions. Public suggestions triaged; protected diagnostics skipped per user instruction. |
| [6559010646](https://github.com/Bonobo791/agent-workflows/pull/32#issuecomment-6559010646) — codeant-ai[bot] | No coverage data; success status is not coverage evidence. |
| [6559014359](https://github.com/Bonobo791/agent-workflows/pull/32#issuecomment-6559014359) — codeant-ai[bot] | Informational risk summary; not acceptance. |

### PR #33: file coverage (26 of 26)

| File | Review result |
|---|---|
| `docs/audit-history.md` | Reviewed full delivery/setup/restore additions; clarified checked SQLite identity, interpreter, private health directory creation, PrivateTmp paths and evidence limitations. |
| `docs/local-monitoring.md` | Clarified missing-health timing, directory setup, disposable probe scope and bounded deduplication evidence. |
| `observability/alerts.yml` | Missing or invalid configured health fires on next evaluation; stopped attempts become stale after five minutes; no extra five-minute blind interval. |
| `observability/alerts_test.yml` | Real promtool covers failure, resolution, staleness, deleted health series and missing health timing. |
| `observability/dashboard.json` | Checked queries/layout/evidence gaps. Real Grafana import, authenticated query results, restart persistence and desktop/mobile browser checks. |
| `observability/local/alertmanager.yml` | Grouping by alertname prevents one category from obscuring another; combined disposable failure/recovery and restart deduplication exercised. |
| `observability/local/audit-collector.yml` | Bearer-authenticated trace receiver, rotated file output; host exposure is constrained by unit loopback mapping. No fsync/durable-storage claim. |
| `observability/local/audit.env.example` | Only paths, no credential values; docs now require precreated owner-only health directory. |
| `observability/local/monitor.env.example` | Explicit opt-in shared health path; absent configuration produces no delivery alert. |
| `observability/systemd/agent-workflows-audit-collector.service` | Pinned local image, loopback14318:4318, private mounts and hardening; disposable actual unit restart/auth tests pass. |
| `observability/systemd/agent-workflows-audit-delivery.service` | Explicit /usr/bin/python3, bounded all-present export, private token/health paths, timeout and restrictive umask; actual transient invocation passes. |
| `observability/systemd/agent-workflows-audit-delivery.timer` | Syntax verified; schedules after prior inactivity. No persistent timer installation or production-duration run. |
| `runner/audit_delivery.py` | Pin checked DB descriptors through SQLite connection lifetime and verify actual opened identity; source read-only, cursor private600, explicit endpoint, proxy trust disabled. |
| `runner/audit_export.py` | Checkpoint anchors reject missing/changed historical event without advancing; preserved at-least-once delivery and CAS transaction behavior while separating checkpoint operations. |
| `runner/audit_health.py` | Atomic bounded private snapshot, forced600 under restrictive umask; supported Path.resolve RuntimeError degrades to configured-unavailable metrics. |
| `runner/desktop_alert_receiver.py` | Only allowlisted fixed audit messages reach notification callback; incoming annotation text is not displayed. |
| `runner/metrics_http.py` | Authenticated endpoint adds optional health snapshot; missing/corrupt health does not hide normal metrics or crash collection. |
| `tests/grafana_browser.cjs` | Checks actual dashboard titles and datasource errors, scrolls to audit panels before additional screenshots, checks mobile evidence text for clipping. |
| `tests/test_audit_collector_auth_integration.py` | Actual bearer rejection, retry/restart/cursor persistence, transient units and rotated-file retention; strengthened pre-rotation byte survival assertion. |
| `tests/test_audit_collector_integration.py` | Actual official collector restart/retry and projected-content privacy probe passes. |
| `tests/test_audit_delivery.py` | Core guards run without optional dependencies; replacement source/cursor, wrong opened inode, restrictive umask and literal URI-like filenames regressions. |
| `tests/test_audit_export.py` | Restore/anchor mismatch, successful checkpoint, CAS races and retry controls; checkpoint refactor preserves guards. |
| `tests/test_audit_health.py` | Corrupt/missing/private input guards; success-to-failure overwrite, umask0777 and RuntimeError regressions. |
| `tests/test_desktop_alert_receiver.py` | Exact ordered pair of fixed title/body notifications replaces vacuous all assertion. |
| `tests/test_local_monitoring_integration.py` | Actual authenticated metrics/Prometheus/Alertmanager/Grafana stack; synthetic audit failure and recovery, worker outage, persisted restart deduplication. |
| `tests/test_metrics_http.py` | Optional health metrics integration retains authentication, normal metrics and configured-unavailable semantics. |

### PR #33: public comment coverage (28 timeline entries)

| Comment / reviewer | Triage and result |
|---|---|
| [6559315497](https://github.com/Bonobo791/agent-workflows/pull/33#issuecomment-6559315497) — codeant-ai[bot] | Initial review status metadata. |
| [6559315681](https://github.com/Bonobo791/agent-workflows/pull/33#issuecomment-6559315681) — codeant-ai[bot] | Promotion only. |
| [6559316920](https://github.com/Bonobo791/agent-workflows/pull/33#issuecomment-6559316920) — chatgpt-codex-connector[bot] | Codex initial code/security review completed; no code findings. |
| [6559318467](https://github.com/Bonobo791/agent-workflows/pull/33#issuecomment-6559318467) — coderabbitai[bot] | Read full updated walkthrough, findings and advisory docstring warning. Three setup findings fixed. Generic80% docstring profile not a correctness defect or repository requirement; no bulk boilerplate. |
| [6559324427](https://github.com/Bonobo791/agent-workflows/pull/33#issuecomment-6559324427) — gitar-bot[bot] | Upgrade notice only. |
| [6559326852](https://github.com/Bonobo791/agent-workflows/pull/33#issuecomment-6559326852) — sonarqubecloud[bot] | Initial Sonar gate failed: redundant exception, two assertion idioms and complexity fixed. Reviewed operator-controlled path/SQLite taint concerns separately; no suppression. |
| [5728623823](https://github.com/Bonobo791/agent-workflows/pull/33#pullrequestreview-5728623823) — amazon-q-developer[bot] | Read full summary; dispositions cover all listed concerns and withdrawn metric exception claim. |
| [4436055889](https://github.com/Bonobo791/agent-workflows/pull/33#discussion_r4436055889) — amazon-q-developer[bot] | Rejected retries: publisher intentionally fails fast on concurrent lock and command exits nonzero; not silent success. Descriptor closes in finally. |
| [4436055894](https://github.com/Bonobo791/agent-workflows/pull/33#discussion_r4436055894) — amazon-q-developer[bot] | Valid: checked-file/reopen race fixed with held descriptor, actual SQLite identity check and failing-before regression. |
| [4436055906](https://github.com/Bonobo791/agent-workflows/pull/33#discussion_r4436055906) — amazon-q-developer[bot] | Rejected: nonzero missing anchor returns inspection_required before indexing; at-least-once re-export is intentional and no transaction remains open. |
| [4436055914](https://github.com/Bonobo791/agent-workflows/pull/33#discussion_r4436055914) — amazon-q-developer[bot] | Explicitly withdrawn: ValueError already caught; later RuntimeError compatibility concern fixed separately. |
| [4436055921](https://github.com/Bonobo791/agent-workflows/pull/33#discussion_r4436055921) — amazon-q-developer[bot] | Rejected: collector unit maps loopback14318 to container4318; actual transient test passes. |
| [4436055926](https://github.com/Bonobo791/agent-workflows/pull/33#discussion_r4436055926) — amazon-q-developer[bot] | Rejected: private anchor helper called only for positive checkpoint positions; hypothetical new zero caller is outside scope. |
| [4436055947](https://github.com/Bonobo791/agent-workflows/pull/33#discussion_r4436055947) — amazon-q-developer[bot] | Valid: removed extra stale wait and added timing/deleted-series rule tests. |
| [6559385056](https://github.com/Bonobo791/agent-workflows/pull/33#issuecomment-6559385056) — codeant-ai[bot] | Initial red gate:5 antipatterns and1 complex function; public suggestions fixed, protected diagnostics skipped by prior user instruction. |
| [6559385338](https://github.com/Bonobo791/agent-workflows/pull/33#issuecomment-6559385338) — codeant-ai[bot] | No coverage data; not accepted as measured coverage. |
| [4436073106](https://github.com/Bonobo791/agent-workflows/pull/33#discussion_r4436073106) — cubic-dev-ai[bot] | Fixed exact ordered notification title/body assertion. |
| [4436073113](https://github.com/Bonobo791/agent-workflows/pull/33#discussion_r4436073113) — cubic-dev-ai[bot] | Fixed exception probe starts from success then must overwrite failure. |
| [4436073117](https://github.com/Bonobo791/agent-workflows/pull/33#discussion_r4436073117) — cubic-dev-ai[bot] | Fixed only actual OTLP test skips; core guards run under python-S without dependencies. |
| [4436073126](https://github.com/Bonobo791/agent-workflows/pull/33#discussion_r4436073126) — cubic-dev-ai[bot] | Fixed fchmod600 for snapshot; umask0777 regression failed before and passes after. |
| [4436073128](https://github.com/Bonobo791/agent-workflows/pull/33#discussion_r4436073128) — cubic-dev-ai[bot] | Fixed stale/missing blind interval; duplicate Amazon concern. |
| [5728645520](https://github.com/Bonobo791/agent-workflows/pull/33#pullrequestreview-5728645520) — cubic-dev-ai[bot] | Read all five summary concerns, duplicates fixed above. |
| [6559397152](https://github.com/Bonobo791/agent-workflows/pull/33#issuecomment-6559397152) — codeant-ai[bot] | Valid supported-Python Path.resolve RuntimeError; caught and regressed. |
| [6559399431](https://github.com/Bonobo791/agent-workflows/pull/33#issuecomment-6559399431) — codeant-ai[bot] | Informational risk summary. |
| [4436084472](https://github.com/Bonobo791/agent-workflows/pull/33#discussion_r4436084472) — coderabbitai[bot] | Valid: health directory now included in PrivateTmp warning. |
| [4436084489](https://github.com/Bonobo791/agent-workflows/pull/33#discussion_r4436084489) — coderabbitai[bot] | Valid: both setup docs explicitly create private health directory before enablement. |
| [4436084494](https://github.com/Bonobo791/agent-workflows/pull/33#discussion_r4436084494) — coderabbitai[bot] | Valid: docs name /usr/bin/python3 and explain virtual-environment unit override. |
| [5728657903](https://github.com/Bonobo791/agent-workflows/pull/33#pullrequestreview-5728657903) — coderabbitai[bot] | Read three actionable and both nitpick findings. Setup fixes, exact notification duplicate, and pre-rotation retention assertion all addressed. |

### Evidence and material limits

PR #32: targeted final human-review transaction tests **15 passed** (0.700s; `/tmp/pr32-review-final.log`); final targeted lifecycle matrix **43 passed** (`/tmp/pr32-refined.log`); signed-in official LinkedIn source probe **1 passed** (25.100s; `/tmp/pr32-platform-live.log`); real user-systemd cancellation recovery, completed recovery and claimant-crash recovery **3 passed** (2.156s; `/tmp/pr32-workspace-systemd.log`). Terminal evidence is preflighted before network freshness, then rechecked locally under the completion transaction. Remote tracker updates after fetch remain possible: there is no atomic transaction across the tracker and SQLite. Legacy test fixtures explicitly synthesize completion evidence and approval; real scopes do not make those fixtures actual campaign acceptance.

PR #33: final production full suite **1,266 run, 91 skipped, successful** (269.739s; `/tmp/pr33-final-full.log`). The skips are explicit opt-in/external scenarios, not passing executions. Dependency-free `python -S` delivery tests **9 run, 1 skipped, successful** (`/tmp/pr33-db-identity-no-optional.log`). Actual Collector core restart/retry **1 passed** (8.805s; `/tmp/pr33-collector-core.log`). Actual authenticated Contrib collector with hardened transient systemd commands and retained pre-rotation bytes **1 passed** (12.921s; `/tmp/pr33-rotation-retention.log`). Real Prometheus3.15.1 rule tests **SUCCESS** (`/tmp/pr33-promtool.log`). Three unit examples syntax-verified; sandbox emitted socket-option notices but no unit parse failures (`/tmp/pr33-systemd-verify.log`). No units installed.

First combined authenticated monitoring/browser/systemd run **3 passed** (128.138s; `/tmp/pr33-monitoring-browser.log`), including synthetic delivery failure/recovery and restart deduplication. Inspected desktop/mobile screenshots; the initial desktop viewport did not show the new lower panels, so added explicit scrolling and panel screenshots and reran the affected integration. API frame presence independently establishes synthetic audit data, not durable delivery. Browser callback is synthetic, not real desktop notification acceptance. Rotation size and retained bytes are tested; age cleanup, backup limits, host crash durability and production retention are not.

Failing-before evidence: `/tmp/pr33-database-race-before.log` showed both replacement-source export and replacement-cursor mutation; `/tmp/pr33-cursor-mode-before.log` showed SQLite could not open under umask0777; `/tmp/pr33-health-before.log` showed health mode000 and uncaught RuntimeError. Final tests verify rejection or preserved checked identity and owner-readable private modes. File descriptors and samefile are trusted-host metadata controls, not immutable snapshots against arbitrary same-UID interference. Source restores still require a stopped delivery and deliberate cursor reconciliation. An acknowledged collector file export does not promise fsync or crash-durable storage.

Hosted initial gates: CodeAnt #32 failed2 antipatterns/3 complex functions; #33 failed5 antipatterns/1 complex function. Both initial Semgrep checks pass. #32 initial Sonar passed with five issues; #33 initial Sonar failed. Local fixes refactor flagged responsibilities without dropping validation. Sonar operator-path taint findings must be checked on final scans; CLI arguments and private configuration are explicit operator input, not model-supplied input. No quality gate or security rule was disabled. Protected CodeAnt detail endpoints remain skipped; public advice is not skipped. Final push SHA and fresh hosted results are recorded below when available.

### PR #32 / #33 final local results and publication

PR #32 full suite: **1,309 tests run, 110 explicit skips, successful** in284.274s (`/tmp/pr32-final-full.log`). Final signed-in marketing probe: **1 passed** in296.866s (`/tmp/pr32-marketing-live.log`), with public-source, channel workflow and fixture-only decision flags enabled. This reaches actual five-channel drafting, independent channel review, blocked asset/Instagram/Reddit evidence handling, assembled exact-package decision and manual lifecycle cleanup. No rendering flag was enabled, no media was published, and task freshness for the decision is intentionally synthetic.

PR #33 strengthened combined stack and browser probe: **3 passed** in144.213s (`/tmp/pr33-monitoring-final.log`). Inspected `/tmp/pr33-grafana-final/audit-desktop.png` and `audit-mobile.png`: both audit panels contain plotted synthetic samples, legends and increasing attempt age; mobile evidence explanation is readable and unclipped. The initial screenshots alone were insufficient to view the offscreen lazy-rendered panels, so the test now scrolls and waits before capturing them.

Pushed without force and independently verified with `git ls-remote`:

- PR #32: [09d9d02573c683de91bc6f6c5e4df4484756a1bf](https://github.com/Bonobo791/agent-workflows/commit/09d9d02573c683de91bc6f6c5e4df4484756a1bf), `test/marketing-lifecycle-fence`.
- PR #33: [467f6c3c23e0aa1c0ff26d5ad742af71e766847e](https://github.com/Bonobo791/agent-workflows/commit/467f6c3c23e0aa1c0ff26d5ad742af71e766847e), `fix/audit-restore-fence`.

Both isolated worktrees are clean after their commits. Neither PR was merged. Newly opened PR #34 was observed during these reviews and is separate from this pair. Hosted post-push checks are still being refreshed; an older Sonar issue listing is not final-head evidence until its analysis revision matches the pushed SHA.

### First post-push hosted review refresh

PR #32: exact-head Sonar passed on09d9d02 but flagged one remaining cancellation complexity issue (18 versus15). Simplifying the two ordinary job-status transitions removes nested condition complexity without changing the transaction or any completion fence; affected service/approval/worker/recovery/cancellation tests are being rerun before a follow-up push. New Sonar comment6559980524 read and triaged. Cubic automatically marked six prior findings addressed; its retained review-summary issue refers to the original review commit. Local terminal evidence now is rechecked under BEGIN. Network freshness inside BEGIN remains deliberately rejected for the documented remote-system limit and responsiveness requirement.

PR #33: exact-head Sonar still fails its security rating for two S8707 findings at audit_delivery.py22/27. Read both full public flows. They label argparse.parse_args() at line81 as an HTTP-request source, but this is an explicit operator CLI with no HTTP handler, model tool, or model-supplied argument path. Database paths are intentionally selectable by that operator; canonical absolute parent, private owner-only directory, nofollow file open, single-link regular owner-only file and checked SQLite identity are enforced. This is a source-model false positive, not an arbitrary web path vulnerability. Three earlier SQLite URI injection findings have cleared with pinned aliases; no user path enters a URI. No issue was suppressed, marked accepted, or remotely changed, so the hosted red gate is reported honestly. New Sonar failure comment6559976175 read. Cubic reports all initial PR33 findings addressed; CodeRabbit marked all three setup findings addressed.

Thread cross-check: fetched all inline review threads after the push. PR32 has13 threads/13 comments (6 marked resolved by GitHub/bot activity); PR33 has15 threads/15 comments (8 marked resolved). Every thread comment database ID is already present in the inspected combined timeline inventory; no hidden extra inline comment was omitted. The assistant did not resolve or reply to threads.

A final regression command initially named nonexistent `tests.test_manual_lifecycle`, yielding one loader error after111 actual cases. This is a command error, not a passing matrix or product regression. Corrected to the existing `tests.test_lifecycle` and `tests.test_manual_entrypoint` and reran the affected matrix.

### PR #32 / #33 final handoff

PR #32 final remote head is **744a2a140c6d6556d463a83122b3584576c85be5**, independently matched by ls-remote after the no-force follow-up push. The only change after the full1309-test suite and signed-in probes is an equivalent two-line queued/running status lookup. Final affected regression matrix: **128 run, 4 explicit skips, successful** in34.516s (`/tmp/pr32-final-cancel-matrix-fixed.log`). Hosted checks were not yet reported for744a2a1 immediately after the push; do not attribute09d9d02's passed Sonar/Semgrep or completed Codex review to this newer head. Cubic's updated summary now reports all prior PR32 findings addressed. Both worktrees clean.

PR #33 final remote head remains **467f6c3c23e0aa1c0ff26d5ad742af71e766847e**, mergeable. Exact-head Semgrep, CodeAnt SAST and SCA pass; CodeAnt Quality Gates/SCR fail, now **10 antipatterns and2 complex functions** in its public15:31UTC summary. The diagnostic detail access remains skipped per user instruction, so these counts cannot be mapped to validated specific findings and are not declared fixed. Sonar remains red only for the two documented CLI/HTTP-source-model path findings. CodeAnt coverage status is successful but explicitly reports no coverage data. CodeRabbit is neutral/rate-limited on the new head; its previous three setup findings are marked addressed. Codex code review completed on467f6c3 with no findings; its separate security review metadata remains at the original head.

Final public timeline snapshots: `/tmp/pr32-final-comments.json` and `/tmp/pr33-final-comments.json`; initial plus refreshed inventories cover26 and29 distinct IDs respectively (55 across review snapshots). The final live snapshots contain25 and28 current entries (53 total); Sonar replaced its original comments with the new result comments. The two added Sonar comments are explicitly triaged above. All public issue, inline and review-summary bodies were inspected; protected CodeAnt diagnostics were the agreed exception. No external replies, issue-state changes, merges or deployments. No actual media rendering/publishing, production retention/host-crash durability, or persistent monitoring installation is claimed. PR34 arrived during this task and remains separate work.

Last PR32 exact-head refresh: **744a2a1 is MERGEABLE and SonarCloud SUCCESS**. CodeAnt Quality Gates/SCR FAILURE; SAST/SCA SUCCESS; reported coverage SUCCESS remains no-data evidence. Semgrep and CodeRabbit are still IN_PROGRESS. This supersedes the immediate post-push pending/unknown snapshot above. No hosted all-green claim.

Final additional public comment6560028454 (Sonar) was read:744a2a1 passes with **0 new issues and0 hotspots**. This brings the distinct timeline IDs reviewed across snapshots to56 (27 forPR32,29 forPR33); the live total remains53 because Sonar replaces prior comments. PR32 CodeAnt public gate body still names09d9d02, reporting2 antipatterns/2 complex functions; do not treat its counts as a new744a2a1 analysis merely because status contexts are present. Its coverage body names744a2a1 and explicitly says no data. CodeRabbit has begun processing744a2a1; no fresh findings are available yet.


## 2026-11-01 — PR #34 controller/local-unit acceptance QA

Initial head99714c1cc17b3e01b1e444cf075098d0b1abb7c1, base52640f0551068949731c55bd7948831b8938dd9c. Used senior-qa, code-reviewer and receiving-code-review. Isolated worktree `/tmp/agent-workflows-pr34-review`; root dirty work preserved. All11 changed files inspected. All16 public timeline entries and all3 inline threads inspected; every thread comment ID matched the combined inventory. Protected CodeAnt endpoints remain skipped per user instruction; every public suggestion body is included.

### File-by-file coverage

| File | QA and result |
|---|---|
| `deploy/local/worker.env.example` | All path-only environment entries checked against worker configuration names and manual lifecycle requirements. No credentials embedded. |
| `deploy/systemd/agent-workflows-local.service` | Read command, restart policy, stop timeout, KillMode and hardening. Syntax verified and actual transient worker exercised; no unit installed. |
| `docs/PILOT_SETUP.md` | Checked all changed setup/acceptance claims against code and live probes. Documented minimum host Python, exact service interpreter/dependency setup, no-overwrite and 16KB writer limit. |
| `runner/local_config_inspect.py` | Atomic owner-only writer preserves canonical/private directory checks, nofollow staging, fsync, hard-link no-overwrite and cleanup. Added exact UTF8 byte limit before staging; actual worker reader round trip and inspection-time destination race tested. |
| `runner/local_preflight.py` | Ambiguous scopes retain private state/inspection path and avoid unverified stop. Replaced Python3.12-only temp API with mkdtemp and explicit cleanup. Real systemd detached-child termination and Codex sandbox probes pass. |
| `tests/test_controller_compose_cleanup.py` | Failed teardown preserves fixture/path; successful down removes it. New setup-write failure regression proves cleanup runs before resources are orphaned. |
| `tests/test_controller_compose_integration.py` | Moved secret/image writes under protected teardown. Real rootless Compose preserves queued job through stop/start, default and timed restart, and killed-container restart. |
| `tests/test_controller_image_integration.py` | Real cached-main image checks Python3.12, FFmpeg and /data owner/mode; real volume queue survives container replacement. It tests the named main image, not a new build of this PR. |
| `tests/test_local_config_inspect.py` | Oversized valid integration-name list rejects before creating output; round trip through real worker reader; existing-destination and inspection-time race preserve original bytes and remove staging. |
| `tests/test_local_preflight.py` | Incomplete/failed sandbox probes fail closed. Ambiguous launch retains SQLite evidence with no unverified backend stop. Compatibility regression reaches launch without delete=False support. |
| `tests/test_local_worker_systemd_live.py` | Reject CR/LF/NUL before writing environment; quote literal paths and verify real systemd round trip for quotes/backslashes/spaces/unicode/$/backticks. Missing unit no longer masks launch failure; unknown/failed cleanup retains evidence. Signed-in actual worker preserves queued job across restart. |

### Complete initial public-comment triage

| Comment / reviewer | Disposition |
|---|---|
| [6559562181](https://github.com/Bonobo791/agent-workflows/pull/34#issuecomment-6559562181) — codeant-ai[bot] | Review status metadata. |
| [6559562336](https://github.com/Bonobo791/agent-workflows/pull/34#issuecomment-6559562336) — codeant-ai[bot] | Promotion only; no technical finding. |
| [6559562906](https://github.com/Bonobo791/agent-workflows/pull/34#issuecomment-6559562906) — coderabbitai[bot] | Read full body: CodeRabbit review limit/neutral, no initial completed technical review. |
| [6559563155](https://github.com/Bonobo791/agent-workflows/pull/34#issuecomment-6559563155) — chatgpt-codex-connector[bot] | Initial Codex code/security reviews completed with no code findings; not release acceptance. |
| [6559567230](https://github.com/Bonobo791/agent-workflows/pull/34#issuecomment-6559567230) — sonarqubecloud[bot] | Read Sonar failure and both public flows: source is sys.argv[4], labelled HTTP despite no HTTP endpoint. Destination is explicit operator CLI input, constrained to canonical owner-only directory and atomic no-overwrite file creation; source-model false positive. No suppression or issue-state change. |
| [4436125347](https://github.com/Bonobo791/agent-workflows/pull/34#discussion_r4436125347) — amazon-q-developer[bot] | Valid: environment values with newline can inject assignments; reject CR/LF/NUL before file creation and quote literals. Actual parser test passes. |
| [5728703233](https://github.com/Bonobo791/agent-workflows/pull/34#pullrequestreview-5728703233) — amazon-q-developer[bot] | Read full summary; duplicate environment finding fixed; no bot mutation instructions executed. |
| [6559574362](https://github.com/Bonobo791/agent-workflows/pull/34#issuecomment-6559574362) — gitar-bot[bot] | Upgrade notice only. |
| [4436132293](https://github.com/Bonobo791/agent-workflows/pull/34#discussion_r4436132293) — cubic-dev-ai[bot] | Valid: only production delete=False call silently failed on Python3.11. Changed to mkdtemp plus explicit cleanup, preserving uncertain evidence. No claim of executing an actual Python3.11 interpreter. |
| [4436132296](https://github.com/Bonobo791/agent-workflows/pull/34#discussion_r4436132296) — cubic-dev-ai[bot] | Valid duplicate environment validation finding; fixed. |
| [5728710455](https://github.com/Bonobo791/agent-workflows/pull/34#pullrequestreview-5728710455) — cubic-dev-ai[bot] | Both summary findings validated and fixed; no thread replies/resolution performed. |
| [6559604989](https://github.com/Bonobo791/agent-workflows/pull/34#issuecomment-6559604989) — codeant-ai[bot] | Initial CodeAnt gate all pass, no antipatterns/complex functions. Two secret false positives were already suppressed by service; assistant changed no secret rules. Gate does not negate its separately published technical suggestions. |
| [6559605318](https://github.com/Bonobo791/agent-workflows/pull/34#issuecomment-6559605318) — codeant-ai[bot] | No coverage data despite successful status; not measured coverage evidence. |
| [6559613571](https://github.com/Bonobo791/agent-workflows/pull/34#issuecomment-6559613571) — codeant-ai[bot] | Both public nitpicks valid: early Compose setup failures now teardown or report retention, and systemd not-found cleanup no longer masks launch error. |
| [6559614070](https://github.com/Bonobo791/agent-workflows/pull/34#issuecomment-6559614070) — codeant-ai[bot] | Valid public major suggestion: prevent generated runtime JSON exceeding worker16384-byte limit. Failing-before regression uses permitted long integration names; rejected before staging. |
| [6559616388](https://github.com/Bonobo791/agent-workflows/pull/34#issuecomment-6559616388) — codeant-ai[bot] | Informational low-risk summary contradicted by actionable public suggestions; not used as acceptance. |

### Reproductions and validation

Baseline44 tests passed in1.796s (`/tmp/pr34-baseline.log`). Oversize writer and Python API regressions both failed before production fixes (`/tmp/pr34-boundaries-before.log`). CR/LF/NUL cases, missing-unit cleanup and setup-failure cleanup failed before harness fixes (`/tmp/pr34-fixtures-before.log`). Focused fixed core18 run/1skip succeeded; final affected loader/writer/preflight/worker/cleanup matrix **52 run, 2 skipped, successful** in1.701s (`/tmp/pr34-final-targeted.log`).

Actual live worker/environment probe **4 passed** in8.841s (`/tmp/pr34-worker-systemd-live.log`): literal environment parsing plus native pinned signed-in discovery and synthetic queued job across stop/start. Real image+Compose acceptance **3 passed** in17.148s (`/tmp/pr34-controller-live.log`), using cached `localhost/agent-workflows-controller:main-52640f0`. Actual detached-child scope preflight and actual Codex sandbox private-read/network-denial/workspace-write probes passed (`/tmp/pr34-preflight-live.log`). Example host unit syntax verification returned0 with sandbox socket-option notices (`/tmp/pr34-unit-verify.log`).

First full suite1258run/93skips had one **new test harness failure**, because package-qualified patching missed the module imported by unittest discovery. Corrected the function patch target to `__name__`, verified discovery-specific4run/2skip success (`/tmp/pr34-discovery-fixture-fixed.log`), and started final full rerun after the no-overwrite race was added. Do not call the earlier full run passing. Live runtime and production behavior tests were already passing. No model workflow stage, real task execution, fresh dependency build, media publication or persistent systemd installation is established by these disposable probes. Opt-in fixture tests use the current host Python; Python3.11 compatibility is established for the removed production API dependency, not a full Python3.11 test run.

### New PR #32 public follow-ups during PR #34 review

CodeAnt updated nitpick6559000755 now contains two new concerns, both valid: a suppressed workspace_recovery_audit insertion could commit a cleared assignment without audit; controller Lifecycle confirmation committed stopped but then rejected an already completed job. New public major suggestion6560053286 identifies a request accepted during completion preflight and then discarded by completion. Read entire bodies and validated against744a2a1. Added regressions; all three fail before fixes (`/tmp/pr32-followup-before.log`).

Workspace release now requires inserted-row count and exact schema/run/assignment/unit/status/timestamp proof, plus cleared-assignment readback, in the same transaction. Ignored/deleted/timestamp-altered audit triggers roll back release. Controller confirmation accepts an already completed job only with completed graph and no remaining claim; preserves job completion and idempotence for both manual and automatic controller stop, rejecting mismatched graph/claims. Worker closes owner request acceptance while applying a decision or completing an approved package; checks already queued decisions/actions before entering completion so no accepted pending work is silently dropped. Stop/status remain responsive during preflight. The completion transition does not make an immutable recorded owner decision revisable.

Affected PR32 matrix **108 run, 3 skips, successful** in25.533s (`/tmp/pr32-followup-fixed.log`); controller confirmation explicit manual/automatic and negative subcases **1 test passed** in0.141s (`/tmp/pr32-controller-confirm-final.log`). Full regression rerun is in progress after production changes. No hosted gate claim is inferred from local success.

### PR #34 final local evidence and push

Final full suite **1259 tests run, 93 explicit skips, successful** in268.156s (`/tmp/pr34-final-full.log`). A test-only collection cleanup then moved an imported Compose TestCase inside its regression method to avoid collecting the opt-in class twice; final affected harness **19 run, 1 skip, successful** in0.447s (`/tmp/pr34-final-harness.log`). No production changes followed the passing full run. The other remaining skip is the explicit real-systemd environment probe, separately passed in the live4-test matrix.

Pushed [af4d55f90b8ae1d2eed16b8a2ee8e51d7103d34a](https://github.com/Bonobo791/agent-workflows/commit/af4d55f90b8ae1d2eed16b8a2ee8e51d7103d34a) without force to `test/controller-main-acceptance`; `git ls-remote` independently matched. No merge or service installation. Hosted checks are being refreshed.

### PR #32 new public follow-up final evidence

Production final full suite **1311 run, 110 explicit skips, successful** in289.726s (`/tmp/pr32-followup-full.log`). The added owner-request-before-completion unit and the completion-gate/stop races also passed separately: **4 passed** in0.252s (`/tmp/pr32-request-races-final.log`); the full run began before that final test-only addition. Real systemd recovery matrix **3 passed** in2.277s (`/tmp/pr32-followup-systemd.log`). Both sides of the request race are covered: an accepted request queued before completion is processed first; requests arriving during preflight receive an unavailable response. Existing stop/status responsiveness and completion-versus-stop serialization remain covered. No production change after the full run; docs now explain the decision/completion request phases and immutable recorded decision.

PR32 CodeRabbit updated overview6558929266 was read in full: completed review of744a2a1 with no actionable comments, plus advisory2.22% versus80% docstring profile. No repository instruction mandates blanket docstrings; no bulk boilerplate or threshold change applied. Empty CodeRabbit review5728859573 inspected (no body). That is old-head review evidence, not acceptance of the new follow-up push.

### PR #32 / #34 pushed follow-ups and current hosted boundaries

PR32 pushed [8b9562ab09c9e8e7787d4122504821ac689ad156](https://github.com/Bonobo791/agent-workflows/commit/8b9562ab09c9e8e7787d4122504821ac689ad156) without force; independent ls-remote matches. PR34 pushedaf4d55f90b8ae1d2eed16b8a2ee8e51d7103d34a as documented. Both worktrees clean. All three open PRs currently MERGEABLE. PR33 is unchanged at467f6c3.

PR34 refreshed Sonar comment6560217413 read: security rating remainsC, matching the two operator-CLI destination path findings whose complete flows were reviewed above; no remote issue state changed. Cubic marks both of its initial PR34 issues addressed. New-head CodeRabbit is neutral/rate-limited for32/34; older completed review is not counted as reviewing these new pushes. Codex code reviews are running for8b9562a andaf4d55f; separate security metadata remains at original heads.

Public refreshed snapshots: `/tmp/pr32-continuation-final-comments.json`, `/tmp/pr33-continuation-final-comments.json`, `/tmp/pr34-continuation-final-comments.json`. No technical comment was omitted merely because its containing comment was called a nitpick, suggestion or summary. Public CodeAnt remains included; protected endpoints remain the agreed skip. Latest32/34 hosted scans are still running or not yet reported at this checkpoint; PR33 Sonar and CodeAnt quality gates remain red as previously triaged. No claim that all hosted gates pass.

### Final-scan quality follow-ups

PR32 Sonar flagged only S3776 release_local complexity18 versus15 after8b9562a. Extracted the unchanged exact recovery-audit insertion/readback into private `_record_recovery`; all fence, assignment and transaction guards remain in release_local. Focused workspace matrix is being verified before push.

PR34 final Sonar exposes the same two CLI-path S8707 findings plus S5778 in the new cleanup regression: constructing a TestCase inside assertRaises counts as an extra possible throw site. Moved construction outside the exception context, leaving only the actual failing setup method call. All3 cleanup tests passed in0.012s (`/tmp/pr34-exception-final.log`). No production change and no full-suite repeat for this test-only adjustment.

### PR #34 additional Codex compatibility finding

Read new inline4436302543 and summary5728893254 onaf4d55f: valid. Removing the production Python3.12-only API was insufficient because the new unconditional Compose cleanup regression and both new live fixture allocations still used TemporaryDirectory(delete=False). Confirmed failure with a Python3.11 constructor contract shim (`/tmp/pr34-fixture-python311-before.log`): TypeError occurs before setup. Replaced those retained fixture allocations with standard mkdtemp and the existing tests' SimpleNamespace(name,cleanup) adapter pattern. Successful cleanup explicitly removes them; ambiguous cleanup still retains them without a destructor. No new dependency or generalized fixture framework.

The unconditional cleanup regression now rejects delete=False in its shim, captures the actual mkdtemp path, and verifies setup failure performs teardown and removes the fixture. Final core19run/1skip success in0.441s (`/tmp/pr34-final-python311-core.log`). Actual controller/Compose/signed-in systemd probes are rerunning with the same legacy-API constraint. This verifies that specific API compatibility, not an actual Python3.11 runtime or its dependency environment.

PR32 private audit extraction passed21run/3skip in3.806s (`/tmp/pr32-audit-extraction-final.log`) and was pushed as9cab8b2858c5fb3849c209379ec09a94b3ee09d4, independently matched by ls-remote. PR34 exception-assertion correction was pushed asa9d2a1ee3c625b8ba7f54a64aa3f2c3faf34ef3b, also independently matched; a compatibility follow-up is still pending at this checkpoint. Public Sonar result6560223872 was read for8b9562a: passed with the one complexity finding now extracted. Public Sonar result6560252340 was read for34: security rating still red for previously evaluated CLI path findings.

CodeAnt's public PR32 gate at8b9562a reports2 antipatterns/1 complex function; public PR34 gate ataf4d55f passes with0 antipatterns/complex functions and3 secret false positives already suppressed by the service. The assistant did not suppress secrets or change any rule. Neither summary substitutes for final-head results.

Final PR34 acceptance matrix under the legacy temporary-directory API constraint: **10 passed** in27.809s (`/tmp/pr34-final-legacy-api-live.log`). Includes3 Compose cleanup regressions,2 actual controller-image checks,1 actual rootless Compose restart sequence and4 environment/systemd worker cases. All newly introduced retained fixtures avoid delete=False. The full1259-case suite's production code is unchanged by these test-only fixes; no unnecessary full rerun after10 affected live checks.

Latest PR32 exact-head9cab8b2 Sonar has **0 unresolved/new issues** and its hosted check is SUCCESS. Still MERGEABLE. CodeAnt quality/SCR remain red, SAST/SCA success, Semgrep in progress and CodeRabbit neutral/rate-limited; protected rule details remain skipped.

### Continuation final pushed heads

PR32 **9cab8b2858c5fb3849c209379ec09a94b3ee09d4** (completion/owner-request/workspace/lifecycle fixes plus exact-audit extraction). PR34 **5895a927c86f3daf6e7a2cae1e41add81f2c85b3**, independently matched by git ls-remote; compatibility commit pushed after10 real/cleanup checks. PR33 remains467f6c3c23e0aa1c0ff26d5ad742af71e766847e. Full QA scope now includes PR34's11files/16initial timeline entries plus all refreshed public findings and result comments described above. Root unrelated changes were preserved; report remains local and untracked. No merges, deployments, persistent service installations, external replies or thread-state mutations.

Final exact-head gate snapshot: PR32 Sonar and Semgrep SUCCESS; CodeAnt quality/SCR FAILURE, SAST/SCA SUCCESS; CodeRabbit NEUTRAL. PR34 Sonar FAILURE (previously triaged operator-path findings), Semgrep IN_PROGRESS, CodeRabbit NEUTRAL; latest CodeAnt detailed summary still belongs to an earlier head. Both PRs MERGEABLE. No all-green claim.


## 2026-11-01 — Marketing workflow code: mandatory complete SEO skill package

User clarified that the requested change is to workflow code, not another acceptance-run-only configuration or article rewrite. PR monitoring remains paused. Updated the shared workspace in place without staging or overwriting unrelated dirty changes.

`runner/skills.py` now resolves all package files recursively, supplies text and base64 binary content with relative filenames and individual SHA-256 hashes, and hashes the complete supplied content for existing provenance/cache verification. Single-file skills preserve their previous content/hash semantics. Symlinks, unreadable/nonregular files, oversized files and aggregate/serialized packages above256KiB fail closed; no references are silently omitted or truncated. The actual installed plan-seo-geo-growth package resolves12files, all9references, and88507 prompt bytes, with package SHA7499117b1300fc79ec2b16ddce971e3d8b3914301b1c596214d36636743f0fee at this check. Private skill content was not copied into the repository.

`runner/worker.py` automatically adds plan-seo-geo-growth to configured canonical_draft and canonical_review roles, preserving caller configuration and preventing silent fallback if the skill is unavailable. The runtime prompts and marketing prompt documentation explicitly require applying applicable full-package guidance to the article and checking that content in a fresh review, while retaining approved factual sources and flagging missing search/site/performance evidence. The existing correction loop remains the mechanism for writer changes after review findings. Updated pilot setup, marketing README and implementation-plan code milestone.

Five regressions failed before the fix (`/tmp/marketing-seo-code-before.log`): omitted nested/binary/reference data, reference-only hash unchanged, escaping-file package accepted, missing default SEO requirement, and missing-package fallback. Baseline59 passed in2.133s. Final targeted worker/skill/dispatch/cache matrix **67 passed** in1.788s (`/tmp/marketing-seo-code-final-targeted.log`). Marketing fake-protocol/graph integration matrix **22 passed** in27.515s (`/tmp/marketing-seo-code-integration.log`): both canonical roles actually receive all9reference markers; independent review evidence binds the package hash; editing only reference8 makes terminal verification fail and restoring it restores verification. A separate graph test proves reference-only edits do not reuse cached stage output. Binary payload, cumulative size and FIFO rejection are covered. Python compilation and git diff --check pass. Full shared-workspace suite is running.

Earlier acceptance attempt before this clarification did revise a candidate article, but its source-coverage review blocked and its correction attempt requires inspection; it was not approved, adapted or published. The runner process is gone and its remaining recorded scope was inspected as inactive/not-found. State and artifacts were preserved; no further acceptance-run work was performed for this code task. Existing sessions bound to earlier skill inputs intentionally require compatible migration or a new run. No code pushed under the paused PR goal.

Full workspace suite first run:687run/10skip, one failure in the existing source-fetch cancellation timing test (0.4s case did not see its child marker), `/tmp/marketing-seo-code-full.log`. No source-fetch production or test code was changed by this task, and that module does not depend on skill resolution. Rechecking the entire source-fetch module passed8tests in3.682s (`/tmp/marketing-seo-code-source-fetch-recheck.log`). This supports a timing-sensitive test observation, not a passing initial full run. Full-suite recheck is underway with final skill safety tests included. No assertions or deadlines were weakened.

Final verification for the workflow-code task: full-suite recheck **689 run, 10 skipped, successful** in165.250s (`/tmp/marketing-seo-code-full-recheck.log`). The initial source-fetch timing failure is recorded above; its isolated module and full rerun pass with no weakened deadline/assertion. Final code review also identified that pathlib.rglob may suppress an unreadable directory error: a failing-before regression (`/tmp/marketing-seo-unreadable-before.log`) showed required references could disappear silently. Replaced discovery with explicit iterdir recursion so errors propagate. After that final guard, the exact final worker/skill/cache/dispatch/marketing integration matrix **71 passed** in23.038s (`/tmp/marketing-seo-code-final.log`). No further production changes. The real12-file package check still matches7499117b1300fc79ec2b16ddce971e3d8b3914301b1c596214d36636743f0fee. Changes remain local, no commit or push; PR monitoring remains paused.
