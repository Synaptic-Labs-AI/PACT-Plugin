"""
Location: pact-plugin/tests/test_agent_handoff_emitter_integration.py
Summary: NON-MOCKED L2 integration coverage for agent_handoff_emitter (b1 — the
TaskCompleted agent_handoff emit). agent_handoff_emitter is in the L3 set
(prior inert-class instance #551); its real-session L3 probe is POST-MERGE, so
THIS is the L2 layer — it drives the REAL read_task_json team-dir seam + the
REAL session journal end-to-end, never stubbing the resolver.

The sibling unit file pins pure gate/marker/schema behavior with synthetic
inputs; THIS file proves the wiring: a real task JSON in the real team dir ->
main() resolves it through read_task_json -> a real agent_handoff event lands in
the real session-journal.jsonl. A re-inert read_task_json can never again pass
green against this.

================================ ANTI-MOCK INVARIANT ===========================
MUST NOT monkeypatch read_task_json / get_task_list / iter_team_task_jsons /
get_team_name. The real team-dir resolution IS the seam under test; mocking it
reproduces the gap that ships an emit inert. The ONLY permitted doubles are
Path.home redirection (tasks + journal live under it) + the pact_context
fixture (session identity) + the deterministic stdin frame.

============================ NON-VACUITY (source-revert) =======================
The emit is DOWNSTREAM of the read_task_json team-dir read: a broken team-dir
resolver returns {} -> metadata.handoff absent -> the hook suppresses -> ZERO
agent_handoff events. So:
  Source-revert read_task_json's team-dir-first resolution
  (hooks/shared/task_utils.py read_task_json) so it cannot locate
  ~/.claude/tasks/{team_name}/{id}.json, then run:
    python -m pytest tests/test_agent_handoff_emitter_integration.py -k non_vacuity_gate
  EXPECTED cardinality: {1 failed} — read_task_json returns {} -> no
  metadata.handoff -> no event (0 emitted, assertion wants 1). Restore -> green.
The same-fixture NEGATIVE control (no task file on disk -> 0 events) is the
positive/negative pairing that proves the assertion is coupled to the real read,
not to an always-true emit.
================================================================================
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

TEAM = "pact-testteam"
SID = "aaaaaaaa-1111-2222-3333-444444444444"


def _run_main(stdin_obj: dict, monkeypatch) -> int:
    """Drive agent_handoff_emitter.main() with a stdin frame; return exit code."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stdin_obj)))
    try:
        ahe.main()
    except SystemExit as e:
        return int(e.code or 0)
    return 0


def _write_task(tasks_dir: Path, task_id: str, owner: str, subject: str,
                with_handoff: bool = True) -> None:
    tasks_dir.mkdir(parents=True, exist_ok=True)
    meta = {}
    if with_handoff:
        meta["handoff"] = {
            "produced": "did the thing", "decisions": "chose X",
            "uncertainty": "none", "integration": "n/a",
            "reasoning_chain": "because", "open_questions": "none",
        }
    task = {"id": task_id, "owner": owner, "subject": subject,
            "status": "completed", "metadata": meta}
    (tasks_dir / f"{task_id}.json").write_text(json.dumps(task), encoding="utf-8")


@pytest.fixture
def live_env(tmp_path, monkeypatch, pact_context):
    """Redirect Path.home -> tmp (real read_task_json team dir + real journal),
    configure the real pact_context. NO resolver stubbed."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name=TEAM, session_id=SID, project_dir="/test/project")
    tasks_dir = tmp_path / ".claude" / "tasks" / TEAM
    return tmp_path, tasks_dir


class TestAgentHandoffEmitRealSeam:
    def test_non_vacuity_gate_real_task_emits_agent_handoff(self, live_env, monkeypatch):
        _, tasks_dir = live_env
        _write_task(tasks_dir, task_id="7", owner="architect", subject="design X")
        code = _run_main(
            {"hook_event_name": "TaskCompleted", "task_id": "7",
             "team_name": TEAM, "task_subject": "design X"},
            monkeypatch,
        )
        assert code == 0
        events = read_events("agent_handoff")
        assert len(events) == 1, (
            "exactly one agent_handoff event must land in the REAL journal once "
            "read_task_json resolves the REAL team dir; got %d (0 = the inert "
            "read_task_json bug)" % len(events)
        )
        assert events[0]["agent"] == "architect"
        assert events[0]["task_id"] == "7"

    def test_negative_control_no_task_on_disk_no_emit(self, live_env, monkeypatch):
        # Same fixture, but NO task file -> read_task_json returns {} -> no
        # handoff -> 0 events. Proves the positive assertion is coupled to the
        # real read (not an always-fire emit).
        code = _run_main(
            {"hook_event_name": "TaskCompleted", "task_id": "7",
             "team_name": TEAM, "task_subject": "design X"},
            monkeypatch,
        )
        assert code == 0
        assert len(read_events("agent_handoff")) == 0
