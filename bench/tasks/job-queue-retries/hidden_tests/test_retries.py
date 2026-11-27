"""Retry, backoff and persistence requirements for the job runner."""

from __future__ import annotations

import pytest

from jobqueue import JobQueue


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Crash(BaseException):
    """Simulates the process disappearing in the middle of a job."""


def _failing_then_succeeding(failures: int):
    calls = {"n": 0}

    def handler(payload):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise RuntimeError(f"transient {calls['n']}")
        return f"ok after {calls['n']}"

    return handler, calls


def test_retries_until_success():
    queue = JobQueue()
    handler, calls = _failing_then_succeeding(2)
    queue.register("flaky", handler)
    job_id = queue.enqueue("flaky", {}, max_retries=3, base_delay=0.0)
    assert queue.drain() == 3
    job = queue.get(job_id)
    assert job.state == "completed"
    assert job.attempts == 3
    assert job.result == "ok after 3"
    assert job.last_error is None
    assert calls["n"] == 3


def test_retries_exhausted_marks_job_failed():
    queue = JobQueue()

    def always_fails(payload):
        raise RuntimeError("always broken")

    queue.register("broken", always_fails)
    job_id = queue.enqueue("broken", {}, max_retries=2, base_delay=0.0)
    assert queue.drain() == 3
    job = queue.get(job_id)
    assert job.state == "failed"
    assert job.attempts == 3
    assert "always broken" in job.last_error


def test_zero_retries_fails_on_the_first_attempt():
    queue = JobQueue()

    def always_fails(payload):
        raise RuntimeError("nope")

    queue.register("broken", always_fails)
    job_id = queue.enqueue("broken", {}, max_retries=0, base_delay=0.0)
    assert queue.drain() == 1
    job = queue.get(job_id)
    assert job.state == "failed"
    assert job.attempts == 1


def test_backoff_doubles_between_attempts():
    clock = FakeClock(0.0)
    queue = JobQueue(clock=clock)

    def always_fails(payload):
        raise RuntimeError("boom")

    queue.register("broken", always_fails)
    job_id = queue.enqueue("broken", {}, max_retries=3, base_delay=10.0)

    first_attempt = queue.run_next()
    assert first_attempt.attempts == 1
    assert first_attempt.state == "pending"
    assert first_attempt.next_run_at == pytest.approx(10.0)

    clock.now = 9.999
    assert queue.run_next() is None

    clock.now = 10.0
    second_attempt = queue.run_next()
    assert second_attempt.attempts == 2
    assert second_attempt.state == "pending"
    assert second_attempt.next_run_at == pytest.approx(30.0)

    clock.now = 29.999
    assert queue.run_next() is None

    clock.now = 30.0
    third_attempt = queue.run_next()
    assert third_attempt.attempts == 3
    assert third_attempt.state == "pending"
    assert third_attempt.next_run_at == pytest.approx(70.0)

    clock.now = 69.999
    assert queue.run_next() is None

    clock.now = 70.0
    final_attempt = queue.run_next()
    assert final_attempt.attempts == 4
    assert final_attempt.state == "failed"


def test_retry_state_survives_reload(tmp_path):
    clock = FakeClock(0.0)
    store = tmp_path / "queue.json"
    queue = JobQueue(store_path=store, clock=clock)

    def fails(payload):
        raise RuntimeError("transient")

    queue.register("task", fails)
    job_id = queue.enqueue("task", {"n": 1}, max_retries=2, base_delay=5.0)
    queue.run_next()
    assert store.exists()

    reloaded = JobQueue(store_path=store, clock=clock)
    job = reloaded.get(job_id)
    assert job is not None
    assert job.state == "pending"
    assert job.attempts == 1
    assert reloaded.run_next() is None

    clock.now = 5.0
    reloaded.register("task", lambda payload: "recovered")
    finished = reloaded.run_next()
    assert finished.state == "completed"
    assert finished.attempts == 2
    assert finished.result == "recovered"


def test_interrupted_running_job_is_recovered(tmp_path):
    clock = FakeClock(0.0)
    store = tmp_path / "queue.json"
    queue = JobQueue(store_path=store, clock=clock)

    def crash(payload):
        raise Crash()

    queue.register("fragile", crash)
    job_id = queue.enqueue("fragile", {}, max_retries=2, base_delay=5.0)
    with pytest.raises(Crash):
        queue.run_next()

    reloaded = JobQueue(store_path=store, clock=clock)
    interrupted = reloaded.get(job_id)
    assert interrupted.state == "pending"
    assert interrupted.attempts == 1

    reloaded.register("fragile", lambda payload: "done")
    recovered = reloaded.run_next()
    assert recovered is not None
    assert recovered.state == "completed"
    assert recovered.attempts == 2
    assert recovered.result == "done"
