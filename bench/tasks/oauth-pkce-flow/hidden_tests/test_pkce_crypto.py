"""Requirements for the PKCE helpers (RFC 7636)."""

from __future__ import annotations

import string

import pytest

from oauth_pkce import code_challenge_s256, generate_code_verifier

UNRESERVED = set(string.ascii_letters + string.digits + "-._~")
RFC_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_default_verifier_is_rfc7636_shaped():
    verifier = generate_code_verifier()
    assert isinstance(verifier, str)
    assert len(verifier) == 64
    assert set(verifier) <= UNRESERVED


def test_verifier_length_is_respected_and_bounded():
    assert len(generate_code_verifier(43)) == 43
    assert len(generate_code_verifier(128)) == 128
    for bad in (42, 129, 0):
        with pytest.raises(ValueError):
            generate_code_verifier(bad)


def test_verifiers_are_random():
    assert generate_code_verifier() != generate_code_verifier()


def test_s256_challenge_matches_rfc7636_vector():
    assert code_challenge_s256(RFC_VERIFIER) == RFC_CHALLENGE


def test_s256_challenge_has_no_padding_and_depends_on_verifier():
    challenge = code_challenge_s256(RFC_VERIFIER)
    assert "=" not in challenge
    assert challenge != code_challenge_s256("A" * 43 + "x")


def test_s256_rejects_invalid_verifiers():
    for bad in ("too-short", "A" * 42, "A" * 129, "A" * 60 + "!", ""):
        with pytest.raises(ValueError):
            code_challenge_s256(bad)
