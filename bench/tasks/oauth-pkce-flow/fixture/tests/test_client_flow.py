"""Tests for the behaviour the stub authorization server already provides."""

from __future__ import annotations

import base64
import hashlib

import pytest

from oauth_pkce import OAuthError, StubAuthServer

CLIENT_ID = "demo-client"
REDIRECT_URI = "https://demo.example/callback"
VERIFIER = "seed-verifier-" + "a" * 40
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode("ascii")).digest())
    .rstrip(b"=")
    .decode("ascii")
)


def _authorize(server: StubAuthServer, **overrides):
    kwargs = dict(
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        state="seed-state",
        code_challenge=CHALLENGE,
        code_challenge_method="S256",
    )
    kwargs.update(overrides)
    return server.authorize(**kwargs)


def test_authorize_returns_code_and_echoes_state():
    server = StubAuthServer()
    response = _authorize(server)
    assert set(response) == {"code", "state", "redirect_uri"}
    assert isinstance(response["code"], str) and response["code"]
    assert response["state"] == "seed-state"
    assert response["redirect_uri"] == REDIRECT_URI


def test_authorize_requires_redirect_uri():
    server = StubAuthServer()
    with pytest.raises(OAuthError) as excinfo:
        server.authorize(client_id=CLIENT_ID, redirect_uri="")
    assert excinfo.value.error == "invalid_request"


def test_token_exchange_returns_bearer_token():
    server = StubAuthServer()
    code = _authorize(server)["code"]
    token = server.token(
        code=code,
        client_id=CLIENT_ID,
        redirect_uri=REDIRECT_URI,
        code_verifier=VERIFIER,
    )
    assert token["token_type"] == "Bearer"
    assert isinstance(token["access_token"], str) and token["access_token"]
    assert token["expires_in"] == 300


def test_token_exchange_rejects_unknown_code():
    server = StubAuthServer()
    with pytest.raises(OAuthError) as excinfo:
        server.token(
            code="not-a-real-code",
            client_id=CLIENT_ID,
            redirect_uri=REDIRECT_URI,
            code_verifier=VERIFIER,
        )
    assert excinfo.value.error == "invalid_grant"


def test_token_exchange_rejects_mismatched_redirect_uri():
    server = StubAuthServer()
    code = _authorize(server)["code"]
    with pytest.raises(OAuthError) as excinfo:
        server.token(
            code=code,
            client_id=CLIENT_ID,
            redirect_uri="https://evil.example/callback",
            code_verifier=VERIFIER,
        )
    assert excinfo.value.error == "invalid_grant"


def test_token_rejects_unsupported_grant_type():
    server = StubAuthServer()
    with pytest.raises(OAuthError) as excinfo:
        server.token(code="x", redirect_uri=REDIRECT_URI, grant_type="client_credentials")
    assert excinfo.value.error == "unsupported_grant_type"


def test_oauth_error_exposes_code_and_description():
    error = OAuthError("invalid_grant", "code expired")
    assert error.error == "invalid_grant"
    assert error.description == "code expired"
    assert "invalid_grant" in str(error)
    assert "code expired" in str(error)
