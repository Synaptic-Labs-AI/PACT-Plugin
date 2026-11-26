# Blind quality rubric: oauth-pkce-flow

Review only the final repository state; hidden tests are not part of the
reviewer's evidence. Score each dimension 0-3 (0 missing/broken, 1 partial,
2 solid, 3 excellent) and record concrete file/line evidence.

## Dimensions

1. **S256 challenge correctness** - the S256 challenge is computed exactly as
   RFC 7636 requires (base64url of SHA-256, no padding); the RFC 7636 Appendix
   B reference vector is handled correctly; invalid verifier inputs are
   rejected rather than silently accepted.
2. **Verifier generation** - verifiers are unpredictable (CSPRNG, not a
   clock/sequence), have the requested length, use only unreserved characters,
   and reject lengths outside 43..128.
3. **Client state handling** - every authorization request gets a fresh
   unpredictable state value; the state is present in the authorization URL
   and validated against the pending request on the callback; the pending
   request is single use, so a replayed or failed callback cannot be retried.
4. **Server-side code lifecycle** - authorization codes are bound to the
   client id and redirect URI, expire after the configured TTL using the
   injected clock, and are single use including failed redemption attempts.
5. **PKCE enforcement** - the stub server requires a challenge and the S256
   method, and the token endpoint rejects a missing, malformed, or mismatched
   verifier with the correct OAuth error code.
6. **Error semantics** - protocol failures raise `OAuthError` with the
   documented codes (`invalid_request`, `invalid_grant`,
   `invalid_state`, `unsupported_grant_type`) and useful descriptions.
7. **Regression discipline** - the existing tests under `tests/` are unchanged
   and still pass; added tests are meaningful rather than vacuous.
8. **Scope and dependency discipline** - standard library only, no network
   access, no unrelated rewrites of the public API, no dead code or leftover
   debug output.

## Scoring guide

- 20-24: excellent, production-quality completion.
- 16-19: solid; minor gaps in error handling or test coverage.
- 12-15: partial; core flow works but at least one security requirement is
  missing or unproven.
- 0-11: incomplete or unsafe; the flow does not meet the stated contract.

Report any dimension scored 0 or 1 with a concrete suggested fix. Do not
award credit for changes that merely satisfy a test without implementing the
described behaviour.
