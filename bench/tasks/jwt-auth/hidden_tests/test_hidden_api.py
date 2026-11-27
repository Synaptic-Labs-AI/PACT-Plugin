"""Behavioral tests for token-protected request routing."""

from __future__ import annotations

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

SECRET = "api-test-secret"


def load_api():
    from authsvc.api import Api
    from authsvc.users import UserStore

    return Api, UserStore


def load_tokens():
    from authsvc import tokens

    return tokens


def make_api(secret=SECRET):
    Api, UserStore = load_api()
    store = UserStore()
    store.add_user("alice", "correct-horse", display_name="Alice A.")
    store.add_user("bob", "battery-staple", roles=("member", "admin"))
    return Api(store, secret=secret), store


def bearer(token: str, header_name: str = "Authorization") -> dict:
    return {header_name: f"Bearer {token}"}


def login(api, username: str, password: str) -> str:
    response = api.handle_request(
        "POST", "/login", {"username": username, "password": password}
    )
    assert response.status == 200, response
    return response.body["token"]


def test_login_issues_token_for_user():
    api, _store = make_api()
    body = api.handle_request(
        "POST", "/login", {"username": "alice", "password": "correct-horse"}
    ).body
    token = body["token"]
    assert isinstance(token, str) and token.count(".") == 2
    assert body["expires_in"] == 900
    claims = load_tokens().verify_token(token, SECRET)
    assert claims["sub"] == "alice"
    assert claims["exp"] - claims["iat"] == 900
    response = api.handle_request("GET", "/me", headers=bearer(token))
    assert response.status == 200
    assert response.body["username"] == "alice"
    assert response.body["display_name"] == "Alice A."
    assert response.body["roles"] == ["member"]


@pytest.mark.parametrize(
    "headers",
    [
        None,
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer   "},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Bearer not-a-token"},
        {"Authorization": "Bearer a.b.c"},
    ],
)
def test_me_requires_valid_bearer_token(headers):
    api, _store = make_api()
    kwargs = {} if headers is None else {"headers": headers}
    response = api.handle_request("GET", "/me", **kwargs)
    assert response.status == 401
    assert response.body == {"error": "unauthorized"}


def test_me_rejects_expired_tampered_or_unknown_subject_tokens():
    api, _store = make_api()
    tokens = load_tokens()
    expired = tokens.issue_token("alice", SECRET, ttl_seconds=0)
    other_secret = tokens.issue_token("alice", "wrong-secret")
    unknown_subject = tokens.issue_token("ghost", SECRET)
    valid = tokens.issue_token("alice", SECRET)
    tampered = valid[:-2] + ("A" if valid[-2] != "A" else "B") + valid[-1]
    for token in (expired, other_secret, unknown_subject, tampered):
        response = api.handle_request("GET", "/me", headers=bearer(token))
        assert response.status == 401
        assert response.body == {"error": "unauthorized"}


def test_bearer_scheme_and_header_name_are_case_insensitive():
    api, _store = make_api()
    token = login(api, "alice", "correct-horse")
    for headers in (
        {"authorization": f"bearer {token}"},
        {"Authorization": f"bearer {token}"},
    ):
        response = api.handle_request("GET", "/me", headers=headers)
        assert response.status == 200


def test_admin_route_requires_admin_role():
    api, _store = make_api()
    alice_token = login(api, "alice", "correct-horse")
    bob_token = login(api, "bob", "battery-staple")
    anonymous = api.handle_request("GET", "/admin/users")
    assert anonymous.status == 401
    assert anonymous.body == {"error": "unauthorized"}
    member = api.handle_request("GET", "/admin/users", headers=bearer(alice_token))
    assert member.status == 403
    assert member.body == {"error": "forbidden"}
    admin = api.handle_request("GET", "/admin/users", headers=bearer(bob_token))
    assert admin.status == 200
    users = admin.body["users"]
    assert [user["username"] for user in users] == ["alice", "bob"]
    bob = users[1]
    assert bob["display_name"] == "bob"
    assert bob["roles"] == ["member", "admin"]


def test_admin_route_rejects_invalid_token():
    api, _store = make_api()
    bad = api.handle_request(
        "GET", "/admin/users", headers={"Authorization": "Bearer nope"}
    )
    assert bad.status == 401
    assert bad.body == {"error": "unauthorized"}


def test_custom_secret_controls_signing_and_verification():
    api, _store = make_api(secret="another-secret")
    token = login(api, "alice", "correct-horse")
    tokens = load_tokens()
    assert tokens.verify_token(token, "another-secret")["sub"] == "alice"
    with pytest.raises(tokens.TokenError):
        tokens.verify_token(token, SECRET)
    assert api.handle_request("GET", "/me", headers=bearer(token)).status == 200


def test_existing_behavior_is_preserved():
    api, _store = make_api()
    assert api.handle_request("GET", "/health").status == 200
    assert api.handle_request("GET", "/profile?username=alice").status == 200
    wrong = api.handle_request(
        "POST", "/login", {"username": "alice", "password": "wrong-password"}
    )
    assert wrong.status == 401
    assert wrong.body == {"error": "invalid_credentials"}
    missing = api.handle_request("POST", "/login", {"username": "alice"})
    assert missing.status == 400
    assert missing.body == {"error": "invalid_request"}
    assert api.handle_request("GET", "/nope").status == 404
