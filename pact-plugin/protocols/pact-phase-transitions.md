## Phase Handoffs

**On completing any phase, state**:
1. What you produced (with file paths)
2. Key decisions made
3. What the next agent needs to know

Keep it brief. No templates required.

---

## Test Engagement

| Test Type | Owner |
|-----------|-------|
| Smoke tests | Coders (minimal verification) |
| Unit tests | Test Engineer |
| Integration tests | Test Engineer |
| E2E tests | Test Engineer |

**Coders**: Your work isn't done until smoke tests pass. Smoke tests verify: "Does it compile? Does it run? Does the happy path not crash?" No comprehensive testing—that's TEST phase work.

**Test Engineer**: Engage after Code phase. You own ALL substantive testing: unit tests, integration, E2E, edge cases, adversarial testing. Target 80%+ meaningful coverage of critical paths.

### CODE → TEST Handoff

Coders provide handoff summaries to the orchestrator, who passes them to the test engineer.

**Handoff Format**:
```
1. Produced: Files created/modified
2. Key decisions: Decisions with rationale, assumptions that could be wrong
3. Areas of uncertainty (PRIORITIZED):
   - [HIGH] {description} — Why risky, suggested test focus
   - [MEDIUM] {description}
   - [LOW] {description}
4. Integration points: Other components touched
5. Open questions: Unresolved items
```

Note: Not all priority levels need to be present. Most handoffs have 1-3 uncertainty items total. If you have no uncertainties to flag, explicitly state "No areas of uncertainty flagged" to confirm you considered the question (rather than forgot or omitted it).

**Example**:
```
1. Produced: `src/auth/token-manager.ts`, `src/auth/token-manager.test.ts`
2. Key decisions: Used JWT with 15min expiry (assumed acceptable for this app)
3. Areas of uncertainty:
   - [HIGH] Token refresh race condition — concurrent requests may get stale tokens; test with parallel calls
   - [MEDIUM] Clock skew handling — assumed <5s drift; may fail with larger skew
4. Integration points: Modified `src/middleware/auth.ts` to use new manager
5. Open questions: Should refresh tokens be stored in httpOnly cookies?
```

**Uncertainty Prioritization**:
- **HIGH**: "This could break in production" — Test engineer MUST cover these
- **MEDIUM**: "I'm not 100% confident" — Test engineer should cover these
- **LOW**: "Edge case I thought of" — Test engineer uses discretion

**Test Engineer Response**:
- HIGH uncertainty areas require explicit test cases (mandatory)
- If skipping a flagged area, document the rationale
- Report findings using the Signal Output System (GREEN/YELLOW/RED)

**This is context, not prescription.** The test engineer decides *how* to test, but flagged HIGH uncertainty areas must be addressed.

---

## Cross-Cutting Concerns

Before completing any phase, consider:
- **Security**: Input validation, auth, data protection
- **Performance**: Query efficiency, caching
- **Accessibility**: WCAG, keyboard nav (frontend)
- **Observability**: Logging, error tracking

Not a checklist—just awareness.

---

## Architecture Review (Optional)

For complex features, before Code phase:
- Coders quickly validate architect's design is implementable
- Flag blockers early, not during implementation

Skip for simple features or when "just build it."

---

