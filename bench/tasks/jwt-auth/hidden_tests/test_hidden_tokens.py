"""Behavioral tests for the HS256 token helpers required by the task."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest


def _add_workspace_to_path() -> None:
    here = Path(__file__).resolve()
    for candidate in here.parents[:4]:
        if (candidate / "authsvc").is_dir():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return


_add_workspace_to_path()

SECRET = "unit-test-secret"
T0 = 1_700_000_000


def tokens_module():
    from authsvc import tokens

    return tokens


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_json(payload: dict) -> str:
    return b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def sign(header_b64: str, payload_b64: str) -> str:
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    return b64url(hmac.new(SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest())


def test_issue_token_has_three_hs256_segments():
    tokens = tokens_module()
    token = tokens.issue_token("alice", SECRET, now=T0)
    assert isinstance(token, str)
    parts = token.split(".")
    assert len(parts) == 3
    padded = parts[0] + "=" * (-len(parts[0]) % 4)
    header = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    assert header["alg"] == "HS256"
    assert header["typ"] == "JWT"


def test_issue_token_claims_and_ttl():
    tokens = tokens_module()
    token = tokens.issue_token("alice", SECRET, roles=("admin",), ttl_seconds=120, now=T0)
    claims = tokens.verify_token(token, SECRET, now=T0)
    assert claims["sub"] == "alice"
    assert claims["iat"] == T0
    assert claims["exp"] == T0 + 120
    assert list(claims["roles"]) == ["admin"]


def test_default_ttl_is_fifteen_minutes():
    tokens = tokens_module()
    token = tokens.issue_token("alice", SECRET, now=T0)
    claims = tokens.verify_token(token, SECRET, now=T0)
    assert claims["exp"] - claims["iat"] == 900


def test_issue_rejects_empty_subject_and_negative_ttl():
    tokens = tokens_module()
    with pytest.raises(ValueError):
        tokens.issue_token("", SECRET, now=T0)
    with pytest.raises(ValueError):
        tokens.issue_token("alice", SECRET, ttl_seconds=-1, now=T0)


def test_verify_accepts_matching_signature_only():
    tokens = tokens_module()
    token = tokens.issue_token("alice", SECRET, now=T0)
    with pytest.raises(tokens.TokenError):
        tokens.verify_token(token, "different-secret", now=T0)


def test_tampered_payload_rejected():
    tokens = tokens_module()
    token = tokens.issue_token("alice", SECRET, now=T0)
    header, _payload, signature = token.split(".")
    forged = b64url_json(
        {"sub": "root", "iat": T0, "exp": T0 + 900, "roles": ["admin"]}
    )
    with pytest.raises(tokens.TokenError):
        tokens.verify_token(f"{header}.{forged}.{signature}", SECRET, now=T0)


def test_tampered_signature_rejected():
    tokens = tokens_module()
    token = tokens.issue_token("alice", SECRET, now=T0)
    header, payload, signature = token.split(".")
    replacement = "A" if signature[-2] != "A" else "B"
    forged_signature = signature[:-2] + replacement + signature[-1]
    with pytest.raises(tokens.TokenError):
        tokens.verify_token(f"{header}.{payload}.{forged_signature}", SECRET, now=T0)


def test_unsigned_alg_none_rejected():
    tokens = tokens_module()
    header = b64url_json({"alg": "none", "typ": "JWT"})
    payload = b64url_json(
        {"sub": "root", "iat": T0, "exp": T0 + 900, "roles": ["admin"]}
    )
    for token in (f"{header}.{payload}.", f"{header}.{payload}"):
        with pytest.raises(tokens.TokenError):
            tokens.verify_token(token, SECRET, now=T0)


@pytest.mark.parametrize(
    "token", ["", "not-a-token", "a.b", "a.b.c.d", "...", "!!!.??.@@@"]
)
def test_malformed_tokens_rejected(token):
    tokens = tokens_module()
    with pytest.raises(tokens.TokenError):
        tokens.verify_token(token, SECRET, now=T0)


def test_expired_tokens_rejected():
    tokens = tokens_module()
    token = tokens.issue_token("alice", SECRET, ttl_seconds=60, now=T0)
    assert tokens.verify_token(token, SECRET, now=T0 + 59)["sub"] == "alice"
    with pytest.raises(tokens.TokenError):
        tokens.verify_token(token, SECRET, now=T0 + 60)
    with pytest.raises(tokens.TokenError):
        tokens.verify_token(token, SECRET, now=T0 + 86_400)


def test_token_without_expiry_rejected():
    tokens = tokens_module()
    header = b64url_json({"alg": "HS256", "typ": "JWT"})
    payload = b64url_json({"sub": "alice", "iat": T0})
    token = f"{header}.{payload}.{sign(header, payload)}"
    with pytest.raises(tokens.TokenError):
        tokens.verify_token(token, SECRET, now=T0)


def test_verify_returns_fresh_claims_dict():
    tokens = tokens_module()
    token = tokens.issue_token("alice", SECRET, now=T0)
    first = tokens.verify_token(token, SECRET, now=T0)
    first["sub"] = "mallory"
    second = tokens.verify_token(token, SECRET, now=T0)
    assert second["sub"] == "alice"
