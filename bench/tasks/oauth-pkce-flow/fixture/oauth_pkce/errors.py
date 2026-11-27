"""OAuth 2.0 error type shared by the client and the stub authorization server."""

from __future__ import annotations


class OAuthError(Exception):
    """An OAuth protocol failure.

    ``error`` is the machine-readable error code defined by RFC 6749
    (for example ``"invalid_request"`` or ``"invalid_grant"``) and
    ``description`` is a short human-readable explanation.
    """

    def __init__(self, error: str, description: str = "") -> None:
        self.error = error
        self.description = description
        message = f"{error}: {description}" if description else error
        super().__init__(message)
