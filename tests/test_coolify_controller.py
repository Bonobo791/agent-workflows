import unittest
from runner.coolify_controller import CoolifyController, CoolifyError

class FakeHTTP:
    def __init__(self): self.calls=[]
    def post(self,url,token): self.calls.append((url,token)); return 200

class CoolifyTests(unittest.TestCase):
    def test_targets_only_exact_configured_application(self):
        http=FakeHTTP(); controller=CoolifyController('https://coolify.example.invalid','synthetic','svc','app',http)
        controller.stop({'service':'svc','application':'app'})
        self.assertEqual(http.calls[0][0],'https://coolify.example.invalid/api/v1/services/svc/applications/app/stop')
        with self.assertRaises(CoolifyError): controller.stop({'service':'svc','application':'other'})
