import tempfile
import unittest
from runner.service import Service, ServiceError

class ServiceTests(unittest.TestCase):
    def test_marketing_restart_approval_and_blocked_asset(self):
        with tempfile.TemporaryDirectory() as root:
            s=Service(root); s.create('r','marketing','task'); token=s.claim('r')
            task={'task_id':'task','format':'guide','leaf':True,'ready':True,'dependencies_complete':True,'owner':None,'existing_work':[],'due':None}
            s.complete('r','intake',token,{'task':task})
            for stage in ('brief','canonical_draft'):
                s.complete('r',stage,token,{'output':'synthetic','session_id':'writer'})
            reviews={role:{'status':'passed','session_id':role,'findings':[]} for role in ('editorial','factual','seo_geo')}
            s.complete('r','canonical_review',token,{'reviews':reviews,'writer_sessions':['writer'],'current_writer_session':'writer','missing_skills':[]})
            with self.assertRaises(ServiceError): s.complete('r','freeze',token,{})
            approved=s.graph.get('r','canonical_review')['version']; s.approve('r','freeze',approved,'reviewer')
            s.complete('r','freeze',token,{})
            s.release('r',token); s.close()
            s=Service(root); token=s.claim('r')
            for channel in ('linkedin','x','facebook','instagram','reddit'):
                s.complete('r',channel+'_draft',token,{'output':'synthetic','session_id':'writer-'+channel})
                evidence={'output':'synthetic','session_id':'reviewer-'+channel}
                if channel=='instagram': evidence['asset']={'status':'verified','ref':'asset'}
                if channel=='reddit': evidence.update({'community':'example','rules_ref':'ref','rules_current':True,'relevant':True,'product_mentioned':False})
                s.complete('r',channel+'_review',token,evidence)
            s.complete('r','asset_brief',token,{'output':'synthetic'})
            self.assertEqual(s.complete('r','asset_production',token,{}),'blocked')
            self.assertEqual(s.complete('r','assembly',token,{}),'awaiting_review')
            self.assertTrue(s.verify_terminal('r'))
            s.close()
    def test_live_execution_and_unsupported_mutations_block(self):
        with tempfile.TemporaryDirectory() as root:
            s=Service(root); s.create('r','engineering','task'); token=s.claim('r')
            with self.assertRaises(ServiceError): s.complete('r','open_draft',token,{})
            with self.assertRaises(ServiceError): s.live_execute()
            s.close()

    def test_engineering_checks_commit_and_independent_qa(self):
        with tempfile.TemporaryDirectory() as root:
            s=Service(root); s.create('r','engineering','task'); token=s.claim('r')
            task={'leaf':True,'ready':True,'dependencies_complete':True,'owner':None,'existing_work':[],'due':None}
            s.complete('r','intake',token,{'task':task,'today':'2026-10-30'})
            s.complete('r','plan',token,{'output':'plan'})
            s.complete('r','implement',token,{'output':'implementation','commit':'a'*40,'session_id':'writer'})
            with self.assertRaises(ServiceError): s.complete('r','test',token,{'output':'test','commit':'b'*40,'passed':True})
            s.complete('r','test',token,{'output':'test','commit':'a'*40,'passed':True})
            with self.assertRaises(ServiceError): s.complete('r','qa',token,{'output':'qa','commit':'a'*40,'session_id':'writer','acceptance_passed':True})
            s.complete('r','qa',token,{'output':'qa','commit':'a'*40,'session_id':'reviewer','acceptance_passed':True})
            s.close()

    def test_approval_is_invalid_when_review_changes(self):
        with tempfile.TemporaryDirectory() as root:
            s=Service(root); s.create('r','marketing','task'); token=s.claim('r')
            task={'task_id':'task','format':'guide','leaf':True,'ready':True,'dependencies_complete':True,'owner':None,'existing_work':[],'due':None}
            s.complete('r','intake',token,{'task':task})
            for stage in ('brief','canonical_draft'): s.complete('r',stage,token,{'output':'synthetic','session_id':'writer'})
            reviews={role:{'status':'passed','session_id':role,'findings':[]} for role in ('editorial','factual','seo_geo')}
            evidence={'reviews':reviews,'writer_sessions':['writer'],'current_writer_session':'writer','missing_skills':[]}
            s.complete('r','canonical_review',token,evidence)
            s.approve('r','freeze',s.graph.get('r','canonical_review')['version'],'reviewer')
            evidence['reviews']['editorial']['session_id']='second-editor'
            s.complete('r','canonical_review',token,evidence)
            with self.assertRaises(ServiceError): s.complete('r','freeze',token,{})
            s.close()

    def test_rejects_insecure_storage_root(self):
        import os
        with tempfile.TemporaryDirectory() as root:
            os.chmod(root,0o755)
            with self.assertRaises(ServiceError): Service(root)

    def test_invalid_claim_cannot_consume_freeze_approval(self):
        with tempfile.TemporaryDirectory() as root:
            s=Service(root); s.create('r','marketing','task'); token=s.claim('r')
            task={'task_id':'task','format':'guide','leaf':True,'ready':True,'dependencies_complete':True,'owner':None,'existing_work':[],'due':None}
            s.complete('r','intake',token,{'task':task})
            for stage in ('brief','canonical_draft'): s.complete('r',stage,token,{'output':'synthetic','session_id':'writer'})
            reviews={role:{'status':'passed','session_id':role,'findings':[]} for role in ('editorial','factual','seo_geo')}
            s.complete('r','canonical_review',token,{'reviews':reviews,'writer_sessions':['writer'],'current_writer_session':'writer','missing_skills':[]})
            s.approve('r','freeze',s.graph.get('r','canonical_review')['version'],'reviewer')
            with self.assertRaises(ServiceError): s.complete('r','freeze','invalid',{})
            s.complete('r','freeze',token,{})
            s.close()

    def test_freeze_rejects_unreviewed_replacement_content(self):
        with tempfile.TemporaryDirectory() as root:
            s=Service(root); s.create('r','marketing','task'); token=s.claim('r')
            task={'task_id':'task','format':'guide','leaf':True,'ready':True,'dependencies_complete':True,'owner':None,'existing_work':[],'due':None}
            s.complete('r','intake',token,{'task':task})
            s.complete('r','brief',token,{'output':'brief'})
            s.complete('r','canonical_draft',token,{'output':'reviewed content','session_id':'writer'})
            reviews={role:{'status':'passed','session_id':role,'findings':[]} for role in ('editorial','factual','seo_geo')}
            s.complete('r','canonical_review',token,{'reviews':reviews,'writer_sessions':['writer'],'current_writer_session':'writer','missing_skills':[]})
            s.approve('r','freeze',s.graph.get('r','canonical_review')['version'],'reviewer')
            with self.assertRaises(ServiceError): s.complete('r','freeze',token,{'output':'unreviewed replacement'})
            s.close()

    def test_invalid_channel_is_terminal_and_assembly_contains_content(self):
        import json
        with tempfile.TemporaryDirectory() as root:
            s=Service(root); s.create('r','marketing','task'); token=s.claim('r')
            task={'task_id':'task','format':'guide','leaf':True,'ready':True,'dependencies_complete':True,'owner':None,'existing_work':[],'due':None}
            s.complete('r','intake',token,{'task':task})
            for stage in ('brief','canonical_draft'): s.complete('r',stage,token,{'output':stage,'session_id':'writer'})
            reviews={role:{'status':'passed','session_id':role,'findings':[]} for role in ('editorial','factual','seo_geo')}
            s.complete('r','canonical_review',token,{'reviews':reviews,'writer_sessions':['writer'],'current_writer_session':'writer','missing_skills':[]})
            s.approve('r','freeze',s.graph.get('r','canonical_review')['version'],'reviewer'); s.complete('r','freeze',token,{})
            for channel in ('linkedin','x','facebook','instagram','reddit'):
                s.complete('r',channel+'_draft',token,{'output':'draft '+channel,'session_id':'writer-'+channel})
                evidence={'output':'review '+channel,'session_id':'reviewer-'+channel}
                if channel=='reddit': evidence.update({'community':'example','rules_ref':'ref','rules_current':True,'relevant':True,'product_mentioned':False})
                result=s.complete('r',channel+'_review',token,evidence)
                if channel=='instagram': self.assertEqual(result,'blocked')
            s.complete('r','asset_brief',token,{'output':'brief'}); s.complete('r','asset_production',token,{})
            self.assertEqual(s.complete('r','assembly',token,{}),'awaiting_review')
            package=json.loads(s.artifacts.get(s.graph.get('r','assembly')['version']))
            self.assertEqual(package['stages']['linkedin_review']['artifact']['output'],'review linkedin')
            self.assertEqual(package['stages']['instagram_review']['status'],'blocked')
            self.assertEqual(package['stages']['canonical_draft']['artifact']['output'],'canonical_draft')
            s.close()

    def test_channel_review_rejects_draft_writer_session(self):
        with tempfile.TemporaryDirectory() as root:
            s=Service(root); s.create('r','marketing','task'); token=s.claim('r')
            task={'task_id':'task','format':'guide','leaf':True,'ready':True,'dependencies_complete':True,'owner':None,'existing_work':[],'due':None}
            s.complete('r','intake',token,{'task':task})
            for stage in ('brief','canonical_draft'): s.complete('r',stage,token,{'output':'content','session_id':'writer'})
            reviews={role:{'status':'passed','session_id':role,'findings':[]} for role in ('editorial','factual','seo_geo')}
            s.complete('r','canonical_review',token,{'reviews':reviews,'writer_sessions':['writer'],'current_writer_session':'writer','missing_skills':[]})
            s.approve('r','freeze',s.graph.get('r','canonical_review')['version'],'reviewer'); s.complete('r','freeze',token,{})
            s.complete('r','linkedin_draft',token,{'output':'draft','session_id':'same'})
            self.assertEqual(s.complete('r','linkedin_review',token,{'output':'review','session_id':'same'}),'blocked')
            s.close()

    def test_canonical_reviewer_cannot_be_actual_draft_writer(self):
        with tempfile.TemporaryDirectory() as root:
            s=Service(root); s.create('r','marketing','task'); token=s.claim('r')
            task={'task_id':'task','format':'guide','leaf':True,'ready':True,'dependencies_complete':True,'owner':None,'existing_work':[],'due':None}
            s.complete('r','intake',token,{'task':task}); s.complete('r','brief',token,{'output':'brief'})
            s.complete('r','canonical_draft',token,{'output':'draft','session_id':'actual-writer'})
            reviews={role:{'status':'passed','session_id':role,'findings':[]} for role in ('editorial','factual','seo_geo')}
            reviews['editorial']['session_id']='actual-writer'
            with self.assertRaises(ServiceError): s.complete('r','canonical_review',token,{'reviews':reviews,'writer_sessions':['other-writer'],'current_writer_session':'other-writer','missing_skills':[]})
            s.close()

    def test_channel_reviewer_session_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as root:
            s=Service(root); s.create('r','marketing','task'); token=s.claim('r')
            task={'task_id':'task','format':'guide','leaf':True,'ready':True,'dependencies_complete':True,'owner':None,'existing_work':[],'due':None}
            s.complete('r','intake',token,{'task':task}); s.complete('r','brief',token,{'output':'brief'}); s.complete('r','canonical_draft',token,{'output':'draft','session_id':'writer'})
            reviews={role:{'status':'passed','session_id':role,'findings':[]} for role in ('editorial','factual','seo_geo')}
            s.complete('r','canonical_review',token,{'reviews':reviews,'writer_sessions':['writer'],'current_writer_session':'writer','missing_skills':[]})
            s.approve('r','freeze',s.graph.get('r','canonical_review')['version'],'reviewer'); s.complete('r','freeze',token,{})
            for channel in ('linkedin','x'): s.complete('r',channel+'_draft',token,{'output':'draft','session_id':'writer-'+channel})
            s.complete('r','linkedin_review',token,{'output':'review','session_id':'shared-review'})
            self.assertEqual(s.complete('r','x_review',token,{'output':'review','session_id':'shared-review'}),'blocked')
            s.close()

    def test_asset_special_path_rechecks_upstream_skills(self):
        from pathlib import Path
        from runner.skills import SkillResolver
        with tempfile.TemporaryDirectory() as root:
            skillroot=Path(root)/'skills'; skillroot.mkdir(); skill=skillroot/'editor'; skill.mkdir(); file=skill/'SKILL.md'; file.write_text('original')
            state=Path(root)/'state'; state.mkdir(mode=0o700)
            s=Service(state,SkillResolver([skillroot]),{'canonical_draft':['editor']}); s.create('r','marketing','task'); token=s.claim('r')
            task={'task_id':'task','format':'guide','leaf':True,'ready':True,'dependencies_complete':True,'owner':None,'existing_work':[],'due':None}
            s.complete('r','intake',token,{'task':task}); s.complete('r','brief',token,{'output':'brief'}); s.complete('r','canonical_draft',token,{'output':'draft','session_id':'writer'})
            reviews={role:{'status':'passed','session_id':role,'findings':[]} for role in ('editorial','factual','seo_geo')}
            s.complete('r','canonical_review',token,{'reviews':reviews,'writer_sessions':['writer'],'current_writer_session':'writer','missing_skills':[]})
            s.approve('r','freeze',s.graph.get('r','canonical_review')['version'],'reviewer'); s.complete('r','freeze',token,{})
            s.complete('r','asset_brief',token,{'output':'brief'}); file.write_text('changed')
            with self.assertRaises(ServiceError): s.complete('r','asset_production',token,{})
            self.assertIsNone(s.graph.get('r','canonical_draft'))
            s.close()

    def test_special_stages_require_their_own_skills(self):
        with tempfile.TemporaryDirectory() as root:
            s=Service(root,required_skills={'assembly':['missing']}); s.create('r','marketing','task'); token=s.claim('r')
            with self.assertRaises(ServiceError): s.complete('r','assembly',token,{})
            s.close()

    def test_valid_asset_skill_fingerprint_allows_assembly(self):
        from pathlib import Path
        from runner.skills import SkillResolver
        with tempfile.TemporaryDirectory() as root:
            skillroot=Path(root)/'skills'; skillroot.mkdir(); skill=skillroot/'media'; skill.mkdir(); (skill/'SKILL.md').write_text('synthetic')
            state=Path(root)/'state'; state.mkdir(mode=0o700)
            s=Service(state,SkillResolver([skillroot]),{'asset_production':['media']}); s.create('r','marketing','task'); token=s.claim('r')
            task={'task_id':'task','format':'guide','leaf':True,'ready':True,'dependencies_complete':True,'owner':None,'existing_work':[],'due':None}
            s.complete('r','intake',token,{'task':task}); s.complete('r','brief',token,{'output':'brief'}); s.complete('r','canonical_draft',token,{'output':'draft','session_id':'writer'})
            reviews={role:{'status':'passed','session_id':role,'findings':[]} for role in ('editorial','factual','seo_geo')}
            s.complete('r','canonical_review',token,{'reviews':reviews,'writer_sessions':['writer'],'current_writer_session':'writer','missing_skills':[]})
            s.approve('r','freeze',s.graph.get('r','canonical_review')['version'],'reviewer'); s.complete('r','freeze',token,{})
            for channel in ('linkedin','x','facebook','instagram','reddit'):
                s.complete('r',channel+'_draft',token,{'output':'draft','session_id':'w-'+channel})
                evidence={'output':'review','session_id':'review-'+channel}
                if channel=='reddit': evidence.update({'community':'example','rules_ref':'ref','rules_current':True,'relevant':True,'product_mentioned':False})
                s.complete('r',channel+'_review',token,evidence)
            s.complete('r','asset_brief',token,{'output':'brief'}); self.assertEqual(s.complete('r','asset_production',token,{}),'blocked')
            self.assertEqual(s.complete('r','assembly',token,{}),'awaiting_review')
            s.close()

    def test_freeze_missing_own_skill_does_not_consume_approval(self):
        with tempfile.TemporaryDirectory() as root:
            s=Service(root,required_skills={'freeze':['missing']}); s.create('r','marketing','task'); token=s.claim('r')
            task={'task_id':'task','format':'guide','leaf':True,'ready':True,'dependencies_complete':True,'owner':None,'existing_work':[],'due':None}
            s.complete('r','intake',token,{'task':task}); s.complete('r','brief',token,{'output':'brief'}); s.complete('r','canonical_draft',token,{'output':'draft','session_id':'writer'})
            reviews={role:{'status':'passed','session_id':role,'findings':[]} for role in ('editorial','factual','seo_geo')}
            s.complete('r','canonical_review',token,{'reviews':reviews,'writer_sessions':['writer'],'current_writer_session':'writer','missing_skills':[]})
            version=s.graph.get('r','canonical_review')['version']; s.approve('r','freeze',version,'reviewer')
            with self.assertRaises(ServiceError): s.complete('r','freeze',token,{})
            status=s.actions.db.execute('SELECT status FROM external_actions WHERE run=? AND action=? AND version=?',('r','freeze',version)).fetchone()[0]
            self.assertEqual(status,'approved'); s.close()

    def test_cancelled_run_cannot_be_enqueued(self):
        with tempfile.TemporaryDirectory() as root:
            s=Service(root); s.create('r','marketing','task'); token=s.claim('r'); s.cancel('r',token)
            with self.assertRaises(ServiceError): s.enqueue('r')
            s.close()

    def test_job_completion_rejects_nonterminal_workflow(self):
        from runner.queue import QueueError
        with tempfile.TemporaryDirectory() as root:
            s=Service(root); s.create('r','marketing','task'); s.enqueue('r'); claim=s.queue.claim('worker',60)
            with self.assertRaises(QueueError): s.finish_job('r',claim['token'])
            self.assertEqual(s.queue.get('r')['status'],'running'); s.close()

    def test_terminal_check_rejects_changed_required_skill(self):
        from pathlib import Path
        from runner.skills import SkillResolver
        with tempfile.TemporaryDirectory() as root:
            skillroot=Path(root)/'skills'; skillroot.mkdir(); skill=skillroot/'editor'; skill.mkdir(); file=skill/'SKILL.md'; file.write_text('original')
            state=Path(root)/'state'; state.mkdir(mode=0o700)
            s=Service(state,SkillResolver([skillroot]),{'canonical_draft':['editor']}); s.create('r','marketing','task'); token=s.claim('r')
            task={'task_id':'task','format':'guide','leaf':True,'ready':True,'dependencies_complete':True,'owner':None,'existing_work':[],'due':None}
            s.complete('r','intake',token,{'task':task}); s.complete('r','brief',token,{'output':'brief'}); s.complete('r','canonical_draft',token,{'output':'draft','session_id':'writer'})
            reviews={role:{'status':'passed','session_id':role,'findings':[]} for role in ('editorial','factual','seo_geo')}
            s.complete('r','canonical_review',token,{'reviews':reviews,'writer_sessions':['writer'],'current_writer_session':'writer','missing_skills':[]})
            s.approve('r','freeze',s.graph.get('r','canonical_review')['version'],'reviewer'); s.complete('r','freeze',token,{})
            for channel in ('linkedin','x','facebook','instagram','reddit'):
                s.complete('r',channel+'_draft',token,{'output':'draft','session_id':'w-'+channel})
                evidence={'output':'review','session_id':'review-'+channel}
                if channel=='reddit': evidence.update({'community':'example','rules_ref':'ref','rules_current':True,'relevant':True,'product_mentioned':False})
                s.complete('r',channel+'_review',token,evidence)
            s.complete('r','asset_brief',token,{'output':'brief'}); s.complete('r','asset_production',token,{}); s.complete('r','assembly',token,{})
            self.assertTrue(s.verify_terminal('r')); file.write_text('changed')
            self.assertFalse(s.verify_terminal('r')); s.close()
