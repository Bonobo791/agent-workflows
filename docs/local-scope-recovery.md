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
The parent passes the same absolute monotonic deadline to the guard and checks
it again before authorization. Failure closes the authorization pipe. A hung
parent leaves the guard waiting only until that deadline, cancellation or exit;
the guard also checks expiry after reading authorization and before execution.

For reconciliation, the controller first checks the receipt and observes the
scope twice as inactive, with an empty recorded cgroup and no current systemd
job. These systemd calls run without a database write lock, allowing cancellation
and other writers to proceed. It then takes the write lock and rechecks the
same receipt and audit, exact pending manual assignment, cancelled graph,
uncertain or cancelling job and absence of graph claims. Changed or missing
evidence leaves the scope unresolved. The terminated transition and distinct
recovery audit commit together. Legacy unconfirmed launches do not qualify.

This evidence assumes the user systemd manager and the installed
`systemd-run` implement the tested scope-start behavior, the controller code
and private state database are trusted, and no other actor submits a new
operation for the reserved unit. A systemd job observation alone cannot rule
out an unrelated request still in transit. The evidence also relies on the
configured sandbox and host retaining descendants in the scope. An empty
cgroup does not prove that an escaped process never existed. Validate those
assumptions for the deployed host before relying on this recovery control.

SonarCloud reported command and argument injection at the guard's `execve`,
tracing its source to an HTTP request. The guard is a local executable and has
no HTTP entry point. Its production callers supply either the configured,
hash-checked Codex executable and managed arguments or the fixed local-command
relay; an approved command reaches the relay through its bounded input protocol.
The guard uses an argument vector without a shell. This assessment depends on
those callers and the trusted local controller; it does not make arbitrary new
callers of `start_guarded` safe. The hosted findings remain open until reviewed
in SonarCloud.

Opt-in systemd tests exercise caller death before authorization and after
target start, a signed-in synthetic worker crash followed by an explicitly
exited scope, and interrupted stops. Separate unit tests cover missing or
mismatched evidence, concurrent database writes, and audit rollback. These
tests do not establish containment against every host configuration or the
outcome of an external mutation.

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
