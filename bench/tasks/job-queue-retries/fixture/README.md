# job-queue

A tiny, dependency-free in-memory job runner. Jobs are registered by kind,
enqueued with a JSON-serialisable payload, and executed one at a time by a
handler. The runner can persist its state to a JSON snapshot so a fresh
process can pick up where the previous one stopped.

Everything is Python 3.12 standard library only. There is no network access
and no third-party runtime dependency.

## Layout

- `jobqueue/jobs.py` - `Job` dataclass and the job state names.
- `jobqueue/runner.py` - `JobQueue`: registration, enqueueing, execution and
  snapshot persistence.
- `tests/` - pytest suite covering the behaviour that already works.

## Public API

    queue = JobQueue(store_path=None, clock=None)
    queue.register(kind, handler)          # handler(payload: dict) -> result
    job_id = queue.enqueue(kind, payload)
    job = queue.run_next()                 # one job, or None when idle
    count = queue.drain()                  # run everything that is runnable
    queue.get(job_id) / queue.jobs()       # inspection
    queue.save() / queue.load()            # explicit snapshot control

`clock` is a callable returning the current time in seconds; it defaults to
`time.time`. When `store_path` is given, the queue loads an existing JSON
snapshot at construction time and writes the snapshot after every state
change.

## Running the tests

    python -m pytest -q

## Current status

A raised handler exception is recorded on the job and the job fails
immediately: there is no retry policy, no backoff schedule, no idempotency key
support, and an interrupted job is simply restored in whatever state the
snapshot held. Those are the gaps this project needs filled.
