"""
PR 4 drain-survival regression suite (architect §8.1 + §8.3).

PINS the post-retire harvest read-path invariant: after the platform drains the
task store (wipes ~/.claude/tasks/{team}/*.json), the secretary's harvest still
recovers EVERY HANDOFF and every load-bearing dispatch-calibration record,
because it reads them JOURNAL-FIRST from the session journal (a different,
session-scoped tree the drain does not touch).

The harvest skill (skills/pact-handoff-harvest/SKILL.md) is prose+pseudocode that
documents calling the journal read surface directly:
  - Step 1 / Step 3 (HANDOFF discovery + read): ``read_events('agent_handoff')``
  - Step 10 / Gap 1 (calibration): ``read_events('variety_assessed')``
Both resolve the session dir IMPLICITLY via pact_context.get_session_dir(). These
tests therefore exercise the REAL ``read_events`` over a REAL on-disk journal —
the integration seam whose correct resolution IS the thing under test — and never
mock ``read_events`` itself (a mocked read would test nothing; see
pact-testing-strategies "Non-mocked seam-integration tests").

Non-vacuity is by SOURCE REMOVAL, not by flag: §8.1 asserts recovery succeeds
with *.json gone but the journal intact, AND asserts it collapses to empty the
moment the journal source is also removed — proving recovery is journal-sourced,
not silently falling through to a surviving task-store read. §8.3 is paired
present/absent for the Gap 1 variety read.

Harness mirrors test_pr4_revision_v2_on_journal.py's live_env seam: redirect
Path.home to tmp_path + pact_context(team, sid, project_dir) so append_event /
read_events resolve a tmp session journal, and seed a real tasks/{team}/ store so
the "drain" (removing *.json) is a real filesystem mutation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared.session_journal import append_event, make_event, read_events  # noqa: E402

TEAM = "pact-pr4-drain"
SID = "dddddddd-1111-2222-3333-555555555555"
PROJECT_DIR = "/test/project"


def _handoff(marker: str) -> dict:
    return {
        "produced": marker,
        "decisions": "chose X",
        "uncertainty": "none",
        "integration": "n/a",
        "reasoning_chain": "because",
        "open_questions": "none",
    }


@pytest.fixture
def live_env(tmp_path, monkeypatch, pact_context):
    """Redirect HOME + session context to a tmp tree so append_event / read_events
    write+read a real on-disk session journal, and so a tasks/{team}/ store can be
    seeded then drained on the real filesystem."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name=TEAM, session_id=SID, project_dir=PROJECT_DIR)
    tasks_dir = tmp_path / ".claude" / "tasks" / TEAM
    return tmp_path, tasks_dir


def _seed_task_store(tasks_dir: Path, task_id: str, owner: str, subject: str,
                     handoff_marker: str) -> None:
    """Write a real ~/.claude/tasks/{team}/{id}.json carrying metadata.handoff —
    the GC-vulnerable copy the drain will wipe (so a journal-first harvest cannot
    be silently rescued by a surviving task-store read)."""
    tasks_dir.mkdir(parents=True, exist_ok=True)
    task = {
        "id": task_id,
        "owner": owner,
        "subject": subject,
        "status": "completed",
        "metadata": {"handoff": _handoff(handoff_marker)},
    }
    (tasks_dir / f"{task_id}.json").write_text(json.dumps(task), encoding="utf-8")


def _journal_file(tmp_path: Path) -> Path:
    """Resolve the on-disk journal path the implicit read surface uses, so §8.1's
    non-vacuity arm can remove the journal SOURCE and prove recovery collapses."""
    sessions_root = tmp_path / ".claude" / "pact-sessions"
    matches = list(sessions_root.rglob("session-journal.jsonl"))
    assert len(matches) == 1, (
        "expected exactly one tmp session journal, found %r" % matches
    )
    return matches[0]


