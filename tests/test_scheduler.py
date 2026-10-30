import tempfile
import unittest
from runner.queue import JobQueue
from runner.scheduler import Scheduler, SchedulerError

class Supervisor:
    def __init__(self): self.started=[]
    def start_guarded(self,run,token,may_start):
        if not may_start(): return False
        self.started.append((run,token)); return True

class SchedulerTests(unittest.TestCase):
    def test_dispatches_one_job_and_leaves_second_waiting(self):
        with tempfile.TemporaryDirectory() as root:
            queue=JobQueue(root+'/db'); queue.enqueue('a'); queue.enqueue('b'); supervisor=Supervisor()
            scheduler=Scheduler(queue,supervisor,'fixture',fixture_mode=True)
            self.assertEqual(scheduler.dispatch()['run'],'a'); self.assertIsNone(scheduler.dispatch())
            self.assertEqual(len(supervisor.started),1); queue.close()
    def test_uncertain_launch_does_not_retry(self):
        class Broken:
            def start_guarded(self,run,token,may_start): raise TimeoutError()
        with tempfile.TemporaryDirectory() as root:
            queue=JobQueue(root+'/db'); queue.enqueue('a'); scheduler=Scheduler(queue,Broken(),'fixture',fixture_mode=True)
            with self.assertRaises(SchedulerError): scheduler.dispatch()
            self.assertEqual(queue.get('a')['status'],'uncertain'); self.assertIsNone(scheduler.dispatch()); queue.close()

    def test_cancelled_claim_is_not_launched(self):
        class Guarded:
            def __init__(self,queue): self.queue=queue; self.launched=False
            def start_guarded(self,run,token,may_start):
                self.queue.cancel(run)
                if may_start(): self.launched=True; return True
                return False
        with tempfile.TemporaryDirectory() as root:
            queue=JobQueue(root+'/db'); queue.enqueue('a'); supervisor=Guarded(queue)
            Scheduler(queue,supervisor,'fixture',fixture_mode=True).dispatch()
            self.assertFalse(supervisor.launched); queue.close()
