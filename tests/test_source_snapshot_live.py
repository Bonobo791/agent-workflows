"""Opt-in read-only snapshot check against the public Example Domain fixture."""
import json
import os
import tempfile
import unittest

from runner.service import Service
from runner.source_fetch import SourceFetcher
from runner.source_snapshots import SourceSnapshots


@unittest.skipUnless(os.environ.get('WORKFLOW_TEST_PUBLIC_SOURCE')=='1','public source probe not configured')
class SourceSnapshotLiveTests(unittest.TestCase):
    def test_exact_public_url_snapshot_and_policy_invalidation(self):
        url='https://example.com/'
        register={'claims':[{'id':'claim-1','claim':'Example Domain is reserved for examples.','source_ids':['source-1']}],
                  'sources':[{'id':'source-1','url':url,'evidence_id':'evidence-1'}]}
        with tempfile.TemporaryDirectory(prefix='workflow-source-live-') as root:
            service=Service(root)
            try:
                service.create('r','marketing','synthetic'); claim=service.claim('r')
                register_ref=service.artifacts.put(json.dumps(register).encode())
                fetcher=SourceFetcher([url],timeout=15); snapshots=SourceSnapshots(service,fetcher)
                bundle=snapshots.capture('r',claim,register_ref,None,lambda:False)
                self.assertTrue(snapshots.verify('r',register_ref,None,bundle))
                items=snapshots.contents(bundle)
                self.assertEqual(len(items),1); self.assertEqual(items[0]['url'],url)
                self.assertIn('Example Domain',items[0]['content'])
                fetcher.max_bytes-=1
                self.assertFalse(snapshots.verify('r',register_ref,None,bundle))
            finally: service.close()
