# Add retries, backoff, and idempotency to the job runner

You are working in a small Python 3.12 project, `job-queue`, that implements an
in-memory job runner with an optional JSON snapshot store. Everything is
standard library only: there is no network access and no third-party runtime
dependency. pytest is available to run the tests.

The runner currently executes each job once: if a handler raises, the job is
marked `failed` immediately, and every `enqueue` call creates a brand new job.
Your job is to add a retry policy with exponential backoff, idempotent
enqueueing, and crash-safe persistence, without changing the parts of the
public API that already work.

## Requirements

### Enqueueing

`JobQueue.enqueue(kind, payload=None, *, idempotency_key=None, max_retries=3,
base_delay=1.0)` returns the job id and accepts these new keyword arguments:

- `max_retries` is the number of retries after the initial attempt, so a job
  is attempted at most `max_retries + 1` times. It must be a non-negative
  integer; raise `ValueError` for anything else.
- `base_delay` is the base backoff delay in seconds. It must be non-negative;
  raise `ValueError` for a negative value.
- `idempotency_key`, when not `None`, makes retries of the same logical
  action safe: the first enqueue with a given key creates the job and binds
  the key to it; any later enqueue with the same key returns the existing job
  id, creates nothing, resets nothing, and never re-runs the job. This holds
  whether the existing job is pending, completed, or failed, and it must keep
  holding after the queue is reloaded from its snapshot. Jobs enqueued with
  `idempotency_key=None` are never deduplicated.

`payload` remains a dict; unknown job kinds still raise `KeyError`.

### Retry policy and backoff

- When the handler raises an exception deriving from `Exception`, the
  attempt is counted and the error message is stored in `job.last_error`.
  If the job has retries left (`job.attempts <= max_retries`), it goes back
  to state `"pending"` with `next_run_at = clock() + base_delay *
  2 ** (job.attempts - 1)` (so the delay before retry N is
  `base_delay * 2 ** (N - 1)`, without jitter). Once the retries are
  exhausted the job becomes `"failed"` and stays there.
- When the handler returns, the job becomes `"completed"`, the return value
  is stored in `job.result`, and `job.last_error` is cleared to `None`.
- Exceptions deriving from `BaseException` (for example `KeyboardInterrupt`
  or `SystemExit`) are not retryable: they must propagate out of
  `run_next()`, leaving the job recorded as `"running"` in the snapshot.
- `run_next()` runs the first pending job in enqueue order whose
  `next_run_at <= clock()` and returns it, or returns `None` when no job is
  due. `drain()` keeps running due jobs until none remain and returns the
  number of attempts it executed.

### Persistence and crash recovery

- The existing snapshot behaviour stays: a queue constructed with
  `store_path` loads the JSON snapshot when the file exists and saves the
  snapshot after every state change. `save()` and `load()` remain available.
- The snapshot must round-trip the retry bookkeeping: `attempts`,
  `max_retries`, `base_delay`, `next_run_at`, `idempotency_key`,
  `last_error`, and `result`. A job that is waiting for its backoff delay
  must still be waiting (with the same `next_run_at` and `attempts`) after
  the queue is recreated from the snapshot, and the idempotency keys must be
  rebuilt on load.
- Crash recovery: when a queue loads a snapshot, any job recorded as
  `"running"` represents an attempt that was interrupted by a process stop.
  It becomes `"pending"` again, keeps its `attempts` count, and is
  immediately runnable (`next_run_at = clock()`), so the next `run_next()`
  continues from the following attempt.
- Handlers are not persisted; they are re-registered with `register()` on the
  new queue before jobs are run.

### Compatibility

Keep the existing public API and behaviour: `JobQueue(store_path=None,
clock=None)`, `register(kind, handler)`, `enqueue`, `run_next`, `drain`,
`get`, `jobs`, `save`, `load`, the state names `pending` / `running` /
`completed` / `failed`, and the `Job` fields that already exist.
`clock` stays an injectable callable returning the time in seconds.

## Constraints

- Python 3.12 standard library only. No network calls and no new runtime
  dependencies.
- Existing tests under `tests/` are the regression suite: do not change them
  and keep them passing. Adding new tests is welcome.
- Run the suite with `python -m pytest -q`.

## Working agreement

- Work autonomously; do not ask questions and do not wait for direction.
- Commit your work on a feature branch (for example `feature/job-retries`).
- Do not push, open a pull request, or merge.
- Leave the repository in a clean, working state when you are done.
