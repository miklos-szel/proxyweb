"""Minimal OIDC Authorization Code client for Okta SSO.

ProxyWeb acts as a confidential client: the ID token is obtained directly
from the token endpoint over TLS, so per OIDC Core 3.1.3.7 (rule 6) the
token signature does not need to be verified — TLS server validation
stands in for it. Claims (iss, aud, exp, nonce) are still validated.

Kept dependency-light on purpose (requests only): the Okta settings are
editable at runtime via the settings UI, so no client is registered at
import time — every function takes the current config values.
"""

import base64
import json
import logging
import os
import time
from urllib.parse import urlencode, urlsplit

import requests

METADATA_CACHE_TTL = 3600
HTTP_TIMEOUT = 10
CLOCK_SKEW = 60

_metadata_cache = {}


class OidcError(Exception):
    """Raised on any OIDC discovery/exchange/validation failure."""


def _allow_http():
    """
    Whether plain-http OIDC endpoints are tolerated. Production must use TLS
    (the module relies on TLS server validation in lieu of signature checks),
    so http is only permitted when PROXYWEB_OKTA_ALLOW_HTTP=1 is set — this is
    for the hermetic test stack / local dev only, never production.
    """
    return os.environ.get('PROXYWEB_OKTA_ALLOW_HTTP') == '1'


def _require_https(url, what):
    """Reject non-https OIDC URLs unless http is explicitly opted into."""
    scheme = urlsplit(url).scheme
    if scheme == 'https':
        return
    if scheme == 'http' and _allow_http():
        return
    raise OidcError(f"{what} must use HTTPS")


def get_provider_metadata(issuer):
    """
    Fetch (and cache per issuer, ~1h) the provider's OIDC discovery document.

    Returns the metadata dict; raises OidcError on network/HTTP/parse errors,
    on a document whose issuer does not match the configured issuer, or on a
    non-https endpoint (unless PROXYWEB_OKTA_ALLOW_HTTP=1).
    """
    issuer = issuer.rstrip('/')
    _require_https(issuer, "issuer")
    cached = _metadata_cache.get(issuer)
    if cached and time.time() - cached[0] < METADATA_CACHE_TTL:
        return cached[1]
    url = f"{issuer}/.well-known/openid-configuration"
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        meta = resp.json()
    except Exception as e:
        logging.error("OIDC discovery failed for %s: %s", url, e)
        raise OidcError(f"discovery failed for {issuer}")
    if not isinstance(meta, dict):
        raise OidcError("discovery document is not a JSON object")
    for key in ('authorization_endpoint', 'token_endpoint'):
        if key not in meta:
            raise OidcError(f"discovery document missing {key}")
    if str(meta.get('issuer', '')).rstrip('/') != issuer:
        raise OidcError("discovery issuer mismatch")
    _require_https(meta['authorization_endpoint'], "authorization_endpoint")
    _require_https(meta['token_endpoint'], "token_endpoint")
    if meta.get('userinfo_endpoint'):
        _require_https(meta['userinfo_endpoint'], "userinfo_endpoint")
    _metadata_cache[issuer] = (time.time(), meta)
    return meta


def build_authorize_url(meta, client_id, redirect_uri, state, nonce, scopes):
    """Build the authorization endpoint redirect URL for the code flow."""
    params = {
        'client_id': client_id,
        'response_type': 'code',
        'scope': scopes,
        'redirect_uri': redirect_uri,
        'state': state,
        'nonce': nonce,
    }
    return f"{meta['authorization_endpoint']}?{urlencode(params)}"


def exchange_code(meta, client_id, client_secret, code, redirect_uri):
    """
    Exchange an authorization code for tokens at the token endpoint.

    Returns the token response dict; raises OidcError on failure. Response
    bodies are logged server-side only — callers must not echo them to the
    browser.
    """
    try:
        resp = requests.post(
            meta['token_endpoint'],
            auth=(client_id, client_secret),
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': redirect_uri,
            },
            timeout=HTTP_TIMEOUT,
        )
    except Exception as e:
        logging.error("OIDC token request failed: %s", e)
        raise OidcError("token endpoint unreachable")
    if resp.status_code != 200:
        logging.error("OIDC token endpoint returned %s: %s",
                      resp.status_code, resp.text[:500])
        raise OidcError("token exchange rejected")
    try:
        return resp.json()
    except ValueError:
        raise OidcError("token endpoint returned invalid JSON")


def decode_id_token_claims(id_token):
    """
    Decode the claims (payload) segment of a JWT without verifying the
    signature — see the module docstring for why that is acceptable here.
    """
    try:
        payload = id_token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        raise OidcError("malformed id_token")


def validate_claims(claims, issuer, client_id, nonce):
    """Validate iss, aud (str or list), exp (with skew) and nonce."""
    if claims.get('iss', '').rstrip('/') != issuer.rstrip('/'):
        raise OidcError("issuer mismatch")
    aud = claims.get('aud')
    if isinstance(aud, list):
        if client_id not in aud:
            raise OidcError("audience mismatch")
    elif aud != client_id:
        raise OidcError("audience mismatch")
    exp = claims.get('exp')
    if not isinstance(exp, (int, float)) or time.time() > exp + CLOCK_SKEW:
        raise OidcError("id_token expired")
    if not nonce or claims.get('nonce') != nonce:
        raise OidcError("nonce mismatch")


def fetch_userinfo(meta, access_token, expected_sub):
    """
    Fetch claims from the userinfo endpoint (used as a fallback when the
    ID token carries no groups claim, e.g. Okta org authorization server).

    Per OIDC Core 5.3.2 the userinfo response's 'sub' MUST match the 'sub'
    of the ID token; a mismatch (token substitution) raises OidcError.
    """
    endpoint = meta.get('userinfo_endpoint')
    if not endpoint:
        raise OidcError("no userinfo_endpoint in discovery document")
    try:
        resp = requests.get(
            endpoint,
            headers={'Authorization': f"Bearer {access_token}"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        claims = resp.json()
    except Exception as e:
        logging.error("OIDC userinfo request failed: %s", e)
        raise OidcError("userinfo request failed")
    if not isinstance(claims, dict) or claims.get('sub') != expected_sub:
        raise OidcError("userinfo subject mismatch")
    return claims
