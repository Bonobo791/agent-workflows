import json
import tempfile
import unittest
from pathlib import Path
from workflows.marketing.adapters import MarketingAdapter, AdapterError
from runner.artifacts import ArtifactStore

class FakeWorker:
    def __init__(self, store): self.store=store; self.calls=[]
    def execute(self, run, stage, claim, inputs, prompt, model):
        self.calls.append((stage,inputs,prompt))
        output=self.store.put(json.dumps({'stage':stage,'status':'passed','input_versions':inputs['input_versions'],'outputs':[],'findings':[]}).encode())
        return output

class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.store=ArtifactStore(self.tmp.name)
        self.worker=FakeWorker(self.store); self.adapter=MarketingAdapter(self.store,self.worker)
        self.refs={name:self.store.put(json.dumps({'body':name}).encode()) for name in ('task','brief','canonical_draft','freeze','linkedin_draft')}
    def tearDown(self): self.tmp.cleanup()
    def test_selects_only_role_inputs(self):
        self.adapter.run('r','linkedin_review','claim',self.refs,'model')
        stage,inputs,prompt=self.worker.calls[-1]
        self.assertEqual([self.refs['linkedin_draft'],self.refs['freeze'],self.refs['task']],inputs['input_versions'])
        self.assertEqual({'linkedin_draft','freeze','task'},set(inputs['artifacts']))
        self.assertIn('linkedin',prompt.lower())
    def test_missing_or_invalid_input_blocks(self):
        with self.assertRaises(AdapterError): self.adapter.run('r','linkedin_review','claim',{},'model')
        with self.assertRaises(AdapterError): self.adapter.run('r','linkedin_review','claim',{'linkedin_draft':'missing'},'model')
    def test_ingestion_confines_path_and_stores_content(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root,'draft.txt').write_text('draft')
            ref=self.adapter.ingest(root,'draft.txt')
            self.assertEqual(b'draft',self.store.get(ref))
            with self.assertRaises(AdapterError): self.adapter.ingest(root,'../outside')
    def test_assembly_contains_artifacts_and_blocked_decisions(self):
        outputs={name:self.store.put(name.encode()) for name in ('canonical','linkedin','x','facebook','instagram','reddit','sources')}
        results={name:{'status':'passed','outputs':[ref]} for name,ref in outputs.items()}
        results['instagram']={'status':'blocked','outputs':[],'reason':'asset missing'}
        results['reddit']={'status':'blocked','outputs':[],'reason':'community unsuitable'}
        package=json.loads(self.store.get(self.adapter.assemble(results)))
        self.assertEqual('canonical',package['canonical']['contents'][0])
        self.assertEqual('sources',package['sources']['contents'][0])
        self.assertEqual('asset missing',package['instagram']['reason'])
        self.assertEqual('community unsuitable',package['reddit']['reason'])
        results['canonical']={'status':'blocked','outputs':[],'reason':'needs review'}
        with self.assertRaises(AdapterError): self.adapter.assemble(results)

if __name__=='__main__': unittest.main()
