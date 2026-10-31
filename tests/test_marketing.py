import unittest
from workflows.marketing import gates

class MarketingGatesTests(unittest.TestCase):
    def test_intake_reference_date_accepts_iso_and_rejects_invalid(self):
        task=dict(leaf=True,ready=True,dependencies_complete=True,owner=None,existing_work=[],due='2026-10-30',format='guide',task_id='T-1')
        self.assertIn('overdue requires review',gates.intake_issues(task,today='2026-10-31'))
        self.assertNotIn('invalid due date',gates.intake_issues(task,today='2026-10-31'))
        self.assertIn('invalid reference date',gates.intake_issues(task,today='not-a-date'))

    def test_noncanonical_due_date_is_rejected(self):
        task=dict(leaf=True,ready=True,dependencies_complete=True,owner=None,existing_work=[],due='20261030',format='guide',task_id='T-1')
        self.assertIn('invalid due date',gates.intake_issues(task))

    def test_intake_fails_closed_for_missing_evidence(self):
        self.assertTrue(gates.intake_issues({}))
        task = dict(leaf=True, ready=True, dependencies_complete=True, owner=None,
                    existing_work=[], due=None, format='guide', task_id='T-1')
        self.assertEqual([], gates.intake_issues(task))
        task['existing_work'] = ['draft']
        self.assertTrue(gates.intake_issues(task))

    def test_canonical_review_requires_all_independent_passes_and_skills(self):
        reviews = {name: {'status': 'passed', 'session_id': name, 'findings': []}
                   for name in ('editorial', 'factual', 'seo_geo')}
        self.assertEqual([], gates.canonical_issues(reviews, ['writer'], [], 'writer'))
        reviews['factual']['session_id'] = 'writer'
        self.assertTrue(gates.canonical_issues(reviews, ['writer'], [], 'writer'))
        reviews['factual']['session_id'] = 'factual'
        reviews['seo_geo']['findings'] = [{'severity': 'blocking', 'resolution': 'open'}]
        self.assertTrue(gates.canonical_issues(reviews, ['writer'], [], 'writer'))
        self.assertTrue(gates.canonical_issues(reviews, ['writer'], ['humanizer'], 'writer'))

    def test_social_generation_requires_frozen_canonical(self):
        self.assertFalse(gates.can_generate_social({'status': 'passed', 'version': ''}))
        self.assertTrue(gates.can_generate_social({'status': 'passed', 'version': 'v1'}))

    def test_instagram_needs_verified_real_assets(self):
        self.assertTrue(gates.channel_issues('instagram', {'asset': {'status': 'proposed'}}))
        self.assertEqual([], gates.channel_issues('instagram', {'asset': {'status': 'verified', 'ref': 'asset-v1'}}))

    def test_reddit_requires_rules_relevance_and_disclosure(self):
        package = {'community':'r/example', 'rules_ref':'rules-v1', 'rules_current':True,
                   'relevant':True, 'product_mentioned':True, 'affiliation_disclosed':True}
        self.assertEqual([], gates.channel_issues('reddit', package))
        package['affiliation_disclosed'] = False
        self.assertTrue(gates.channel_issues('reddit', package))
        package['relevant'] = False
        self.assertTrue(gates.channel_issues('reddit', package))

    def test_openfilm_never_claims_a_render_without_verified_output(self):
        self.assertEqual('blocked', gates.openfilm_status({'brief_ref':'brief-v1'}))
        self.assertEqual('blocked', gates.openfilm_status({'brief_ref':'brief-v1', 'render_ref':'v1'}))
        self.assertEqual('ready_for_review', gates.openfilm_status({'brief_ref':'brief-v1', 'project_ref':'p1', 'render_ref':'v1', 'verification_ref':'check-v1', 'verified':True}))

    def test_graph_keeps_channel_reviews_independent(self):
        deps = gates.STAGE_DEPENDENCIES
        self.assertEqual(('freeze',), deps['linkedin_draft'])
        self.assertEqual(('linkedin_draft',), deps['linkedin_review'])
        self.assertNotIn('reddit_review', deps['linkedin_review'])

if __name__ == '__main__': unittest.main()
