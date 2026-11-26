# Finish the PKCE authorization-code flow

You are working in a small Python 3.12 project, `oauth-pkce`, that implements
the OAuth 2.0 authorization-code grant against an in-process stub authorization
server. Everything is standard library only: there is no network access and no
third-party runtime dependency. pytest is available to run the tests.

Right now the stub server issues and redeems authorization codes with almost no
protection, and the client-side flow is a placeholder. Your job is to complete
the flow so that it uses PKCE with the S256 challenge method as specified by
RFC 7636, validates the anti-CSRF `state` value on the client, and hardens the
authorization-code exchange.

## Requirements

### PKCE helpers (`oauth_pkce/pkce.py`, re-exported from `oauth_pkce`)

- `generate_code_verifier(length=64)` returns a fresh, unpredictable RFC 7636
  code verifier of exactly `length` characters. A verifier is 43 to 128
  characters long (inclusive) and uses only the unreserved characters
  `A-Z a-z 0-9 - . _ ~`. Raise `ValueError` for a length outside 43..128.
  Two calls must not return the same value.
- `code_challenge_s256(verifier)` returns the RFC 7636 S256 challenge:
  `base64url(sha256(ASCII(verifier)))` with the `=` padding removed. Raise
  `ValueError` when `verifier` is not a valid verifier (wrong length or
  characters outside the unreserved set). As a reference, RFC 7636 Appendix B
  maps verifier `dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk` to challenge
  `E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM`.

### Stub authorization server (`oauth_pkce/server.py`)

`StubAuthServer(ttl_seconds=300.0, clock=None)` keeps authorization codes in
memory; `clock` is a callable returning the current time in seconds (it
defaults to `time.monotonic`) and drives expiry.

`authorize(*, client_id=None, redirect_uri, state=None, code_challenge=None,
code_challenge_method=None)` must reject a request that has no
`code_challenge` or whose `code_challenge_method` is not exactly `"S256"`
with `OAuthError("invalid_request")`. `redirect_uri` stays required. A valid
request returns a dict with `code`, `state`, and `redirect_uri`.

`token(*, code, client_id=None, redirect_uri=None, code_verifier=None,
grant_type="authorization_code")` redeems a code and returns a dict with
`access_token`, `token_type` equal to `"Bearer"`, and `expires_in` equal to
`int(ttl_seconds)`. It must raise `OAuthError` with:

- `unsupported_grant_type` when `grant_type` is not `"authorization_code"`;
- `invalid_grant` when the code is unknown or already used, when the client id
  or redirect URI does not match the authorization request, when the code is
  older than `ttl_seconds` according to `clock` (that is,
  `clock() - issued_at > ttl_seconds`), or when the code was issued with PKCE
  and the verifier is missing, malformed, or does not hash to the stored
  challenge.

An authorization code is single use: the first redemption attempt consumes it
even when that attempt fails, so replaying a code always fails with
`invalid_grant`.

### Client (`oauth_pkce/client.py`)

- `begin_authorization()` starts a new request. It generates a fresh code
  verifier and a fresh anti-CSRF `state` (a random URL-safe token of at least
  16 characters), remembers them, and returns an
  `AuthorizationRequest(url=..., state=..., code_verifier=...)`. `url` is the
  configured `authorize_url` with query parameters `response_type=code`,
  `client_id`, `redirect_uri`, `state`, `code_challenge`, and
  `code_challenge_method=S256`. Starting a request replaces any earlier
  pending request.
- `exchange_callback(callback_url)` handles the redirect back. It parses
  `code` and `state` from the callback URL's query string, validates that
  `state` matches the pending request, then redeems the code at the stub server
  with the kept verifier, the client id, and the redirect URI, and returns the
  token dict. If there is no pending request or the `state` value does not
  match, raise `OAuthError("invalid_state")`. The pending request is consumed
  by the first call, so replaying a callback URL (whether or not the first call
  succeeded) raises `OAuthError("invalid_state")`. If the callback carries an
  OAuth `error` parameter, raise `OAuthError` using that error code.

The existing public names must stay importable from the package root:
`OAuthClient`, `AuthorizationRequest`, `StubAuthServer`, `OAuthError`,
`generate_code_verifier`, and `code_challenge_s256`.

## Constraints

- Python 3.12 standard library only. No network calls and no new runtime
  dependencies.
- Existing tests under `tests/` are the regression suite: do not change them
  and keep them passing. Adding new tests is welcome.
- Run the suite with `python -m pytest -q`.

## Working agreement

- Work autonomously; do not ask questions and do not wait for direction.
- Commit your work on a feature branch (for example `feature/pkce-flow`).
- Do not push, open a pull request, or merge.
- Leave the repository in a clean, working state when you are done.
