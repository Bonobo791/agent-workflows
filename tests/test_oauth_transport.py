import unittest
from runner.oauth_transport import OAuthTransport, TransportError

class FakeHTTP:
    def __init__(self, metadata): self.metadata=metadata; self.calls=[]
    def request(self,method,url,body=None,headers=None):
        self.calls.append((method,url,body))
        if url.endswith('openid-configuration'): return self.metadata
        if url.endswith('/keys'): return {'keys':[]}
        return {'access_token':'synthetic'}

class OAuthTransportTests(unittest.TestCase):
    def test_rejects_off_origin_jwks_and_uses_fixed_token_endpoint(self):
        http=FakeHTTP({'issuer':'https://auth.openai.com','jwks_uri':'https://evil.invalid/keys'})
        client=OAuthTransport(http)
        with self.assertRaises(TransportError): client.jwks()
        client.post_form({'grant_type':'authorization_code','code':'synthetic'})
        self.assertEqual(http.calls[-1][1],'https://auth.openai.com/api/accounts/oauth/token')
    def test_fetches_same_origin_keys(self):
        http=FakeHTTP({'issuer':'https://auth.openai.com','jwks_uri':'https://auth.openai.com/keys'})
        self.assertEqual(OAuthTransport(http).jwks(),{'keys':[]})
