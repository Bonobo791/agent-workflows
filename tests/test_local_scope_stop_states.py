import unittest
from runner.local_scope import LocalScopes, ScopeError

UNIT='agent-workflows-'+'a'*48+'.scope'
INVOCATION='b'*32
CGROUP='/user.slice/'+UNIT


class StoppedBackend:
    def __init__(self,active): self.active=active
    def inspect(self,unit): return {'id':unit,'load':'loaded','active':self.active,'invocation':INVOCATION,'cgroup':CGROUP,'result':'timeout'}
    def empty_cgroup(self,cgroup): return True
    def no_pending_job(self,unit): return True


class StoppedStateTests(unittest.TestCase):
    def scopes(self,active):
        scopes=LocalScopes.__new__(LocalScopes); scopes.backend=StoppedBackend(active); return scopes

    def test_scope_stopped_by_timeout_counts_as_quiescent(self):
        # systemd reports a scope killed after its stop timeout as 'failed'; the cgroup must still be empty.
        self.scopes('failed')._require_inactive(UNIT,INVOCATION,CGROUP)

    def test_running_scope_is_not_quiescent(self):
        with self.assertRaises(ScopeError): self.scopes('active')._require_inactive(UNIT,INVOCATION,CGROUP)


if __name__=='__main__':
    unittest.main()
