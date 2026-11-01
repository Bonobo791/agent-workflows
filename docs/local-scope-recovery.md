# Local scope recovery evidence

The owner can reconcile a guarded scope that has already exited only after the
run is fenced. This is separate from stopping an active scope, confirming the
worker stopped, releasing an engineering workspace, or resolving a remote
request. None of those operations is implied by reconciliation.

The guarded launcher waits inside the systemd scope. It sends a private
receipt containing its PID and a nonce, and it will not execute the target
until the parent sends authorization. The parent verifies that PID is in the
observed scope, then commits the scope identity, receipt and audit together.
The final fence check and authorization hold the state database write lock, so
cancellation cannot commit between those two operations. If the parent dies
before authorization, the guard sees the closed pipe and exits.
The parent controls launch deadlines and closes the authorization pipe on
failure. The guard does not impose a shorter timeout while scope binding is
still in progress; a hung parent leaves it waiting until cancellation or exit.

For reconciliation, the controller holds that write lock while it rechecks
the exact pending manual assignment, cancelled graph, uncertain or cancelling
job and absence of graph claims. It requires the committed receipt and matching
audit, the same recorded invocation and cgroup, two inactive observations, an
empty recorded cgroup and no current systemd job. The terminated transition
and a distinct recovery audit commit together. Missing or changed evidence
leaves the scope unresolved. Legacy unconfirmed launches do not qualify.

This evidence assumes the user systemd manager and the installed
`systemd-run` implement the tested scope-start behavior, the controller code
and private state database are trusted, and no other actor submits a new
operation for the reserved unit. A systemd job observation alone cannot rule
out an unrelated request still in transit. The evidence also relies on the
configured sandbox and host retaining descendants in the scope. An empty
cgroup does not prove that an escaped process never existed. Validate those
assumptions for the deployed host before relying on this recovery control.

The opt-in systemd tests exercise caller death before authorization and after
target start, missing and mismatched evidence, concurrent database writes,
audit rollback, and a signed-in synthetic worker crash followed by an
explicitly exited scope. They do not establish containment against every host
configuration, recover an interrupted stop request, or establish the outcome
of an external mutation.

An interrupted stop needs separate evidence. An opt-in experiment in
`tests/test_stop_witness_live.py` keeps a stop client in flight while killing
its caller. A separate witness can observe successful client exit afterward;
if the witness is killed, the client can remain in flight and there is no
completion evidence. Normal systemd stops reserve an audited operation and
use the detached witness to record successful client completion against the
exact scope identity. The owner can reconcile a fenced `stopping` scope after
that record, its ordered audit and inactive, empty scope are verified.
Timeouts, witness death, legacy stops without a receipt, and missing audit
evidence remain unresolved.
