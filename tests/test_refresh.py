import tempfile
import unittest
from runner.credentials import CredentialStore
from runner.refresh import refresh, RefreshError

class RefreshTests(unittest.TestCase):
    def test_refresh_persists_rotated_tokens_under_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            store=CredentialStore(directory)
            with store.locked() as record: record.save({'client_id':'issued', 'refresh_token':'old', 'subject':'subject', 'scope':'chatgpt.tokens.use.direct'})
            def fake_post(form):
                self.assertEqual(form['refresh_token'],'old')
                return {'access_token':'new-access','refresh_token':'new-refresh','scope':'openid chatgpt.tokens.use.direct','expires_in':3600,'token_type':'Bearer'}
            self.assertEqual(refresh(store, fake_post, now=100)['access_token'],'new-access')
            with store.locked() as record: self.assertEqual(record.load()['refresh_token'],'new-refresh')
    def test_invalid_response_does_not_replace_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            store=CredentialStore(directory)
            with store.locked() as record: record.save({'client_id':'issued','refresh_token':'old'})
            with self.assertRaises(RefreshError): refresh(store,lambda form:{'scope':'openid'},now=100)
            with store.locked() as record: self.assertEqual(record.load()['refresh_token'],'old')

    def test_uncertain_refresh_does_not_retry_rotating_token(self):
        with tempfile.TemporaryDirectory() as directory:
            store=CredentialStore(directory)
            with store.locked() as record: record.save({'client_id':'issued','refresh_token':'old','scope':'chatgpt.tokens.use.direct'})
            def interrupted(form): raise TimeoutError()
            with self.assertRaises(RefreshError): refresh(store,interrupted,now=100)
            calls=[]
            with self.assertRaises(RefreshError): refresh(store,lambda form: calls.append(form),now=101)
            self.assertEqual(calls,[])
