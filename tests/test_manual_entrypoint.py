import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

class ManualEntrypointTests(unittest.TestCase):
    def test_manual_operator_stop_survives_restart(self):
        with tempfile.TemporaryDirectory() as root:
            secret=Path(root)/'secret'; secret.write_text('x'*40); secret.chmod(0o600)
            env=dict(os.environ,CONTROL_SECRET_FILE=str(secret),WORKFLOW_DATA_DIR=root,WORKFLOW_LIFECYCLE_MODE='manual',MANUAL_WORKER_TARGET='worker-example',MANUAL_WORKER_INCARNATION='inc-example')
            process=None
            def start():
                p=subprocess.Popen([sys.executable,'-m','runner.worker'],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                for _ in range(200):
                    if (Path(root)/'control.sock').exists(): return p
                    if p.poll() is not None: self.fail('worker startup failed: '+p.stderr.read().decode())
                    time.sleep(.01)
                p.kill(); self.fail('worker socket unavailable')
            def stop(p):
                p.terminate(); p.wait(timeout=4)
                p.stderr.close()
            def call(action,**fields):
                p=subprocess.run([sys.executable,'-m','runner.cli'],input=json.dumps({'action':action,**fields}),text=True,capture_output=True,env=env,timeout=4)
                return json.loads(p.stdout)
            try:
                process=start()
                self.assertTrue(call('create',run='r',workflow='marketing',task='synthetic')['ok'])
                self.assertTrue(call('enqueue',run='r')['ok'])
                self.assertEqual(call('manual_claim')['result']['claim']['incarnation'],'inc-example')
                self.assertFalse(call('manual_confirm_stopped',run='r',incarnation='inc-example',attestation='terminated_and_fenced')['ok'])
                self.assertTrue(call('manual_stop',run='r')['ok'])
                self.assertEqual(call('job_status',run='r')['result']['job']['status'],'cancelling')
                self.assertTrue(call('create',run='r2',workflow='marketing',task='synthetic-2')['ok'])
                self.assertTrue(call('enqueue',run='r2')['ok'])
                self.assertFalse(call('manual_claim')['ok'])
                self.assertFalse(call('manual_confirm_stopped',run='r',incarnation='stale',attestation='terminated_and_fenced')['ok'])
                self.assertEqual(call('job_status',run='r')['result']['job']['status'],'cancelling')
                stop(process); process=None
                process=start()
                result=call('manual_confirm_stopped',run='r',incarnation='inc-example',attestation='terminated_and_fenced')
                self.assertTrue(result['ok']); self.assertEqual(result['result']['confirmation'],'operator-attested')
                self.assertFalse(call('manual_confirm_stopped',run='r',incarnation='inc-example',attestation='terminated_and_fenced')['ok'])
                self.assertEqual(call('job_status',run='r')['result']['job']['status'],'cancelled')
                self.assertFalse(call('enable_lifecycle_auto')['ok'])
                stop(process); process=None
                process=start()
                self.assertEqual(call('lifecycle_status',run='r')['result']['status'],'stopped')
                self.assertFalse(call('manual_register_replacement',old_incarnation='stale',new_incarnation='inc-2',attestation='replacement_started')['ok'])
                self.assertTrue(call('manual_register_replacement',old_incarnation='inc-example',new_incarnation='inc-2',attestation='replacement_started')['ok'])
                stop(process); process=None
                env['MANUAL_WORKER_INCARNATION']='inc-2'
                process=start()
                self.assertEqual(call('manual_claim')['result']['claim']['run'],'r2')
            finally:
                if process is not None: stop(process)
