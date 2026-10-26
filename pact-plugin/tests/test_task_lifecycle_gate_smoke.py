"""
Smoke tests for task_lifecycle_gate.py — PostToolUse hook enforcing
PACT lifecycle rules (#662 Commit 3).

NOT comprehensive coverage — that is TEST-phase scope. These cases lock
the load-bearing decisions in place so a future regression surfaces fast:

  S1. teachback_addblocks_missing advisory: TEACHBACK Task owner-wiring TaskUpdate
      lands without paired addBlocks and task_a has no pre-existing blocks
  S2. work_addblockedby_missing advisory: pact-* Task created without addBlockedBy
  S3. RETIRED — the handoff_missing / handoff_schema_invalid completion advisories
      (dormant is_work_task branch) were retired; their smoke case was removed
  S4. self_completion advisory + writeback: teammate self-completes; assert
      metadata.completion_disputed=True AND gate_writeback=True written
      to disk
  S5. self_completion recursion-marker self-skip: tool_input.metadata.gate_writeback
      already True → no advisories, no writeback (counter-test)
  S6. Module-load fail-closed counter-test: simulate cross-package import failure
      via the public _emit_load_failure_advisory helper → advisory emitted
      with hookEventName + exit 0
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


import task_lifecycle_gate as tlg  # noqa: E402
from fixtures.actor_frames import (  # noqa: E402
    SUBAGENT_ID,
    cloud_meta,
    cloud_subagent_frame,
    inprocess_frame,
    write_subagent_meta,
    write_team_config,
)
from shared.paths import get_claude_config_dir  # noqa: E402


# ─── helpers ───────────────────────────────────────────────────────────────


def _stdin(payload: dict) -> io.StringIO:
    return io.StringIO(json.dumps(payload))


def _capture_main(payload: dict, capsys) -> tuple[int, dict | None]:
    """Run main() with payload as stdin; return (exit_code, parsed_stdout)."""
    with patch.object(sys, "stdin", _stdin(payload)):
        with pytest.raises(SystemExit) as exc:
            tlg.main()
    raw_code = exc.value.code if exc.value.code is not None else 0
    code = int(raw_code) if isinstance(raw_code, int) else 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out) if out else None
    return code, parsed


# ─── S1: teachback_addblocks_missing — owner-wiring TaskUpdate without addBlocks ─


def test_teachback_wiring_update_without_addblocks(tmp_path, monkeypatch, pact_context, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    tasks_dir = tmp_path / ".claude" / "tasks" / "test-team"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "A.json").write_text(
        json.dumps({
            "id": "A",
            "subject": "preparer: TEACHBACK for foo",
            "status": "pending",
            "blocks": [],
        }),
        encoding="utf-8",
    )
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "A",
            "owner": "pact-preparer",
            # addBlocks deliberately omitted
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(rule == "teachback_addblocks_missing" for rule, _ in advisories), (
        f"expected teachback_addblocks_missing advisory, got: {advisories}"
    )


# ─── S2: work_addblockedby_missing — pact-* Task created without addBlockedBy ───


def test_work_task_create_without_addblockedby(pact_context, capsys):
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "implement foo",
            "owner": "pact-backend-coder",
            # addBlockedBy deliberately omitted
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any(rule == "work_addblockedby_missing" for rule, _ in advisories), (
        f"expected work_addblockedby_missing advisory, got: {advisories}"
    )


# ─── S3: RETIRED — the handoff_missing / handoff_schema_invalid completion ─────
# advisories were gated on a permanently-dormant is_work_task / owner.startswith
# ("pact-") guard (bare teammate owner names never matched) and were retired
# with their branch; the smoke case that exercised them via a synthetic pact-*
# owner was removed. Lead-side HANDOFF presence is covered elsewhere.


# ─── S4: self_completion — teammate self-completes → advisory + FS writeback ────


def _stage_task(config_root: Path, task_id: str, owner: str) -> Path:
    tasks_dir = config_root / "tasks" / "test-team"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    task_path = tasks_dir / f"{task_id}.json"
    task_path.write_text(json.dumps({
        "id": task_id, "subject": "implement foo", "owner": owner, "metadata": {},
    }), encoding="utf-8")
    return task_path


def _completed(frame: dict, task_id: str, owner: str) -> dict:
    frame["tool_input"] = {"taskId": task_id, "status": "completed"}
    frame["tool_response"] = {"task": {
        "id": task_id, "subject": "implement foo", "owner": owner,
        "metadata": {
            "handoff": {  # well-formed handoff; this test isolates the self_completion advisory
                "produced": "x", "decisions": "x", "reasoning_chain": "x",
                "uncertainty": "x", "integration": "x", "open_questions": "x",
            }
        },
    }}
    return frame


def _assert_writeback(advisories, task_path: Path) -> None:
    assert any(rule == "self_completion" for rule, _ in advisories), (
        f"expected self_completion advisory, got: {advisories}"
    )
    # Writeback must have landed on disk with both markers.
    written = json.loads(task_path.read_text(encoding="utf-8"))
    assert written["metadata"]["completion_disputed"] is True
    assert written["metadata"]["gate_writeback"] is True


def test_self_completion_writeback_inprocess(pact_context):
    """In-process teammate (agent_type = its name, a<name>-<16hex> id, team
    config member) self-completes → advisory + on-disk writeback."""
    pact_context(team_name="test-team", session_id="test-session")
    root = get_claude_config_dir()
    task_path = _stage_task(root, "99", "backend-coder-3")
    write_team_config(root, "test-team", [
        {"name": "backend-coder-3", "agentType": "pact-backend-coder"},
    ])
    payload = _completed(inprocess_frame(root, "backend-coder-3"), "99", "backend-coder-3")
    _assert_writeback(tlg.evaluate_lifecycle(payload), task_path)


def test_self_completion_writeback_cloud(pact_context):
    """Cloud teammate (type in agent_type, a<16hex> id, meta.json, no team
    config) self-completes → advisory + on-disk writeback."""
    pact_context(team_name="test-team", session_id="test-session")
    root = get_claude_config_dir()
    task_path = _stage_task(root, "99", "backend-coder-3")
    write_subagent_meta(root, SUBAGENT_ID, cloud_meta("backend-coder-3", "pact-backend-coder"))
    payload = _completed(
        cloud_subagent_frame(root, "pact-backend-coder"), "99", "backend-coder-3"
    )
    _assert_writeback(tlg.evaluate_lifecycle(payload), task_path)


# ─── S5: self_completion recursion-marker self-skip ─────────────────────────────


def test_recursion_marker_self_skip(pact_context):
    pact_context(team_name="test-team", session_id="test-session")
    root = get_claude_config_dir()
    write_subagent_meta(root, SUBAGENT_ID, cloud_meta("backend-coder-3", "pact-backend-coder"))
    payload = _completed(
        cloud_subagent_frame(root, "pact-backend-coder"), "99", "backend-coder-3"
    )
    # gate_writeback=True → step ① short-circuit, even though this actor
    # would otherwise draw self_completion.
    marker = {"gate_writeback": True, "completion_disputed": True}
    payload["tool_input"]["metadata"] = dict(marker)
    payload["tool_response"]["task"]["metadata"] = dict(marker)
    advisories = tlg.evaluate_lifecycle(payload)
    assert advisories == [], (
        f"expected silent skip on recursion marker, got: {advisories}"
    )


# ─── S6: Module-load fail-closed-as-advisory simulation ─────────────────────────


def test_load_failure_emits_advisory_exit_0(capsys):
    err = ImportError("simulated cross-package import failure")
    with pytest.raises(SystemExit) as exc:
        tlg._emit_load_failure_advisory("module imports", err)
    # PostToolUse cannot DENY — exit 0, advisory output, hookEventName present.
    assert exc.value.code == 0
    parsed = json.loads(capsys.readouterr().out.strip())
    hso = parsed["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"
    assert "additionalContext" in hso
    assert "task_lifecycle_gate" in hso["additionalContext"]
    assert "ImportError" in hso["additionalContext"]
