"""
PR 4 Gap 2 regression test (architect §8.2) — DELIVERABLE-READY for TEST phase.

PINS the runtime fact that retires Gap 2: in the reject->revise->accept
lifecycle, the single journal agent_handoff event carries the REVISED (v2)
HANDOFF, NOT the rejected first (v1) submission. If this ever regresses (e.g. a
future change makes a teammate self-complete, or fires an emit while in_progress),
this test fails — which is exactly the guard the Gap-2 retire needs so the
deleted revision_number>1 harvest branch can never silently become load-bearing
again.

Mechanism under guard (architect's "lead-only-completion TIMING" framing):
  - The emitter's transition signal is PRIMARY `hook_event_name=="TaskCompleted"`
    (agent_handoff_emitter.py:198), with a disk-status fallback (`status==
    "completed"`) used ONLY when stdin omits hook_event_name (:199). PLUS
    metadata.handoff truthy AND the O_EXCL marker absent. (This primary/fallback
    split is independently pinned in tests/test_emitter_happy_and_gates.py by
    TestStatusFallbackGate + TestProductionShapeMetadataOnly.)
  - In production the PLATFORM sends a TaskCompleted frame ONLY at an actual
    completion. Rejection is PRE-completion: the teammate stores HANDOFF +
    remains in_progress and never self-completes (pact-agent-teams SKILL.md;
    pact-completion-authority) — so NO TaskCompleted frame fires during the
    in_progress / rejection phases.
  - So a revised task is completed exactly ONCE — at the lead's acceptance — by
    which point metadata.handoff == v2. The single emit reads v2.

IMPORTANT (faithful platform modeling): the in_progress phases are driven with
a frame that OMITS hook_event_name (the disk-status fallback path), which
correctly SUPPRESSES because status=="in_progress". This mirrors production —
the platform does NOT send a TaskCompleted frame while a task is in_progress.
(A test that injected hook_event_name=="TaskCompleted" while in_progress would
emit, because b1 TRUSTS the platform signal over disk state — but that frame
never occurs in production, so modeling it would be a false regression.)

Two assertions:
  1. POSITIVE: after the full lifecycle, the journal holds exactly one
     agent_handoff event for the task, and its handoff content is v2.
  2. NON-VACUITY CONTROL (append_event spy): ZERO emits fire across the entire
     rejection phase (disk-status fallback suppresses while in_progress), and
     exactly ONE fires at acceptance. This proves the v2-on-journal result is
     CAUSED by lead-only-completion timing, not by an always-fire emit.

Harness: drives the REAL agent_handoff_emitter.main() against a real on-disk
task.json + the real session journal (no emitter internals mocked), mirroring
test_agent_handoff_emitter_integration.py's L2 seam. append_event is wrapped
(not replaced) so the real journal write still happens AND we count calls.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import agent_handoff_emitter as ahe  # noqa: E402
from shared.session_journal import read_events  # noqa: E402

TEAM = "pact-pr4-gap2"
SID = "cccccccc-1111-2222-3333-444444444444"

V1 = "V1_REJECTED_FIRST_SUBMISSION"
V2 = "V2_REVISED_ACCEPTED_CONTENT"


def _handoff(marker: str) -> dict:
    return {
        "produced": marker,
        "decisions": "chose X",
        "uncertainty": "none",
        "integration": "n/a",
        "reasoning_chain": "because",
        "open_questions": "none",
    }


def _write_task(tasks_dir: Path, task_id: str, owner: str, subject: str,
                handoff_marker: str, status: str,
                revision_number: int | None = None) -> None:
    tasks_dir.mkdir(parents=True, exist_ok=True)
    meta: dict = {"handoff": _handoff(handoff_marker)}
    if revision_number is not None:
        meta["revision_number"] = revision_number
    task = {"id": task_id, "owner": owner, "subject": subject,
            "status": status, "metadata": meta}
    (tasks_dir / f"{task_id}.json").write_text(json.dumps(task), encoding="utf-8")


def _run_main(stdin_obj: dict, monkeypatch) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stdin_obj)))
    try:
        ahe.main()
    except SystemExit as e:
        return int(e.code or 0)
    return 0


@pytest.fixture
def live_env(tmp_path, monkeypatch, pact_context):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name=TEAM, session_id=SID, project_dir="/test/project")
    tasks_dir = tmp_path / ".claude" / "tasks" / TEAM
    return tmp_path, tasks_dir


class TestPR4Gap2RevisionV2OnJournal:
    def test_reject_revise_accept_journal_carries_v2_with_zero_emit_across_rejection(
            self, live_env, monkeypatch):
        _, tasks_dir = live_env
        tid, owner, subject = "8", "backend-coder", "impl Y"

        # append_event spy — wrap the real one so journal writes still occur.
        calls: list[dict] = []
        real_append = ahe.append_event

        def spy_append(event, *a, **k):
            calls.append(event)
            return real_append(event, *a, **k)

        monkeypatch.setattr(ahe, "append_event", spy_append)

        # --- Phase 1: teammate stores v1 HANDOFF, remains in_progress ---
        # (teammate never self-completes; this is the On-Completion store step).
        _write_task(tasks_dir, tid, owner, subject, V1, status="in_progress")
        # Drive an emitter invocation via the disk-status FALLBACK path (omit
        # hook_event_name) — production sends NO TaskCompleted frame while
        # in_progress, so the fallback's status gate is what governs here. It
        # MUST suppress because status=="in_progress".
        _run_main({"task_id": tid, "team_name": TEAM,
                   "task_subject": subject}, monkeypatch)
        emits_after_v1 = len(calls)

        # --- Phase 2: lead REJECTS (metadata write only; NO status flip) ---
        # Task stays in_progress; teammate revises metadata.handoff -> v2 and
        # increments revision_number. Still no completion -> still no
        # TaskCompleted frame; fallback path must still suppress.
        _write_task(tasks_dir, tid, owner, subject, V2, status="in_progress",
                    revision_number=2)
        _run_main({"task_id": tid, "team_name": TEAM,
                   "task_subject": subject}, monkeypatch)
        emits_after_revision = len(calls)

        # NON-VACUITY CONTROL: zero emits across the entire rejection phase.
        assert emits_after_v1 == 0, (
            "emitter must NOT fire while task is in_progress (v1 store); "
            "got %d emit(s)" % emits_after_v1
        )
        assert emits_after_revision == 0, (
            "emitter must NOT fire while task is in_progress (post-revision, "
            "pre-acceptance); got %d emit(s)" % emits_after_revision
        )

        # --- Phase 3: lead ACCEPTS (the single, only completion) ---
        # metadata.handoff is already v2 on disk; status flips to completed.
        _write_task(tasks_dir, tid, owner, subject, V2, status="completed",
                    revision_number=2)
        _run_main({"hook_event_name": "TaskCompleted", "task_id": tid,
                   "team_name": TEAM, "task_subject": subject}, monkeypatch)

        # POSITIVE: exactly one emit, carrying v2.
        assert len(calls) == 1, (
            "exactly one agent_handoff emit across the whole lifecycle (at the "
            "single acceptance completion); got %d" % len(calls)
        )
        events = [e for e in read_events("agent_handoff") if e["task_id"] == tid]
        assert len(events) == 1, "exactly one journal event for the task"
        produced = events[0]["handoff"]["produced"]
        assert produced == V2, (
            "REGRESSION: journal agent_handoff event carried %r, expected the "
            "REVISED v2 content. If this fails, the reject->revise->accept flow "
            "no longer guarantees v2-on-journal and the Gap-2 retire is unsafe." % produced
        )
        assert V1 not in json.dumps(events[0]), (
            "journal event must not contain any v1/rejected content"
        )
