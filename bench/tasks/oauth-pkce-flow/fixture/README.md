# oauth-pkce

A tiny, dependency-free OAuth 2.0 authorization-code client with an in-process
stub authorization server. Everything is Python 3.12 standard library only and
nothing touches the network: `oauth_pkce.server.StubAuthServer` stands in for
the authorization and token endpoints.

## Layout

- `oauth_pkce/errors.py` - `OAuthError`, carrying an OAuth error code plus a
  description.
- `oauth_pkce/pkce.py` - PKCE (RFC 7636) code verifier and S256 challenge
  helpers. Not implemented yet.
- `oauth_pkce/server.py` - `StubAuthServer`: issues authorization codes and
  redeems them for bearer tokens.
- `oauth_pkce/client.py` - `OAuthClient`: builds the authorization URL and
  handles the redirect back from the user agent.
- `tests/` - pytest suite covering the behaviour that already works.

## Running the tests

    python -m pytest -q

No third-party runtime dependencies are used or allowed; pytest is only needed
to run the tests.

## Current status

The authorization-code plumbing exists in the stub server, but the PKCE
helpers and the client-side flow are placeholders that raise
`NotImplementedError`. See the docstrings in `oauth_pkce/pkce.py` and
`oauth_pkce/client.py` for the intended contract.