class TestPR4DrainSurvivalJournalRecovery:
    """§8.1 — after the task-store drain, harvest recovers ALL HANDOFFs + ALL
    load-bearing dispatch-calibration metadata from the journal alone."""

    def test_harvest_recovers_all_records_journal_first_after_drain(self, live_env):
        tmp_path, tasks_dir = live_env

        # --- Seed: two completed agent tasks in BOTH the task store AND the
        # journal (the platform mirrors metadata.handoff to the journal at the
        # lead's completion; the task store holds the GC-vulnerable copy). ---
        _seed_task_store(tasks_dir, "8", "backend-coder", "impl Y", "HANDOFF_8")
        _seed_task_store(tasks_dir, "12", "frontend-coder", "build Z", "HANDOFF_12")

        assert append_event(make_event(
            "agent_handoff", agent="backend-coder", task_id="8",
            task_subject="impl Y", handoff=_handoff("HANDOFF_8"),
        ))
        assert append_event(make_event(
            "agent_handoff", agent="frontend-coder", task_id="12",
            task_subject="build Z", handoff=_handoff("HANDOFF_12"),
        ))
        # Load-bearing dispatch-calibration records (the GC-immune mirrors):
        assert append_event(make_event(
            "variety_assessed", task_id="2",
            variety={"novelty": 1, "scope": 2, "uncertainty": 2,
                     "risk": 2, "total": 7},
        ))
        assert append_event(make_event(
            "dispatch_variety", task_id="8",
            variety={"novelty": 1, "scope": 2, "uncertainty": 2,
                     "risk": 2, "total": 7},
        ))
        assert append_event(make_event(
            "teachback_ack", task_id="8",
            rationale_articulates_this_dispatch="yes",
        ))

        # --- Sanity: the task store *.json exist pre-drain. ---
        json_files = list(tasks_dir.glob("*.json"))
        assert len(json_files) == 2, "pre-drain: two task *.json seeded"

        # --- THE DRAIN: platform wipes every task *.json (team-scoped). ---
        for jf in json_files:
            jf.unlink()
        assert list(tasks_dir.glob("*.json")) == [], "drain removed all task *.json"

        # --- HARVEST (post-retire, journal-first) over the UNSTUBBED read surface. ---
        handoffs = read_events("agent_handoff")
        recovered = {e["task_id"]: e["handoff"]["produced"] for e in handoffs}
        assert recovered == {"8": "HANDOFF_8", "12": "HANDOFF_12"}, (
            "harvest must recover BOTH HANDOFFs from the journal after the drain; "
            "got %r" % recovered
        )

        variety = read_events("variety_assessed")
        assert [e["variety"]["total"] for e in variety] == [7], (
            "initial_variety_score (Gap 1) recovered from the journal post-drain"
        )

        dispatch = read_events("dispatch_variety")
        assert [e["variety"]["total"] for e in dispatch] == [7], (
            "per-dispatch variety recovered from the journal post-drain"
        )

        acks = read_events("teachback_ack")
        assert [e["rationale_articulates_this_dispatch"] for e in acks] == ["yes"], (
            "teachback ack recovered from the journal post-drain"
        )

    def test_recovery_collapses_when_the_journal_source_is_also_removed(self, live_env):
        """NON-VACUITY (source removal). The §8.1 positive could pass for the
        WRONG reason if harvest were silently reading some surviving copy. Remove
        the JOURNAL itself (the source the post-retire path depends on) and prove
        recovery collapses to empty — so the positive's success is CAUSED by the
        journal being the source, not by an incidental fallback."""
        tmp_path, tasks_dir = live_env

        _seed_task_store(tasks_dir, "8", "backend-coder", "impl Y", "HANDOFF_8")
        assert append_event(make_event(
            "agent_handoff", agent="backend-coder", task_id="8",
            task_subject="impl Y", handoff=_handoff("HANDOFF_8"),
        ))

        # Pre-condition: the positive recovery works with the journal present.
        assert [e["task_id"] for e in read_events("agent_handoff")] == ["8"]

        # The drain wipes the task store...
        for jf in list(tasks_dir.glob("*.json")):
            jf.unlink()
        # ...AND we additionally remove the journal source itself.
        _journal_file(tmp_path).unlink()

        # With BOTH the task store and the journal gone, harvest recovers nothing.
        assert read_events("agent_handoff") == [], (
            "with the journal source removed, journal-first harvest MUST recover "
            "nothing — proving the positive recovery was journal-sourced, not a "
            "surviving task-store read"
        )


class TestPR4Gap1VarietyReadPairedPresentAbsent:
    """§8.3 — Gap 1 Step 10 reads initial_variety_score from the journal
    variety_assessed event when present; human-ask fallback (no event) when
    absent. Paired present/absent for the new read predicate."""

    def test_variety_assessed_present_resolves_score_from_journal(self, live_env):
        tmp_path, tasks_dir = live_env
        assert append_event(make_event(
            "variety_assessed", task_id="2",
            variety={"novelty": 1, "scope": 2, "uncertainty": 2,
                     "risk": 2, "total": 7},
        ))

        # The drain has no bearing on the journal — but assert no task *.json is
        # consulted: the variety read is journal-only by construction.
        assert not (tasks_dir.exists() and list(tasks_dir.glob("*.json"))), (
            "Gap 1 read must not depend on any task *.json"
        )

        events = read_events("variety_assessed")
        # Mirror the skill's documented resolution: first event's variety.total.
        first = next((e["variety"] for e in events if e.get("variety")), None)
        total = first.get("total") if isinstance(first, dict) else None
        assert total == 7, (
            "Gap 1: initial_variety_score resolves to variety['total'] from the "
            "first variety_assessed journal event"
        )

    def test_variety_assessed_absent_triggers_human_fallback(self, live_env):
        """Paired ABSENT arm: with NO variety_assessed event, the journal read
        yields no score, so the skill falls back to asking the team-lead. The
        observable seam-level fact the fallback keys on is an empty read."""
        tmp_path, tasks_dir = live_env
        # Write an UNRELATED event so the journal exists but has no variety_assessed.
        assert append_event(make_event(
            "agent_handoff", agent="backend-coder", task_id="8",
            task_subject="impl Y", handoff=_handoff("HANDOFF_8"),
        ))

        events = read_events("variety_assessed")
        first = next((e["variety"] for e in events if e.get("variety")), None)
        total = first.get("total") if isinstance(first, dict) else None
        assert total is None, (
            "Gap 1 ABSENT arm: no variety_assessed event → no journal score → the "
            "skill takes the human-ask fallback (preserved). A non-None here would "
            "mean the absent-case fallback was lost."
        )
