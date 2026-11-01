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

    def test_complete_package_includes_nested_text_metadata_and_binary_assets(self):
        import hashlib
        with tempfile.TemporaryDirectory() as root:
            package=Path(root)/'seo';(package/'references').mkdir(parents=True);(package/'assets').mkdir()
            (package/'SKILL.md').write_text('Main instructions')
            (package/'references'/'unlinked.md').write_text('Reference guidance')
            (package/'agents.yaml').write_text('name: SEO')
            (package/'assets'/'icon.png').write_bytes(b'\x89PNG\r\n\x1a\n')
            resolved=SkillResolver([root]).resolve(['seo'])['seo']
            self.assertIn('Reference guidance',resolved['content'])
            self.assertIn('name: SEO',resolved['content'])
            self.assertIn('iVBORw0KGgo=',resolved['content'])
            self.assertEqual(set(resolved['files']),{'SKILL.md','references/unlinked.md','agents.yaml','assets/icon.png'})
            self.assertEqual(resolved['files']['references/unlinked.md'],hashlib.sha256(b'Reference guidance').hexdigest())
            self.assertEqual(resolved['sha256'],hashlib.sha256(resolved['content'].encode()).hexdigest())

    def test_reference_only_changes_invalidate_package_hash(self):
        with tempfile.TemporaryDirectory() as root:
            package=Path(root)/'seo';package.mkdir();(package/'SKILL.md').write_text('Unchanged instructions')
            reference=package/'reference.md';reference.write_text('first')
            resolver=SkillResolver([root]);before=resolver.resolve(['seo'])['seo']['sha256']
            reference.write_text('second')
            self.assertNotEqual(before,resolver.resolve(['seo'])['seo']['sha256'])

    def test_package_rejects_escaping_reference_and_oversized_file(self):
        with tempfile.TemporaryDirectory() as root,tempfile.TemporaryDirectory() as outside:
            package=Path(root)/'seo';package.mkdir();(package/'SKILL.md').write_text('Main')
            private=Path(outside)/'private.md';private.write_text('outside')
            reference=package/'reference.md';reference.symlink_to(private)
            with self.assertRaises(SkillError):SkillResolver([root]).resolve(['seo'])
            reference.unlink();reference.write_bytes(b'x'*262145)
            with self.assertRaises(SkillError):SkillResolver([root]).resolve(['seo'])

    def test_package_size_limit_covers_all_files_together(self):
        with tempfile.TemporaryDirectory() as root:
            package=Path(root)/'seo';package.mkdir();(package/'SKILL.md').write_text('Main')
            (package/'one.md').write_bytes(b'a'*140000);(package/'two.md').write_bytes(b'b'*140000)
            with self.assertRaises(SkillError):SkillResolver([root]).resolve(['seo'])

    def test_package_rejects_nonregular_files_without_waiting(self):
        import os
        with tempfile.TemporaryDirectory() as root:
            package=Path(root)/'seo';package.mkdir();(package/'SKILL.md').write_text('Main')
            os.mkfifo(package/'pipe')
            with self.assertRaises(SkillError):SkillResolver([root]).resolve(['seo'])

    def test_unreadable_reference_directory_is_not_silently_omitted(self):
        with tempfile.TemporaryDirectory() as root:
            package=Path(root)/'seo';reference=package/'references';reference.mkdir(parents=True)
            (package/'SKILL.md').write_text('Main');(reference/'guide.md').write_text('Required guidance')
            reference.chmod(0)
            try:
                with self.assertRaises(SkillError):SkillResolver([root]).resolve(['seo'])
            finally:reference.chmod(0o700)
