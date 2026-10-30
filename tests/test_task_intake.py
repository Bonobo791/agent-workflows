import json
import tempfile
import unittest
from runner.control import ControlError, ControlPlane
from runner.service import Service
from runner.task_intake import SingleTaskIntake

class FakePlan:
    def __init__(self): self.owner=None
    def get_issue(self,project,number):
        return {'ok':True,'data':{'id':1,'identifier':'DEMO-1','title':'Synthetic','description':'Example','columnId':10,'archivedAt':None,'assigneeUserId':self.owner,'delegateUserId':None,'dueDate':None,'startDate':None,'subtasks':[],'links':[],'development':[],'updatedAt':'v1'}}
    def call(self,name,args):
        return {'ok':True,'data':{'items':[],'nextCursor':None} if name=='list_issue_activity' else []}

class SingleTaskTests(unittest.TestCase):
    def test_executor_failure_fence_keeps_job_until_operator_attests(self):
        from runner.manual_lifecycle import ManualLifecycle
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); service.lifecycle=ManualLifecycle(root+'/state.db','worker','inc-1')
            service.create('run','marketing','task'); service.enqueue('run'); service.lifecycle.claim()
            service.fence_execution('run')
            self.assertEqual(service.queue.get('run')['status'],'cancelling')
            service.lifecycle.confirm_operator_stop('run','inc-1','terminated_and_fenced')
            self.assertEqual(service.queue.get('run')['status'],'cancelled')
            service.lifecycle.close(); service.close()

    def test_authenticated_admission_records_source_and_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); plan=FakePlan()
            service.task_intake=SingleTaskIntake(service,plan,lambda project,number:[],{'ready_columns':{10},'completed_columns':{20},'blocker_direction':'inward'})
            control=ControlPlane(service,'secret')
            req={'action':'submit_task','run':'run1','workflow':'engineering','project':'DEMO','number':1}
            with self.assertRaises(ControlError): control.handle('wrong',req)
            value=control.handle('secret',req)
            self.assertEqual(value['source_version'],'v1')
            self.assertEqual(json.loads(service.artifacts.get(value['artifact_ref']))['task_id'],'DEMO-1')
            self.assertEqual(service.queue.get('run1')['status'],'queued')
            with self.assertRaises(ControlError): control.handle('secret',{**req,'run':'run2'})
            service.close()
    def test_unknown_or_existing_work_and_owner_block_admission(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); plan=FakePlan(); policy={'ready_columns':{10},'completed_columns':{20},'blocker_direction':'inward'}
            for discovered,owner in ((None,None),(['existing'],None),([], 'someone')):
                plan.owner=owner; intake=SingleTaskIntake(service,plan,lambda p,n:discovered,policy)
                with self.assertRaises(Exception): intake.submit('run','marketing','DEMO',1)
            self.assertIsNone(service.queue.get('run')); service.close()
