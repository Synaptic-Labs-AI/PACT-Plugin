"""Tests for the job runner behaviour that already works."""

from __future__ import annotations

import pytest

from jobqueue import JobQueue


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_enqueue_requires_registered_kind():
    queue = JobQueue()
    with pytest.raises(KeyError):
        queue.enqueue("missing", {})


def test_run_next_executes_handler_and_completes():
    queue = JobQueue()
    queue.register("add", lambda payload: payload["a"] + payload["b"])
    job_id = queue.enqueue("add", {"a": 2, "b": 3})
    job = queue.run_next()
    assert job is not None
    assert job.id == job_id
    assert job.state == "completed"
    assert job.result == 5
    assert job.attempts == 1
    assert queue.get(job_id).state == "completed"


def test_run_next_returns_none_when_idle():
    queue = JobQueue()
    assert queue.run_next() is None


def test_handler_error_is_recorded_on_the_job():
    queue = JobQueue()

    def boom(payload):
        raise RuntimeError("boom")

    queue.register("boom", boom)
    job_id = queue.enqueue("boom", {})
    job = queue.run_next()
    assert job.last_error == "boom"
    assert job.attempts == 1
    assert job.state != "running"


def test_drain_runs_all_pending_jobs_in_enqueue_order():
    queue = JobQueue()
    seen: list[int] = []
    queue.register("record", lambda payload: seen.append(payload["n"]))
    first = queue.enqueue("record", {"n": 1})
    second = queue.enqueue("record", {"n": 2})
    third = queue.enqueue("record", {"n": 3})
    assert queue.drain() == 3
    assert seen == [1, 2, 3]
    for job_id in (first, second, third):
        assert queue.get(job_id).state == "completed"


def test_clock_is_used_for_created_at():
    clock = FakeClock(1234.5)
    queue = JobQueue(clock=clock)
    queue.register("noop", lambda payload: None)
    job_id = queue.enqueue("noop", {})
    assert queue.get(job_id).created_at == 1234.5


def test_snapshot_is_loaded_into_a_fresh_queue(tmp_path):
    clock = FakeClock(10.0)
    store = tmp_path / "queue.json"
    queue = JobQueue(store_path=store, clock=clock)
    queue.register("record", lambda payload: payload["n"])
    done = queue.enqueue("record", {"n": 1})
    pending = queue.enqueue("record", {"n": 2})
    assert queue.run_next().id == done
    assert store.exists()

    reloaded = JobQueue(store_path=store, clock=clock)
    assert reloaded.get(done).state == "completed"
    assert reloaded.get(done).result == 1
    assert reloaded.get(pending).state == "pending"
    assert len(reloaded.jobs()) == 2
    reloaded.register("record", lambda payload: payload["n"])
    assert reloaded.drain() == 1
    assert reloaded.get(pending).state == "completed"
