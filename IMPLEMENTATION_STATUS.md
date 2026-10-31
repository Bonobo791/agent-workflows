# Current implementation status

This is experimental local code, not a verified deployment. The runtime rejects
`WORKFLOW_EXECUTION_MODE=live`; separately configured private controls can still
access their configured task services or Docker controller, so that setting is
not a substitute for network or host isolation.

The current priority is a production-ready local workflow using the owner's
existing Codex-managed ChatGPT sign-in. Coolify is deferred until the local
workflow and its operational boundaries are verified. On 2026-10-30, the local
CLI and a read-only app-server account check reported a ChatGPT sign-in, and a
synthetic tool-free app-server turn completed using GPT-6.1 Terra with medium
reasoning. The worker entrypoint accepts an explicit private local configuration.
Do not copy the Codex credential
store into images, repositories, or job workspaces to bridge that gap.

A 2026-10-30 synthetic Linux sandbox probe with a minimal filesystem profile
allowed workspace writes and denied a separate private canary and network socket
creation. The sandboxed process could read its sandbox parent's environment,
including a synthetic inherited marker. A local launcher therefore needs a
minimal environment as well as filesystem and network restrictions. This probe
does not establish app-server policy enforcement, MCP isolation, process-tree
termination, or protection against all paths to account credentials.
An app-server turn using that profile ran a synthetic command successfully:
the workspace write succeeded, while the private canary and synthetic inherited
environment marker remained inaccessible. The first command failed because an
empty tool environment omitted PATH; the launcher now supplies a fixed system
PATH while still disabling environment inheritance. These probes do not prove
complete isolation or production readiness.

The local app-server configuration readback contained three MCP servers and 31
plugins. An empty MCP table did not remove inherited servers; explicit temporary
per-integration overrides disabled all of them in readback. The managed launch
spec can apply those overrides and disable hooks, and a fail-closed checker rejects
enabled integrations or changed sandbox settings. The local supervisor supplies
this guard before thread start or resume. App connectors, web search, browser
and computer use, multi-agent tools, goals, and memories are disabled and
checked as well; sandboxed-command network rules do not constrain those tools.
Effective configuration is checked for the actual workspace and process.
The local supervisor also runs synthetic sandbox and systemd scope preflights:
they test workspace writes, private-file denial, network socket denial, and
termination of a detached child. Both checks passed on this host; they are
bounded probes, not proof against all isolation failures.

Local scope tracking now persists a unique scope reservation before launch,
binds it to the active systemd invocation and cgroup, and requires a successful
stop plus an empty or removed cgroup before recording termination. A synthetic
real-scope probe stopped a detached child. A scope not yet observed active stays
unresolved, including after restart: a successful stop of a nonexistent unit is
not evidence that a concurrent launch cannot still occur. A local process handle
now combines this tracker, managed sign-in launch, and workspace configuration
checking. A synthetic signed-in turn through the handle wrote only to its
workspace, could not see a private canary or inherited marker, and terminated
its scope before output acceptance. A local executor supervisor now binds writer
sessions to the workspace, skills, model, and launch settings and claims resumed
sessions before launching. A synthetic writer turn and resume completed through
the real executor, with verified output from each turn. A trusted Python worker
integration can now select this supervisor with a manual lifecycle; marketing
model dispatch and model discovery use it. Each launch has a fresh private
output directory outside its workspace, separately granted to the sandbox. The worker entrypoint can construct this
integration with a pinned executable and explicitly disabled integrations.
Engineering can bind an exact private checkout and claim it for a local scope;
a synthetic signed-in implementation turn made a commit, produced an output,
left the checkout clean and released the claim after scope termination.
Uncertain-launch recovery, full workflow recovery and broader isolation
verification remain incomplete.
The sandbox blocks Git metadata writes by default. A synthetic Git checkout
successfully added and committed a file only after an explicit permission for
its exact `.git` directory. Local implementation turns grant and verify that
permission; review and test turns do not receive it. A local command backend
runs an explicitly approved command under a scope and sandbox, captures its
bounded result, and requires scope termination before accepting evidence. A
synthetic command passed through the real backend with a clean checkout and
redacted output. Real GitHub mutation and full workflow validation remain
incomplete.

