"""PKCE (RFC 7636) helpers.

The OAuth client needs a fresh code verifier for every authorization request
and must send the matching S256 challenge to the authorization server.
"""

from __future__ import annotations

#: Characters allowed in an RFC 7636 code verifier.
UNRESERVED_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~"
MIN_VERIFIER_LENGTH = 43
MAX_VERIFIER_LENGTH = 128


def _is_valid_verifier(verifier: object) -> bool:
    """Return True when ``verifier`` is a legal RFC 7636 code verifier."""
    return (
        isinstance(verifier, str)
        and MIN_VERIFIER_LENGTH <= len(verifier) <= MAX_VERIFIER_LENGTH
        and all(char in UNRESERVED_CHARS for char in verifier)
    )


def generate_code_verifier(length: int = 64) -> str:
    """Return a new random PKCE code verifier of ``length`` characters.

    The verifier must satisfy RFC 7636 section 4.1: 43 to 128 characters
    (inclusive) drawn from the unreserved character set. Raise ``ValueError``
    when ``length`` is outside that range.
    """
    raise NotImplementedError("generate_code_verifier is not implemented yet")


def code_challenge_s256(verifier: str) -> str:
    """Return the RFC 7636 S256 code challenge for ``verifier``.

    The challenge is ``base64url(sha256(verifier))`` without ``=`` padding.
    Raise ``ValueError`` when ``verifier`` is not a valid RFC 7636 verifier.
    """
    raise NotImplementedError("code_challenge_s256 is not implemented yet")
