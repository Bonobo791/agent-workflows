import tempfile
import unittest
import json
from runner.service import Service
from runner.stage_dispatch import StageDispatcher, StageError
from tests.source_fixture import outputs as fixture_outputs, verifier as fixture_verifier

class StageDispatchTests(unittest.TestCase):
    def test_verified_other_channel_asset_does_not_supply_instagram_media(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                manifest=service.artifacts.put(json.dumps({'assets':[{'channel':'linkedin','artifact_ref':'media'}]}).encode())
                record=service.artifacts.put(json.dumps({'status':'passed','manifest_ref':manifest}).encode())
                with patch.object(service.graph,'get',return_value={'status':'passed','version':record}), patch('runner.openfilm_review.verify_evidence',return_value=True):
                    self.assertIsNone(StageDispatcher(service)._instagram_asset_inputs('r'))
                with patch.object(service.graph,'get',return_value={'status':'passed','version':record}), patch('runner.openfilm_review.verify_evidence',return_value=False):
                    with self.assertRaises(StageError): StageDispatcher(service)._instagram_asset_inputs('r')
            finally: service.close()
    def test_binary_canonical_output_is_blocked_before_review(self):
        from pathlib import Path
        from runner.manual_lifecycle import ManualLifecycle
        from runner.skills import SkillResolver
        class Policy:
            def for_role(self,role): return {'model':'fixture','effort':'medium'}
        class Executor:
            def __init__(self,store): self.store=store
            def execute(self,**kw):
                data=b'\x89PNG\r\n\x1a\n' if kw['role']=='canonical_draft' else b'brief'
                ref=self.store.put(data); outputs=fixture_outputs(self.store,kw['role'],ref)
                return {'session_id':kw['role']+'-session','outputs':outputs,'output':json.dumps({'stage':kw['role'],'status':'passed','input_versions':kw['input_versions'],'outputs':outputs,'findings':[]})}
        with tempfile.TemporaryDirectory() as root:
            skill=Path(root)/'skills'/'role'; skill.mkdir(parents=True); (skill/'SKILL.md').write_text('fixture')
            service=Service(root); service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc')
            try:
                service.create('r','marketing','task'); service.enqueue('r'); queue=service.lifecycle.claim(); claim=service.claim('r')
                task={'task_id':'task','format':'guide','ready':True,'leaf':True,'dependencies_complete':True,'owner':None,'due':None,'existing_work':[]}
                ref=service.artifacts.put(json.dumps(task).encode()); service.graph.db.execute('CREATE TABLE task_input_versions(run TEXT PRIMARY KEY,source_version TEXT NOT NULL,artifact_ref TEXT NOT NULL)'); service.graph.db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',('r','v1',ref))
                service.source_verifier=fixture_verifier; service.docker_executor=Executor(service.artifacts); service.model_policy=Policy(); service.skill_resolver=SkillResolver([skill.parent]); service.required_skills={'brief':['role'],'canonical_draft':['role']}
                dispatcher=StageDispatcher(service)
                for _ in range(3): outcome=dispatcher.step('r',queue['token'],claim)
                self.assertEqual((outcome['stage'],outcome['status']),('canonical_draft','blocked'))
                self.assertIsNone(service.graph.get('r','canonical_review'))
            finally: service.lifecycle.close(); service.close()
    def test_source_only_review_block_does_not_request_draft_correction(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            try:
                service.create('r','marketing','task');claim=service.claim('r')
                subject=service.artifacts.put(b'draft')
                writer=service.artifacts.put(json.dumps({'outputs':[subject]}).encode())
                review=service.artifacts.put(json.dumps({'findings':[{'severity':'blocking','resolution':'open','evidence':'source-proof'}]}).encode())
                for stage,ref,status in [('intake',subject,'passed'),('brief',subject,'passed'),('canonical_draft',writer,'passed'),('canonical_review',review,'blocked')]:
                    service.graph.finish('r',stage,service.graph.prepare('r',stage,claim),status,ref,claim)
                self.assertEqual(StageDispatcher(service).next_stage('r',claim),'blocked')
            finally:service.close()
    def test_first_quota_error_is_reported_and_persisted_through_control(self):
        import json
        from pathlib import Path
        from runner.control import ControlPlane
        from runner.manual_lifecycle import ManualLifecycle
        from runner.protocol import RateLimited
        from runner.skills import SkillResolver
        class Policy:
            def for_role(self,role): return {'model':'fixture','effort':'medium'}
        class Executor:
            def execute(self,**kwargs): raise RateLimited('subscription limit reached')
        with tempfile.TemporaryDirectory() as root:
            skill=Path(root)/'skills'/'role'; skill.mkdir(parents=True); (skill/'SKILL.md').write_text('fixture')
            service=Service(root); service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc'); service.create('r','marketing','task'); service.enqueue('r')
            try:
                queue=service.lifecycle.claim(); claim=service.claim('r')
                task={'task_id':'task','format':'guide','ready':True,'leaf':True,'dependencies_complete':True,'owner':None,'due':None,'existing_work':[]}
                ref=service.artifacts.put(json.dumps(task).encode()); service.graph.db.execute('CREATE TABLE task_input_versions(run TEXT PRIMARY KEY,source_version TEXT,artifact_ref TEXT)'); service.graph.db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',('r','v1',ref))
                service.docker_executor=Executor(); service.model_policy=Policy(); service.skill_resolver=SkillResolver([skill.parent]); service.required_skills={}; service.stage_dispatcher=StageDispatcher(service)
                control=ControlPlane(service,'x'*40); request={'action':'dispatch_stage','run':'r','queue_claim':queue['token'],'graph_claim':claim}
                self.assertEqual(control.handle('x'*40,request)['stage'],'intake')
                self.assertEqual(control.handle('x'*40,request),{'stage':'brief','status':'blocked','reason':'required role skills unavailable'})
                service.required_skills={'brief':['role']}
                result=control.handle('x'*40,request)
                self.assertEqual(result,{'stage':'brief','status':'rate_limited','reason':'subscription quota paused; explicit resume required'})
                self.assertEqual(service.graph.get('r','brief')['status'],'rate_limited')
                self.assertTrue(service.graph.quota_paused('default'))
                self.assertEqual(control.handle('x'*40,request)['status'],'rate_limited')
                control.handle('x'*40,{'action':'resume_quota','scope':'default'})
                self.assertEqual(control.handle('x'*40,request)['status'],'rate_limited')
                control.handle('x'*40,{'action':'resume_rate_limited','run':'r','stage':'brief','claim':claim})
                self.assertEqual(control.handle('x'*40,request)['status'],'rate_limited')
                self.assertTrue(service.graph.quota_paused('default'))
            finally: service.lifecycle.close(); service.close()
    def test_selects_graph_stage_and_reports_approval_gate(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); service.create('r','marketing','task'); claim=service.claim('r')
            dispatcher=StageDispatcher(service)
            self.assertEqual(dispatcher.next_stage('r',claim),'intake')
            service.close()
    def test_does_not_dispatch_without_queue_or_configured_role_runtime(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); service.create('r','engineering','task'); claim=service.claim('r')
            dispatcher=StageDispatcher(service)
            with self.assertRaises(StageError): dispatcher.step('r','wrong',claim)
            self.assertIsNone(service.graph.get('r','intake')); service.close()
    def test_claimed_intake_advances_from_stored_versioned_task(self):
        import json
        from runner.manual_lifecycle import ManualLifecycle
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc')
            service.create('r','engineering','task'); service.enqueue('r'); queue=service.lifecycle.claim(); claim=service.claim('r')
            task={'task_id':'task','ready':True,'leaf':True,'dependencies_complete':True,'owner':None,'due':None,'existing_work':[]}
            ref=service.artifacts.put(json.dumps(task).encode())
            service.graph.db.execute('CREATE TABLE IF NOT EXISTS task_input_versions(run TEXT PRIMARY KEY, source_version TEXT NOT NULL, artifact_ref TEXT NOT NULL)')
            service.graph.db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',('r','v1',ref))
            dispatcher=StageDispatcher(service)
            result=dispatcher.step('r',queue['token'],claim)
            self.assertEqual(result['stage'],'intake'); self.assertEqual(service.graph.get('r','intake')['status'],'passed')
            self.assertEqual(dispatcher.next_stage('r',claim),'plan')
            service.lifecycle.close(); service.close()
    def test_plan_runs_executor_with_versioned_input_and_records_output(self):
        import hashlib, json
        from pathlib import Path
        from runner.manual_lifecycle import ManualLifecycle
        from runner.skills import SkillResolver
        class Policy:
            def for_role(self,role): return {'model':'synthetic-terra','effort':'medium'}
        class Executor:
            def __init__(self,store): self.store=store; self.calls=[]
            def execute(self,**kwargs):
                self.calls.append(kwargs); ref=self.store.put(b'synthetic plan')
                output=json.dumps({'stage':kwargs['role'],'status':'passed','input_versions':kwargs['input_versions'],'outputs':[ref],'findings':[]})
                return {'session_id':'plan-session','output':output,'outputs':[ref]}
        with tempfile.TemporaryDirectory() as root:
            skill=Path(root)/'skills'/'planner'; skill.mkdir(parents=True); (skill/'SKILL.md').write_text('Synthetic planning instructions')
            service=Service(root); service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc')
            service.create('r','engineering','task'); service.enqueue('r'); queue=service.lifecycle.claim(); claim=service.claim('r')
            task={'task_id':'task','ready':True,'leaf':True,'dependencies_complete':True,'owner':None,'due':None,'existing_work':[]}
            ref=service.artifacts.put(json.dumps(task).encode()); service.graph.db.execute('CREATE TABLE IF NOT EXISTS task_input_versions(run TEXT PRIMARY KEY, source_version TEXT NOT NULL, artifact_ref TEXT NOT NULL)'); service.graph.db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',('r','v1',ref))
            service.docker_executor=Executor(service.artifacts); service.model_policy=Policy(); service.skill_resolver=SkillResolver([Path(root)/'skills']); service.required_skills={'plan':['planner']}
            dispatcher=StageDispatcher(service); dispatcher.step('r',queue['token'],claim); result=dispatcher.step('r',queue['token'],claim)
            self.assertEqual(result['stage'],'plan'); self.assertEqual(service.graph.get('r','plan')['status'],'passed')
            self.assertEqual(service.docker_executor.calls[0]['input_versions'],[ref]); self.assertEqual(service.docker_executor.calls[0]['skills']['planner'],hashlib.sha256(b'Synthetic planning instructions').hexdigest())
            service.lifecycle.close(); service.close()
    def test_worker_control_dispatches_intake_via_registered_stage_loop(self):
        import json
        from runner.manual_lifecycle import ManualLifecycle
        from runner.worker import build_control
        with tempfile.TemporaryDirectory() as root:
            service,control=build_control(root,'x'*40,lifecycle_factory=lambda service:ManualLifecycle(root+'/state.db','worker','inc'))
            try:
                service.create('r','engineering','task'); service.enqueue('r')
                queue=control.handle('x'*40,{'action':'manual_claim'})['claim']; claim=control.handle('x'*40,{'action':'claim','run':'r'})['claim']
                task={'task_id':'task','ready':True,'leaf':True,'dependencies_complete':True,'owner':None,'due':None,'existing_work':[]}
                ref=service.artifacts.put(json.dumps(task).encode()); service.graph.db.execute('CREATE TABLE IF NOT EXISTS task_input_versions(run TEXT PRIMARY KEY, source_version TEXT NOT NULL, artifact_ref TEXT NOT NULL)'); service.graph.db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',('r','v1',ref))
                result=control.handle('x'*40,{'action':'dispatch_stage','run':'r','queue_claim':queue['token'],'graph_claim':claim})
                self.assertEqual(result,{'stage':'intake','status':'passed'})
            finally: service.lifecycle.close(); service.close()
    def test_brief_blocks_when_independent_source_verification_unavailable(self):
        import json
        from pathlib import Path
        from runner.manual_lifecycle import ManualLifecycle
        from runner.skills import SkillResolver
        class Policy:
            def for_role(self,role): return {'model':'fixture','effort':'medium'}
        class Executor:
            def __init__(self,store): self.store=store
            def execute(self,**kw):
                refs=fixture_outputs(self.store,kw['role'],self.store.put(b'brief'))
                return {'session_id':'brief-session','outputs':refs,'output':json.dumps({'stage':kw['role'],'status':'passed','input_versions':kw['input_versions'],'outputs':refs,'findings':[]})}
        with tempfile.TemporaryDirectory() as root:
            skill=Path(root)/'skills'/'role'; skill.mkdir(parents=True); (skill/'SKILL.md').write_text('fixture')
            service=Service(root); service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc'); service.create('r','marketing','task'); service.enqueue('r'); queue=service.lifecycle.claim(); claim=service.claim('r')
            try:
                task={'task_id':'task','format':'guide','ready':True,'leaf':True,'dependencies_complete':True,'owner':None,'due':None,'existing_work':[]}
                ref=service.artifacts.put(json.dumps(task).encode()); service.graph.db.execute('CREATE TABLE task_input_versions(run TEXT PRIMARY KEY,source_version TEXT,artifact_ref TEXT)'); service.graph.db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',('r','v1',ref))
                service.docker_executor=Executor(service.artifacts); service.model_policy=Policy(); service.skill_resolver=SkillResolver([skill.parent]); service.required_skills={'brief':['role']}
                dispatcher=StageDispatcher(service); dispatcher.step('r',queue['token'],claim)
                result=dispatcher.step('r',queue['token'],claim); self.assertEqual(result['status'],'blocked')
                record=json.loads(service.artifacts.get(result['artifact_ref'])); self.assertIn('verification',record['_blocked_reason'])
                self.assertIsNone(service.graph.get('r','canonical_draft'))
            finally: service.lifecycle.close(); service.close()
    def test_canonical_draft_consumes_versioned_brief_and_records_writer_session(self):
        import json
        from pathlib import Path
        from runner.manual_lifecycle import ManualLifecycle
        from runner.skills import SkillResolver
        class Policy:
            def for_role(self,role): return {'model':'synthetic-terra','effort':'medium'}
        class Executor:
            def __init__(self,store): self.store=store; self.calls=[]
            def execute(self,**kw):
                self.calls.append(kw); ref=self.store.put(('synthetic '+kw['role']).encode()); refs=fixture_outputs(self.store,kw['role'],ref)
                return {'session_id':kw['role']+'-session','outputs':refs,'output':json.dumps({'stage':kw['role'],'status':'passed','input_versions':kw['input_versions'],'outputs':refs,'findings':[]})}
        with tempfile.TemporaryDirectory() as root:
            skills=Path(root)/'skills'
            for name in ('brief','writer'):
                (skills/name).mkdir(parents=True); (skills/name/'SKILL.md').write_text('Synthetic '+name)
            service=Service(root); service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc'); service.create('r','marketing','task'); service.enqueue('r'); queue=service.lifecycle.claim(); claim=service.claim('r')
            task={'task_id':'task','format':'guide','ready':True,'leaf':True,'dependencies_complete':True,'owner':None,'due':None,'existing_work':[]}
            ref=service.artifacts.put(json.dumps(task).encode()); service.graph.db.execute('CREATE TABLE IF NOT EXISTS task_input_versions(run TEXT PRIMARY KEY, source_version TEXT NOT NULL, artifact_ref TEXT NOT NULL)'); service.graph.db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',('r','v1',ref))
            service.source_verifier=fixture_verifier; service.docker_executor=Executor(service.artifacts); service.model_policy=Policy(); service.skill_resolver=SkillResolver([skills]); service.required_skills={'brief':['brief'],'canonical_draft':['writer']}
            dispatcher=StageDispatcher(service)
            for expected in ('intake','brief','canonical_draft'): self.assertEqual(dispatcher.step('r',queue['token'],claim)['stage'],expected)
            brief=json.loads(service.artifacts.get(service.graph.get('r','brief')['version']))
            self.assertEqual(service.docker_executor.calls[-1]['input_versions'],[brief['outputs'][0],brief['outputs'][1],brief['_source_verification']])
            prompt=service.docker_executor.calls[-1]['prompt']
            self.assertIn('source_register',prompt); self.assertIn('source_verification',prompt)
            self.assertEqual(service.graph.session('r','canonical_draft'),'canonical_draft-session')
            service.lifecycle.close(); service.close()
    def test_review_requires_a_fresh_subject_bound_session(self):
        import hashlib, json
        from pathlib import Path
        from runner.manual_lifecycle import ManualLifecycle
        from runner.skills import SkillResolver
        class Policy:
            def for_role(self,role): return {'model':'synthetic-terra','effort':'medium'}
        class Executor:
            def __init__(self,service): self.service=service; self.reviews=0
            def execute(self,**kw):
                ref=self.service.artifacts.put((('# Fixture\n\n' if kw['role']=='canonical_draft' else '')+'synthetic '+kw['role']).encode()); refs=fixture_outputs(self.service.artifacts,kw['role'],ref); return {'session_id':kw['role']+'-session','outputs':refs,'output':json.dumps({'stage':kw['role'],'status':'passed','input_versions':kw['input_versions'],'outputs':refs,'findings':[]})}
            def review(self,subject,**kw):
                self.assert_prompt=kw['prompt']
                self.reviews+=1; ref=self.service.artifacts.put(b'synthetic review'); session='review-session-'+str(self.reviews)
                output=json.dumps({'stage':kw['role'],'status':'passed','input_versions':kw['input_versions'],'outputs':[ref],'findings':[]})
                self.service.provenance.bind(kw['run'],kw['role'],session,kw['skills'],kw['claim'],fresh=True,subject=subject)
                self.service.provenance.mark_completed(kw['run'],kw['role'],session,kw['claim'],hashlib.sha256(output.encode()).hexdigest())
                proof={'run':kw['run'],'stage':'canonical_draft','subject_hash':subject,'reviewer_role':kw['role'],'reviewer_session':session,'skills':kw['skills'],'review_output_ref':self.service.artifacts.put(output.encode())}
                return {'session_id':session,'outputs':[ref],'output':output,'review_status':'passed','evidence_ref':self.service.artifacts.put(json.dumps(proof).encode())}
        with tempfile.TemporaryDirectory() as root:
            skills=Path(root)/'skills'
            for name in ('brief','writer','reviewer'):
                (skills/name).mkdir(parents=True); (skills/name/'SKILL.md').write_text('Synthetic '+name)
            service=Service(root); service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc'); service.create('r','marketing','task'); service.enqueue('r'); queue=service.lifecycle.claim(); claim=service.claim('r')
            task={'task_id':'task','format':'guide','ready':True,'leaf':True,'dependencies_complete':True,'owner':None,'due':None,'existing_work':[]}
            ref=service.artifacts.put(json.dumps(task).encode()); service.graph.db.execute('CREATE TABLE IF NOT EXISTS task_input_versions(run TEXT PRIMARY KEY, source_version TEXT NOT NULL, artifact_ref TEXT NOT NULL)'); service.graph.db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',('r','v1',ref))
            service.source_verifier=fixture_verifier; service.docker_executor=Executor(service); service.model_policy=Policy(); service.skill_resolver=SkillResolver([skills]); service.required_skills={'brief':['brief'],'canonical_draft':['writer'],'canonical_review':['reviewer']}
            dispatcher=StageDispatcher(service)
            for expected in ('intake','brief','canonical_draft'): self.assertEqual(dispatcher.step('r',queue['token'],claim)['stage'],expected)
            missing=dispatcher.step('r',queue['token'],claim)
            self.assertEqual((missing['stage'],missing['status']),('canonical_review','blocked'))
            self.assertEqual(dispatcher.next_stage('r',claim),'blocked')
            service.graph.invalidate('r','canonical_review',service.graph.prepare('r','canonical_review',claim),claim)
            service.page_rules={'guide':{'headings':[{'level':1,'text':'Fixture'}]}}
            self.assertEqual(dispatcher.step('r',queue['token'],claim)['stage'],'canonical_review')
            self.assertEqual(service.graph.get('r','canonical_review')['status'],'passed')
            self.assertIn('Each finding evidence must exactly match one SHA-256 string in input_versions',service.docker_executor.assert_prompt)
            self.assertIn('Return only passed or blocked',service.docker_executor.assert_prompt)
            self.assertEqual(dispatcher.next_stage('r',claim),'freeze')
            service.page_rules={'guide':{'headings':[{'level':1,'text':'Changed'}]}}
            with self.assertRaises(StageError): dispatcher._verify_recorded_review('r','canonical_review')
            service.page_rules={'guide':{'headings':[{'level':1,'text':'Fixture'}]}}
            self.assertTrue(dispatcher._verify_recorded_review('r','canonical_review'))
            review=service.graph.get('r','canonical_review'); record=json.loads(service.artifacts.get(review['version']))
            (Path(service.artifacts.directory)/record['_review_evidence']).unlink()
            service.approve('r','freeze',review['version'],'operator')
            with self.assertRaises(StageError): dispatcher.step('r',queue['token'],claim)
            service.lifecycle.close(); service.close()
    def test_blocked_review_triggers_one_resumed_writer_correction(self):
        import hashlib, json
        from pathlib import Path
        from runner.manual_lifecycle import ManualLifecycle
        from runner.skills import SkillResolver
        class Policy:
            def for_role(self,role): return {'model':'synthetic-terra','effort':'medium'}
        class Executor:
            def __init__(self,service): self.service=service; self.writer_sessions=[]; self.reviews=0
            def execute(self,**kw):
                if kw['role']=='canonical_draft': self.writer_sessions.append(kw.get('session_id'))
                ref=self.service.artifacts.put((('# Fixture\n\n' if kw['role']=='canonical_draft' else '')+'content '+kw['role']+str(len(self.writer_sessions))).encode()); refs=fixture_outputs(self.service.artifacts,kw['role'],ref)
                return {'session_id':kw.get('session_id') or kw['role']+'-session','outputs':refs,'output':json.dumps({'stage':kw['role'],'status':'passed','input_versions':kw['input_versions'],'outputs':refs,'findings':[]})}
            def review(self,subject,**kw):
                self.reviews+=1; session='review-'+str(self.reviews); ref=self.service.artifacts.put(('review '+str(self.reviews)).encode())
                status='blocked' if self.reviews==1 else 'passed'
                findings=[{'id':'f1','severity':'blocking','description':'Fix source','location':'draft','evidence':subject,'remedy':'Add source','resolution':'open'}] if status=='blocked' else []
                value={'stage':kw['role'],'status':status,'input_versions':kw['input_versions'],'outputs':[ref],'findings':findings}
                if status=='blocked': value['reason']='correction required'
                output=json.dumps(value); self.service.provenance.bind(kw['run'],kw['role'],session,kw['skills'],kw['claim'],fresh=True,subject=subject)
                self.service.provenance.mark_completed(kw['run'],kw['role'],session,kw['claim'],hashlib.sha256(output.encode()).hexdigest())
                proof={'run':kw['run'],'stage':'canonical_draft','subject_hash':subject,'reviewer_role':kw['role'],'reviewer_session':session,'skills':kw['skills'],'review_output_ref':self.service.artifacts.put(output.encode())}
                return {'session_id':session,'outputs':[ref],'output':output,'review_status':status,'evidence_ref':self.service.artifacts.put(json.dumps(proof).encode())}
        with tempfile.TemporaryDirectory() as root:
            skills=Path(root)/'skills'
            for name in ('brief','writer','reviewer'):
                (skills/name).mkdir(parents=True); (skills/name/'SKILL.md').write_text('Synthetic '+name)
            service=Service(root); service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc'); service.create('r','marketing','task'); service.enqueue('r'); queue=service.lifecycle.claim(); claim=service.claim('r')
            task={'task_id':'task','format':'guide','ready':True,'leaf':True,'dependencies_complete':True,'owner':None,'due':None,'existing_work':[]}
            ref=service.artifacts.put(json.dumps(task).encode()); service.graph.db.execute('CREATE TABLE IF NOT EXISTS task_input_versions(run TEXT PRIMARY KEY, source_version TEXT NOT NULL, artifact_ref TEXT NOT NULL)'); service.graph.db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',('r','v1',ref))
            service.source_verifier=fixture_verifier; service.docker_executor=Executor(service); service.model_policy=Policy(); service.skill_resolver=SkillResolver([skills]); service.required_skills={'brief':['brief'],'canonical_draft':['writer'],'canonical_review':['reviewer']}
            service.page_rules={'guide':{'headings':[{'level':1,'text':'Fixture'}]}}
            dispatcher=StageDispatcher(service)
            for expected in ('intake','brief','canonical_draft','canonical_review'): self.assertEqual(dispatcher.step('r',queue['token'],claim)['stage'],expected)
            self.assertEqual(service.graph.get('r','canonical_review')['status'],'blocked')
            original_writer=service.graph.get('r','canonical_draft'); original_review=service.graph.get('r','canonical_review')
            reserved=dispatcher._reserve_correction('r','canonical_draft','canonical_review',claim)
            self.assertEqual(dispatcher.recover_correction('r',queue['token'],claim)['correction_id'],reserved)
            for outcome in ('not_executed','failed'):
                reserved=dispatcher._reserve_correction('r','canonical_draft','canonical_review',claim)
                version='fixture-'+outcome
                service.graph.begin_attempt('r','canonical_draft',version,claim,2,correction_id=reserved)
                with self.assertRaises(StageError): dispatcher.recover_correction('r',queue['token'],claim)
                self.assertEqual(service.graph.get('r','canonical_draft'),original_writer)
                self.assertEqual(service.graph.get('r','canonical_review'),original_review)
                service.graph.resolve_attempt('r','canonical_draft',version,claim,outcome)
                self.assertEqual(dispatcher.recover_correction('r',queue['token'],claim)['correction_id'],reserved)
            reserved=dispatcher._reserve_correction('r','canonical_draft','canonical_review',claim)
            service.graph.begin_attempt('r','canonical_draft','terminated-fixture',claim,2,correction_id=reserved)
            service.graph.resolve_attempt('r','canonical_draft','terminated-fixture',claim,'failed')
            db=service.graph.db
            db.execute('CREATE TABLE docker_execution_map(task_run TEXT,role TEXT,assignment TEXT)'); db.execute('CREATE TABLE docker_assignments(run TEXT,container_id TEXT,state TEXT)'); db.execute('CREATE TABLE docker_terminal_evidence(run TEXT,container_id TEXT)')
            db.execute('INSERT INTO docker_execution_map VALUES(?,?,?)',('r','canonical_draft','job-1')); db.execute('INSERT INTO docker_assignments VALUES(?,?,?)',('job-1','id-1','terminated'))
            with self.assertRaises(StageError): dispatcher.recover_correction('r',queue['token'],claim)
            db.execute('INSERT INTO docker_terminal_evidence VALUES(?,?)',('job-1','id-1'))
            self.assertEqual(dispatcher.recover_correction('r',queue['token'],claim)['correction_id'],reserved)
            self.assertEqual(dispatcher.step('r',queue['token'],claim)['stage'],'canonical_draft')
            self.assertIsNotNone(service.artifacts.get(original_writer['version']))
            self.assertIsNotNone(service.artifacts.get(original_review['version']))
            self.assertEqual(service.docker_executor.writer_sessions,[None,'canonical_draft-session'])
            self.assertIsNone(service.graph.get('r','canonical_review'))
            self.assertEqual(dispatcher.step('r',queue['token'],claim)['status'],'passed')
            self.assertEqual(service.docker_executor.reviews,2)
            service.lifecycle.close(); service.close()
    def test_missing_heading_produces_bound_correction_and_fresh_review(self):
        import hashlib, json
        from pathlib import Path
        from runner.manual_lifecycle import ManualLifecycle
        from runner.skills import SkillResolver
        class Policy:
            def for_role(self,role): return {'model':'fixture','effort':'medium'}
        class Executor:
            def __init__(self,service): self.service=service; self.writer_calls=0; self.reviews=0
            def execute(self,**kw):
                if kw['role']=='canonical_draft':
                    self.writer_calls+=1
                    if self.writer_calls==2:
                        self.assert_feedback= 'page_structure_judgment' in kw['prompt']
                        self.correction_payload=json.JSONDecoder().raw_decode(kw['prompt'].split('\n',1)[1])[0]
                    data=b'No required heading' if self.writer_calls==1 else b'# Fixture\n\nCorrected text'
                else: data=b'synthetic brief'
                ref=self.service.artifacts.put(data); refs=fixture_outputs(self.service.artifacts,kw['role'],ref)
                return {'session_id':kw.get('session_id') or kw['role']+'-session','outputs':refs,'output':json.dumps({'stage':kw['role'],'status':'passed','input_versions':kw['input_versions'],'outputs':refs,'findings':[]})}
            def review(self,subject,**kw):
                self.reviews+=1; session='review-'+str(self.reviews); ref=self.service.artifacts.put(b'review')
                output=json.dumps({'stage':kw['role'],'status':'passed','input_versions':kw['input_versions'],'outputs':[ref],'findings':[]})
                self.service.provenance.bind(kw['run'],kw['role'],session,kw['skills'],kw['claim'],fresh=True,subject=subject)
                self.service.provenance.mark_completed(kw['run'],kw['role'],session,kw['claim'],hashlib.sha256(output.encode()).hexdigest())
                proof={'run':kw['run'],'stage':'canonical_draft','subject_hash':subject,'reviewer_role':kw['role'],'reviewer_session':session,'skills':kw['skills'],'review_output_ref':self.service.artifacts.put(output.encode())}
                return {'session_id':session,'outputs':[ref],'output':output,'evidence_ref':self.service.artifacts.put(json.dumps(proof).encode())}
        with tempfile.TemporaryDirectory() as root:
            skills=Path(root)/'skills'; (skills/'role').mkdir(parents=True); (skills/'role'/'SKILL.md').write_text('Synthetic role')
            service=Service(root); service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc'); service.create('r','marketing','task'); service.enqueue('r'); queue=service.lifecycle.claim(); claim=service.claim('r')
            try:
                task={'task_id':'task','format':'guide','ready':True,'leaf':True,'dependencies_complete':True,'owner':None,'due':None,'existing_work':[]}
                ref=service.artifacts.put(json.dumps(task).encode()); service.graph.db.execute('CREATE TABLE task_input_versions(run TEXT PRIMARY KEY,source_version TEXT NOT NULL,artifact_ref TEXT NOT NULL)'); service.graph.db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',('r','v1',ref))
                service.source_verifier=fixture_verifier; service.docker_executor=Executor(service); service.model_policy=Policy(); service.skill_resolver=SkillResolver([skills]); service.required_skills={role:['role'] for role in ('brief','canonical_draft','canonical_review')}
                service.page_rules={'guide':{'headings':[{'level':1,'text':'Fixture'}]}}; dispatcher=StageDispatcher(service)
                for _ in range(4): result=dispatcher.step('r',queue['token'],claim)
                self.assertEqual((result['stage'],result['status']),('canonical_review','blocked'))
                record=json.loads(service.artifacts.get(result['artifact_ref'])); self.assertIn('_page_structure_blocked',record)
                service.page_rules={'guide':{'headings':[{'level':1,'text':'Changed'}]}}
                with self.assertRaises(StageError): dispatcher._model_stage('r','canonical_draft',queue['token'],claim,feedback_ref=result['artifact_ref'])
                self.assertEqual(service.docker_executor.writer_calls,1)
                service.page_rules={'guide':{'headings':[{'level':1,'text':'Fixture'}]}}
                self.assertEqual(dispatcher.step('r',queue['token'],claim)['stage'],'canonical_draft')
                self.assertTrue(service.docker_executor.assert_feedback)
                payload=service.docker_executor.correction_payload
                self.assertEqual(payload['artifacts']['page_rules'],{'format':'guide','headings':[{'level':1,'text':'Fixture'}]})
                self.assertIn(record['_page_rules'],payload['input_versions'])
                self.assertEqual(dispatcher.step('r',queue['token'],claim)['status'],'passed')
                self.assertEqual(service.docker_executor.reviews,2)
            finally: service.lifecycle.close(); service.close()
    def test_marketing_fanout_assembles_with_asset_blocker_after_freeze(self):
        import hashlib, json
        from pathlib import Path
        from runner.manual_lifecycle import ManualLifecycle
        from runner.skills import SkillResolver
        from workflows.marketing.gates import CHANNELS
        class Policy:
            def for_role(self,role): return {'model':'synthetic-terra','effort':'medium'}
        class Executor:
            def __init__(self,service): self.service=service; self.counter=0
            def execute(self,**kw):
                self.counter+=1; ref=self.service.artifacts.put((('# Fixture\n\n' if kw['role']=='canonical_draft' else '')+'content '+kw['role']).encode()); refs=fixture_outputs(self.service.artifacts,kw['role'],ref)
                return {'session_id':kw.get('session_id') or 'session-'+str(self.counter),'outputs':refs,'output':json.dumps({'stage':kw['role'],'status':'passed','input_versions':kw['input_versions'],'outputs':refs,'findings':[]})}
            def review(self,subject,**kw):
                self.counter+=1; session='review-'+str(self.counter); ref=self.service.artifacts.put(('review '+kw['role']).encode())
                output=json.dumps({'stage':kw['role'],'status':'passed','input_versions':kw['input_versions'],'outputs':[ref],'findings':[]})
                self.service.provenance.bind(kw['run'],kw['role'],session,kw['skills'],kw['claim'],fresh=True,subject=subject)
                self.service.provenance.mark_completed(kw['run'],kw['role'],session,kw['claim'],hashlib.sha256(output.encode()).hexdigest())
                proof={'run':kw['run'],'stage':kw['role'].removesuffix('_review')+'_draft','subject_hash':subject,'reviewer_role':kw['role'],'reviewer_session':session,'skills':kw['skills'],'review_output_ref':self.service.artifacts.put(output.encode())}
                return {'session_id':session,'outputs':[ref],'output':output,'review_status':'passed','evidence_ref':self.service.artifacts.put(json.dumps(proof).encode())}
        with tempfile.TemporaryDirectory() as root:
            skills=Path(root)/'skills'; (skills/'role').mkdir(parents=True); (skills/'role'/'SKILL.md').write_text('Synthetic role')
            service=Service(root); service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc'); service.create('r','marketing','task'); service.enqueue('r'); queue=service.lifecycle.claim(); claim=service.claim('r')
            task={'task_id':'task','format':'guide','ready':True,'leaf':True,'dependencies_complete':True,'owner':None,'due':None,'existing_work':[]}
            ref=service.artifacts.put(json.dumps(task).encode()); service.graph.db.execute('CREATE TABLE IF NOT EXISTS task_input_versions(run TEXT PRIMARY KEY, source_version TEXT NOT NULL, artifact_ref TEXT NOT NULL)'); service.graph.db.execute('INSERT INTO task_input_versions VALUES(?,?,?)',('r','v1',ref))
            service.source_verifier=fixture_verifier; service.docker_executor=Executor(service); service.model_policy=Policy(); service.skill_resolver=SkillResolver([skills])
            service.page_rules={'guide':{'headings':[{'level':1,'text':'Fixture'}]}}
            roles=['brief','canonical_draft','canonical_review','asset_brief']+[c+suffix for c in CHANNELS for suffix in ('_draft','_review')]
            service.required_skills={role:['role'] for role in roles}; dispatcher=StageDispatcher(service)
            for _ in range(4): dispatcher.step('r',queue['token'],claim)
            self.assertEqual(dispatcher.step('r',queue['token'],claim)['status'],'awaiting_approval')
            from runner.metrics import snapshot, prometheus_text
            wait_metric='workflow_stages{workflow="marketing",stage="freeze",status="awaiting_approval"} 1'
            self.assertIn(wait_metric,prometheus_text(snapshot(service.graph.db)))
            from types import SimpleNamespace
            from runner.checkpoint import checkpoint
            from runner.local_scope import LocalScopes
            from tests.test_local_scope import Backend
            scopes=LocalScopes(root+'/state.db',Backend());scopes.close()
            service.graph.db.execute('CREATE TABLE local_sessions(run TEXT,role TEXT,session TEXT,binding TEXT,state TEXT)')
            service.model_supervisor=SimpleNamespace(db=root+'/state.db')
            review_version=service.graph.get('r','canonical_review')['version']
            service.approve('r','freeze',review_version,'operator')
            from runner.control import ControlPlane
            control=ControlPlane(service,'secret')
            self.assertTrue(control.handle('secret',{'action':'revoke_freeze_approval','run':'r','version':review_version})['revoked'])
            old_queue,old_claim=queue['token'],claim
            checkpoint(service,'r',old_queue,old_claim)
            with self.assertRaises(StageError): dispatcher.step('r',old_queue,old_claim)
            self.assertNotIn(wait_metric,prometheus_text(snapshot(service.graph.db)))
            service.lifecycle.register_checkpoint_replacement('r','inc','inc-next')
            service.lifecycle.close();service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc-next')
            queue=service.lifecycle.claim();claim=service.claim('r');service.model_supervisor=None
            from runner.graph import GraphError
            with self.assertRaises(GraphError): dispatcher.step('r',queue['token'],old_claim)
            self.assertEqual(dispatcher.step('r',queue['token'],claim)['status'],'awaiting_approval')
            self.assertIn(wait_metric,prometheus_text(snapshot(service.graph.db)))
            service.approve('r','freeze',service.graph.get('r','canonical_review')['version'],'operator')
            self.assertNotIn('stage="freeze",status="awaiting_approval"',prometheus_text(snapshot(service.graph.db)))
            for _ in range(30):
                result=dispatcher.step('r',queue['token'],claim)
                if result.get('status')=='awaiting_review': break
            self.assertEqual(result['status'],'awaiting_review'); self.assertEqual(service.graph.get('r','asset_production')['status'],'blocked')
            self.assertEqual(service.graph.get('r','instagram_review')['status'],'blocked')
            instagram=json.loads(service.artifacts.get(service.graph.get('r','instagram_review')['version']))
            self.assertIn('asset',instagram['_blocked_reason'])
            service.lifecycle.close(); service.close()
    def test_engineering_implementation_reports_missing_verified_workspace(self):
        import json
        from runner.manual_lifecycle import ManualLifecycle
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc'); service.create('r','engineering','task'); service.enqueue('r'); queue=service.lifecycle.claim(); claim=service.claim('r')
            data=service.artifacts.put(json.dumps({'output':'synthetic'}).encode())
            for stage in ('intake','plan'):
                snapshot=service.graph.prepare('r',stage,claim); service.graph.finish('r',stage,snapshot,'passed',data,claim)
            result=StageDispatcher(service).step('r',queue['token'],claim)
            self.assertEqual(result['stage'],'implement'); self.assertEqual(result['status'],'blocked'); self.assertIn('workspace',result['reason'])
            service.lifecycle.close(); service.close()
