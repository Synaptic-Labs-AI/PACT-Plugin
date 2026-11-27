"""Seed tests for the request router."""

from authsvc.api import Api
from authsvc.users import UserStore


def make_api() -> Api:
    store = UserStore()
    store.add_user("alice", "correct-horse", display_name="Alice A.")
    store.add_user("bob", "battery-staple", roles=("member", "admin"))
    return Api(store)


def test_health_is_public():
    response = make_api().handle_request("GET", "/health")
    assert response.status == 200
    assert response.body == {"status": "ok"}


def test_login_returns_username_and_roles():
    response = make_api().handle_request(
        "POST", "/login", {"username": "bob", "password": "battery-staple"}
    )
    assert response.status == 200
    assert response.body["username"] == "bob"
    assert response.body["roles"] == ["member", "admin"]


def test_login_rejects_wrong_password():
    response = make_api().handle_request(
        "POST", "/login", {"username": "alice", "password": "nope"}
    )
    assert response.status == 401
    assert response.body == {"error": "invalid_credentials"}


def test_login_requires_username_and_password():
    response = make_api().handle_request("POST", "/login", {"username": "alice"})
    assert response.status == 400
    assert response.body == {"error": "invalid_request"}


def test_profile_is_public():
    response = make_api().handle_request("GET", "/profile?username=alice")
    assert response.status == 200
    assert response.body["display_name"] == "Alice A."


def test_profile_errors():
    api = make_api()
    assert api.handle_request("GET", "/profile?username=carol").status == 404
    assert api.handle_request("GET", "/profile").status == 400


def test_unknown_route_returns_404():
    response = make_api().handle_request("DELETE", "/health")
    assert response.status == 404
    assert response.body == {"error": "not_found"}
