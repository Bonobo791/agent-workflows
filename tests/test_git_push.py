import unittest
from runner.git_push import GitPushError, push_argv, require_fast_forward
from workflows.engineering.github import GitHubError

SHA='a'*40

class GitPushTests(unittest.TestCase):
    def test_push_requires_full_object_ids(self):
        with self.assertRaises(GitHubError): push_argv('owner/repo','feature',"abc",None)
        self.assertIn('--force-with-lease=refs/heads/feature:',push_argv('owner/repo','feature',SHA,None))

    def test_fast_forward_check_is_skipped_only_without_expected_remote(self):
        require_fast_forward('/nonexistent',None,SHA)
        with self.assertRaises(GitPushError): require_fast_forward('/nonexistent',SHA,SHA)

if __name__=='__main__': unittest.main()
