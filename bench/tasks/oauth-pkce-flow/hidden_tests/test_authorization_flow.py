"""End-to-end and security requirements for the authorization-code flow."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from oauth_pkce import (
    OAuthClient,
    OAuthError,
    StubAuthServer,
    code_challenge_s256,
    generate_code_verifier,
)

CLIENT_ID = "demo-app"
REDIRECT_URI = "https://app.example/oauth/callback"
AUTHORIZE_URL = "https://auth.example/authorize"


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _one(params, name):
    values = params.get(name)
    assert values, f"missing {name} parameter"
    return values[0]


def _begin(client):
    request = client.begin_authorization()
    assert isinstance(request.url, str) and request.url
    assert isinstance(request.state, str) and len(request.state) >= 16
    assert isinstance(request.code_verifier, str) and len(request.code_verifier) >= 43
    return request


def _authorize(server, request):
    params = parse_qs(urlparse(request.url).query)
    return server.authorize(
        client_id=_one(params, "client_id"),
        redirect_uri=_one(params, "redirect_uri"),
        state=_one(params, "state"),
        code_challenge=_one(params, "code_challenge"),
        code_challenge_method=_one(params, "code_challenge_method"),
    )


def _callback(code, state, base=REDIRECT_URI):
    query = urlencode({"code": code, "state": state})
    return f"{base}?{query}"


def _client(server):
    return OAuthClient(
        server,
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        authorize_url=AUTHORIZE_URL,
    )


def test_begin_authorization_builds_pkce_authorization_url():
    client = _client(StubAuthServer())
    request = _begin(client)
    parsed = urlparse(request.url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == AUTHORIZE_URL
    params = parse_qs(parsed.query)
    assert _one(params, "response_type") == "code"
    assert _one(params, "client_id") == CLIENT_ID
    assert _one(params, "redirect_uri") == REDIRECT_URI
    assert _one(params, "state") == request.state
    assert _one(params, "code_challenge_method") == "S256"
    assert _one(params, "code_challenge") == code_challenge_s256(request.code_verifier)


def test_full_authorization_code_flow_returns_bearer_token():
    clock = FakeClock()
    server = StubAuthServer(ttl_seconds=300, clock=clock)
    client = _client(server)
    request = _begin(client)
    authorization = _authorize(server, request)
    token = client.exchange_callback(_callback(authorization["code"], request.state))
    assert token["token_type"] == "Bearer"
    assert isinstance(token["access_token"], str) and token["access_token"]
    assert token["expires_in"] == 300


def test_verifier_mismatch_is_rejected():
    server = StubAuthServer()
    real_verifier = generate_code_verifier()
    authorization = server.authorize(
        redirect_uri=REDIRECT_URI,
        code_challenge=code_challenge_s256(real_verifier),
        code_challenge_method="S256",
    )
    with pytest.raises(OAuthError) as excinfo:
        server.token(
            code=authorization["code"],
            redirect_uri=REDIRECT_URI,
            code_verifier=generate_code_verifier(),
        )
    assert excinfo.value.error == "invalid_grant"


def test_missing_verifier_is_rejected():
    server = StubAuthServer()
    verifier = generate_code_verifier()
    authorization = server.authorize(
        redirect_uri=REDIRECT_URI,
        code_challenge=code_challenge_s256(verifier),
        code_challenge_method="S256",
    )
    with pytest.raises(OAuthError) as excinfo:
        server.token(code=authorization["code"], redirect_uri=REDIRECT_URI)
    assert excinfo.value.error == "invalid_grant"


def test_authorization_code_is_single_use():
    server = StubAuthServer()
    verifier = generate_code_verifier()
    authorization = server.authorize(
        redirect_uri=REDIRECT_URI,
        code_challenge=code_challenge_s256(verifier),
        code_challenge_method="S256",
    )
    server.token(
        code=authorization["code"],
        redirect_uri=REDIRECT_URI,
        code_verifier=verifier,
    )
    with pytest.raises(OAuthError) as excinfo:
        server.token(
            code=authorization["code"],
            redirect_uri=REDIRECT_URI,
            code_verifier=verifier,
        )
    assert excinfo.value.error == "invalid_grant"


def test_failed_pkce_exchange_consumes_the_code():
    server = StubAuthServer()
    verifier = generate_code_verifier()
    authorization = server.authorize(
        redirect_uri=REDIRECT_URI,
        code_challenge=code_challenge_s256(verifier),
        code_challenge_method="S256",
    )
    wrong = generate_code_verifier()
    with pytest.raises(OAuthError):
        server.token(code=authorization["code"], redirect_uri=REDIRECT_URI, code_verifier=wrong)
    with pytest.raises(OAuthError) as excinfo:
        server.token(code=authorization["code"], redirect_uri=REDIRECT_URI, code_verifier=verifier)
    assert excinfo.value.error == "invalid_grant"


def test_expired_authorization_code_is_rejected():
    clock = FakeClock()
    server = StubAuthServer(ttl_seconds=10, clock=clock)
    verifier = generate_code_verifier()
    authorization = server.authorize(
        redirect_uri=REDIRECT_URI,
        code_challenge=code_challenge_s256(verifier),
        code_challenge_method="S256",
    )
    clock.advance(10.5)
    with pytest.raises(OAuthError) as excinfo:
        server.token(
            code=authorization["code"],
            redirect_uri=REDIRECT_URI,
            code_verifier=verifier,
        )
    assert excinfo.value.error == "invalid_grant"


def test_authorize_requires_s256_pkce():
    server = StubAuthServer()
    verifier = generate_code_verifier()
    with pytest.raises(OAuthError) as excinfo:
        server.authorize(redirect_uri=REDIRECT_URI)
    assert excinfo.value.error == "invalid_request"
    with pytest.raises(OAuthError) as excinfo:
        server.authorize(
            redirect_uri=REDIRECT_URI,
            code_challenge=code_challenge_s256(verifier),
            code_challenge_method="plain",
        )
    assert excinfo.value.error == "invalid_request"


def test_state_mismatch_is_rejected_and_attempt_is_consumed():
    server = StubAuthServer()
    client = _client(server)
    request = _begin(client)
    authorization = _authorize(server, request)
    with pytest.raises(OAuthError) as excinfo:
        client.exchange_callback(_callback(authorization["code"], "tampered-state"))
    assert excinfo.value.error == "invalid_state"
    with pytest.raises(OAuthError) as excinfo:
        client.exchange_callback(_callback(authorization["code"], request.state))
    assert excinfo.value.error == "invalid_state"


def test_callback_cannot_be_replayed_after_success():
    server = StubAuthServer()
    client = _client(server)
    request = _begin(client)
    authorization = _authorize(server, request)
    callback = _callback(authorization["code"], request.state)
    client.exchange_callback(callback)
    with pytest.raises(OAuthError) as excinfo:
        client.exchange_callback(callback)
    assert excinfo.value.error == "invalid_state"
