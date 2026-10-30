# Workflow foundation implementation plan

**Goal:** Provide a private, approval-gated runner that can later execute engineering and marketing workflows with subscription-authorized Codex.

**Architecture:** SQLite stores durable claims, stage versions, sessions and approvals. Integration adapters are injected and disabled by default. External mutations use a recorded intent and exact-version approval; an uncertain result is never retried automatically. No network control plane is exposed by default.

**Technology:** Python, SQLite, Codex app-server over stdio, isolated private persistent storage.

1. Harden persistence and approval boundaries with tests for stale claims, changed commits, interrupted mutations and cancellation.
2. Implement mockable orchestration for ready-task intake, independent QA, bounded remediation, and review triage. Separate fresh review sessions from resumed implementer sessions.
3. Implement OAuth PKCE, callback validation and verified identity through a maintained cryptographic library; test synthetic JWTs and callback failures. Do not initiate a real login.
4. Implement app-server stdio adapter and serialized refresh, with synthetic process and transport fixtures. Explicitly block unsupported tools.
5. Add private deployment packaging and operation documentation, without deployment or credentials.
6. Verify end-to-end using fake integrations, document unimplemented or unverified external behavior, and examine public files for sensitive material.
7. Integrate the separately tracked observability proposal through the existing structured audit and event boundaries; no telemetry service or credentials are configured here.

Approval is required before any live credential setup, remote mutation or publication. Public source does not imply a public control plane. No API-key fallback, automatic merge or deployment.
