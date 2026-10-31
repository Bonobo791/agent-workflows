import json
import subprocess
import tempfile
import unittest
from pathlib import Path


class OpenFilmRenderHelperTests(unittest.TestCase):
    def test_still_runs_range_scan_and_exports_full_size_frame(self):
        helper=(Path(__file__).resolve().parents[1]/'runner'/'openfilm_render.mjs').as_uri()
        script="""
const {renderAssets}=await import(process.argv[1]);
const {writeFile}=await import('node:fs/promises');
const {join}=await import('node:path');
const [project,out]=process.argv.slice(2), calls=[];
const api={look:async(args)=>{calls.push(args.length); const file=join(project,args.length===1?'sheet.png':'frame.png'); await writeFile(file,args.length===1?'sheet':'frame'); return {ok:true,lines:['✓ same picture'],files:[file]}},render:async()=>{throw Error('unexpected video render')}};
const result=await renderAssets({assets:[{kind:'png'}]},project,out,api); console.log(JSON.stringify({result,calls}));
"""
        with tempfile.TemporaryDirectory() as root:
            project=Path(root)/'project'; output=Path(root)/'output'; project.mkdir(); output.mkdir(); (project/'film.html').write_text('<html></html>')
            result=subprocess.run(['node','--input-type=module','-e',script,helper,str(project),str(output)],capture_output=True,text=True,timeout=10,check=True)
            data=json.loads(result.stdout)
            self.assertEqual(data['calls'],[1,2])
            self.assertEqual((output/data['result']['outputs'][0]['path']).read_bytes(),b'frame')
            self.assertEqual((output/data['result']['outputs'][0]['previews'][0]['path']).read_bytes(),b'sheet')

    def test_fixed_render_paths_and_visual_failure(self):
        helper=(Path(__file__).resolve().parents[1]/'runner'/'openfilm_render.mjs').as_uri()
        script="""
import {renderAssets} from process.argv[1];
import {writeFile} from 'node:fs/promises';
import {join} from 'node:path';
const [project,out,mode]=process.argv.slice(2);
const api={look:async()=>{const file=join(project,'preview.png'); await writeFile(file,'synthetic preview'); return {ok:mode!=='bad',lines:mode==='cutoff' ? ['! text cut off by the edge of the frame at 0, 0.5', '✓ drawn again: same picture'] : [mode==='spoof' ? '! text cut off: same picture' : '✓ drawn again: same picture'],files:[file]}},
  render:async(_args,opts)=>{await writeFile(opts.out,'synthetic video'); return {out:opts.out}}};
try {console.log(JSON.stringify(await renderAssets({assets:[{kind:'mp4'}]},project,out,api)))} catch {process.exitCode=1}
"""
        # Static imports cannot take runtime paths; dynamic import keeps the local helper fixed by the test.
        script=script.replace("import {renderAssets} from process.argv[1];", "const {renderAssets}=await import(process.argv[1]);")
        with tempfile.TemporaryDirectory() as root:
            project=Path(root)/'project'; output=Path(root)/'output'; project.mkdir(); output.mkdir()
            (project/'film.html').write_text('<html></html>')
            result=subprocess.run(['node','--input-type=module','-e',script,helper,str(project),str(output),'good'],capture_output=True,text=True,timeout=10,check=True)
            manifest=json.loads(result.stdout)
            self.assertEqual(manifest['outputs'][0]['path'],'asset-0.mp4')
            self.assertEqual((output/'asset-0.mp4').read_bytes(),b'synthetic video')
            self.assertTrue((output/'preview-0.png').is_file())
            second=Path(root)/'second'; second.mkdir()
            bad=subprocess.run(['node','--input-type=module','-e',script,helper,str(project),str(second),'bad'],capture_output=True,text=True,timeout=10)
            self.assertNotEqual(bad.returncode,0)
            self.assertFalse((second/'asset-0.mp4').exists())
            self.assertFalse(json.loads((second/'look-failure.json').read_text())['ok'])
            spoof=Path(root)/'spoof'; spoof.mkdir()
            spoofed=subprocess.run(['node','--input-type=module','-e',script,helper,str(project),str(spoof),'spoof'],capture_output=True,text=True,timeout=10)
            self.assertNotEqual(spoofed.returncode,0)
            cutoff=Path(root)/'cutoff'; cutoff.mkdir()
            cropped=subprocess.run(['node','--input-type=module','-e',script,helper,str(project),str(cutoff),'cutoff'],capture_output=True,text=True,timeout=10,check=True)
            report=json.loads((cutoff/json.loads(cropped.stdout)['outputs'][0]['report']['path']).read_text())
            self.assertTrue(any(line.startswith('! text cut off ') for line in report['lines']))
