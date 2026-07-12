"""
Location: pact-plugin/tests/test_snapshot_roundtrip.py
Summary: Acceptance round-trip for the journal-mirror durability contract —
         write load-bearing metadata keys on a task, emit the snapshot at
         a (real) lead completion, DRAIN the task file, and recover the
         keys from the journal alone; plus the journal-also-removed
         collapse leg (graceful degrade to an explicit gap, never invented
         content). Uses a REAL on-disk journal (no append spies) so the
         write→read cycle exercises the production serialization,
         validation, and read paths end to end.
Used by: pytest (the acceptance-criteria trace: load-bearing keys survive
         task GC via journal events).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import shared.session_journal as session_journal  # noqa: E402
import task_lifecycle_gate as tlg  # noqa: E402

TEAM = "pact-test"
LEAD = "PACT:pact-orchestrator"

LOAD_BEARING = {
    "teachback_submit": {
        "understanding": "implement the mirror",
        "first_action": "read the contract",
    },
    "variety": {"novelty": 2, "scope": 3, "uncertainty": 2, "risk": 3,
                "total": 10},
    "consultation_analysis": {"axes": ["scope", "risks"], "verdict": "go"},
    "r2_verification": {"verified": True, "anchors": ["gate.py:1306"]},
}


def _seed_task(tmp_path, team, task_id, **fields):
    tasks_dir = tmp_path / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    task_file = tasks_dir / f"{task_id}.json"
    task_file.write_text(
        json.dumps({"id": task_id, **fields}), encoding="utf-8"
    )
    return task_file


def _completion_frame(task_id, subject, owner, metadata):
    return {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": task_id, "status": "completed"},
        "tool_response": {
            "task": {
                "id": task_id,
                "subject": subject,
                "owner": owner,
                "metadata": metadata,
            }
        },
        "agent_type": LEAD,
    }


class TestAcceptanceRoundTrip:
    def test_keys_survive_task_drain_via_journal(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Write keys → complete (real journal write) → delete the task
        file → recover every load-bearing key from the journal alone."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        session_dir = tmp_path / "session"
        monkeypatch.setattr(
            session_journal, "_get_session_dir", lambda: str(session_dir)
        )
        pact_context(team_name=TEAM, session_id="s1")

        task_file = _seed_task(
            tmp_path,
            TEAM,
            "9",
            subject="backend-coder: implement the mirror",
            owner="backend-coder",
            status="completed",
            metadata=dict(LOAD_BEARING),
        )
        tlg.evaluate_lifecycle(
            _completion_frame(
                "9",
                "backend-coder: implement the mirror",
                "backend-coder",
                dict(LOAD_BEARING),
            )
        )

        # The drain: whole task-store destruction at a workflow boundary.
        task_file.unlink()
        assert not task_file.exists()

        events = session_journal.read_events_from(
            str(session_dir), "task_metadata_snapshot"
        )
        assert len(events) == 1
        event = events[0]
        assert event["task_id"] == "9"
        assert event["subject"] == "backend-coder: implement the mirror"
        assert event["owner"] == "backend-coder"
        # Every load-bearing key recovered byte-equal from the journal.
        assert event["metadata"] == LOAD_BEARING
        assert "handoff" not in event["metadata"]

    def test_journal_also_removed_collapses_to_explicit_gap(
        self, tmp_path, monkeypatch, pact_context
    ):
        """The collapse leg: task file AND journal gone → the read returns
        an empty list (an explicit, recordable gap) — no crash, no invented
        content."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        session_dir = tmp_path / "session"
        monkeypatch.setattr(
            session_journal, "_get_session_dir", lambda: str(session_dir)
        )
        pact_context(team_name=TEAM, session_id="s1")

        task_file = _seed_task(
            tmp_path,
            TEAM,
            "10",
            subject="backend-coder: collapse leg",
            owner="backend-coder",
            status="completed",
            metadata=dict(LOAD_BEARING),
        )
        tlg.evaluate_lifecycle(
            _completion_frame(
                "10",
                "backend-coder: collapse leg",
                "backend-coder",
                dict(LOAD_BEARING),
            )
        )
        task_file.unlink()
        journal = session_dir / "session-journal.jsonl"
        assert journal.exists()
        journal.unlink()

        events = session_journal.read_events_from(
            str(session_dir), "task_metadata_snapshot"
        )
        assert events == []

    def test_supersession_latest_ts_is_end_state(
        self, tmp_path, monkeypatch, pact_context
    ):
        """A post-completion metadata change re-emits; after the drain the
        LATEST snapshot for the (task_id, occupant) group is the
        authoritative end-state readers must take."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        session_dir = tmp_path / "session"
        monkeypatch.setattr(
            session_journal, "_get_session_dir", lambda: str(session_dir)
        )
        pact_context(team_name=TEAM, session_id="s1")

        subject = "backend-coder: supersession probe"
        _seed_task(
            tmp_path,
            TEAM,
            "11",
            subject=subject,
            owner="backend-coder",
            status="completed",
            metadata=dict(LOAD_BEARING),
        )
        tlg.evaluate_lifecycle(
            _completion_frame(
                "11", subject, "backend-coder", dict(LOAD_BEARING)
            )
        )
        # Late verification write on the already-completed task (backstop).
        amended = {**LOAD_BEARING, "r2_verification": {"verified": False}}
        _seed_task(
            tmp_path,
            TEAM,
            "11",
            subject=subject,
            owner="backend-coder",
            status="completed",
            metadata=amended,
        )
        tlg.evaluate_lifecycle(
            {
                "tool_name": "TaskUpdate",
                "tool_input": {
                    "taskId": "11",
                    "metadata": {"r2_verification": {"verified": False}},
                },
                "tool_response": {},
                "agent_type": LEAD,
            }
        )

        events = session_journal.read_events_from(
            str(session_dir), "task_metadata_snapshot"
        )
        ours = [e for e in events if e["task_id"] == "11"]
        assert len(ours) == 2, "changed payload must re-emit (supersession)"
        # Reader rule: latest-ts, LAST-wins on an equal ts (make_event
        # stamps at second granularity, so a same-second re-emit ties;
        # journal order breaks the tie — the same semantics as the
        # resolve_latest_artifacts supersede). sorted() is stable, so the
        # last element is the latest-ts, last-written event.
        latest = sorted(ours, key=lambda e: e["ts"])[-1]
        assert latest["metadata"]["r2_verification"] == {"verified": False}
        # Both events carry the same occupant — the reader's join key.
        assert ours[0]["occupant"] == ours[1]["occupant"]
