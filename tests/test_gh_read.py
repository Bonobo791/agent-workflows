import json
import os
import tempfile
import unittest
from pathlib import Path
from runner.gh_read import GHRead, GHReadError

class GHReadTests(unittest.TestCase):
    def make_cli(self,root,script):
        path=Path(root)/'gh';path.write_text('#!/usr/bin/python3\n'+script);path.chmod(0o700);return str(path)
    def test_read_only_and_environment_filtered(self):
        with tempfile.TemporaryDirectory() as root:
            binary=self.make_cli(root,'import json,os; print(json.dumps({"secret_present":"GH_TOKEN" in os.environ,"ok":True}))\n')
            client=GHRead(binary,{'HOME':root,'GH_TOKEN':'secret','PATH':'unsafe'})
            self.assertEqual(client.request('GET','/repos/owner/repo'),{'secret_present':False,'ok':True})
            with self.assertRaises(GHReadError):client.request('POST','/repos/owner/repo/pulls',{})
            with self.assertRaises(GHReadError):client.request('GET','/repos/{owner}/repo')
    def test_bad_json_and_binary_change_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            binary=self.make_cli(root,'print("not json")\n');client=GHRead(binary,{'HOME':root})
            with self.assertRaises(GHReadError):client.request('GET','/repos/owner/repo')
            Path(binary).write_text('#!/usr/bin/python3\nprint("{}")\n')
            with self.assertRaises(GHReadError):client.request('GET','/repos/owner/repo')
