"""Opt-in host render fixture; never downloads or invokes media providers."""
import json
import io
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from runner.artifacts import ArtifactStore
from workflows.marketing.assets import ingest_verified_asset, ingest_verified_preview
from runner.openfilm_builder import _attach_media
from tests.test_assets import png


@unittest.skipUnless(os.environ.get('OPENFILM_TEST_PACKAGE_ROOT'), 'OpenFilm package path not configured')
class OpenFilmHostIntegrationTests(unittest.TestCase):
    def test_image_browser_check_requires_executable_matching_pinned_package(self):
        import shlex
        package=Path(os.environ['OPENFILM_TEST_PACKAGE_ROOT']).resolve(strict=True)
        dockerfile=(Path(__file__).resolve().parents[1]/'Dockerfile.openfilm').read_text()
        line=next(line for line in dockerfile.splitlines() if line.startswith('RUN node -e ') and 'chromium_headless_shell-' in line)
        command=shlex.split(line)[1:]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'node_modules').mkdir(); (root/'node_modules'/'openfilm').symlink_to(package,target_is_directory=True)
            empty=root/'empty'; empty.mkdir(); env={**os.environ,'PLAYWRIGHT_BROWSERS_PATH':str(empty)}
            missing=subprocess.run(command,cwd=root,env=env,capture_output=True,text=True,timeout=10)
            self.assertNotEqual(missing.returncode,0)
            env['PLAYWRIGHT_BROWSERS_PATH']=str(Path.home()/'.cache'/'ms-playwright')
            present=subprocess.run(command,cwd=root,env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(present.returncode,0,present.stderr)

    def test_missing_browser_does_not_download_or_render(self):
        package=Path(os.environ['OPENFILM_TEST_PACKAGE_ROOT']).resolve(strict=True)
        helper=(Path(__file__).resolve().parents[1]/'runner'/'openfilm_render.mjs').as_uri()
        fixture=Path(__file__).parent/'fixtures'/'openfilm'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); project=root/'project'; output=root/'output'; browser=root/'empty-browser'
            shutil.copytree(fixture,project); output.mkdir(); browser.mkdir()
            script="""
import {pathToFileURL} from 'node:url';
const [helper,packageRoot,project,output]=process.argv.slice(1);
const {renderAssets}=await import(helper);
const {look}=await import(pathToFileURL(packageRoot+'/src/look.mjs'));
const {render}=await import(pathToFileURL(packageRoot+'/src/render.mjs'));
await renderAssets({assets:[{kind:'png'}]},project,output,{look,render});
"""
            env={**os.environ,'PLAYWRIGHT_BROWSERS_PATH':str(browser),'OPENFILM_HOME':str(root/'home'),'OPENFILM_NO_BROWSER':'1','OPENFILM_NO_UPDATE_CHECK':'1'}
            result=subprocess.run(['node','--input-type=module','-e',script,helper,str(package),str(project),str(output)],env=env,capture_output=True,text=True,timeout=30)
            self.assertNotEqual(result.returncode,0)
            self.assertIn("Executable doesn't exist",result.stderr)
            self.assertFalse(list(browser.iterdir()))
            self.assertFalse((output/'asset-0.png').exists())

    def test_real_still_range_check_reports_cropped_text(self):
        package=Path(os.environ['OPENFILM_TEST_PACKAGE_ROOT']).resolve(strict=True)
        helper=(Path(__file__).resolve().parents[1]/'runner'/'openfilm_render.mjs').as_uri()
        fixture=Path(__file__).parent/'fixtures'/'openfilm'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); project=root/'project'; output=root/'output'; shutil.copytree(fixture,project); output.mkdir()
            (project/'scene.html').write_text('<html><body style="margin:0"><div style="position:absolute;left:160px;top:30px;width:100px;font-size:20px">Synthetic text cut off</div><script>window.film={duration:1,width:180,height:320,frame(t){}}</script></body></html>')
            script="""
import {pathToFileURL} from 'node:url';
const [helper,packageRoot,project,output]=process.argv.slice(1);
const {renderAssets}=await import(helper);
const {look}=await import(pathToFileURL(packageRoot+'/src/look.mjs'));
const {render}=await import(pathToFileURL(packageRoot+'/src/render.mjs'));
process.stdout.write(JSON.stringify(await renderAssets({assets:[{kind:'png'}]},project,output,{look,render})));
"""
            env={**os.environ,'OPENFILM_HOME':str(root/'openfilm-home'),'OPENFILM_NO_BROWSER':'1','OPENFILM_NO_UPDATE_CHECK':'1'}
            result=subprocess.run(['node','--input-type=module','-e',script,helper,str(package),str(project),str(output)],env=env,capture_output=True,text=True,timeout=120,check=True)
            report=json.loads((output/json.loads(result.stdout)['outputs'][0]['report']['path']).read_text())
            self.assertTrue(any(line.startswith('! text cut off ') for line in report['lines']),report['lines'])

    def test_real_openfilm_renders_supplied_project_media(self):
        package=Path(os.environ['OPENFILM_TEST_PACKAGE_ROOT']).resolve(strict=True)
        helper=(Path(__file__).resolve().parents[1]/'runner'/'openfilm_render.mjs').as_uri()
        fixture=Path(__file__).parent/'fixtures'/'openfilm'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); project=root/'project'; output=root/'output'; storage=root/'storage'
            project.mkdir(); output.mkdir(); storage.mkdir(mode=0o700); store=ArtifactStore(str(storage))
            media=store.put(png()); rights=store.put(b'synthetic rights')
            brief={'assets':[{'kind':'png','media':[{'artifact_ref':media,'rights_ref':rights}]}]}
            archive=io.BytesIO()
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('film.html',(fixture/'film.html').read_bytes())
                z.writestr('scene.html',f'<html><body style="margin:0"><img src="media/{media}.png" style="width:180px;height:320px"><script>window.film={{duration:1,width:180,height:320,frame(t){{}}}}</script></body></html>')
            package_bytes=_attach_media(store,store.put(archive.getvalue()),brief)
            with zipfile.ZipFile(io.BytesIO(package_bytes)) as z: z.extractall(project)
            script="""
import {pathToFileURL} from 'node:url';
const [helper,packageRoot,project,output]=process.argv.slice(1);
const {renderAssets}=await import(helper);
const {look}=await import(pathToFileURL(packageRoot+'/src/look.mjs'));
const {render}=await import(pathToFileURL(packageRoot+'/src/render.mjs'));
process.stdout.write(JSON.stringify(await renderAssets({assets:[{kind:'png'}]},project,output,{look,render})));
"""
            env={**os.environ,'OPENFILM_HOME':str(root/'openfilm-home'),'OPENFILM_NO_BROWSER':'1','OPENFILM_NO_UPDATE_CHECK':'1'}
            result=subprocess.run(['node','--input-type=module','-e',script,helper,str(package),str(project),str(output)],env=env,capture_output=True,text=True,timeout=120,check=True)
            filename=json.loads(result.stdout)['outputs'][0]['path']
            pixel=subprocess.run(['ffmpeg','-v','error','-i',str(output/filename),'-vf','crop=1:1:10:10,format=rgb24','-frames:v','1','-f','rawvideo','-'],capture_output=True,check=True).stdout
            self.assertEqual(pixel,b'\xff\x00\x00')

    def test_real_openfilm_still_and_vertical_video(self):
        package=Path(os.environ['OPENFILM_TEST_PACKAGE_ROOT']).resolve(strict=True)
        self.assertEqual(json.loads((package/'package.json').read_text())['version'],'0.2.6')
        helper=(Path(__file__).resolve().parents[1]/'runner'/'openfilm_render.mjs').as_uri()
        fixture=Path(__file__).parent/'fixtures'/'openfilm'
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); project=root/'project'; output=root/'output'; storage=root/'storage'
            project.mkdir(); output.mkdir(); storage.mkdir(mode=0o700)
            for index in range(2): shutil.copytree(fixture,project/'projects'/str(index))
            script="""
import {pathToFileURL} from 'node:url';
const [helper,packageRoot,project,output]=process.argv.slice(1);
const {renderAssets}=await import(helper);
const {look}=await import(pathToFileURL(packageRoot+'/src/look.mjs'));
const {render}=await import(pathToFileURL(packageRoot+'/src/render.mjs'));
const result=await renderAssets({assets:[{kind:'png'},{kind:'mp4'}]},project,output,{look,render});
process.stdout.write(JSON.stringify(result));
"""
            env={**os.environ,'OPENFILM_HOME':str(root/'openfilm-home'),'OPENFILM_NO_BROWSER':'1','OPENFILM_NO_UPDATE_CHECK':'1'}
            completed=subprocess.run(['node','--input-type=module','-e',script,helper,str(package),str(project),str(output)],env=env,capture_output=True,text=True,timeout=120,check=True)
            result=json.loads(completed.stdout); store=ArtifactStore(str(storage))
            for kind,item in zip(('png','mp4'),result['outputs']):
                brief={'version':'brief','kind':kind,'width':180,'height':320,'duration_seconds':1}
                run={'brief_version':'brief','project_version':'project','asset_sha256':item['sha256']}
                self.assertEqual(ingest_verified_asset(store,output,item['path'],brief,run,'project')['status'],'awaiting_review')
                preview=item['previews'][0]
                self.assertIn('artifact_ref',ingest_verified_preview(store,output,preview['path'],preview['sha256'],'brief','project'))
                report=json.loads((output/item['report']['path']).read_bytes())
                self.assertTrue(report['ok'])
                self.assertTrue(any(line.startswith('✓ ') and 'same picture' in line for line in report['lines']))
