"""Job model for the in-memory job runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PENDING = "pending"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"

JOB_STATES = (PENDING, RUNNING, COMPLETED, FAILED)


@dataclass
class Job:
    """A unit of work stored by :class:`jobqueue.JobQueue`.

    ``attempts`` counts how many times the handler has been invoked,
    ``last_error`` keeps the message of the most recent failure, and
    ``result`` keeps the handler's return value after a success.
    """

    id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    state: str = PENDING
    attempts: int = 0
    last_error: str | None = None
    result: Any = None
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly representation of the job."""
        return {
            "id": self.id,
            "kind": self.kind,
            "payload": self.payload,
            "state": self.state,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "result": self.result,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Job":
        """Build a job from a snapshot dictionary."""
        return cls(
            id=data["id"],
            kind=data["kind"],
            payload=dict(data.get("payload") or {}),
            state=data.get("state", PENDING),
            attempts=int(data.get("attempts", 0)),
            last_error=data.get("last_error"),
            result=data.get("result"),
            created_at=float(data.get("created_at", 0.0)),
        )
