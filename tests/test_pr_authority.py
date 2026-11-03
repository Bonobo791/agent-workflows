import unittest
from tests.test_engineering_workspace import EngineeringWorkspaceTests
from runner.pr_authority import ReadAndIsolatedPush
from workflows.engineering.github import GitHubError

class PRAuthorityTests(EngineeringWorkspaceTests):
    def test_read_only_transport_rejects_direct_mutations(self):
        class HTTP:
            calls=[]
            def request(self,method,path,body=None): self.calls.append((method,path)); return {'ok':True}
        class Push:
            def push_exact(self,branch,sha): return True
        http=HTTP(); transport=ReadAndIsolatedPush(http,Push())
        self.assertTrue(transport.request('GET','/repos/owner/repo')['ok'])
        with self.assertRaises(GitHubError): transport.request('POST','/repos/owner/repo/pulls',{})
        with self.assertRaises(GitHubError): transport.mark_ready('node')
        self.assertEqual(http.calls,[('GET','/repos/owner/repo')])
