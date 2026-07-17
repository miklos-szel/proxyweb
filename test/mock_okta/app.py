"""Minimal mock Okta OIDC provider for ProxyWeb integration tests.

Implements exactly the endpoints ProxyWeb's oidc.py client calls: the
discovery document, /authorize, /token and /userinfo. Test cases control
the identity that gets "authenticated" by appending mock_* query params to
the authorize URL before following it:

  mock_groups=a,b            groups claim (comma separated; default none)
  mock_sub=...               subject (default: mock-user)
  mock_email=...             email claim (default: mock-user@example.com)
  mock_no_groups_in_idtoken=1  omit groups from the id_token so ProxyWeb
                               must use the /userinfo fallback

The id_token carries a fake signature: ProxyWeb receives it directly from
/token over the (test-network) channel and validates claims only, per
OIDC Core 3.1.3.7. Single process + in-memory code store by design.
"""

import base64
import json
import os
import secrets
import time

from flask import Flask, jsonify, redirect, request

app = Flask(__name__)

ISSUER = os.environ.get('MOCK_ISSUER', 'http://mock-okta:9000')
CLIENT_ID = os.environ.get('MOCK_CLIENT_ID', 'proxyweb-test-client')
CLIENT_SECRET = os.environ.get('MOCK_CLIENT_SECRET', 'mock-secret')

# code -> {'claims': ..., 'redirect_uri': ..., 'omit_groups_in_idtoken': bool}
_codes = {}
# access_token -> claims (for /userinfo)
_tokens = {}


def _b64url(data):
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b'=').decode()


@app.route('/.well-known/openid-configuration')
def discovery():
    return jsonify({
        'issuer': ISSUER,
        'authorization_endpoint': f'{ISSUER}/authorize',
        'token_endpoint': f'{ISSUER}/token',
        'userinfo_endpoint': f'{ISSUER}/userinfo',
        'jwks_uri': f'{ISSUER}/keys',
    })


@app.route('/keys')
def keys():
    return jsonify({'keys': []})


@app.route('/authorize')
def authorize():
    if request.args.get('client_id') != CLIENT_ID:
        return 'unknown client_id', 400
    redirect_uri = request.args.get('redirect_uri', '')
    if not redirect_uri:
        return 'missing redirect_uri', 400
    groups_raw = request.args.get('mock_groups', '')
    claims = {
        'sub': request.args.get('mock_sub', 'mock-user'),
        'email': request.args.get('mock_email', 'mock-user@example.com'),
        'nonce': request.args.get('nonce', ''),
        'groups': [g for g in groups_raw.split(',') if g],
    }
    code = secrets.token_urlsafe(16)
    _codes[code] = {
        'claims': claims,
        'redirect_uri': redirect_uri,
        'omit_groups_in_idtoken': request.args.get('mock_no_groups_in_idtoken') == '1',
    }
    sep = '&' if '?' in redirect_uri else '?'
    state = request.args.get('state', '')
    return redirect(f'{redirect_uri}{sep}code={code}&state={state}')


@app.route('/token', methods=['POST'])
def token():
    auth = request.authorization
    if not auth or auth.username != CLIENT_ID or auth.password != CLIENT_SECRET:
        return jsonify({'error': 'invalid_client'}), 401
    entry = _codes.pop(request.form.get('code', ''), None)
    if entry is None:
        return jsonify({'error': 'invalid_grant'}), 400
    if request.form.get('redirect_uri') != entry['redirect_uri']:
        return jsonify({'error': 'invalid_grant', 'error_description': 'redirect_uri mismatch'}), 400

    claims = entry['claims']
    now = int(time.time())
    id_claims = {
        'iss': ISSUER,
        'aud': CLIENT_ID,
        'iat': now,
        'exp': now + 3600,
        'sub': claims['sub'],
        'email': claims['email'],
        'nonce': claims['nonce'],
    }
    if not entry['omit_groups_in_idtoken']:
        id_claims['groups'] = claims['groups']

    access_token = secrets.token_urlsafe(16)
    _tokens[access_token] = claims
    id_token = f"{_b64url({'alg': 'none', 'typ': 'JWT'})}.{_b64url(id_claims)}.ZmFrZXNpZw"
    return jsonify({
        'access_token': access_token,
        'token_type': 'Bearer',
        'id_token': id_token,
    })


@app.route('/userinfo')
def userinfo():
    header = request.headers.get('Authorization', '')
    claims = _tokens.get(header.removeprefix('Bearer '))
    if claims is None:
        return jsonify({'error': 'invalid_token'}), 401
    return jsonify({
        'sub': claims['sub'],
        'email': claims['email'],
        'groups': claims['groups'],
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('MOCK_PORT', 9000)))
