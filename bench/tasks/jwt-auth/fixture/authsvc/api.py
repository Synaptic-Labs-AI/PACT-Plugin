"""HTTP-less request router for the authsvc demo service.

Callers pass plain dictionaries (method, path, body, headers) and receive a
`Response`. That keeps the service easy to exercise from tests without a server
or network access.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .users import User, UserStore


@dataclass(frozen=True)
class Response:
    status: int
    body: dict[str, Any]


class Api:
    """Routes requests to the user store."""

    def __init__(self, store: UserStore) -> None:
        self.store = store

    def handle_request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        method = method.upper()
        body = body if isinstance(body, dict) else {}
        headers = headers if isinstance(headers, dict) else {}
        parts = urlsplit(path)
        if method == "GET" and parts.path == "/health":
            return Response(200, {"status": "ok"})
        if method == "POST" and parts.path == "/login":
            return self._login(body)
        if method == "GET" and parts.path == "/profile":
            return self._profile(parts.query)
        return Response(404, {"error": "not_found"})

    def _login(self, body: dict[str, Any]) -> Response:
        username = body.get("username")
        password = body.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            return Response(400, {"error": "invalid_request"})
        user = self.store.authenticate(username, password)
        if user is None:
            return Response(401, {"error": "invalid_credentials"})
        return Response(
            200,
            {
                "username": user.username,
                "roles": list(user.roles),
            },
        )

    def _profile(self, query: str) -> Response:
        usernames = parse_qs(query).get("username") or []
        if not usernames or not usernames[0]:
            return Response(400, {"error": "username_required"})
        user = self.store.get(usernames[0])
        if user is None:
            return Response(404, {"error": "not_found"})
        return self._profile_body(user)

    @staticmethod
    def _profile_body(user: User) -> Response:
        return Response(
            200,
            {
                "username": user.username,
                "display_name": user.display_name,
                "roles": list(user.roles),
            },
        )
