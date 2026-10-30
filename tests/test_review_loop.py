import tempfile
import unittest
from workflows.engineering.review_loop import BoundLedger, ReviewLoop, LoopError
from workflows.engineering.github import GitHubError

class HTTP:
    def __init__(self): self.head='a'*40; self.draft=True; self.fail=False; self.writes=0
    def request(self,method,path,body=None):
        if '/git/ref/heads/' in path: return {'object':{'sha':self.head}}
        if path.endswith('/pulls/7'):
            return {'number':7,'node_id':'node','draft':self.draft,'state':'open','title':'title','body':'body','head':{'sha':self.head,'ref':'branch','repo':{'full_name':'example/project'}},'base':{'ref':'main','repo':{'full_name':'example/project'}}}
        if 'check-runs' in path: return {'check_runs':[{'head_sha':self.head,'status':'completed','conclusion':'success'}]}
        if path.endswith('/status'): return {'state':'success'}
        if 'comments?' in path or '/reviews?' in path: return []
        if method=='POST':
            self.writes+=1
            if self.fail: raise TimeoutError()
            return {'number':7,'draft':True,'head':{'sha':self.head}}
        raise AssertionError(path)
    def push_exact(self,branch,sha):
        self.writes+=1; self.head=sha
        if self.fail: raise TimeoutError()
    def mark_ready(self,node): self.writes+=1; self.draft=False; return {'id':node,'isDraft':False}

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.now=100; self.http=HTTP()
        self.target={'task':'T-1','repo':'example/project','branch':'branch','base':'main','head':'b'*40,'diff':'c'*64}
        self.workspace=lambda run:dict(self.target)
        self.ledger=BoundLedger(self.tmp.name+'/state.db',self.workspace,lambda run:True,lambda:self.now)
        self.loop=ReviewLoop(self.http,'example','project',self.ledger,lambda run:True,self.workspace)
    def tearDown(self): self.ledger.db.close(); self.tmp.cleanup()
    def test_push_expiry_and_workspace_drift(self):
        self.loop.approve_push('r','a'*40,'owner',110)
        self.assertEqual(self.ledger.db.execute('SELECT actor FROM action_authorization_history').fetchone(),('owner',))
        self.now=111
        with self.assertRaises(GitHubError): self.loop.push('r','a'*40)
        self.now=100; self.target['diff']='d'*64
        with self.assertRaises(GitHubError): self.loop.push('r','a'*40)
        self.assertEqual(self.http.writes,0)
    def test_push_draft_ready_and_second_pass(self):
        self.loop.approve_push('r','a'*40,'owner',110); self.loop.push('r','a'*40)
        self.loop.approve_draft('r','title','body','owner',110); self.loop.draft('r','title','body')
        _,digest=self.loop.review('r',7,{})
        self.loop.approve_ready('r',7,'node',digest,'owner',110); self.loop.ready('r',7,'node',digest,{})
        self.assertTrue(self.ledger.pending_followups('r'))
        self.loop.post_ready('r',7,'node',{})
        self.assertFalse(self.ledger.pending_followups('r'))
        with self.assertRaises(GitHubError): self.loop.ready('r',7,'node',digest,{})
    def test_changed_checks_reject_readiness_approval(self):
        self.loop.approve_push('r','a'*40,'owner',110); self.loop.push('r','a'*40)
        self.loop.approve_draft('r','title','body','owner',110); self.loop.draft('r','title','body')
        _,digest=self.loop.review('r',7,{})
        self.loop.approve_ready('r',7,'node',digest,'owner',110)
        original=self.http.request
        def changed(method,path,body=None):
            if 'check-runs' in path: return {'check_runs':[{'head_sha':self.http.head,'status':'in_progress','conclusion':None}]}
            return original(method,path,body)
        self.http.request=changed
        with self.assertRaises(GitHubError): self.loop.ready('r',7,'node',digest,{})
        self.assertTrue(self.http.draft)

    def test_uncertain_requires_independent_quiescent_observation(self):
        action=self.loop.approve_push('r','a'*40,'owner',110); self.http.fail=True
        with self.assertRaises(GitHubError): self.loop.push('r','a'*40)
        scope='github:example/project:branch:branch'
        with self.assertRaises(LoopError): self.loop.reconcile('r',action,self.target,scope,lambda *x:{'quiescent':False,'outcome':'executed'})
        with self.assertRaises(GitHubError): self.loop.push('r','a'*40)
        self.loop.reconcile('r',action,self.target,scope,lambda *x:{'quiescent':True,'outcome':'executed'})
        self.assertEqual(self.ledger.db.execute('select status from external_actions').fetchone()[0],'executed')
        self.assertEqual(self.http.writes,1)
    def test_remote_head_drift_does_not_write(self):
        self.loop.approve_push('r','a'*40,'owner',110); self.http.head='e'*40
        with self.assertRaises(GitHubError): self.loop.push('r','a'*40)
        self.assertEqual(self.http.writes,0)
        self.assertEqual(self.ledger.db.execute('select status from external_actions').fetchone()[0],'not_executed')

if __name__=='__main__': unittest.main()
