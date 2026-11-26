# Add token authentication to authsvc

We are adding login-backed token authentication to the `authsvc` demo service
before it is handed to the platform team. The service currently has a public
login endpoint and a public profile lookup, but it cannot protect anything:
there is no token support at all, and every route is open.

Implement the requirements below in the repository in your workspace.

## 1. HMAC-SHA256 JSON Web Tokens (`authsvc/tokens.py`)

Add a new standard-library-only module `authsvc/tokens.py` implementing compact
JWT (RFC 7519 style) tokens signed with HS256:

- `issue_token(subject, secret, *, roles=(), ttl_seconds=900, now=None) -> str`
  - `subject` is a non-empty username string; `secret` is a non-empty `str`
    or `bytes`; `roles` is an iterable of role-name strings.
  - `now` overrides the clock for tests (seconds since the epoch); the default
    is the current time. `ttl_seconds` defaults to 900 and must be >= 0.
  - The token has exactly three base64url-encoded segments joined by ".":
    a JSON header `{"alg": "HS256", "typ": "JWT"}`, a JSON payload, and an
    HMAC-SHA256 signature over `"<header>.<payload>"` keyed with `secret`.
    Base64url encoding uses no "=" padding.
  - Payload claims are `sub` (the subject), `iat` and `exp` (whole seconds
    since the epoch; `exp` = issue time + ttl), and `roles` (a JSON list).
  - Invalid arguments raise `ValueError`.
- `verify_token(token, secret, *, now=None) -> dict`
  - Verifies the signature with a constant-time comparison, then parses the
    payload and returns the claims as a plain `dict` (a fresh copy per call).
  - Raises `authsvc.tokens.TokenError` (a subclass of `ValueError`) for:
    malformed structure, bad base64/JSON, unsupported algorithm (including an
    unsigned `alg: none` token), signature mismatch, a missing or non-numeric
    `exp`, and expired tokens (a token is expired when `now >= exp`).
  - Claims must not be trusted before the signature is verified.

## 2. Protect the service (`authsvc/api.py`)

Keep the existing public routes and their response shapes intact: `GET /health`
-> 200, `POST /login` -> 200/400/401 as today, `GET /profile?username=...` ->
200/400/404 as today, and unknown routes -> 404.

Then extend the router:

- `Api(store, secret=None)`: the router uses the given secret for signing and
  verification. `None` may fall back to an environment variable or a documented
  development default; an explicitly passed empty secret is a `ValueError`.
- `POST /login` on valid credentials: the existing 200 body keeps
  `username`/`roles` and now also includes:
  - `"token"`: a token issued for the user with the configured secret, a
    900 second lifetime, and the user's roles in the `roles` claim;
  - `"expires_in": 900`.
- `GET /me` (new): requires an `Authorization: Bearer <token>` header.
  - Missing/invalid/expired/tampered token, or a token whose subject is no
    longer a known user -> 401 with body `{"error": "unauthorized"}`.
  - Valid token -> 200 with the same shape as the profile endpoint:
    `{"username", "display_name", "roles"}`.
- `GET /admin/users` (new): requires a valid bearer token for a user whose
  `admin` role is present in the token's `roles` claim.
  - Missing/invalid token -> 401 `{"error": "unauthorized"}`.
  - Valid token without `admin` -> 403 `{"error": "forbidden"}`.
  - Admin token -> 200 with `{"users": [{"username", "display_name", "roles"}, ...]}`
    sorted by username.
- The `Authorization` header name and the `Bearer` scheme are matched
  case-insensitively. Other headers are ignored.

## Constraints

- Python 3.12, standard library only: no PyJWT, no third-party runtime packages.
- Keep the existing seed tests passing and add tests for the new behavior.
- Do not change the public `UserStore`/`User` API or weaken password handling.
- Work autonomously: do not ask questions. Commit your work on a feature
  branch. Do not push, open a PR, or merge.
- Run the suite with `python -m pytest -q`.
