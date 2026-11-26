"""OAuth 2.0 authorization-code client for :class:`StubAuthServer`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import OAuthError


@dataclass
class AuthorizationRequest:
    """A pending authorization-code request.

    ``url`` is the authorization endpoint URL the user agent must visit,
    ``state`` is the anti-CSRF value remembered for this request and
    ``code_verifier`` is the PKCE verifier kept for the token exchange.
    """

    url: str
    state: str
    code_verifier: str


class OAuthClient:
    """Client for the authorization-code grant with PKCE.

    The client talks to a :class:`StubAuthServer` that plays the role of the
    authorization and token endpoints. :meth:`begin_authorization` builds the
    URL the user agent should visit and :meth:`exchange_callback` handles the
    redirect back from the user agent.
    """

    def __init__(
        self,
        server: Any,
        *,
        client_id: str,
        redirect_uri: str,
        authorize_url: str = "https://auth.example/authorize",
    ) -> None:
        self.server = server
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.authorize_url = authorize_url
        self._pending: AuthorizationRequest | None = None

    def begin_authorization(self) -> AuthorizationRequest:
        """Start a new authorization-code plus PKCE request.

        Generates a fresh code verifier and state, remembers them for the
        redirect back, and returns the :class:`AuthorizationRequest`.
        """
        raise NotImplementedError("begin_authorization is not implemented yet")

    def exchange_callback(self, callback_url: str) -> dict[str, Any]:
        """Handle the redirect back from the authorization server.

        Validates the state value and redeems the authorization code at the
        stub token endpoint, returning the token response dict.
        """
        raise NotImplementedError("exchange_callback is not implemented yet")
