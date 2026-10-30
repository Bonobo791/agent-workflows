import tempfile
import unittest
from runner.actions import ActionLedger
from workflows.engineering.github import GitHub, GitHubError

class FakeHTTP:
    def __init__(self): self.calls=[]; self.head='a'*40
    def request(self,method,path,body=None):
        self.calls.append((method,path,body))
        if '/git/ref/heads/' in path: return {'object':{'sha':self.head}}
        if path.endswith('/pulls/3'): return {'head':{'sha':self.head}}
        if path.endswith('/pulls/7'): return {'number':7,'draft':True,'state':'open','title':'title','body':'body','head':{'sha':self.head,'ref':'branch','repo':{'full_name':'example/project'}},'base':{'ref':'main','repo':{'full_name':'example/project'}}}
        if 'check-runs' in path: return {'check_runs':[{'head_sha':self.head,'status':'completed','conclusion':'success'}]}
        if path.endswith('/status'): return {'state':'success','statuses':[]}
        if 'comments?' in path or '/reviews?' in path: return []
        if method=='POST': return {'number':7,'draft':True,'head':{'sha':self.head},'html_url':'https://github.com/example/project/pull/7'}
        raise AssertionError(path)

class GitHubTests(unittest.TestCase):
    def test_snapshot_includes_checks_and_both_comment_types(self):
        http=FakeHTTP(); client=GitHub(http,'example','project')
        result=client.review_snapshot(3,'a'*40)
        self.assertEqual(result['head'],'a'*40)
        self.assertEqual(len(result['checks']),2)
        self.assertEqual(len([p for _,p,_ in http.calls if 'comments?' in p]),2)
    def test_create_draft_requires_one_use_exact_approval(self):
        with tempfile.TemporaryDirectory() as root:
            ledger=ActionLedger(root+'/db'); http=FakeHTTP(); client=GitHub(http,'example','project',ledger,lock_check=lambda run: True)
            with self.assertRaises(GitHubError): client.create_draft('run','a'*40,'branch','main','title','body')
            ledger.approve('run',client.draft_action('branch','main','title','body'),'a'*40,'owner')
            self.assertEqual(client.create_draft('run','a'*40,'branch','main','title','body')['number'],7)
            with self.assertRaises(GitHubError): client.create_draft('run','a'*40,'branch','main','title','body')
            ledger.db.close()

    def test_mutation_draft_ref_change_does_not_consume_approval(self):
        with tempfile.TemporaryDirectory() as root:
            ledger=ActionLedger(root+'/db'); http=FakeHTTP(); client=GitHub(http,'example','project',ledger,lock_check=lambda run: True)
            ledger.approve('run',client.draft_action('branch','main','title','body'),'b'*40,'owner')
            with self.assertRaises(GitHubError): client.create_draft('run','b'*40,'branch','main','title','body')
            self.assertEqual(ledger.db.execute('select status from external_actions').fetchone()[0],'not_executed')
            ledger.db.close()

    def test_transport_rejects_external_or_malformed_paths(self):
        from workflows.engineering.github import HTTPSGitHub
        client=HTTPSGitHub('synthetic')
        with self.assertRaises(GitHubError): client.request('GET','https://evil.invalid/steal')
        with self.assertRaises(GitHubError): client.request('DELETE','/repos/example/project')

    def test_changes_requested_review_blocks_triage_even_with_empty_body(self):
        from workflows.engineering.triage import review_snapshot
        class Changes(FakeHTTP):
            def request(self,method,path,body=None):
                if '/reviews?' in path: return [{'id':10,'state':'CHANGES_REQUESTED','body':'','commit_id':'a'*40,'submitted_at':'2026-01-01T00:00:00Z','user':{'login':'reviewer'}}]
                return super().request(method,path,body)
        snapshot=GitHub(Changes(),'example','project').review_snapshot(3,'a'*40)
        self.assertFalse(review_snapshot(snapshot,'a'*40,{'review_decision:10':'invalid'}))

    def test_ready_requires_exact_approval_and_rechecks_head(self):
        class ReadyHTTP(FakeHTTP):
            def __init__(self): super().__init__(); self.draft=True
            def request(self,method,path,body=None):
                if path.endswith('/pulls/3'): return {'node_id':'node-3','draft':self.draft,'head':{'sha':self.head}}
                return super().request(method,path,body)
            def mark_ready(self,node): self.draft=False; return {'id':node,'isDraft':False}
        with tempfile.TemporaryDirectory() as root:
            ledger=ActionLedger(root+'/db'); http=ReadyHTTP(); client=GitHub(http,'example','project',ledger,lock_check=lambda run:True)
            snapshot=client.review_snapshot(3,'a'*40); digest=client.triage_digest(snapshot,{})
            with self.assertRaises(GitHubError): client.mark_ready('run',3,'node-3','a'*40,{},digest)
            ledger.approve('run',client.ready_action(3,'node-3',digest),'a'*40,'owner')
            client.mark_ready('run',3,'node-3','a'*40,{},digest)
            self.assertFalse(http.draft); ledger.db.close()

    def test_push_requires_exact_approval_and_verifies_remote(self):
        class PushHTTP(FakeHTTP):
            def push_exact(self,branch,sha): self.head=sha
        with tempfile.TemporaryDirectory() as root:
            ledger=ActionLedger(root+'/db'); http=PushHTTP(); client=GitHub(http,'example','project',ledger,lock_check=lambda run:True)
            action=client.push_action('branch','a'*40)
            ledger.approve('run',action,'b'*40,'owner')
            client.push('run','branch','a'*40,'b'*40)
            self.assertEqual(http.head,'b'*40); ledger.db.close()

    def test_git_push_uses_approved_commit_without_force(self):
        from workflows.engineering.github import GitPushTransport
        class Supervisor:
            def __init__(self): self.args=None
            def verify_isolated_git_context(self,cwd,url): return True
            def run(self,args,cwd,env): self.args=args; return 0
        supervisor=Supervisor(); push=GitPushTransport('/private/worktree','example','project',supervisor,{})
        push.push_exact('feature/test','a'*40)
        self.assertIn('a'*40+':refs/heads/feature/test',supervisor.args)
        self.assertFalse(any('force' in arg for arg in supervisor.args))

    def test_uncertain_draft_blocks_changed_payload_for_same_branch(self):
        class Timeout(FakeHTTP):
            def request(self,method,path,body=None):
                if method=='POST': raise TimeoutError()
                return super().request(method,path,body)
        with tempfile.TemporaryDirectory() as root:
            ledger=ActionLedger(root+'/db'); http=Timeout(); client=GitHub(http,'example','project',ledger,lock_check=lambda run:True)
            ledger.approve('run',client.draft_action('branch','main','one','body'),'a'*40,'owner')
            ledger.approve('run',client.draft_action('branch','main','two','body'),'a'*40,'owner')
            with self.assertRaises(GitHubError): client.create_draft('run','a'*40,'branch','main','one','body')
            with self.assertRaises(GitHubError): client.create_draft('run','a'*40,'branch','main','two','body')
            self.assertEqual(ledger.db.execute("SELECT count(*) FROM action_scopes").fetchone()[0],2); ledger.db.close()

    def test_push_can_create_new_branch_after_confirmed_absence(self):
        class NewBranch(FakeHTTP):
            def __init__(self): super().__init__(); self.head=None
            def request(self,method,path,body=None):
                if '/git/matching-refs/heads/' in path: return [] if self.head is None else [{'ref':'refs/heads/new-branch','object':{'sha':self.head}}]
                return super().request(method,path,body)
            def push_exact(self,branch,sha): self.head=sha
        with tempfile.TemporaryDirectory() as root:
            ledger=ActionLedger(root+'/db'); http=NewBranch(); client=GitHub(http,'example','project',ledger,lock_check=lambda run:True)
            ledger.approve('run',client.push_action('new-branch',None),'b'*40,'owner')
            client.push('run','new-branch',None,'b'*40)
            self.assertEqual(http.head,'b'*40); ledger.db.close()

    def test_post_ready_review_clears_followup_only_with_current_evidence(self):
        class ReadyHTTP(FakeHTTP):
            def __init__(self): super().__init__(); self.draft=True
            def request(self,method,path,body=None):
                if path.endswith('/pulls/3'): return {'node_id':'node-3','draft':self.draft,'head':{'sha':self.head}}
                return super().request(method,path,body)
            def mark_ready(self,node): self.draft=False; return {'id':node,'isDraft':False}
        with tempfile.TemporaryDirectory() as root:
            ledger=ActionLedger(root+'/db'); http=ReadyHTTP(); client=GitHub(http,'example','project',ledger,lock_check=lambda run:True)
            digest=client.triage_digest(client.review_snapshot(3,'a'*40),{})
            ledger.approve('run',client.ready_action(3,'node-3',digest),'a'*40,'owner')
            client.mark_ready('run',3,'node-3','a'*40,{},digest)
            self.assertTrue(ledger.pending_followups('run'))
            client.complete_post_ready_review('run',3,'node-3','a'*40,{})
            self.assertFalse(ledger.pending_followups('run')); ledger.db.close()
