"""A tiny in-memory job runner with an optional JSON snapshot store."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .jobs import COMPLETED, FAILED, PENDING, RUNNING, Job

Handler = Callable[[dict[str, Any]], Any]


class JobQueue:
    """Run jobs registered by kind, one at a time, in enqueue order.

    ``clock`` is a callable returning the current time in seconds and is used
    for job timestamps. When ``store_path`` is given the queue loads an
    existing JSON snapshot at construction time and writes the snapshot after
    every state change.
    """

    def __init__(
        self,
        store_path: str | Path | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock if clock is not None else time.time
        self._store_path = Path(store_path) if store_path is not None else None
        self._handlers: dict[str, Handler] = {}
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        if self._store_path is not None and self._store_path.exists():
            self.load()

    # -- registration and enqueueing ---------------------------------
    def register(self, kind: str, handler: Handler) -> None:
        """Register the callable that executes jobs of ``kind``."""
        self._handlers[kind] = handler

    def enqueue(self, kind: str, payload: dict[str, Any] | None = None) -> str:
        """Add a job of a registered kind and return its id.

        Raises ``KeyError`` when no handler has been registered for ``kind``.
        """
        if kind not in self._handlers:
            raise KeyError(f"no handler registered for job kind {kind!r}")
        job = Job(
            id=uuid.uuid4().hex,
            kind=kind,
            payload=dict(payload or {}),
            created_at=self._clock(),
        )
        self._jobs[job.id] = job
        self._order.append(job.id)
        self.save()
        return job.id

    # -- inspection ---------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        """Return the job with ``job_id``, or ``None`` when unknown."""
        return self._jobs.get(job_id)

    def jobs(self) -> list[Job]:
        """Return the known jobs in enqueue order."""
        return [self._jobs[job_id] for job_id in self._order if job_id in self._jobs]

    # -- execution ----------------------------------------------------
    def run_next(self) -> Job | None:
        """Run the next runnable job and return it, or ``None`` when idle."""
        job = self._next_runnable()
        if job is None:
            return None
        job.state = RUNNING
        job.attempts += 1
        self.save()
        try:
            job.result = self._handlers[job.kind](dict(job.payload))
        except Exception as exc:  # noqa: BLE001 - the failure is recorded on the job
            job.state = FAILED
            job.last_error = str(exc) or exc.__class__.__name__
        else:
            job.state = COMPLETED
        self.save()
        return job

    def drain(self) -> int:
        """Run runnable jobs until none are left; return the execution count."""
        executed = 0
        while True:
            job = self.run_next()
            if job is None:
                return executed
            executed += 1

    def _next_runnable(self) -> Job | None:
        for job_id in self._order:
            job = self._jobs[job_id]
            if job.state == PENDING:
                return job
        return None

    # -- persistence --------------------------------------------------
    def load(self) -> None:
        """Load the snapshot from ``store_path`` when one is configured."""
        if self._store_path is None:
            return
        if not self._store_path.exists():
            self._jobs = {}
            self._order = []
            return
        data = json.loads(self._store_path.read_text(encoding="utf-8"))
        items = data.get("jobs", [])
        self._jobs = {item["id"]: Job.from_dict(item) for item in items}
        self._order = [item["id"] for item in items if item["id"] in self._jobs]

    def save(self) -> None:
        """Write the current state to ``store_path`` when one is configured."""
        if self._store_path is None:
            return
        data = {
            "schema": "jobqueue/1",
            "jobs": [
                self._jobs[job_id].to_dict()
                for job_id in self._order
                if job_id in self._jobs
            ],
        }
        temporary = self._store_path.with_name(self._store_path.name + ".tmp")
        temporary.write_text(json.dumps(data, indent=2, default=repr), encoding="utf-8")
        temporary.replace(self._store_path)