A synthetic engineering task was also exercised through the local stage
dispatcher with the signed-in app-server: intake, plan, implementation,
supervised test and independent QA passed, ending at the draft-PR approval gate.
An approved synthetic push and draft then completed against a temporary local
Git remote and fake GitHub CLI, leaving the checkout clean. An extended
synthetic run continued through review, approved readiness and a second review
to the human merge-review gate using the same local remote and fake GitHub. It
did not contact a real task service or make a GitHub write. The app-server client now separates the final message from
intermediate commentary and supplies the exact result schema to each role.
Local task discovery can use the owner's existing GitHub CLI sign-in for
read-only requests without exporting a token; an authenticated repository read
was verified. GitHub read access alone does not authorize writes.
For the future local push path, a scoped sandbox can export a Git bundle and
import it into a fresh bare repository with clean Git configuration. The import
checks the exact commit and runs Git object verification; a real synthetic
export/import left the original checkout clean. A local push transport now binds
the exact approval, verifies fast-forward ancestry, uses the native GitHub CLI
credential helper from the clean snapshot, and requires scope termination. Its
complete path passed against a temporary local remote, including lease and
ledger checks; no GitHub push was performed. A read-only observer can reconcile
a positive remote head only after both recorded scopes have terminated. The
local worker can construct the authority when using the existing GitHub CLI
sign-in. Draft and readiness mutations require their own exact approvals and run
in separate tracked scopes; a fake GitHub CLI verified the draft path and private
input pipe without making an external write. Real GitHub mutations remain
untested, and ambiguous outcomes require independent reconciliation.

After manual stop fences a run, `stop_local_scope` can bind and terminate its
observed active scope; absent launches and interrupted stops remain unresolved.
Manual stop confirmation now blocks when a local scope remains unresolved and
reports the recorded scope identity. An interrupted stop can be reconciled only
with a trusted quiescence verifier, an inactive matching scope, and an empty or
removed recorded cgroup. No production quiescence verifier or recovery control
is configured; an unobserved launch remains fenced.

The OpenFilm image has a local build using a pinned Playwright base and locked
npm dependencies. The rootless Podman render adapter uses `keep-id` and private
SELinux relabeling, and checks the resulting image, namespace and labels. A real
synthetic PNG render and a wrong-label rejection passed through the adapter;
the project builder and policy verifier in that test were synthetic. A separate
synthetic run used the existing Codex sign-in and installed OpenFilm manual to
author a project and render it with the real adapter; its policy verifiers were
synthetic. The standard local worker now constructs a policy verifier that checks
the rootless Podman endpoint, isolation features, image identity and helper
contents; the synthetic Codex-to-PNG run also passed using that verifier. The
local reviewer supports still-image and preview delivery.

Local source review can now retrieve individually approved HTTPS URLs through a
bounded public-address-only fetcher and bind private, time-limited snapshots to
the exact reviewer turn. A synthetic claim against a real public page passed
with the signed-in local reviewer. This establishes retrieval and review
evidence, not the truth of arbitrary claims or a complete marketing workflow.
An optional local platform reviewer binds configured source URLs and posting
surfaces to a fresh reviewer session and terminated scope. A synthetic LinkedIn
Page logo brief passed against a live official specification; other surfaces
and channels are not thereby validated.
With an asset-review role skill, a fresh local reviewer receives the actual PNG
assets and previews as image inputs. Delivery is bound to the completed turn,
artifact hashes and terminated scope; a matching synthetic still passed and a
missing-text image was blocked.
MP4 review uses 12 timestamp-verified frames extracted from the actual video in
a local scope. An experimental approved offline policy can also transcribe one
audio stream in an explicitly configured language. A synthetic Portuguese video
passed transcription and a signed-in reviewer blocked an inconsistent fixture.
Machine transcription can be wrong and does not establish audio quality. The
result requires full human playback and listening; unsupported audio remains
awaiting review.

