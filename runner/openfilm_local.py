"""Local OpenFilm render: Codex authors the project, this host renders it. No isolation boundary."""
from runner.trace import trace
from runner.openfilm_builder import ProjectRejected
import io
import json
import os
import shutil
import signal
import subprocess
import tempfile
import zipfile
from pathlib import Path

RENDER_TIMEOUT = 600
HERE = Path(__file__).resolve().parent


class LocalOpenFilmBackend:
    def __init__(self, service, builder, package_root, work_root):
        self.service = service
        self.builder = builder
        self.package_root = Path(package_root)
        self.work_root = Path(work_root)

    @property
    def timeout_seconds(self):
        # The stage deadline covers Codex authoring and the Node render together.
        return getattr(self.builder, 'timeout_seconds', 120) + RENDER_TIMEOUT + 120

    def ready(self):
        return self.builder.ready() is True and self.package_root.is_dir()

    def blocker(self, brief):
        return None if self.builder.supports(brief) is True else 'project builder does not support this brief'

    def execute(self, *, run, attempt_id, brief_ref, brief, canonical, graph_claim, queue_claim):
        try:
            built = self.builder.build(run=run, attempt_id=attempt_id, brief_ref=brief_ref, brief=brief, canonical=canonical, graph_claim=graph_claim, queue_claim=queue_claim)
        except ProjectRejected as exc:
            # The model's project was wrong: the asset is blocked and the run stays active (no fence, no cancel).
            trace('render.local.rejected', run=run, error=str(exc)[:400])
            terminal = {'attempt_id': attempt_id, 'rejected': str(exc)[:1000]}
            return {'attempt_id': attempt_id, 'status': 'failed', 'root': None, 'project_ref': None, 'outputs': [],
                    'terminal_ref': self.service.artifacts.put(json.dumps(terminal, sort_keys=True).encode())}
        trace('render.local.built', run=run, project=built['project_ref'], assets=len(brief['assets']))
        work = Path(tempfile.mkdtemp(prefix='openfilm-', dir=self.work_root)); os.chmod(work, 0o700)
        project = work / 'project'; output = work / 'output'
        project.mkdir(); output.mkdir()
        # The builder already verified the archive layout (film.html per asset, no links or traversal).
        with zipfile.ZipFile(io.BytesIO(self.service.artifacts.get(built['project_ref']))) as archive:
            archive.extractall(project)
            trace('render.local.project', run=run, entries=sorted(archive.namelist()), project_dir=str(project))
        (work / 'brief.json').write_text(json.dumps(brief))
        (work / 'node_modules').mkdir(); (work / 'node_modules' / 'openfilm').symlink_to(self.package_root, target_is_directory=True)
        for name in ('openfilm_render.mjs', 'openfilm_local_render.mjs'): shutil.copy(HERE / name, work / name)
        env = {
            'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
            'HOME': str(work),
            # Playwright finds its browsers under HOME unless told where they are; the temporary HOME hides them.
            'PLAYWRIGHT_BROWSERS_PATH': os.environ.get('PLAYWRIGHT_BROWSERS_PATH') or str(Path.home() / '.cache' / 'ms-playwright'),
            'OPENFILM_NO_UPDATE_CHECK': '1',
        }
        proc = subprocess.Popen(['node', 'openfilm_local_render.mjs', str(work / 'brief.json'), str(project), str(output)], cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            stdout, stderr = proc.communicate(timeout=RENDER_TIMEOUT)
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            # The browser and ffmpeg children share the session: kill the group, not just node.
            try: os.killpg(proc.pid, signal.SIGKILL)
            except OSError: pass
            stdout, stderr = proc.communicate()
            returncode = -1
            stderr = b'render timed out\n' + stderr
        ok = returncode == 0
        trace('render.local.node', run=run, returncode=returncode, stdout_bytes=len(stdout), stderr_lines=len(stderr.splitlines()))
        for line in stderr.decode('utf-8', 'replace').splitlines()[-200:]: trace('render.node', run=run, line=line)
        outputs = []
        if ok:
            try: outputs = json.loads((output / 'render-result.json').read_bytes().decode('utf-8'))['outputs']
            except (OSError, ValueError, KeyError, TypeError) as exc:
                trace('render.local.no_result', run=run, error=f'{type(exc).__name__}: {exc}')
                ok = False
        if not ok and (output / 'look-failure.json').is_file():
            # A layout or visual check failure leaves its report here; the stage ingests it as the render's evidence.
            outputs = [{'look_report_ref': self.service.artifacts.put((output / 'look-failure.json').read_bytes())}]
        trace('render.local.result', run=run, status='exited' if ok else 'failed', returncode=returncode, outputs=outputs)
        terminal = {'attempt_id': attempt_id, 'returncode': returncode, 'stderr': stderr.decode('utf-8', 'replace')[-4000:]}
        return {
            'attempt_id': attempt_id,
            'status': 'exited' if ok else 'failed',
            'root': str(output),
            'project_ref': built['project_ref'],
            'outputs': outputs,
            'terminal_ref': self.service.artifacts.put(json.dumps(terminal, sort_keys=True).encode()),
        }

    def verify_terminal(self, run, attempt_id, brief_ref, result):
        # The render ran in this process; the child (and its group) has exited or been killed before execute returns.
        return result['attempt_id'] == attempt_id

    def cancel(self, run, attempt_id):
        return None
