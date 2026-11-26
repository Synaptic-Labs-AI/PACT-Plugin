# Blind quality rubric: job-queue-retries

Review only the final repository state; hidden tests are not part of the
reviewer's evidence. Score each dimension 0-3 (0 missing/broken, 1 partial,
2 solid, 3 excellent) and record concrete file/line evidence.

## Dimensions

1. **Retry accounting** - a job is attempted at most `max_retries + 1` times;
   `attempts` counts handler invocations; exhaustion lands in `failed`;
   success clears the error and stores the result; `max_retries=0` means a
   single attempt.
2. **Backoff schedule** - the delay before retry N is
   `base_delay * 2 ** (N - 1)` with no jitter; `next_run_at` is computed
   from the injected clock at the time of failure; jobs are not run before
   they are due.
3. **Idempotency** - a non-`None` idempotency key binds to exactly one job;
   duplicate enqueues return the same id without creating or resetting or
   re-running anything, for pending, completed, and failed jobs, and across
   snapshot reloads; `None` keys do not deduplicate.
4. **Persistence of retry state** - the snapshot round-trips attempts,
   retry limits, base delay, next run time, idempotency keys, error and result,
   atomically enough that a fresh queue resumes the same schedule.
5. **Crash recovery** - a job left `running` in a loaded snapshot is treated
   as an interrupted attempt and becomes immediately runnable again with its
   attempt count preserved; non-`Exception` failures propagate.
6. **Execution semantics** - `run_next()` picks the first due job in enqueue
   order and returns `None` when idle; `drain()` runs all due jobs and
   returns the attempt count; scheduling a retry in the future stops
   `drain()` instead of busy-looping.
7. **Regression discipline** - the existing tests under `tests/` are
   unchanged and still pass; added tests are meaningful rather than vacuous.
8. **Scope and dependency discipline** - standard library only, no network,
   no unrelated API rewrites, no dead code or leftover debug output.

## Scoring guide

- 20-24: excellent, production-quality completion.
- 16-19: solid; minor gaps in scheduling edge cases or test coverage.
- 12-15: partial; retries or idempotency work but persistence/crash recovery
  or backoff timing is incomplete or unproven.
- 0-11: incomplete or unsafe; the runner does not meet the stated contract.

Report any dimension scored 0 or 1 with a concrete suggested fix. Do not
award credit for changes that merely satisfy a test without implementing the
described behaviour.
