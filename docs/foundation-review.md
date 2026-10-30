# Foundation review follow-up

The independent review on [foundation PR #1](https://github.com/Bonobo791/agent-workflows/pull/1)
was checked against the current source and synthetic workflow tests:

- A standalone `GraphStore` could not finish an attempt because `graph_results`
  was created by `GraphRunner`. The store now creates its own required table.
- PR reconciliation requires action and PR bridge tables. Missing integrations
  now produce a controlled error and roll back the stage; normal reconciliation
  still requires the exact existing approval and scope.
- `action_bindings` is created by `BoundLedger`; the engineering entrypoint
  tests exercise approval expiry, reauthorization and reconciliation.
- `GraphRunner` refuses external stages before consulting its result cache, so
  missing cached entries cannot cause it to issue a PR mutation. Mutations go
  through the separate approval-bound PR bridge.
- The credential lock context manager masked a caller's `OSError`. It now
  translates lock acquisition errors without replacing caller exceptions.
- A failed backup can remain fenced. Recovery now requires a trusted quiescence
  and credential-boundary verifier and an unchanged, safe home. No production
  verifier is configured; uncertain backup outcomes are not automatically
  declared safe.

The tests use synthetic credentials, services and Docker engines. They do not
establish production isolation, credential delivery, remote state or deployment.
