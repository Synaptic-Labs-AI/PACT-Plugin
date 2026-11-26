"""In-memory user store for the authsvc demo service.

Passwords are never kept in plain text: each user gets a random salt and the
stored value is a PBKDF2-HMAC-SHA256 hash. The store is intentionally small and
process-local.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

KNOWN_ROLES = ("member", "admin")
DEFAULT_ROLES = ("member",)
_PBKDF2_ITERATIONS = 120_000
_MIN_PASSWORD_LENGTH = 8


class UserError(ValueError):
    """Raised when a user record cannot be created."""


def _hash_password(password: str, salt: bytes) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    )
    return digest.hex()


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    roles: tuple[str, ...]
    password_hash: str
    salt_hex: str


class UserStore:
    """Small in-memory user directory."""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}

    def add_user(
        self,
        username: str,
        password: str,
        *,
        roles: tuple[str, ...] | list[str] | None = None,
        display_name: str | None = None,
    ) -> User:
        if not isinstance(username, str) or not username:
            raise UserError("username must be a non-empty string")
        if not isinstance(password, str) or len(password) < _MIN_PASSWORD_LENGTH:
            raise UserError("password must be at least 8 characters")
        resolved_roles = tuple(roles) if roles is not None else DEFAULT_ROLES
        for role in resolved_roles:
            if role not in KNOWN_ROLES:
                raise UserError(f"unknown role: {role}")
        if username in self._users:
            raise UserError(f"duplicate user: {username}")
        salt = secrets.token_bytes(16)
        user = User(
            username=username,
            display_name=display_name or username,
            roles=resolved_roles,
            password_hash=_hash_password(password, salt),
            salt_hex=salt.hex(),
        )
        self._users[username] = user
        return user

    def get(self, username: str) -> User | None:
        return self._users.get(username)

    def list_users(self) -> list[User]:
        return [self._users[name] for name in sorted(self._users)]

    def authenticate(self, username: str, password: str) -> User | None:
        user = self._users.get(username) if isinstance(username, str) else None
        if user is None:
            return None
        candidate = _hash_password(password, bytes.fromhex(user.salt_hex))
        if hmac.compare_digest(candidate, user.password_hash):
            return user
        return None
