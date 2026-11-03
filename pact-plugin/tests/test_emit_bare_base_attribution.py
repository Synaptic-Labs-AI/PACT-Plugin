"""
Location: pact-plugin/tests/test_emit_bare_base_attribution.py
Summary: A task file found only at the BARE tasks base is never journaled
         under a named store's `team` by the lead-side emit routes in
         task_lifecycle_gate.py: the lead-completion disk fallback
         (agent_handoff + snapshot), the post-completion backstop
         (agent_handoff + snapshot), and the per-write snapshot helper's own
         read. Each route has a bare-base arm and a named-store control; the
         bare-base arm reddens when that route's `base_fallback=False` is
         deleted. The teammate-frame emitter (b1) is covered through a real
         process in test_emitter_team_subprocess.py.
Used by: pytest.
"""

import json
from pathlib import Path

import pytest

import shared.task_metadata_snapshot as tms  # noqa: E402
import task_lifecycle_gate as tlg  # noqa: E402
from fixtures.emitter import VALID_HANDOFF  # noqa: E402

TEAM = "pact-test"
LEAD = "PACT:pact-orchestrator"
SIBLINGS = {"variety": {"total": 7}}


@pytest.fixture
def journal(monkeypatch):
    """Every event either append_event binding writes, in order."""
    events: list[dict] = []

    def _spy(event):
        events.append(event)
        return True

    monkeypatch.setattr(tms, "append_event", _spy)
    monkeypatch.setattr(tlg, "append_event", _spy)
    return events


@pytest.fixture
def home(tmp_path, monkeypatch, pact_context):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name=TEAM, session_id="s1")
    (tmp_path / ".claude" / "tasks" / TEAM).mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write(home: Path, where: str, task_id: str, **fields) -> None:
    """where='bare' -> tasks/{id}.json; where='named' -> tasks/TEAM/{id}.json."""
    base = home / ".claude" / "tasks"
    target = base if where == "bare" else base / TEAM
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{task_id}.json").write_text(
        json.dumps({"id": task_id, **fields}), encoding="utf-8"
    )


def _types(events):
    return [e.get("type") for e in events]


def _completed_task(**extra):
    return dict(
        subject="devops: implement the thing",
        owner="devops",
        status="completed",
        metadata={**SIBLINGS, "handoff": VALID_HANDOFF},
        **extra,
    )


class TestLeadCompletionDiskFallback:
    """Completion with no task in tool_response: the gate reads the disk."""

    def _payload(self):
        return {
            "tool_name": "TaskUpdate",
            "agent_type": LEAD,
            "tool_input": {"taskId": "42", "status": "completed"},
            "tool_response": {},
        }

    def test_bare_base_file_emits_nothing_for_a_named_store(self, home, journal):
        _write(home, "bare", "42", **_completed_task())
        tlg.evaluate_lifecycle(self._payload())
        assert "agent_handoff" not in _types(journal)
        assert "task_metadata_snapshot" not in _types(journal)

    def test_control_named_store_file_emits_with_team(self, home, journal):
        _write(home, "named", "42", **_completed_task())
        tlg.evaluate_lifecycle(self._payload())
        handoffs = [e for e in journal if e["type"] == "agent_handoff"]
        snaps = [e for e in journal if e["type"] == "task_metadata_snapshot"]
        assert handoffs and all(e.get("team") == TEAM for e in handoffs)
        assert snaps and all(e.get("team") == TEAM for e in snaps)


class TestPostCompletionBackstop:
    """A metadata-only write on an already-completed task (task_a read)."""

    def _payload(self, metadata):
        return {
            "tool_name": "TaskUpdate",
            "agent_type": LEAD,
            "tool_input": {"taskId": "55", "metadata": metadata},
            "tool_response": {},
        }

    def test_bare_base_file_emits_nothing_for_a_named_store(self, home, journal):
        _write(home, "bare", "55", **_completed_task())
        tlg.evaluate_lifecycle(
            self._payload({"handoff": VALID_HANDOFF, "late_note": "x"})
        )
        assert "agent_handoff" not in _types(journal)
        assert "task_metadata_snapshot" not in _types(journal)

    def test_control_named_store_file_emits_with_team(self, home, journal):
        _write(home, "named", "55", **_completed_task())
        tlg.evaluate_lifecycle(
            self._payload({"handoff": VALID_HANDOFF, "late_note": "x"})
        )
        handoffs = [e for e in journal if e["type"] == "agent_handoff"]
        snaps = [e for e in journal if e["type"] == "task_metadata_snapshot"]
        assert handoffs and all(e.get("team") == TEAM for e in handoffs)
        assert snaps and all(e.get("team") == TEAM for e in snaps)


class TestPerWriteSnapshotRead:
    """The helper's own read (task=None): a bare-base file's content never
    enters a snapshot stamped with the named store."""

    def test_bare_base_content_is_not_read_for_a_named_store(self, home, journal):
        _write(
            home, "bare", "61", subject="foreign subject", owner="stranger",
            metadata={"audit_summary": "FOREIGN"},
        )
        tlg._emit_per_write_snapshot(TEAM, "61", {"variety": {"total": 3}})
        snaps = [e for e in journal if e["type"] == "task_metadata_snapshot"]
        for snap in snaps:
            assert "audit_summary" not in snap.get("metadata", {})
            assert snap.get("subject") != "foreign subject"

    def test_control_named_store_content_is_read(self, home, journal):
        _write(
            home, "named", "61", subject="own subject", owner="devops",
            metadata={"audit_summary": "OWN"},
        )
        tlg._emit_per_write_snapshot(TEAM, "61", {"variety": {"total": 3}})
        snaps = [e for e in journal if e["type"] == "task_metadata_snapshot"]
        assert snaps and snaps[0]["metadata"].get("audit_summary") == "OWN"
        assert snaps[0].get("team") == TEAM
