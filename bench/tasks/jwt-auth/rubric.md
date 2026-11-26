# Blind quality rubric: jwt-auth

Score the agent's work without looking at the hidden tests. Use the patch, the
repository state, and the agent's own test run as evidence. Each criterion is
scored 0-4; the weighted total is the score.

## Criteria and weights

### 1. Token implementation (30%)

- 4: produces well-formed three-segment HS256 tokens (base64url, no padding),
  with correct `sub`/`iat`/`exp`/`roles` claims, a 900 second default TTL,
  deterministic `now` handling, and tokens verify round-trip.
- 3: works, but one contract detail is off (e.g. padding, claim types, TTL
  default).
- 2: tokens round-trip but claims or encoding do not match the contract.
- 1: partial attempt; signature or claims not derivable.
- 0: missing or not a JWT.

### 2. Verification hardening (25%)

- 4: signature checked (constant-time) before claims are trusted; rejects
  tampered payload/signature, malformed structure, unsupported algorithm and
  `alg: none`, wrong secret, missing/non-numeric `exp`, and expired tokens via
  the documented `TokenError`; accepts valid tokens at the `now < exp`
  boundary.
- 3: correct except one edge case (e.g. alg confusion or missing `exp`).
- 2: basic verification but several edge cases unhandled.
- 1: verification accepts invalid tokens or uses non-constant-time compare in a
  way that is easy to exploit.
- 0: no verification.

### 3. Router authorization (25%)

- 4: `/login` issues a usable token; `/me` and `/admin/users` enforce the
  exact 200/401/403 behavior and error bodies; header/scheme matching is
  case-insensitive; token subjects are re-checked against the store; existing
  public routes and error responses are unchanged.
- 3: protected routes work but one response detail or case-insensitivity rule
  is off.
- 2: one protected route implemented or authorization incomplete.
- 1: routes exist but accept requests without a valid token.
- 0: no route changes.

### 4. Tests and regression safety (10%)

- 4: seed tests pass untouched and the agent added focused tests covering
  tampering, expiry, malformed tokens, and authorized/forbidden access.
- 3: seed tests pass; some new behavior untested.
- 2: seed tests pass; no meaningful new tests.
- 1: seed tests were weakened or changed to pass trivially.
- 0: seed tests fail.

### 5. Engineering quality and process (10%)

- 4: stdlib only, clean module split (`tokens` vs `api`), readable typed code,
  no dead code or duplicated crypto, secret configurable, work committed on a
  feature branch with no push/PR/merge.
- 3: solid, with minor style or organization issues.
- 2: works but noticeably messy or secret handling is awkward.
- 1: poor structure or hard-coded secrets in the token module.
- 0: unreadable or violates constraints.

## Gates and labels

- All hidden tests must pass before the score can exceed 2.
- Accepting unsigned `alg: none` tokens, trusting unverified claims, or
  comparing signatures non-constant-time caps the score at 1.
- Adding a third-party dependency, weakening password handling, or breaking a
  seed test caps the score at 2.
- 3.5-4: excellent, ready to merge. 2.5-3.4: solid, minor gaps. 1.5-2.4:
  acceptable internal only. 0.5-1.4: weak. 0-0.4: broken.
