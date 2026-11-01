# Local workflow readiness plan

Updated October 31, 2026 from current source inspection, GitHub state, and fresh tests. Current status is in [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md); the earlier detailed review is in [QA-review-10-31-26.md](QA-review-10-31-26.md).

## Goal and scope

Deliver a reliable local engineering and marketing runner using the existing managed Codex sign-in, with exact approvals, independent review, durable evidence, bounded execution, and safe recovery. Coolify remains deferred. Merge, deployment, publication, credential installation, and real external mutations require their separately scoped authority.

This plan updates the backlog; it does not authorize those external actions or declare the linked TOOLS tasks complete. Existing repository direction supplies the scope, so no additional product decision is needed to document these next steps.

## Verified checkpoint

| Item | Status and evidence |
| --- | --- |
| Foundation/local infrastructure PR | PR #4 merged October 31 at 13:01:59 UTC; candidate `9f25d21debc23e1e9bd9ddbef07c9ac20bb25a84`, merge `604bdc0dd9c55c7ba9bbc1c793ab2cc899dd3546` |
| Remote main | Independently read as merge `604bdc0`; this workspace's local HEAD is still `6a18ab6` |
| Current local full suite | 679 tests run, 670 passed, 9 skipped, zero failures/errors; 60.550 seconds, with local socket access |
| Merged main focused regression suite | 45 tests run, 44 passed, 1 skipped, zero failures/errors; 8.453 seconds |
| QA-01 shutdown fencing | Fix present in merged main and local; regression suite passes |
| QA-02 command frame truncation | Fix present in merged main and local; escaped large-command/frame regressions pass |
| QA-03 source deadline/cancellation | Fix present in merged main and local; DNS, slow stream, helper deadline and cancellation regressions pass |
| QA-04 approval-wait metrics | Fix present in merged main and local; metrics regressions pass |
| Existing artifact-response limit | Fix present in merged main and local; large artifact socket/CLI regression passes |
| Hosted final-candidate gates | SonarCloud failure; Semgrep in progress; CodeRabbit skipped at inspection. No current CodeAnt status returned. Earlier CodeAnt failure must not be assumed cleared. |

The PR description reports a configured three-test rootless-Podman integration pass and focused Python3.12 container tests. Those are reported prior evidence, not newly reproduced in this update. The earlier QA's seven/nine skipped render tests therefore do not erase the later reported run, and the later run does not establish a full live campaign.

## Ordered remaining work

### 1. Reconcile the checkout with merged main

- [ ] Preserve the current dirty tree and inventory local-only files before changing branches or history.
- [ ] Use an isolated checkout based on `604bdc0` for follow-up development; compare and selectively carry genuine local changes. Do not replay the entire apparent dirty diff against the old HEAD: most of it already merged in PR #4.
- [ ] Retain the local configuration inspector and review/plan documents as appropriate; distinguish documentation additions from runtime changes.
- [ ] Confirm branch, base, intended diff, and clean tested snapshot before committing or proposing a follow-up PR.

Acceptance: an exact current-main-based candidate containing only intentional follow-up changes, with unrelated work preserved. No reset, cleanup, push, or merge is implied by this plan.

### 2. Finish the local render-wait timeout follow-up

The current comparison against merged main finds seven differing tracked files: `runner/docker_controller.py`, `runner/docker_openfilm.py`, `tests/test_command_supervisor.py`, `tests/test_docker_controller.py`, `tests/test_openfilm_builder.py`, `tests/test_openfilm_podman_integration.py`, and `tests/test_openfilm_stage.py`.

The runtime change allows an explicit bounded timeout only for the Engine wait operation and passes the configured render timeout into that operation. This avoids a long render inheriting the short generic Engine request timeout. Fixtures adapt to the optional argument, and the Podman test returns to the default Engine client rather than hiding the issue with a global 120-second timeout.

- [ ] Review the complete seven-file diff and all Engine callers/fixtures against current main.
- [ ] Keep validation for invalid/nonfinite timeouts and reject timeout overrides on unrelated operations.
- [ ] Retain the real Unix-socket delayed-response regression and configured-render-timeout assertion.
- [ ] Run the configured rootless-Podman integration using the default Engine timeout on the exact follow-up candidate.
- [ ] Rerun the full suite after assembling the isolated candidate and record its SHA.

Acceptance: delayed renders complete within their own allowed budget, expired waits fail closed, and uncertain termination remains fenced. Current local suite is green; the final isolated candidate and real render still need validation.

### 3. Resolve hosted analysis debt

- [ ] Recheck check-runs and statuses at the final candidate SHA; obtain Semgrep's terminal outcome.
- [ ] Review current Sonar findings and resolve genuine defects; document contextual/false-positive adjudication through the service's normal review process.
- [ ] Recheck CodeAnt rather than carrying forward the earlier count or inferring success from absence. The earlier head reported 33 antipatterns and 36 complex functions.
- [ ] Address maintainability debt in focused changes where warranted; preserve trust-boundary validation and error handling.
- [ ] Record final hosted outcomes separately from local tests. Do not disable gates or weaken checks to get a green result.

