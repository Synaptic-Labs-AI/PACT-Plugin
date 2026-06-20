---
name: pact-testing-strategies
description: |
  Testing strategies, test pyramid guidance, and quality assurance patterns for PACT Test phase.
  Use when: designing test suites, implementing unit tests, integration tests, E2E tests,
  performance testing, security testing, or determining test coverage priorities.
  Triggers on: test design, unit testing, integration testing, E2E testing,
  test coverage, test pyramid, mocking, fixtures, performance testing, test phase.
---

# PACT Testing Strategies

Testing guidance for the Test phase of PACT. This skill provides frameworks
for designing comprehensive test suites and links to detailed testing patterns.

## Test Pyramid

The test pyramid guides the distribution of test types for optimal coverage and speed.

```
                    /\
                   /  \      E2E Tests (Few)
                  /    \     - Critical user journeys
                 / E2E  \    - Slow, expensive
                /--------\
               /          \  Integration Tests (Some)
              /Integration \  - API contracts
             /--------------\  - Service interactions
            /                \
           /    Unit Tests    \ Unit Tests (Many)
          /                    \ - Fast, isolated
         /______________________\ - Business logic
```

### Coverage Targets

| Layer | Target | Focus | Speed |
|-------|--------|-------|-------|
| **Unit** | 80%+ line coverage | Business logic, edge cases | <1s per test |
| **Integration** | Key paths covered | API contracts, data flow | <10s per test |
| **E2E** | Critical flows only | User journeys, happy paths | <60s per test |

---

## Unit Testing Patterns

### Arrange-Act-Assert (AAA)

```javascript
describe('OrderService', () => {
  describe('calculateTotal', () => {
    it('should apply discount for orders over $100', () => {
      // Arrange
      const orderService = new OrderService();
      const items = [
        { price: 50, quantity: 2 },
        { price: 20, quantity: 1 }
      ];

      // Act
      const total = orderService.calculateTotal(items);

      // Assert
      expect(total).toBe(108); // $120 - 10% discount
    });
  });
});
```

### Test Behavior, Not Implementation

```javascript
// BAD: Testing implementation details
it('should call repository.save once', () => {
  await userService.createUser(userData);
  expect(userRepository.save).toHaveBeenCalledTimes(1);
});

// GOOD: Testing behavior
it('should create a user with hashed password', async () => {
  const user = await userService.createUser({
    email: 'test@example.com',
    password: 'plaintext'
  });

  expect(user.email).toBe('test@example.com');
  expect(user.password).not.toBe('plaintext');
  expect(await bcrypt.compare('plaintext', user.password)).toBe(true);
});
```

### Mocking External Dependencies

```javascript
// Mock setup
const mockEmailService = {
  send: jest.fn().mockResolvedValue({ id: 'msg_123' })
};

const mockUserRepository = {
  findByEmail: jest.fn(),
  save: jest.fn().mockImplementation(user => ({ ...user, id: 'user_123' }))
};

describe('UserService', () => {
  let userService;

  beforeEach(() => {
    jest.clearAllMocks();
    userService = new UserService(mockUserRepository, mockEmailService);
  });

  it('should send welcome email after creating user', async () => {
    mockUserRepository.findByEmail.mockResolvedValue(null);

    await userService.createUser({ email: 'new@example.com', name: 'New User' });

    expect(mockEmailService.send).toHaveBeenCalledWith({
      to: 'new@example.com',
      template: 'welcome',
      data: expect.objectContaining({ name: 'New User' })
    });
  });
});
```

### Testing Edge Cases

```javascript
describe('validateEmail', () => {
  // Happy path
  it('should accept valid email', () => {
    expect(validateEmail('user@example.com')).toBe(true);
  });

  // Edge cases
  it.each([
    ['email with subdomain', 'user@mail.example.com'],
    ['email with plus sign', 'user+tag@example.com'],
    ['email with numbers', 'user123@example.com'],
  ])('should accept %s', (_, email) => {
    expect(validateEmail(email)).toBe(true);
  });

  // Invalid cases
  it.each([
    ['empty string', ''],
    ['missing @', 'userexample.com'],
    ['missing domain', 'user@'],
    ['spaces', 'user @example.com'],
    ['double @', 'user@@example.com'],
  ])('should reject %s', (_, email) => {
    expect(validateEmail(email)).toBe(false);
  });

  // Boundary cases
  it('should handle very long emails', () => {
    const longEmail = 'a'.repeat(64) + '@' + 'b'.repeat(63) + '.com';
    expect(validateEmail(longEmail)).toBe(true);
  });

  it('should reject emails exceeding max length', () => {
    const tooLongEmail = 'a'.repeat(65) + '@' + 'b'.repeat(64) + '.com';
    expect(validateEmail(tooLongEmail)).toBe(false);
  });
});
```

