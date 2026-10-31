import json
import unittest
from runner.page_structure import check_page, policy_bytes


class PageStructureTests(unittest.TestCase):
    def setUp(self):
        self.rules={'guide':{'headings':[{'level':1,'text':'Guide'},{'level':2,'text':'Details'}]}}
        self.policy=json.loads(policy_bytes(self.rules,'guide'))
    def test_real_heading_structure_and_order(self):
        self.assertTrue(check_page('# Guide\n\n## Details\nText',self.policy))
        self.assertFalse(check_page('## Details\n\n# Guide',self.policy))
        self.assertFalse(check_page('# Guide\n\nText',self.policy))
    def test_hidden_or_code_headings_do_not_count(self):
        self.assertFalse(check_page('<!--\n# Guide\n## Details\n-->\n\nText',self.policy))
        self.assertFalse(check_page('```markdown\n# Guide\n## Details\n```',self.policy))
        with self.assertRaises(ValueError): check_page('# <span>Guide</span>\n\n## Details',self.policy)
    def test_missing_and_ambiguous_rules_are_rejected(self):
        with self.assertRaises(ValueError): policy_bytes(self.rules,'article')
        with self.assertRaises(ValueError): policy_bytes({'guide':{'headings':[]}},'guide')
        with self.assertRaises(ValueError): policy_bytes({'guide':{'headings':[{'level':True,'text':'Guide'}]}},'guide')
