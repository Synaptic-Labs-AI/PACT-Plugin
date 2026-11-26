"""Seed tests for the in-memory user store."""

import pytest

from authsvc.users import UserError, UserStore


def make_store() -> UserStore:
    store = UserStore()
    store.add_user("alice", "correct-horse", display_name="Alice A.")
    store.add_user("bob", "battery-staple", roles=("member", "admin"))
    return store


def test_add_user_returns_profile():
    store = UserStore()
    user = store.add_user("alice", "correct-horse")
    assert user.username == "alice"
    assert user.display_name == "alice"
    assert user.roles == ("member",)
    assert "correct-horse" not in user.password_hash
    assert store.get("alice") is user


def test_list_users_is_sorted():
    store = make_store()
    assert [user.username for user in store.list_users()] == ["alice", "bob"]


def test_duplicate_user_rejected():
    store = UserStore()
    store.add_user("alice", "correct-horse")
    with pytest.raises(UserError):
        store.add_user("alice", "other-password")


def test_short_password_rejected():
    store = UserStore()
    with pytest.raises(UserError):
        store.add_user("alice", "short")


def test_unknown_role_rejected():
    store = UserStore()
    with pytest.raises(UserError):
        store.add_user("alice", "correct-horse", roles=("superuser",))


def test_authenticate_with_valid_credentials():
    store = make_store()
    user = store.authenticate("alice", "correct-horse")
    assert user is not None
    assert user.username == "alice"


def test_authenticate_rejects_bad_credentials():
    store = make_store()
    assert store.authenticate("alice", "wrong-password") is None
    assert store.authenticate("nobody", "correct-horse") is None
