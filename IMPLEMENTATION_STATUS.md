# Current implementation status

This is experimental local code, not a verified deployment. The runtime rejects
`WORKFLOW_EXECUTION_MODE=live`; separately configured private controls can still
access their configured task services or Docker controller, so that setting is
not a substitute for network or host isolation.

| Area | Locally implemented | Remaining boundary |
| --- | --- | --- |
| Task and control | One exact task, readiness and linked-work checks; private authenticated Unix control; manual or explicitly started background dispatch; atomic queue/graph lease renewal | Real service credentials and task access not configured or tested; automatic-loop host shutdown needs validation |
| Marketing | Versioned writer/reviewer stages, source register, source and draft coverage review, one bound-session correction, channel outputs, blocked assets, exact-package human decision | No real source-tool authority; claim completeness and semantic truth remain reviewer judgments; no automatic publication |
| Engineering | Approved workspace/branch binding; implementation, supervised-command evidence and fresh QA; correction, PR action proposals and exact approvals; valid-comment gate, readiness and second review | No configured Git credential delivery, or scoped mutation credential issuer; origin URL is not proof of remote identity or current base; no merge or deploy |
| Lifecycle and state | Durable claims, exact-container assignment and terminal checks, uncertain-outcome fences, operator-attested manual stop, optional separately gated automatic lifecycle, role-specific private session bindings | Engine authorization, container and process-tree isolation, real session resume and recovery not verified |
| Authentication | ChatGPT-plan app-server command and model catalog selection; no API-key fallback | Protected OAuth token delivery and refresh boundary not implemented or configured; no sign-in or entitlement test |

Marketing terminal verification rechecks current assembly stage versions,
source-register and verifier artifacts, and canonical/channel reviewer subject,
skill and provenance bindings. Engineering approval rechecks the exact workspace,
commit, supervisor command record and independent QA. Role-written test reports
are labeled separately from supervisor observations. Both command execution and
GitHub/source evidence were tested through synthetic boundaries only.

The PR bridge uses the existing action ledger and GitHub client. Each push,
draft creation or readiness action requires a distinct exact proposal and
owner approval. Valid review findings remain blocked until an evidenced writer
correction and fresh QA; a second review precedes human merge review. Unknown
remote outcomes require exact stopped-process and operation-specific remote-state evidence.
A claim-scoped authority can use an exact-container push adapter and a read-only remote observer. The observer requires exact stopped-container evidence and a positive match of remote state; it does not claim which request caused that state. Negative or conflicting outcomes remain uncertain. The ledger records `desired_state_observed` separately from acknowledged execution. No real credential-delivery provider or scoped mutation credential issuer is configured, so GitHub writes remain unavailable as shipped.

Source-tool receipts and reviewer judgments are version-bound, but they are not
mathematical guarantees of truth or completeness. The default worker has no
source-tool authority and blocks marketing at the source gate. A rejected human
review remains revision-required, not completed.

See [docs/PILOT_SETUP.md](docs/PILOT_SETUP.md) for exact intended private inputs
and separate approvals. Independent QA and actual host/runtime validation are
still required. The selected Codex app-server command is experimental and not
supported for production workloads according to OpenAI's documentation.

Uncertain draft and readiness actions can be reconciled only after a observer verifies an exact stopped process and matching remote receipt; the approval ledger, verified receipt and graph transition are committed together. Expired or confirmed-nonexecuted actions can be explicitly reauthorized with an audit record, while uncertain actions cannot. Fake Engine/GitHub tests do not establish real host isolation or remote-request quiescence.
