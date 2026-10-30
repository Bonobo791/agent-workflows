import unittest
from urllib.parse import parse_qs, urlsplit
from runner.oauth import AuthorizationAttempt, OAuthError

class OAuthTests(unittest.TestCase):
    def test_request_and_callback_are_bound(self):
        attempt = AuthorizationAttempt('urn:uuid:host', 1455)
        query = parse_qs(urlsplit(attempt.authorization_url()).query)
        self.assertEqual(query['client_id'], ['dynamic_agent_client'])
        self.assertEqual(query['redirect_uri'], ['http://127.0.0.1:1455/auth/callback'])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        with self.assertRaises(OAuthError): attempt.callback({'state':['wrong'], 'code':['code'], 'client_id':['issued']})
        attempt = AuthorizationAttempt('urn:uuid:host', 1455)
        result = attempt.callback({'state':[attempt.state], 'code':['code'], 'client_id':['issued']})
        self.assertEqual(result['client_id'], 'issued')
        with self.assertRaises(OAuthError): attempt.callback({'state':[attempt.state], 'code':['again'], 'client_id':['issued']})
    def test_denial_and_client_mismatch_fail(self):
        attempt = AuthorizationAttempt('host', 1455, client_id='saved')
        with self.assertRaises(OAuthError): attempt.callback({'state':[attempt.state], 'error':['access_denied']})
        attempt = AuthorizationAttempt('host', 1455, client_id='saved')
        with self.assertRaises(OAuthError): attempt.callback({'state':[attempt.state], 'code':['code'], 'client_id':['other']})

class TokenTests(unittest.TestCase):
    def test_missing_plan_scope_rejected_before_identity(self):
        from runner.oauth import validate_token_response
        with self.assertRaises(OAuthError):
            validate_token_response({'scope':'openid', 'id_token':'synthetic'}, 'client', 'nonce', {'keys':[]})
    def test_missing_or_untrusted_key_rejected(self):
        from runner.oauth import validate_token_response
        with self.assertRaises(OAuthError):
            validate_token_response({'scope':'openid chatgpt.tokens.use.direct', 'id_token':'synthetic'}, 'client', 'nonce', {'keys':[]})

try:
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    jwt = None

@unittest.skipIf(jwt is None, 'optional authentication test dependencies unavailable')
class SignedTokenTests(unittest.TestCase):
    def test_signature_issuer_audience_expiry_and_nonce(self):
        import json
        import time
        from runner.oauth import validate_token_response
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        jwk.update({'kid':'synthetic-key','alg':'RS256'})
        now=int(time.time())
        claims={'iss':'https://auth.openai.com','aud':'issued','sub':'subject','nonce':'nonce','exp':now+60}
        def token(payload,signing_key=key):
            return {'scope':'openid chatgpt.tokens.use.direct','id_token':jwt.encode(payload,signing_key,algorithm='RS256',headers={'kid':'synthetic-key'})}
        self.assertEqual(validate_token_response(token(claims),'issued','nonce',{'keys':[jwk]})['sub'],'subject')
        variants=[dict(claims,aud='other'),dict(claims,iss='https://wrong.invalid'),dict(claims,nonce='other'),dict(claims,exp=now-10)]
        for value in variants:
            with self.subTest(value=value):
                with self.assertRaises(OAuthError): validate_token_response(token(value),'issued','nonce',{'keys':[jwk]})
        wrong=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        with self.assertRaises(OAuthError): validate_token_response(token(claims,wrong),'issued','nonce',{'keys':[jwk]})
        with self.assertRaises(OAuthError): validate_token_response(token(claims),'issued','nonce',{'keys':[jwk]},expected_subject='different')

@unittest.skipIf(jwt is None, 'optional authentication test dependencies unavailable')
class ExchangeTests(unittest.TestCase):
    def test_exchange_validates_identity_before_storing(self):
        import json, tempfile, time
        from cryptography.hazmat.primitives.asymmetric import rsa
        from runner.credentials import CredentialStore
        from runner.oauth import complete_authorization
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())); jwk['kid']='key'
        attempt=AuthorizationAttempt('host',1455)
        def post(form):
            claims={'iss':'https://auth.openai.com','aud':'issued','sub':'subject','nonce':attempt.nonce,'exp':int(time.time())+60}
            return {'scope':'openid chatgpt.tokens.use.direct','access_token':'synthetic','refresh_token':'synthetic','token_type':'Bearer','expires_in':3600,'id_token':jwt.encode(claims,key,algorithm='RS256',headers={'kid':'key'})}
        with tempfile.TemporaryDirectory() as root:
            store=CredentialStore(root)
            result=complete_authorization(attempt,{'state':[attempt.state],'code':['code'],'client_id':['issued']},post,{'keys':[jwk]},store,now=100)
            self.assertEqual(result,'subject')
            with store.locked() as record: self.assertEqual(record.load()['client_id'],'issued')
