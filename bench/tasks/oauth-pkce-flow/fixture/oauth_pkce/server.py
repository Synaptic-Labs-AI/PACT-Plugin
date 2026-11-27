"""In-memory stub of an OAuth 2.0 authorization server.

The stub keeps authorization codes in memory. It stands in for the real
authorization and token endpoints so the client flow can be exercised without
network access: codes are issued by :meth:`StubAuthServer.authorize` and
redeemed by :meth:`StubAuthServer.token`.
"""

from __future__ import annotations

import secrets
import time
from typing import Any, Callable

from .errors import OAuthError


class StubAuthServer:
    """An in-process authorization server with a token endpoint.

    ``ttl_seconds`` is how long an issued authorization code stays valid and
    ``clock`` is a callable returning the current time in seconds (it defaults
    to :func:`time.monotonic`).
    """

    def __init__(
        self,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock = clock if clock is not None else time.monotonic
        self._codes: dict[str, dict[str, Any]] = {}

    def authorize(
        self,
        *,
        client_id: str | None = None,
        redirect_uri: str,
        state: str | None = None,
        code_challenge: str | None = None,
        code_challenge_method: str | None = None,
    ) -> dict[str, Any]:
        """Validate an authorization request and issue an authorization code.

        Returns a dict with the issued ``code``, the echoed ``state`` and the
        ``redirect_uri`` the code is bound to.
        """
        if not redirect_uri:
            raise OAuthError("invalid_request", "redirect_uri is required")
        code = secrets.token_urlsafe(32)
        self._codes[code] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "issued_at": self._clock(),
        }
        return {"code": code, "state": state, "redirect_uri": redirect_uri}

    def token(
        self,
        *,
        code: str,
        client_id: str | None = None,
        redirect_uri: str | None = None,
        code_verifier: str | None = None,
        grant_type: str = "authorization_code",
    ) -> dict[str, Any]:
        """Redeem an authorization code for a bearer token.

        Returns a dict with ``access_token``, ``token_type`` and
        ``expires_in``. Raises :class:`OAuthError` for protocol failures.
        """
        if grant_type != "authorization_code":
            raise OAuthError(
                "unsupported_grant_type",
                "only the authorization_code grant is supported",
            )
        record = self._codes.get(code)
        if record is None:
            raise OAuthError("invalid_grant", "unknown authorization code")
        if client_id != record["client_id"]:
            raise OAuthError("invalid_grant", "client_id does not match the authorization request")
        if redirect_uri != record["redirect_uri"]:
            raise OAuthError("invalid_grant", "redirect_uri does not match the authorization request")
        return {
            "access_token": secrets.token_urlsafe(32),
            "token_type": "Bearer",
            "expires_in": int(self.ttl_seconds),
        }