| Area | Locally implemented | Remaining boundary |
| --- | --- | --- |
| Task and control | One exact task, readiness and linked-work checks; private authenticated Unix control; manual or explicitly started background dispatch; atomic queue/graph lease renewal | Real service credentials and task access not configured or tested; automatic-loop host shutdown needs validation |
| Marketing | Versioned writer/reviewer stages, source snapshots and draft coverage review, configured platform and sampled media review, bound-session correction, Markdown structure checks, five channel outputs, exact-package human decision and synthetic verified-media handoff | Real community context and a complete marketing run remain unverified; sampling and transcription require human playback; semantic truth remains a reviewer judgment |
| Engineering | Approved workspace/branch binding; implementation, supervised-command evidence and fresh QA; correction, PR action proposals and exact approvals; valid-comment gate, readiness and second review | Local GitHub mutations have only synthetic verification; origin URL alone does not prove remote identity or current base; no merge or deploy |
| Lifecycle and state | Durable claims, exact-container assignment and terminal checks, uncertain-outcome fences, operator-attested manual stop, optional separately gated automatic lifecycle, role-specific private session bindings | Complete host isolation and recovery remain unverified; unobserved launches stay fenced |
| Authentication | Local managed-auth supervisor and entrypoint; synthetic preferred-model turn and writer resume observed; no API-key fallback | Complete production isolation and end-to-end workflow execution remain unverified |

Marketing terminal verification rechecks current assembly stage versions,
source-register and verifier artifacts, and canonical/channel reviewer subject,
skill and provenance bindings. Engineering approval rechecks the exact workspace,
commit, supervisor command record and independent QA. Role-written test reports
are labeled separately from supervisor observations. GitHub mutations remain
tested only through synthetic boundaries; local command execution and a public
source fetch have also been exercised on this host.

The PR bridge uses the existing action ledger and GitHub client. Each push,
draft creation or readiness action requires a distinct exact proposal and
owner approval. Valid review findings remain blocked until an evidenced writer
correction and fresh QA; a second review precedes human merge review. Unknown
remote outcomes require exact stopped-process and operation-specific remote-state evidence.
A claim-scoped authority can use an exact-container push adapter and a read-only remote observer. The observer requires exact stopped-container evidence and a positive match of remote state; it does not claim which request caused that state. Negative or conflicting outcomes remain uncertain. The ledger records `desired_state_observed` separately from acknowledged execution. The Docker path has no real credential issuer. The local `gh` path is wired for separately approved writes but has only synthetic mutation tests.

Source snapshots, tool receipts and reviewer judgments are version-bound, but
they are not guarantees of truth or completeness. Without an explicit source URL
policy or other source authority, the worker blocks at the source gate. A synthetic
signed-in local run using an approved public source reached a human-review
package: LinkedIn, X and Facebook reviews passed; Reddit lacked community
context and Instagram lacked verified media. Its proposed media brief used an
invalid generic channel; the prompt now requires a supported concrete channel,
but that correction has not yet been verified in another signed-in run. This
partial package does not establish full marketing acceptance. A separate synthetic
entrypoint fixture reaches five passed channel reviews with a decoded private
image and synthetic platform, community and media-review authorities; a second
fixture demonstrates blocked missing media. These fixtures do not verify actual
platform rules or independently assess real media. A rejected human review
remains revision-required, not completed.

See [docs/PILOT_SETUP.md](docs/PILOT_SETUP.md) for exact intended private inputs
and separate approvals. Independent QA and actual host/runtime validation are
still required. The selected Codex app-server command is experimental and not
supported for production workloads according to OpenAI's documentation.

Uncertain draft and readiness actions can be reconciled only after a observer verifies an exact stopped process and matching remote receipt; the approval ledger, verified receipt and graph transition are committed together. Expired or confirmed-nonexecuted actions can be explicitly reauthorized with an audit record, while uncertain actions cannot. Fake Engine/GitHub tests do not establish real host isolation or remote-request quiescence.
