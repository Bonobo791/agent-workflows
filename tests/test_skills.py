import tempfile
import unittest
from pathlib import Path
from runner.skills import SkillResolver, SkillError

class SkillTests(unittest.TestCase):
    def test_resolves_installed_skill_and_detects_changes(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'editing'; path.mkdir(); (path/'SKILL.md').write_text('first')
            resolver=SkillResolver([root]); first=resolver.resolve(['editing'])
            self.assertEqual(first['editing']['content'],'first')
            (path/'SKILL.md').write_text('second')
            self.assertNotEqual(first['editing']['sha256'],resolver.resolve(['editing'])['editing']['sha256'])
    def test_missing_or_escaping_skill_blocks(self):
        with tempfile.TemporaryDirectory() as root:
            resolver=SkillResolver([root])
            with self.assertRaises(SkillError): resolver.resolve(['missing'])
            with self.assertRaises(SkillError): resolver.resolve(['../outside'])