Acceptance: current-head findings are resolved or explicitly adjudicated with evidence, and hosted results are recorded. PR #4 being merged does not establish that its failed gates passed.

### 4. Complete operational recovery acceptance

- [ ] Exercise actual worker startup/restart using inspected pinned native configuration on the intended host, including a real signed-in session resume.
- [ ] Exercise controller SIGTERM while work is running; verify both logical fencing and actual scope termination/reconciliation.
- [ ] Define and implement trusted quiescence evidence and operator recovery controls for unobserved launches, interrupted stops, retained workspace claims, and interrupted session backups.
- [ ] Test ambiguous remote outcomes, approval expiry, stale claims, changed policies, and process loss without automatically retrying uncertain work.
- [ ] Verify approved source-policy changes invalidate old snapshots and require fresh review.

Acceptance: operators can safely inspect and recover an interrupted task with exact generation/scope/claim evidence. Unknown physical or remote outcomes remain blocked. Existing synthetic/native low-level probes are partial evidence, not complete workflow recovery.

### 5. Validate the complete engineering journey

- [ ] Admit one explicitly authorized real task with exact repository/workspace/base/branch binding.
- [ ] Complete planning, implementation, supervisor-observed command evidence, independent QA and correction at the exact commit.
- [ ] With separately authorized real writes, exercise push, draft PR and readiness as distinct exact approvals; independently read remote results.
- [ ] Verify valid review findings trigger correction and fresh QA, followed by second review and the human merge-review gate.

Acceptance: current run evidence establishes the complete real journey to human merge review. No automatic merge or deployment is added. Existing signed-in/local-remote/fake-GitHub demonstrations remain synthetic remote-action evidence.

### 6. Validate the complete marketing journey

- [ ] Configure approved source URLs, current platform authorities, required skills/page rules, supplied media and real community context.
- [x] Require `plan-seo-geo-growth` for configured canonical drafting and independent SEO/editorial review. Load every package file with per-file hashes, bind the complete content into evidence, and reject stale cached work after reference-only changes. Apply the guidance to the article through the writer and existing correction/review loop; preserve approved factual-source boundaries.
- [ ] Run from actual task admission through source/canonical review, correction, freeze approval, all applicable channel reviews, assets, assembly, and exact-package human decision.
- [ ] Include real OpenFilm still and vertical-video rendering through the approved rootless adapter, independent review, and configured audio transcription where applicable.
- [ ] Retest missing/stale evidence, changed brief/source/policy, rejected package, cancellation and failed renders in the integrated journey.
- [ ] Perform complete human playback/listening and rights/editorial checks for actual deliverables.

Acceptance: applicable channels have verified current evidence; unsuitable/blocked channels remain explicit; the human decision matches the final package. Sampling/transcripts do not replace human review, and approval does not publish anything.

### 7. Validate monitoring and local packaging

- [ ] Build/test the controller on its declared Python3.12 image, including ffmpeg availability and private persistent-volume ownership/permissions.
- [ ] Validate actual start/restart and the intended filesystem/network/process policies on the target local host.
- [ ] Configure the audit exporter/collector and dashboard; prove real delivery, replay after failure, privacy, and cursor behavior.
- [ ] Trigger and recover uncertain execution, expired lease, quota, approval-wait, stale snapshot and missing snapshot alerts; verify notification routing.
- [ ] Rerun `promtool` checks and rules tests for any alert change and record the tested tool version.

Acceptance: deployed local collection and notifications work, image startup/restart works, and evidence is tied to exact images/configuration. Coolify remains deferred until local acceptance is complete.

## Next execution order and completion rule

Start with checkout reconciliation and the render-wait follow-up. Recheck hosted analysis in parallel with that work. Then close recovery acceptance before relying on full engineering/marketing runs; packaging and monitoring can be verified independently once their configuration is authorized.

Local readiness is complete only when the exact candidate has passing required local checks, current hosted outcomes, real recovery evidence, both complete workflow acceptance records, and operational monitoring. Do not mark TOOLS-36/33/34/35 complete based on the merge, test count, or this plan alone; inspect each task's current acceptance criteria before changing tracker status.

## Validation record for this update

- Source inspection: current local runtime, regression tests, status/setup docs, historical QA, and comparison with isolated merged main.
- GitHub metadata plus independent `git ls-remote`: PR #4 merged; remote main `604bdc0`.
- Full local run: `/tmp/agent-workflows-status-tests-sockets.log`.
- Exact merged-main focused run: `/tmp/agent-workflows-status-main-focused.log`.
- A preliminary run without socket access encountered environmental permission errors; the successful socket-enabled run above is the meaningful full-suite result.
- No live model turn, GitHub write, deployment, Podman render, or fresh coverage measurement was performed in this status update.
