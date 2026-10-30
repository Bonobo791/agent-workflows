"""Build a bounded Sign in with ChatGPT authorization attempt.

No listener, browser, network request, token validation or credential persistence
is enabled here. Callers must implement all of those before this can sign in.
"""
import base64
import hashlib
import hmac
import secrets
from urllib.parse import urlencode

AUTH_URL = 'https://auth.openai.com/api/accounts/authorize'
RESOURCE = 'https://api.openai.com/v1'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'

class OAuthError(ValueError):
    pass

class AuthorizationAttempt:
    def __init__(self, host_id, port, client_id=None):
        if not host_id or not isinstance(port, int) or not 1 <= port <= 65535: raise ValueError('host ID and loopback port required')
        if client_id == 'dynamic_agent_client': raise ValueError('issued client ID required for returning account')
        self.host_id, self.client_id = host_id, client_id
        self.redirect_uri = f'http://127.0.0.1:{port}/auth/callback'
        self.state, self.nonce, self.verifier = (secrets.token_urlsafe(32) for _ in range(3))
        self.used = False

    def authorization_url(self):
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
        params = {'client_id': self.client_id or 'dynamic_agent_client', 'ext_agent_host_id': self.host_id,
                  'response_type':'code', 'redirect_uri':self.redirect_uri, 'scope':SCOPES,
                  'resource':RESOURCE, 'state':self.state, 'nonce':self.nonce,
                  'code_challenge_method':'S256', 'code_challenge':challenge}
        if not self.client_id: params['agent_name_hint'] = 'agent_workflows'
        return AUTH_URL + '?' + urlencode(params)

    def callback(self, params):
        if self.used: raise OAuthError('attempt already consumed')
        self.used = True
        def one(name, required=False):
            values=params.get(name, [])
            if len(values)>1 or (required and (len(values)!=1 or not values[0])): raise OAuthError('invalid callback')
            return values[0] if values else None
        state=one('state', True)
        if not hmac.compare_digest(state, self.state): raise OAuthError('state mismatch')
        if one('error'): raise OAuthError('authorization denied or failed')
        code, returned = one('code', True), one('client_id')
        if self.client_id and returned and self.client_id != returned: raise OAuthError('client mismatch')
        issued = self.client_id or returned
        if not issued or issued == 'dynamic_agent_client': raise OAuthError('issued client ID missing')
        return {'grant_type':'authorization_code', 'client_id':issued, 'code':code,
                'code_verifier':self.verifier, 'redirect_uri':self.redirect_uri, 'resource':RESOURCE}

def validate_token_response(response, client_id, nonce, trusted_jwks, expected_subject=None):
    """Validate against JWKS securely fetched from OpenAI by the caller.

    Live use is disabled until the fixed-origin JWKS fetch and integration are
    implemented. Requires PyJWT with its cryptography extra; never trusts an
    unsigned token or keys supplied by a callback.
    """
    scopes = response.get('scope', '').split()
    if 'chatgpt.tokens.use.direct' not in scopes: raise OAuthError('plan permission missing')
    if not client_id or not nonce or not isinstance(trusted_jwks, dict): raise OAuthError('missing validation input')
    try:
        import jwt
        token = response['id_token']
        header = jwt.get_unverified_header(token)
        if header.get('alg') != 'RS256' or not header.get('kid'): raise OAuthError('unsupported signing key')
        matches = [key for key in trusted_jwks.get('keys', []) if key.get('kid') == header['kid'] and key.get('kty') == 'RSA']
        if len(matches) != 1: raise OAuthError('signing key not found')
        key = jwt.PyJWK.from_dict(matches[0], algorithm='RS256').key
        claims = jwt.decode(token, key, algorithms=['RS256'], issuer='https://auth.openai.com', audience=client_id,
                            options={'require':['iss','aud','exp','sub','nonce']})
        if not isinstance(claims['sub'], str) or not claims['sub'] or not hmac.compare_digest(claims['nonce'], nonce): raise OAuthError('identity mismatch')
        if expected_subject and not hmac.compare_digest(claims['sub'], expected_subject): raise OAuthError('account mismatch')
        return claims
    except OAuthError:
        raise
    except Exception:
        raise OAuthError('identity validation failed') from None

def complete_authorization(attempt, callback, post_form, trusted_jwks, store, *, expected_subject=None, now=None):
    """Complete a user-initiated attempt using injected transport and verified keys.

    No browser or network action is started here. A caller must securely fetch
    OpenAI's JWKS and provide a fixed-origin token transport, then explicitly
    choose a private per-registration store. Failures never persist tokens.
    """
    import os
    import time
    form=attempt.callback(callback)
    try: response=post_form(form)
    except Exception: raise OAuthError('token exchange failed') from None
    if not isinstance(response,dict): raise OAuthError('invalid token response')
    if (not all(isinstance(response.get(k),str) and response[k] for k in ('access_token','refresh_token','id_token'))
        or str(response.get('token_type','')).lower() != 'bearer'
        or type(response.get('expires_in')) is not int or response['expires_in']<=0): raise OAuthError('invalid token response')
    claims=validate_token_response(response,form['client_id'],attempt.nonce,trusted_jwks,expected_subject)
    record={k:response[k] for k in ('access_token','refresh_token','id_token','token_type','expires_in','scope')}
    record.update({'subject':claims['sub'],'issuer':claims['iss'],'client_id':form['client_id'],
                   'ext_agent_host_id':attempt.host_id,'saved_at':time.time() if now is None else now})
    with store.locked() as target:
        try: os.stat('credentials.json',dir_fd=target.directory,follow_symlinks=False)
        except FileNotFoundError: existing=None
        else: existing=target.load()
        if existing and (existing.get('client_id') != record['client_id'] or existing.get('subject') != record['subject']): raise OAuthError('credential store belongs to another registration')
        target.save(record)
    return claims['sub']
