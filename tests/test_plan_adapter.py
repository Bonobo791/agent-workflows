import unittest
from runner.plan_adapter import inspect_issue, PlanError

class FakeMCP:
    def get_issue(self,project,number): return {'ok':True,'data':{'id':1,'identifier':'DEMO-1','title':'Example','description':'Synthetic','columnId':10,'archivedAt':None,'assigneeUserId':None,'delegateUserId':None,'dueDate':None,'startDate':None,'subtasks':[],'links':[],'development':[],'updatedAt':'v1'}}
    def call(self,name,args):
        if name=='list_attachments': return {'ok':True,'data':[]}
        if name=='list_issue_activity': return {'ok':True,'data':{'items':[],'nextCursor':None}}
        raise AssertionError(name)

class PlanAdapterTests(unittest.TestCase):
    def test_normalizes_only_confirmed_ready_leaf(self):
        value=inspect_issue(FakeMCP(),'DEMO',1,ready_columns={10},completed_columns={20},blocker_direction='inward',external_work=[])
        self.assertTrue(value['ready']); self.assertTrue(value['leaf']); self.assertTrue(value['dependencies_complete'])
        self.assertEqual(value['existing_work'],[])
    def test_policy_is_required_and_unknown_data_blocks(self):
        with self.assertRaises(PlanError): inspect_issue(FakeMCP(),'DEMO',1,ready_columns=set(),completed_columns={20},blocker_direction='inward')

    def test_future_start_date_blocks_readiness(self):
        class Future(FakeMCP):
            def get_issue(self,project,number):
                result=super().get_issue(project,number); result['data']['startDate']='2030-01-02'; return result
        value=inspect_issue(Future(),'DEMO',1,ready_columns={10},completed_columns={20},blocker_direction='inward',external_work=[],today='2030-01-01')
        self.assertFalse(value['ready'])

    def test_malformed_subtask_never_counts_as_leaf(self):
        class Malformed(FakeMCP):
            def get_issue(self,project,number):
                result=super().get_issue(project,number); result['data']['subtasks']=[{'archived':'false'}]; return result
        with self.assertRaises(PlanError): inspect_issue(Malformed(),'DEMO',1,ready_columns={10},completed_columns={20},blocker_direction='inward',external_work=[])
    def test_comments_are_included_and_incomplete_activity_blocks(self):
        class WithComments(FakeMCP):
            def call(self,name,args):
                if name=='list_issue_activity': return {'ok':True,'data':{'items':[{'id':3,'issueId':1,'body':'synthetic comment','kind':'comment'}],'nextCursor':None}}
                return super().call(name,args)
        value=inspect_issue(WithComments(),'DEMO',1,ready_columns={10},completed_columns={20},blocker_direction='inward',external_work=[])
        self.assertEqual(value['comments'][0]['body'],'synthetic comment')
        class Incomplete(WithComments):
            def call(self,name,args):
                if name=='list_issue_activity': return {'ok':True,'data':{'items':[],'nextCursor':{'id':3,'ts':'t'}}}
                return super().call(name,args)
        with self.assertRaises(PlanError): inspect_issue(Incomplete(),'DEMO',1,ready_columns={10},completed_columns={20},blocker_direction='inward',external_work=[])
