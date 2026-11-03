"""Local OpenFilm render: Codex authors the project, this host renders it. No isolation boundary."""
import io
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

RENDER_TIMEOUT = 600
HERE = Path(__file__).resolve().parent


class LocalOpenFilmBackend:
    timeout_seconds = RENDER_TIMEOUT

    def __init__(self, service, builder, package_root, work_root):
        self.service = service
        self.builder = builder
        self.package_root = Path(package_root)
        self.work_root = Path(work_root)

    def ready(self):
        return self.builder.ready() is True and self.package_root.is_dir()

    def blocker(self, brief):
        return None if self.builder.supports(brief) is True else 'project builder does not support this brief'

    def execute(self, *, run, attempt_id, brief_ref, brief, canonical, graph_claim, queue_claim):
        built = self.builder.build(run=run, attempt_id=attempt_id, brief_ref=brief_ref, brief=brief, canonical=canonical, graph_claim=graph_claim, queue_claim=queue_claim)
        work = Path(tempfile.mkdtemp(prefix='openfilm-', dir=self.work_root)); os.chmod(work, 0o700)
        project = work / 'project'; output = work / 'output'
        project.mkdir(); output.mkdir()
        # The builder already verified the archive layout (film.html per asset, no links or traversal).
        with zipfile.ZipFile(io.BytesIO(self.service.artifacts.get(built['project_ref']))) as archive:
            archive.extractall(project)
        (work / 'brief.json').write_text(json.dumps(brief))
        (work / 'node_modules').mkdir(); (work / 'node_modules' / 'openfilm').symlink_to(self.package_root, target_is_directory=True)
        for name in ('openfilm_render.mjs', 'openfilm_local_render.mjs'): shutil.copy(HERE / name, work / name)
        try:
            proc = subprocess.run(['node', 'openfilm_local_render.mjs', str(work / 'brief.json'), str(project), str(output)], cwd=work, capture_output=True, timeout=RENDER_TIMEOUT, env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(work)})
            returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            returncode, stdout, stderr = -1, b'', b'render timed out'
        ok = returncode == 0
        outputs = json.loads(stdout.decode('utf-8'))['outputs'] if ok else []
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
        # The render ran in this process; subprocess.run has already returned or killed the child.
        return result['attempt_id'] == attempt_id

    def cancel(self, run, attempt_id):
        return None
