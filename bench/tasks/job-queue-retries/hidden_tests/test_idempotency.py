"""Idempotent enqueueing requirements for the job runner."""

from __future__ import annotations

from jobqueue import JobQueue


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_duplicate_idempotency_key_returns_the_existing_job():
    queue = JobQueue()
    executed: list[int] = []

    def handler(payload):
        executed.append(payload["value"])
        return payload["value"] * 2

    queue.register("charge", handler)
    first = queue.enqueue("charge", {"value": 7}, idempotency_key="order-42")
    second = queue.enqueue("charge", {"value": 7}, idempotency_key="order-42")
    assert first == second
    assert len(queue.jobs()) == 1
    assert queue.drain() == 1
    assert executed == [7]
    assert queue.get(first).result == 14


def test_idempotency_holds_after_completion():
    queue = JobQueue()
    sent: list[str] = []

    def send(payload):
        sent.append(payload["to"])
        return "sent"

    queue.register("email", send)
    job_id = queue.enqueue("email", {"to": "a@example.com"}, idempotency_key="welcome-1")
    queue.drain()
    again = queue.enqueue("email", {"to": "a@example.com"}, idempotency_key="welcome-1")
    assert again == job_id
    assert queue.drain() == 0
    assert sent == ["a@example.com"]
    assert queue.get(job_id).attempts == 1


def test_distinct_idempotency_keys_create_distinct_jobs():
    queue = JobQueue()
    queue.register("noop", lambda payload: None)
    first = queue.enqueue("noop", {}, idempotency_key="a")
    second = queue.enqueue("noop", {}, idempotency_key="b")
    assert first != second
    assert len(queue.jobs()) == 2
    assert queue.drain() == 2


def test_jobs_without_idempotency_keys_are_never_deduplicated():
    queue = JobQueue()
    queue.register("noop", lambda payload: None)
    first = queue.enqueue("noop", {"same": "payload"})
    second = queue.enqueue("noop", {"same": "payload"})
    assert first != second
    assert len(queue.jobs()) == 2


def test_idempotency_survives_reload(tmp_path):
    clock = FakeClock(0.0)
    store = tmp_path / "queue.json"
    queue = JobQueue(store_path=store, clock=clock)
    queue.register("noop", lambda payload: "ok")
    job_id = queue.enqueue("noop", {}, idempotency_key="persisted")
    queue.run_next()

    reloaded = JobQueue(store_path=store, clock=clock)
    reloaded.register("noop", lambda payload: "ok")
    assert reloaded.enqueue("noop", {}, idempotency_key="persisted") == job_id
    assert len(reloaded.jobs()) == 1
    assert reloaded.drain() == 0


def test_failed_job_is_not_reenqueued_by_its_idempotency_key():
    queue = JobQueue()

    def boom(payload):
        raise RuntimeError("permanent")

    queue.register("bad", boom)
    job_id = queue.enqueue(
        "bad",
        {},
        idempotency_key="only-once",
        max_retries=0,
        base_delay=0.0,
    )
    queue.drain()
    assert queue.get(job_id).state == "failed"
    assert queue.enqueue("bad", {}, idempotency_key="only-once") == job_id
    assert queue.drain() == 0
