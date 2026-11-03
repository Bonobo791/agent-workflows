import hashlib
import io
import json
import socketserver
import struct
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path

from runner.artifacts import ArtifactStore
from runner.openfilm_builder import _attach_media, _media
from runner.worker import build_control
from runner.manual_lifecycle import ManualLifecycle


class MediaAttachmentTests(unittest.TestCase):
    def test_project_author_cannot_change_or_reorder_attempt_inputs(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from runner.openfilm_builder import CodexOpenFilmProjectBuilder
        from runner.service import Service
        from runner.skills import SkillResolver
        with tempfile.TemporaryDirectory() as root:
            skill=Path(root)/'skills'/'author'; skill.mkdir(parents=True); (skill/'SKILL.md').write_text('fixture')
            service=Service(root)
            try:
                service.create('r','marketing','synthetic'); claim=service.graph.claim('r',60)
                service.model_supervisor=SimpleNamespace(db=str(Path(root)/'state.db'),policy_hash='a'*64,verify_policy=lambda:True)
                service.docker_supervisor=SimpleNamespace(config=SimpleNamespace(policy_hash='b'*64))
                service.model_policy=SimpleNamespace(for_role=lambda role:{'model':'fixture'})
                service.skill_resolver=SkillResolver([str(skill.parent)]); service.required_skills={'asset_production':['author']}
                content=service.artifacts.put(b'Approved text'); brief={'content_ref':content,'assets':[{'media':[]}]}
                brief_ref=service.artifacts.put(json.dumps(brief).encode()); attempt='1'*64
                raw=io.BytesIO()
                with zipfile.ZipFile(raw,'w') as archive: archive.writestr('film.html','fixture')
                project=service.artifacts.put(raw.getvalue())
                builder=CodexOpenFilmProjectBuilder(service)
                def execute(**kwargs):
                    inputs=list(kwargs['input_versions'])
                    if variant=='changed': inputs[2]='2'*64
                    elif variant=='reordered': inputs.reverse()
                    else: inputs.pop()
                    response={'stage':'asset_production','status':'passed','input_versions':inputs,'outputs':[project],'findings':[]}
                    return {'output':json.dumps(response),'outputs':[project]}
                for variant in ('changed','reordered','missing'):
                    with self.subTest(variant=variant),patch.object(builder,'ready',return_value=True),patch.object(builder,'_active',return_value=True),patch('runner.openfilm_builder.CodexExecutor') as executor,patch('runner.openfilm_builder._attach_media') as attach:
                        executor.return_value.execute.side_effect=execute
                        with self.assertRaisesRegex(ValueError,'project output invalid'): builder.build(run='r',attempt_id=attempt,brief_ref=brief_ref,brief=brief,canonical='Approved text',graph_claim=claim,queue_claim='fixture')
                        attach.assert_not_called()
            finally: service.close()
    def test_media_is_private_per_asset_and_reproducible(self):
        with tempfile.TemporaryDirectory() as root:
            store=ArtifactStore(root)
            image=b'\x89PNG\r\n\x1a\nexample'
            image_ref=store.put(image); rights=store.put(b'rights evidence')
            brief={'assets':[{'media':[{'artifact_ref':image_ref,'rights_ref':rights}]},{'media':[]}]}
            raw=io.BytesIO()
            with zipfile.ZipFile(raw,'w') as z:
                z.writestr('projects/0/film.html','film'); z.writestr('projects/1/film.html','film')
            project=store.put(raw.getvalue())
            packaged=_attach_media(store,project,brief)
            self.assertEqual(packaged,_attach_media(store,project,brief))
            with zipfile.ZipFile(io.BytesIO(packaged)) as z:
                self.assertEqual(z.read('projects/0/media/'+image_ref+'.png'),image)
                self.assertNotIn('projects/1/media/'+image_ref+'.png',z.namelist())
            self.assertEqual(hashlib.sha256(image).hexdigest(),image_ref)
            brief['assets'][0]['media'][0]['rights_ref']='0'*64
            with self.assertRaises(Exception): _media(store,brief)

    def test_author_cannot_replace_supplied_media(self):
        with tempfile.TemporaryDirectory() as root:
            store=ArtifactStore(root); image=store.put(b'\x89PNG\r\n\x1a\nimage'); rights=store.put(b'rights')
            brief={'assets':[{'media':[{'artifact_ref':image,'rights_ref':rights}]}]}
            raw=io.BytesIO()
            with zipfile.ZipFile(raw,'w') as z:
                z.writestr('film.html','film'); z.writestr('media/fake.png','fake')
            with self.assertRaises(ValueError): _attach_media(store,store.put(raw.getvalue()),brief)

    def test_unknown_media_type_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            store=ArtifactStore(root); item={'artifact_ref':store.put(b'unknown'), 'rights_ref':store.put(b'rights')}
            with self.assertRaises(ValueError): _media(store,{'assets':[{'media':[item]}]})
