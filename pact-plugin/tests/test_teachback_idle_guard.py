"""Tests for pact-plugin/hooks/teachback_idle_guard.py (#401 Commit #8).

Covers: inferred-state check, sidecar increment + reset, threshold
emission, carve-out bypasses, reassignment-detection reset, fail-open
on malformed stdin, hooks.json TeammateIdle chain ordering.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))
_SHARED_DIR = _HOOKS_DIR / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

import teachback_idle_guard as guard  # noqa: E402
from teachback_idle_guard import (  # noqa: E402
    _find_teammate_task,
    _increment_teachback_idle,
    _inferred_state_needs_algedonic,
    _reset_teachback_idle,
    _sidecar_path,
)


def _valid_variety(total=11):
    return {"total": total, "novelty": 3, "scope": 3,
            "uncertainty": 3, "risk": total - 9}


def _valid_submit():
    return {
        "understanding": "Short but present for state inference test purposes.",
        "first_action": {"action": "file.py:1", "expected_signal": "ok"},
    }


# ---------------------------------------------------------------------------
# _inferred_state_needs_algedonic
# ---------------------------------------------------------------------------

class TestInferredStateNeedsAlgedonic:
    def test_no_submit_no_algedonic(self):
        assert _inferred_state_needs_algedonic({}) is False

    def test_submit_only_needs_algedonic(self):
        meta = {"teachback_submit": _valid_submit()}
        assert _inferred_state_needs_algedonic(meta) is True

    def test_approved_clears_algedonic(self):
        # Lead responded with approval — teammate is not stuck
        meta = {
            "teachback_submit": _valid_submit(),
            "teachback_approved": {"conditions_met": {"unaddressed": []}},
        }
        assert _inferred_state_needs_algedonic(meta) is False

    def test_approved_with_unaddressed_clears_algedonic(self):
        # Auto-downgrade — ball is in teammate's court
        meta = {
            "teachback_submit": _valid_submit(),
            "teachback_approved": {"conditions_met": {"unaddressed": ["x"]}},
        }
        assert _inferred_state_needs_algedonic(meta) is False

    def test_corrections_clears_algedonic(self):
        meta = {
            "teachback_submit": _valid_submit(),
            "teachback_corrections": {"issues": ["fix"]},
        }
        assert _inferred_state_needs_algedonic(meta) is False

    def test_non_dict_metadata_safe(self):
        assert _inferred_state_needs_algedonic(None) is False  # type: ignore[arg-type]
        assert _inferred_state_needs_algedonic("str") is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _find_teammate_task
# ---------------------------------------------------------------------------

class TestFindTeammateTask:
    def test_finds_in_progress_task(self):
        tasks = [
            {"owner": "a", "status": "completed", "id": "1"},
            {"owner": "a", "status": "in_progress", "id": "2"},
        ]
        assert _find_teammate_task(tasks, "a")["id"] == "2"

    def test_returns_none_when_no_match(self):
        tasks = [{"owner": "b", "status": "in_progress", "id": "1"}]
        assert _find_teammate_task(tasks, "a") is None

    def test_ignores_completed_only(self):
        tasks = [{"owner": "a", "status": "completed", "id": "1"}]
        assert _find_teammate_task(tasks, "a") is None


# ---------------------------------------------------------------------------
# Sidecar increment/reset round trip
# ---------------------------------------------------------------------------

class TestSidecarAtomicIncrement:
    def test_first_increment_is_one(self, tmp_path):
        sidecar = tmp_path / "teachback_idle_counts.json"
        count = _increment_teachback_idle(sidecar, "coder-1", "17")
        assert count == 1

    def test_repeated_increments(self, tmp_path):
        sidecar = tmp_path / "teachback_idle_counts.json"
        _increment_teachback_idle(sidecar, "coder-1", "17")
        _increment_teachback_idle(sidecar, "coder-1", "17")
        c3 = _increment_teachback_idle(sidecar, "coder-1", "17")
        assert c3 == 3

    def test_reassignment_resets_count(self, tmp_path):
        sidecar = tmp_path / "teachback_idle_counts.json"
        _increment_teachback_idle(sidecar, "coder-1", "17")
        _increment_teachback_idle(sidecar, "coder-1", "17")
        _increment_teachback_idle(sidecar, "coder-1", "17")
        # Switch to a different task — count resets
        new = _increment_teachback_idle(sidecar, "coder-1", "25")
        assert new == 1

    def test_reset_removes_entry(self, tmp_path):
        sidecar = tmp_path / "teachback_idle_counts.json"
        _increment_teachback_idle(sidecar, "coder-1", "17")
        _reset_teachback_idle(sidecar, "coder-1")
        # Next increment starts fresh
        c = _increment_teachback_idle(sidecar, "coder-1", "17")
        assert c == 1

    def test_per_teammate_isolation(self, tmp_path):
        sidecar = tmp_path / "teachback_idle_counts.json"
        _increment_teachback_idle(sidecar, "coder-1", "17")
        _increment_teachback_idle(sidecar, "coder-1", "17")
        c_new = _increment_teachback_idle(sidecar, "coder-2", "20")
        # coder-2 starts independently
        assert c_new == 1


# ---------------------------------------------------------------------------
# main() integration tests with mocked task list
# ---------------------------------------------------------------------------

def _run_main(monkeypatch, capsys, stdin_payload, *, tasks=None,
               team_name="pact-test", sidecar_dir=None):
    """Helper to run main() with injected stdin + task list."""
    if isinstance(stdin_payload, (dict, list)):
        raw = json.dumps(stdin_payload)
    else:
        raw = stdin_payload

    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    monkeypatch.setattr(guard, "get_task_list",
                         lambda: tasks if tasks is not None else [])
    monkeypatch.setattr(guard, "get_team_name", lambda: team_name)

    if sidecar_dir is not None:
        monkeypatch.setattr(
            guard, "_sidecar_path",
            lambda _team: sidecar_dir / "teachback_idle_counts.json",
        )

    # Silence journal writes in tests
    monkeypatch.setattr(guard, "append_event", lambda *a, **kw: None)
    monkeypatch.setattr(guard, "make_event", lambda *a, **kw: {"type": "fake"})

    with pytest.raises(SystemExit) as exc:
        guard.main()
    captured = capsys.readouterr()
    return exc.value.code, captured.out, captured.err


class TestMainStdinFailOpen:
    def test_malformed_stdin(self, monkeypatch, capsys):
        code, out, _err = _run_main(monkeypatch, capsys, "{{not-json}")
        assert code == 0
        assert '"suppressOutput": true' in out

    def test_empty_stdin(self, monkeypatch, capsys):
        code, out, _err = _run_main(monkeypatch, capsys, "")
        assert code == 0


class TestMainCarveOuts:
    def test_no_teammate_name(self, monkeypatch, capsys):
        code, out, _err = _run_main(
            monkeypatch, capsys, {"team_name": "pact-test"},
        )
        assert code == 0
        assert '"suppressOutput": true' in out

    def test_exempt_agent(self, monkeypatch, capsys):
        code, out, _err = _run_main(
            monkeypatch, capsys,
            {"teammate_name": "secretary", "team_name": "pact-test"},
        )
        assert code == 0

    def test_no_in_progress_task(self, monkeypatch, capsys, tmp_path):
        code, out, _err = _run_main(
            monkeypatch, capsys,
            {"teammate_name": "coder-1", "team_name": "pact-test"},
            tasks=[], sidecar_dir=tmp_path,
        )
        assert code == 0

    def test_low_variety_bypass(self, monkeypatch, capsys, tmp_path):
        tasks = [{
            "owner": "coder-1",
            "status": "in_progress",
            "id": "5",
            "metadata": {"variety": {"total": 5}},
        }]
        code, out, _err = _run_main(
            monkeypatch, capsys,
            {"teammate_name": "coder-1", "team_name": "pact-test"},
            tasks=tasks, sidecar_dir=tmp_path,
        )
        assert code == 0

    def test_signal_task_bypass(self, monkeypatch, capsys, tmp_path):
        tasks = [{
            "owner": "coder-1",
            "status": "in_progress",
            "id": "5",
            "metadata": {"type": "blocker", "variety": _valid_variety()},
        }]
        code, out, _err = _run_main(
            monkeypatch, capsys,
            {"teammate_name": "coder-1", "team_name": "pact-test"},
            tasks=tasks, sidecar_dir=tmp_path,
        )
        assert code == 0

    def test_skipped_task_bypass(self, monkeypatch, capsys, tmp_path):
        tasks = [{
            "owner": "coder-1",
            "status": "in_progress",
            "id": "5",
            "metadata": {"skipped": True, "variety": _valid_variety()},
        }]
        code, out, _err = _run_main(
            monkeypatch, capsys,
            {"teammate_name": "coder-1", "team_name": "pact-test"},
            tasks=tasks, sidecar_dir=tmp_path,
        )
        assert code == 0


class TestMainAlgedonicEmission:
    def _build_tasks(self, metadata):
        return [{
            "owner": "coder-1",
            "status": "in_progress",
            "id": "17",
            "metadata": metadata,
        }]

    def test_below_threshold_silent(self, monkeypatch, capsys, tmp_path):
        tasks = self._build_tasks({
            "variety": _valid_variety(11),
            "teachback_submit": _valid_submit(),
        })
        # First idle event — count=1 (below threshold 3)
        code, out, _err = _run_main(
            monkeypatch, capsys,
            {"teammate_name": "coder-1", "team_name": "pact-test"},
            tasks=tasks, sidecar_dir=tmp_path,
        )
        assert code == 0
        assert '"suppressOutput": true' in out

    def test_threshold_emits_algedonic(self, monkeypatch, capsys, tmp_path):
        tasks = self._build_tasks({
            "variety": _valid_variety(11),
            "teachback_submit": _valid_submit(),
        })
        # Fire 3 times — 3rd emits the algedonic
        for i in range(3):
            code, out, _err = _run_main(
                monkeypatch, capsys,
                {"teammate_name": "coder-1", "team_name": "pact-test"},
                tasks=tasks, sidecar_dir=tmp_path,
            )
            assert code == 0
        # Last captured output should have the systemMessage
        payload = json.loads(out.strip())
        assert "systemMessage" in payload
        assert "ALGEDONIC ALERT" in payload["systemMessage"]
        assert "coder-1" in payload["systemMessage"]
        assert "17" in payload["systemMessage"]
        assert "teachback_approved" in payload["systemMessage"]
        assert "teachback_corrections" in payload["systemMessage"]

    def test_continuing_algedonic_at_count_4(self, monkeypatch, capsys, tmp_path):
        tasks = self._build_tasks({
            "variety": _valid_variety(11),
            "teachback_submit": _valid_submit(),
        })
        for i in range(4):
            code, out, _err = _run_main(
                monkeypatch, capsys,
                {"teammate_name": "coder-1", "team_name": "pact-test"},
                tasks=tasks, sidecar_dir=tmp_path,
            )
        # 4th event still emits algedonic (persistence observation)
        payload = json.loads(out.strip())
        assert "ALGEDONIC ALERT" in payload.get("systemMessage", "")

    def test_approved_resets_count(self, monkeypatch, capsys, tmp_path):
        """When the lead writes teachback_approved, the sidecar resets
        so a subsequent stall on a new submit starts from 1 again."""
        # Build up count=2
        tasks_pending = self._build_tasks({
            "variety": _valid_variety(11),
            "teachback_submit": _valid_submit(),
        })
        _run_main(
            monkeypatch, capsys,
            {"teammate_name": "coder-1", "team_name": "pact-test"},
            tasks=tasks_pending, sidecar_dir=tmp_path,
        )
        _run_main(
            monkeypatch, capsys,
            {"teammate_name": "coder-1", "team_name": "pact-test"},
            tasks=tasks_pending, sidecar_dir=tmp_path,
        )
        # Lead approves — count resets
        tasks_active = self._build_tasks({
            "variety": _valid_variety(11),
            "teachback_submit": _valid_submit(),
            "teachback_approved": {"conditions_met": {"unaddressed": []}},
        })
        code, out, _err = _run_main(
            monkeypatch, capsys,
            {"teammate_name": "coder-1", "team_name": "pact-test"},
            tasks=tasks_active, sidecar_dir=tmp_path,
        )
        assert code == 0
        assert '"suppressOutput": true' in out
        # Now a NEW stall starts fresh from count=1
        code2, out2, _err2 = _run_main(
            monkeypatch, capsys,
            {"teammate_name": "coder-1", "team_name": "pact-test"},
            tasks=tasks_pending, sidecar_dir=tmp_path,
        )
        # Only count=1 — below threshold; no algedonic
        assert '"suppressOutput": true' in out2


# ---------------------------------------------------------------------------
# hooks.json invariant — placement in TeammateIdle chain
# ---------------------------------------------------------------------------

class TestHooksJsonPlacement:
    def test_teachback_idle_guard_registered_between(self):
        hooks_json = Path(__file__).resolve().parent.parent / "hooks" / "hooks.json"
        config = json.loads(hooks_json.read_text(encoding="utf-8"))

        chain: list[str] = []
        for entry in config["hooks"].get("TeammateIdle", []):
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                if "teammate_completion_gate.py" in cmd:
                    chain.append("completion_gate")
                elif "teachback_idle_guard.py" in cmd:
                    chain.append("teachback_idle_guard")
                elif "teammate_idle.py" in cmd:
                    chain.append("teammate_idle")

        # Expected order per COMPONENT-DESIGN.md Hook 4 Registration:
        #   completion_gate -> teachback_idle_guard -> teammate_idle
        assert chain == ["completion_gate", "teachback_idle_guard", "teammate_idle"], (
            f"TeammateIdle chain order broken: {chain}"
        )
