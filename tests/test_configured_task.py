import tempfile
import unittest
from runner.configured_task import ConfiguredTask, ConfiguredTaskError

CONFIG={'run':'run-1','workflow':'engineering','project':'DEMO','number':1,'ready_columns':[10],'completed_columns':[20],'blocker_direction':'inward','plan_endpoint':'https://plan.example.invalid/mcp','github_owner':'owner','github_repo':'repo','github_branch':'task-1'}
class TaskTests(unittest.TestCase):
    def test_team_scoped_project_through_mcp_to_admission(self):
        import json
        from runner.plan_mcp import PlanMCP
        from runner.service import Service
        from runner.task_intake import IntakeError
        from tests.test_plan_mcp import FakeTransport
        class Transport(FakeTransport):
            def post(self,message,headers):
                if message['method']!='tools/call': return super().post(message,headers)
                self.calls.append((message,headers))
                name=message['params']['name']
                if name=='get_issue_by_number':
                    self.assert_project=message['params']['arguments']['projectKey']
                    data={'id':1,'identifier':'DEMO-1','title':'Synthetic','description':'Example','columnId':10,'archivedAt':None,'assigneeUserId':None,'delegateUserId':None,'dueDate':None,'startDate':None,'subtasks':[],'links':[],'development':[],'updatedAt':'v1'}
                    if self.assert_project=='other.DEMO': data['id']=2
                elif name=='list_issue_activity': data={'items':[],'nextCursor':None}
                else: data=[]
                result={'jsonrpc':'2.0','id':message['id'],'result':{'structuredContent':{'ok':True,'data':data}}}
                return 200,{'Content-Type':'application/json'},json.dumps(result).encode()
        class GitHub:
            def request(self,method,path,body=None): return []
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); transport=Transport()
            try:
                intake=ConfiguredTask(service,{**CONFIG,'project':'team.DEMO'},PlanMCP(transport),GitHub())
                result=intake.submit('run-1','engineering','team.DEMO',1)
                self.assertEqual(result['task'],'DEMO-1')
                self.assertEqual(transport.assert_project,'team.DEMO')
                self.assertEqual(service.queue.get('run-1')['status'],'queued')
                other=ConfiguredTask(service,{**CONFIG,'run':'run-2','project':'other.DEMO'},PlanMCP(Transport()),GitHub())
                second=other.submit_configured()
                self.assertEqual(second['task'],'DEMO-1')
                self.assertEqual(service.queue.get('run-2')['status'],'queued')
                tasks=[json.loads(service.artifacts.get(item['artifact_ref'])) for item in (result,second)]
                self.assertEqual([task['identifier'] for task in tasks],['DEMO-1','DEMO-1'])
                self.assertEqual([task['task_id'] for task in tasks],['plan:1','plan:2'])
                claims=[service.claim(run) for run in ('run-1','run-2')]
                self.assertEqual(service.graph.db.execute('SELECT task_id FROM graph_claims ORDER BY task_id').fetchall(),[('plan:1',),('plan:2',)])
                for run,claim in zip(('run-1','run-2'),claims): service.release(run,claim)
                for project in ('DEMO','team.DEMO'):
                    alias=ConfiguredTask(service,{**CONFIG,'run':'duplicate','project':project},PlanMCP(Transport()),GitHub())
                    with self.subTest(project=project),self.assertRaisesRegex(IntakeError,'task already admitted'): alias.submit_configured()
                self.assertIsNone(service.queue.get('duplicate'))
                with self.assertRaises(ConfiguredTaskError): ConfiguredTask(service,{**CONFIG,'project':'team..DEMO'},PlanMCP(Transport()),GitHub())
            finally: service.close()

    def test_exact_reference_and_duplicate_admission(self):
        from runner.service import Service
        class Plan:
            def get_issue(self,p,n): return {'ok':True,'data':{'id':1,'identifier':'DEMO-1','title':'Synthetic','description':'Example','columnId':10,'archivedAt':None,'assigneeUserId':None,'delegateUserId':None,'dueDate':None,'startDate':None,'subtasks':[],'links':[],'development':[],'updatedAt':'v1'}}
            def call(self,name,args): return {'ok':True,'data':{'items':[],'nextCursor':None} if name=='list_issue_activity' else []}
        class GitHub:
            def request(self,method,path,body=None): return []
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); intake=ConfiguredTask(service,CONFIG,Plan(),GitHub())
            with self.assertRaises(ConfiguredTaskError): intake.submit('other','engineering','DEMO',1)
            result=intake.submit('run-1','engineering','DEMO',1)
            self.assertEqual(result['task'],'DEMO-1'); self.assertEqual(service.queue.get('run-1')['status'],'queued')
            with self.assertRaises(Exception): intake.submit('run-1','engineering','DEMO',1)
            service.close()
    def test_existing_branch_or_incomplete_pr_results_block_admission(self):
        from runner.service import Service
        class Plan:
            def get_issue(self,p,n): return {'ok':True,'data':{'id':1,'identifier':'DEMO-1','title':'Synthetic','description':'Example','columnId':10,'archivedAt':None,'assigneeUserId':None,'delegateUserId':None,'dueDate':None,'startDate':None,'subtasks':[],'links':[],'development':[],'updatedAt':'v1'}}
            def call(self,name,args): return {'ok':True,'data':{'items':[],'nextCursor':None} if name=='list_issue_activity' else []}
        for response in ([{'ref':'refs/heads/task-1'}],None):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as root:
                class GitHub:
                    def request(self,method,path,body=None): return response
                service=Service(root); intake=ConfiguredTask(service,CONFIG,Plan(),GitHub())
                with self.assertRaises(Exception): intake.submit('run-1','engineering','DEMO',1)
                self.assertIsNone(service.queue.get('run-1')); service.close()
    def test_dot_path_components_rejected_before_network(self):
        from runner.service import Service
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            for field in ('run','project','github_owner','github_repo'):
                with self.subTest(field=field), self.assertRaises(ConfiguredTaskError): ConfiguredTask(service,{**CONFIG,field:'..'},object(),object())
            service.close()
    def test_marketing_admission_preserves_explicit_content_format(self):
        import json
        from runner.service import Service
        class Plan:
            def get_issue(self,p,n): return {'ok':True,'data':{'id':1,'identifier':'DEMO-1','title':'Synthetic','description':'Example','columnId':10,'archivedAt':None,'assigneeUserId':None,'delegateUserId':None,'dueDate':None,'startDate':None,'subtasks':[],'links':[],'development':[],'updatedAt':'v1'}}
            def call(self,name,args): return {'ok':True,'data':{'items':[],'nextCursor':None} if name=='list_issue_activity' else []}
        class GitHub:
            def request(self,method,path,body=None): return []
        with tempfile.TemporaryDirectory() as root:
            service=Service(root); config={**CONFIG,'workflow':'marketing','task_format':'guide'}
            intake=ConfiguredTask(service,config,Plan(),GitHub()); result=intake.submit_configured()
            self.assertEqual(json.loads(service.artifacts.get(result['artifact_ref']))['format'],'guide'); service.close()