---

## Integration Testing Patterns

### Non-mocked seam-integration tests (the mock-hid-the-seam trap)

Any hook (or component) whose observable value depends on an **integration seam** —
task-directory resolution, the real session journal/inbox, an env-keyed path
(`CLAUDE_*`), or the real platform task store — MUST have at least one test that
exercises that seam **for real** (a temp git repo, a real on-disk task JSON, a
real journal write), not a mock or monkeypatch of the seam itself.

**Why a fully-mocked suite is not enough.** A hook can pass its entire mocked unit
suite while never firing in live operation, because the one broken seam is the one
every mocked test stubs. The canonical failure: an inert hook shipped green — its
suite mocked the task-list read, so the exact seam that was broken in production
was the seam every test replaced with a stub. Mocking is still correct for
*external* dependencies you don't own (third-party APIs, the network — see
[Mocking External Dependencies](#mocking-external-dependencies)); the rule here is
narrower: do not mock the *integration seam whose correct resolution IS the thing
under test*.

**What a non-mocked seam test looks like.** Build the real seam state on disk (e.g.
a real `~/.claude/tasks/<team>/<id>.json`, or a tmp-redirected equivalent), invoke
the component over the **unstubbed** read, and assert the observable outcome. The
test passes only if the component resolves the real seam — so a regression in
seam resolution turns it red, where a mocked test would stay green.

**The authoritative seam-dependent set** is `SEAM_DEPENDENT_HOOKS` in
`hooks/shared/hook_infra_classifier.py` — a pure-data SSOT (no I/O, not a runtime
hook) whose companion meta-test re-derives each hook's transitive helper closure
from the live import graph and pins it, so the enumeration cannot silently drift.
The skill is the *why/how*; that module is the machine-checkable *which-hooks*.

**Honest residual (not every gap is testable in pytest).** A small class of
behaviors is genuinely **platform-runtime-only** and cannot be pytest-verified —
e.g. whether the platform actually delivers a PreToolUse `additionalContext` field
to the model, or whether a `UserPromptSubmit` hook fires at turn start. These get
a **lightweight, documented manual smoke-note** (the reusable live-probe procedure
template), NOT a shipped runtime hook — baking such a check into consumer runtime
is how maintainer process-discipline leaks into the product. Name the residual
explicitly so it is not mistaken for a testable gap that was skipped.

### API Contract Testing

```javascript
describe('POST /api/users', () => {
  let app;
  let db;

  beforeAll(async () => {
    db = await setupTestDatabase();
    app = createApp(db);
  });

  afterAll(async () => {
    await db.close();
  });

  beforeEach(async () => {
    await db.clear();
  });

  it('should create a user and return 201', async () => {
    const response = await request(app)
      .post('/api/users')
      .send({
        email: 'new@example.com',
        name: 'New User',
        password: 'securepassword123'
      })
      .expect(201);

    expect(response.body).toMatchObject({
      id: expect.any(String),
      email: 'new@example.com',
      name: 'New User',
      createdAt: expect.any(String)
    });

    // Verify password not returned
    expect(response.body.password).toBeUndefined();
  });

  it('should return 400 for invalid email', async () => {
    const response = await request(app)
      .post('/api/users')
      .send({
        email: 'invalid-email',
        name: 'Test User',
        password: 'password123'
      })
      .expect(400);

    expect(response.body).toMatchObject({
      error: {
        code: 'VALIDATION_ERROR',
        message: expect.any(String)
      }
    });
  });

  it('should return 409 for duplicate email', async () => {
    // Create first user
    await request(app)
      .post('/api/users')
      .send({ email: 'exists@example.com', name: 'First', password: 'pass123' });

    // Try to create duplicate
    const response = await request(app)
      .post('/api/users')
      .send({ email: 'exists@example.com', name: 'Second', password: 'pass456' })
      .expect(409);

    expect(response.body.error.code).toBe('DUPLICATE_EMAIL');
  });
});
```

### Database Integration Testing

```javascript
describe('UserRepository', () => {
  let db;
  let userRepo;

  beforeAll(async () => {
    // Use test database (Docker or in-memory)
    db = await setupTestDatabase();
    await db.migrate();
    userRepo = new UserRepository(db);
  });

  afterAll(async () => {
    await db.close();
  });

  beforeEach(async () => {
    await db.clear();
  });

  it('should persist and retrieve user', async () => {
    const userData = {
      email: 'test@example.com',
      name: 'Test User',
      passwordHash: 'hashed'
    };

    const created = await userRepo.save(userData);
    const retrieved = await userRepo.findById(created.id);

    expect(retrieved).toMatchObject({
      id: created.id,
      email: 'test@example.com',
      name: 'Test User'
    });
  });

  it('should return null for non-existent user', async () => {
    const user = await userRepo.findById('non-existent-id');
    expect(user).toBeNull();
  });

  it('should enforce unique email constraint', async () => {
    await userRepo.save({ email: 'unique@example.com', name: 'First' });

    await expect(
      userRepo.save({ email: 'unique@example.com', name: 'Second' })
    ).rejects.toThrow('duplicate');
  });
});
```

For detailed integration patterns: See [references/integration-patterns.md](references/integration-patterns.md)

---

## E2E Testing Patterns

### Critical User Journey Testing

```javascript
// Using Playwright
describe('Checkout Flow', () => {
  let page;

  beforeAll(async () => {
    // Set up authenticated user
    await seedTestData();
  });

  beforeEach(async () => {
    page = await browser.newPage();
    await page.goto('/login');
    await loginAsTestUser(page);
  });

  afterEach(async () => {
    await page.close();
  });

  it('should complete purchase successfully', async () => {
    // Add item to cart
    await page.goto('/products/test-product');
    await page.click('[data-testid="add-to-cart"]');

    // Go to cart
    await page.click('[data-testid="cart-icon"]');
    await expect(page.locator('[data-testid="cart-item"]')).toBeVisible();

    // Proceed to checkout
    await page.click('[data-testid="checkout-button"]');

    // Fill shipping info
    await page.fill('[data-testid="address"]', '123 Test St');
    await page.fill('[data-testid="city"]', 'Test City');
    await page.fill('[data-testid="zip"]', '12345');
    await page.click('[data-testid="continue-to-payment"]');

    // Complete payment (test card)
    await page.fill('[data-testid="card-number"]', '4242424242424242');
    await page.fill('[data-testid="expiry"]', '12/28');
    await page.fill('[data-testid="cvc"]', '123');
    await page.click('[data-testid="place-order"]');

    // Verify confirmation
    await expect(page.locator('[data-testid="order-confirmation"]')).toBeVisible();
    await expect(page.locator('[data-testid="order-number"]')).toContainText(/ORD-/);
  });
});
```

---

## Test Organization

### Directory Structure

```
tests/
├── unit/                           # Fast, isolated tests
│   ├── services/
│   │   ├── UserService.test.js
│   │   └── OrderService.test.js
│   ├── utils/
│   │   └── validation.test.js
│   └── models/
│       └── Order.test.js
│
├── integration/                    # API and database tests
│   ├── api/
│   │   ├── users.test.js
│   │   └── orders.test.js
│   └── repositories/
│       └── UserRepository.test.js
│
├── e2e/                           # End-to-end tests
│   ├── checkout.spec.js
│   ├── authentication.spec.js
│   └── user-profile.spec.js
│
├── fixtures/                       # Shared test data
│   ├── users.js
│   └── orders.js
│
├── helpers/                        # Shared test utilities
│   ├── setup.js
│   ├── factories.js
│   └── matchers.js
│
└── mocks/                          # Shared mocks
    ├── emailService.js
    └── paymentGateway.js
```

### Test Naming Convention

```javascript
// Format: should [expected behavior] when [condition]
it('should return 404 when user does not exist', () => {});
it('should apply 10% discount when order total exceeds $100', () => {});
it('should send confirmation email when order is placed', () => {});
it('should throw ValidationError when email is invalid', () => {});
```

---

## Decision Log Integration

Read CODE phase decision logs at `docs/decision-logs/{feature}-{domain}.md` for:

- **Areas of uncertainty**: Where bugs often hide
- **Assumptions made**: Validate them with tests
- **Known limitations**: Test boundaries
- **Trade-offs**: Verify acceptable behavior

---

## Test Quality Checklist

Before completing TEST phase:

### Coverage
- [ ] Unit tests cover business logic (80%+ coverage)
- [ ] Integration tests verify API contracts
- [ ] E2E tests cover critical user journeys
- [ ] Edge cases and error scenarios tested

### Quality
- [ ] Tests are independent (no shared state)
- [ ] Tests have clear names describing behavior
- [ ] No flaky tests (all tests deterministic)
- [ ] Tests run quickly (unit < 1s, integration < 10s)

### Maintenance
- [ ] Tests use factories/fixtures (DRY)
- [ ] Mocks are minimal and focused
- [ ] Test data is realistic
- [ ] CI/CD pipeline runs all tests

### Security
- [ ] Authentication tests verify access control
- [ ] Input validation tests check edge cases
- [ ] Error messages don't leak sensitive info
- [ ] Rate limiting is tested

---

## Counter-test-by-revert methodology

A counter-test-by-revert pass falsifies a regression-coverage test by reverting the production fix and asserting that the targeted tests fail with the expected cardinality. Use it whenever you ship a regression test alongside a fix and need evidence that the test is actually coupled to the regression rather than an independent assertion that happens to pass.

Restore-mechanism rules (crash-atomic — survives an interrupted session without losing the original tree):

- **Prefer `git revert -n -- <paths>`** when the target commit can be cleanly reverted in isolation. `-n` (`--no-commit`) leaves the inverse change staged; `git restore --staged --worktree -- <paths>` drops it after measuring cardinality.
- **Use `git stash push -- <paths>`** for an in-place edit when the target commit bundles consequential test or fixture edits that revert can't isolate, or when you are reverting a hand-edit that was never committed. `git stash pop` re-applies atomically.
- **Never** in-place-edit a tracked file and rely on `cp` from a `/tmp` copy or hand-typed restoration. A crash mid-measurement leaves the working tree in a corrupt half-edited state with no atomic recovery primitive.

After restore, re-run the test scope and confirm the original tree is byte-identical: `git diff --quiet -- <paths>` exits 0, `git status --porcelain -- <paths>` prints nothing.

Document the expected cardinality (`{N fail, M pass}`) in the design or test docstring so a future verifier can check the assertion without re-deriving it.

### Bundled-commit cardinality: source-only revert vs whole-commit revert

When a commit bundles new source AND the new tests that exercise it (a common pattern for refactors that can't be split without a transient broken-import state), `git revert -n <sha>` reverts BOTH halves at once — the inverted source is reapplied without the test that detects the regression, so the failure cardinality collapses to the small number of pre-existing tests that happened to cover the surface. This **masks** the protection the new tests actually provide.

For bundled commits, measure cardinality via **source-only revert** instead:

```sh
# Restore source files to their pre-commit shape; leave the new tests in place.
git checkout <sha>^ -- <source-file-1> <source-file-2> ...

# Run the affected test scope and record cardinality.
pytest <scope> -x

# Restore atomically.
git checkout <sha> -- <source-file-1> <source-file-2> ...
git diff --quiet -- <source-file-1> <source-file-2>  # exits 0
```

The two cardinalities are not interchangeable. Empirical example from a bundled predicate refactor + retargeted tests: `git revert -n` produced `{1 fail}` (only the pre-existing categorical-invariant test broke); source-only revert produced `{33 fail + 1 collection error}` — the 33 retargeted tests are the protection, and only the source-only technique surfaces them.

**Rule for Verification Matrices**: when documenting the expected cardinality for a bundled commit, specify the technique and the expected count. Example row:

> `Counter-test (source-only revert of <sha>): pytest <scope> → {33 fail + 1 collection error}. Source-only because <sha> bundles new tests with the source they exercise; `git revert -n` would mask to {1 fail}.`

Whole-commit revert is still correct for commits that ship source-only (or tests-only); the bundled distinction only applies when both move together.

---

## Testing Craft Patterns

Testing-craft patterns that surface during PACT review cycles and reach the codification threshold (≥2 instances, multiple specialists, or explicit reviewer flag) live here. Each rule names a specific failure mode in how tests, fixtures, or HANDOFFs are constructed — and the canonical mitigation that closes the gap. The patterns compose: a single PACT cycle can hit all three independently, and each cites the sister patterns it interacts with through their pact-memory IDs.

### Author-blindness in HANDOFF arithmetic

When a HANDOFF author asserts a cardinality, set-membership, or fidelity claim about their own work — commit-test counts, suppression-cardinality matrices, SSOT-fidelity tallies — the claim often contains an arithmetic or set-shape error that the author does NOT catch in self-review. **Author-bias** on one's own work suppresses recounting: the asserting act feels equivalent to the verifying act, so the recount never happens. The **numeric pre-verification illusion** is stronger on arithmetic claims than on prose claims because numbers feel pre-verified at the moment they are written down.
<!-- planning-artifact-exempt: pact-memory ID, content-addressable via secretary / PACT:pact-memory skill, not a commit SHA -->
Canonical body: pact-memory `d319e8e1`.

#### Discriminator vs the ASPIRATIONAL-HANDOFF sister pattern

<!-- planning-artifact-exempt: pact-memory ID, content-addressable via secretary / PACT:pact-memory skill, not a commit SHA -->
AUTHOR-BLINDNESS and **ASPIRATIONAL-HANDOFF** (pact-memory `0bc2c78d`) are sister failure modes of HANDOFF-author bias but differ in the source-of-belief failure they expose:

- **ASPIRATIONAL-HANDOFF**: the author claims X-as-verified when X was actually believed-from-the-dispatch-prompt — a source-of-belief failure. The author never empirically checked X.
- **AUTHOR-BLINDNESS**: the author claims X-as-verified when the author DID empirically check X but mis-counted under author-bias — a counting-fidelity failure. The empirical work happened; the recount didn't.

Distinguishing the two matters because the mitigations differ: ASPIRATIONAL-HANDOFF wants a "is this from the prompt or from your verification?" challenge; AUTHOR-BLINDNESS wants a literal cross-stream recount of the cardinality claim.

#### Worked example — three instances

- **Tier-2 SSOT-fidelity miscount**: a Tier-2 SSOT-fidelity check miscounted prefix-tuple cardinality in the author's own HANDOFF — the empirical scan happened, but the recount step that would have caught the off-by-one was skipped because the asserting act felt sufficient.
- **Suppression-cardinality narrowing**: a fixture mis-characterization claimed double-suppression that empirical retest narrowed to single-suppression — the author had run the suppression check but the cardinality summary line in the HANDOFF was written from memory of the intended shape, not from the actual run.
- **Cross-pass cluster miscount**: a review-HANDOFF re-counted a test-emission tally and surfaced two author-blind miscounts in the SAME specialist's prior implementation HANDOFF — confirming the bias is stream-role-contextual (one specialist switching from implementation-role to review-role catches their own prior miscount only when the role-switch is explicit).

#### Canonical mitigation

<!-- planning-artifact-exempt: pact-memory ID, content-addressable via secretary / PACT:pact-memory skill, not a commit SHA -->
Cross-stream verification (pact-memory `f3f3d093`) MUST run on every cardinality, set-membership, fidelity, or structural-shape claim in a HANDOFF. The recount is performed by an agent operating in an explicitly different role-context from the HANDOFF author — one of:

- **Test-author** running review-role checks on their own implementation HANDOFF (same specialist OK if the role-switch is explicit).
- **Fix-builder** recounting the test-engineer's HANDOFF cardinality before integrating the fix.
- **Review-test-engineer-who-found-but-did-not-fix** recounting the fix-builder's claim before sign-off.
- **Fresh agent** with no prior context in the cycle, recounting from the dispatch artifact and the live tree.

The loop closes when the recount is done literally — re-run the count, re-list the set, re-derive the fidelity tally from the source — not by re-asserting confidence in the HANDOFF's number. AUTHOR-BLINDNESS is the problem; cross-stream verification is the treatment.

#### Detection signature

HANDOFFs that assert cardinality matrices, set-membership tallies, or fidelity counts about the author's own work are at elevated risk. The shape "I verified N of M cases" — especially when N and M are small integers — is the highest-yield surface for a cross-stream recount.

### count_active_tasks fixture-completeness audit

Any test asserting on `count_active_tasks` boundary conditions (`count == 0` vs `count == 1` vs `count == N`) MUST either register all task-owners in the fixture's `team_config.members[]` so the count reflects the intended suppression mechanism, or explicitly assert on the unknown-owner-exclusion path with a docstring comment naming the exclusion as the load-bearing mechanism being tested.

`count_active_tasks` filters by two conjoined conditions: `status == in_progress` AND `owner` is a registered teammate. Tasks with `owner=null` or `owner=<unregistered name>` are **silently excluded** from the count. An under-registered fixture passes a `count == 0` assertion for the wrong reason — the **wrong-reason green** is that the count reads 0 because no owners matched, not because the suppression-or-aggregation mechanism the test intended to exercise actually fired.

#### Worked example — fixture mis-registration

An SSOT-fidelity test passed because of unknown-owner exclusion rather than umbrella-suppression: the fixture's `team_config.members[]` under-registered the task-owners the test exercised, so the `count == 0` assertion held on the exclusion path while the umbrella-suppression mechanism the test was supposed to falsify remained un-exercised. A separate fixture under-registered task-owners in a way that masked which suppression mechanism was load-bearing in the assertion — the test stayed green through a change that should have produced a cardinality shift.

#### Canonical mitigation

**Register all task-owners** — default discipline: list the task-owners the test exercises in `team_config.members[]` so the count reflects the intended suppression mechanism. If the test is intentionally exercising the exclusion path, name it in the test docstring AND verify the assertion would hold without the exclusion (so the suppression mechanism stands on its own). The "would hold without the exclusion" check is the load-bearing verification — without it, the test is indistinguishable from a fixture-completeness defect.

#### Detection signature

Tests asserting on `count_active_tasks` boundary conditions (`count == 0`, `count == 1`, `count == N`) where the fixture's `team_config.members[]` lists only a subset of the task-owners the test exercises are at elevated risk. The shape "count assertion + sparsely-constructed `members[]`" is the highest-yield surface for a fixture-completeness audit.

### Sibling-file convention for parametrized noise-budget regression

Parametrized noise-budget regression tests — N×M matrices counting events across simulated scenarios — MUST live in a sibling test file named `test_{phase-domain}_noise_budget.py`, never packed into the primary phase-specific test files.

Primary phase-specific test files count specific event types and require clean fire-counts to assert on Tier-N cardinality. Mixing them with parametrized N×M matrix tests reduces **signal-to-noise** on the cardinality assertions because the parametrized matrix's setup/teardown noise drowns out the primary file's tight fire-count assertions. **Fire-count cleanliness** is the property that lets a primary test file's `assert events.count == N` reliably localize a regression — once a parametrized matrix sits alongside, the same assertion has to defend against matrix-induced cross-contamination.

#### Worked example — sibling-file split

A PACT-internal regression added the sibling test file `pact-plugin/tests/test_phase_lull_noise_budget.py` alongside a phase-specific test family, splitting the N-cell parametrized cardinality matrix out of the primary file. The split preserved the primary file's fire-count assertions at their original tightness while letting the matrix expand independently for new scenarios.

#### Canonical mitigation

**Sibling-file split** — default discipline: when adding a parametrized noise-budget regression test for a phase-specific test family, create a sibling file named `test_{phase-domain}_noise_budget.py` rather than appending to the primary file. Cross-reference the primary phase-specific file in the sibling's docstring so a future reader can find the family. When 3+ instances of HANDOFF-cardinality-matrix patterns cluster in a single review cycle, the cluster suggests a HANDOFF-shape risk-factor specific to phase-lull tests with N-cell parametrized cardinality matrices — pair these tests with cross-stream-verifier review (see Author-blindness above) at elevated priority.

#### Detection signature

Phase-specific test files that already count specific event types AND gain a parametrized N×M matrix test for the same event-type are at elevated risk. The shape "tight fire-count assertion in the same file as a parametrized matrix" is the highest-yield surface for the sibling-file split.

---

## Detailed References

For comprehensive testing guidance:

- **Test Pyramid**: [references/test-pyramid.md](references/test-pyramid.md)
  - Detailed guidance per test layer
  - When to use each layer
  - Anti-patterns to avoid

- **Integration Patterns**: [references/integration-patterns.md](references/integration-patterns.md)
  - Database testing strategies
  - API testing patterns
  - External service testing

- **Performance Testing**: [references/performance-testing.md](references/performance-testing.md)
  - Load testing approaches
  - Benchmark patterns
  - Performance metrics
