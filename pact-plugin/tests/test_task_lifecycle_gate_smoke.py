"""
Smoke tests for task_lifecycle_gate.py — PostToolUse hook enforcing
PACT lifecycle invariants F8-F13 (#662 Commit 3).

NOT comprehensive coverage — that is TEST-phase scope. These cases lock
the load-bearing decisions in place so a future regression surfaces fast:

  S1. F8 advisory: TEACHBACK Task created without addBlocks
  S2. F9 advisory: pact-* Task created without addBlockedBy
  S3. F11 advisory: pact-* work-Task completed with empty metadata.handoff
  S4. F12 advisory + writeback: teammate self-completes; assert
      metadata.completion_disputed=True AND gate_writeback=True written
      to disk
  S5. F12 recursion-marker self-skip: tool_input.metadata.gate_writeback
      already True → no advisories, no writeback (counter-test)
  S6. F21 fail-closed counter-test: simulate cross-package import failure
      via the public _emit_load_failure_advisory helper → advisory emitted
      with hookEventName + exit 0
"""

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import task_lifecycle_gate as tlg  # noqa: E402


# ─── helpers ───────────────────────────────────────────────────────────────


def _stdin(payload: dict) -> io.StringIO:
    return io.StringIO(json.dumps(payload))


def _capture_main(payload: dict, capsys) -> tuple[int, dict | None]:
    """Run main() with payload as stdin; return (exit_code, parsed_stdout)."""
    with patch.object(sys, "stdin", _stdin(payload)):
        with pytest.raises(SystemExit) as exc:
            tlg.main()
    code = exc.value.code if exc.value.code is not None else 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out) if out else None
    return code, parsed


# ─── S1: F8 — TEACHBACK Task created without addBlocks ────────────────────


def test_s1_f8_teachback_create_without_addblocks(pact_context, capsys):
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "preparer: TEACHBACK for foo",
            "owner": "pact-preparer",
            # addBlocks deliberately omitted
        },
        "tool_response": {},
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any("F8" in a for a in advisories), (
        f"expected F8 advisory, got: {advisories}"
    )


# ─── S2: F9 — pact-* Task created without addBlockedBy ────────────────────


def test_s2_f9_work_task_create_without_addblockedby(pact_context, capsys):
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
    assert any("F9" in a for a in advisories), (
        f"expected F9 advisory, got: {advisories}"
    )


# ─── S3: F11 — pact-* work-Task completed without metadata.handoff ────────


def test_s3_f11_work_task_completed_without_handoff(pact_context, capsys):
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "42", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "42",
                "subject": "implement foo",
                "owner": "pact-backend-coder",
                "metadata": {},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any("F11" in a for a in advisories), (
        f"expected F11 advisory, got: {advisories}"
    )
    # F13 must NOT also fire — disjoint per lead clarification.
    assert not any("F13" in a for a in advisories)


# ─── S4: F12 — teammate self-completes → advisory + FS writeback ──────────


def test_s4_f12_self_completion_writeback(tmp_path, monkeypatch, pact_context):
    pact_context(team_name="test-team", session_id="test-session")

    # Stage a fake task on disk under HOME/.claude/tasks/test-team/.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    tasks_dir = tmp_path / ".claude" / "tasks" / "test-team"
    tasks_dir.mkdir(parents=True)
    task_path = tasks_dir / "99.json"
    task_payload = {
        "id": "99",
        "subject": "implement foo",
        "owner": "backend-coder-3",
        "metadata": {},
    }
    task_path.write_text(json.dumps(task_payload), encoding="utf-8")

    # agent_id=name@team format → trustworthy_actor_name returns "backend-coder-3".
    payload = {
        "tool_name": "TaskUpdate",
        "agent_id": "backend-coder-3@test-team",
        "tool_input": {"taskId": "99", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "99",
                "subject": "implement foo",
                "owner": "backend-coder-3",
                "metadata": {
                    "handoff": {  # well-formed so F11/F13 don't also fire
                        "produced": "x",
                        "decisions": "x",
                        "reasoning_chain": "x",
                        "uncertainty": "x",
                        "integration": "x",
                        "open_questions": "x",
                    }
                },
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert any("F12" in a for a in advisories), (
        f"expected F12 advisory, got: {advisories}"
    )

    # Writeback must have landed on disk with both markers.
    written = json.loads(task_path.read_text(encoding="utf-8"))
    assert written["metadata"]["completion_disputed"] is True
    assert written["metadata"]["gate_writeback"] is True


# ─── S5: F12 recursion-marker self-skip ───────────────────────────────────


def test_s5_recursion_marker_self_skip(pact_context):
    pact_context(team_name="test-team", session_id="test-session")
    payload = {
        "tool_name": "TaskUpdate",
        "agent_id": "backend-coder-3@test-team",
        "tool_input": {
            "taskId": "99",
            "status": "completed",
            # gate_writeback=True → step ① short-circuit
            "metadata": {"gate_writeback": True, "completion_disputed": True},
        },
        "tool_response": {
            "task": {
                "id": "99",
                "subject": "implement foo",
                "owner": "backend-coder-3",
                "metadata": {"gate_writeback": True, "completion_disputed": True},
            }
        },
    }
    advisories = tlg.evaluate_lifecycle(payload)
    assert advisories == [], (
        f"expected silent skip on recursion marker, got: {advisories}"
    )


# ─── S6: F21 fail-closed-as-advisory on simulated module-load failure ─────


def test_s6_load_failure_emits_advisory_exit_0(capsys):
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
