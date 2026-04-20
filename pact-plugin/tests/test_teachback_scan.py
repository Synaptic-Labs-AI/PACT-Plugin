"""Tests for shared/teachback_scan.py (#401 Commit #7).

Covers: _classify_task_state precedence, is_exempt_agent, protocol_level
classification, carve-out bypasses, scan_teachback_state aggregation with
ALL-match semantics, fail-open on OS / JSON errors.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))
_SHARED_DIR = _HOOKS_DIR / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

from shared import teachback_scan  # noqa: E402
from shared.teachback_scan import (  # noqa: E402
    _EXEMPT_AGENTS,
    _classify_task_state,
    _protocol_level,
    is_exempt_agent,
    scan_teachback_state,
)


# ---------------------------------------------------------------------------
# is_exempt_agent
# ---------------------------------------------------------------------------

class TestIsExemptAgent:
    @pytest.mark.parametrize("name", [
        "secretary", "SECRETARY", "Secretary",
        "pact-secretary", "Pact-Secretary",
        "auditor", "AUDITOR",
        "pact-auditor", "Pact-Auditor",
    ])
    def test_exempt(self, name):
        assert is_exempt_agent(name) is True

    @pytest.mark.parametrize("name", [
        "backend-coder-1", "frontend-coder-2", "architect",
        "preparer", "test-engineer", "qa-engineer",
        "",
    ])
    def test_not_exempt(self, name):
        assert is_exempt_agent(name) is False

    def test_non_string_safe(self):
        assert is_exempt_agent(None) is False  # type: ignore[arg-type]
        assert is_exempt_agent(123) is False  # type: ignore[arg-type]

    def test_exempt_set_matches_teachback_check(self):
        """Drift guard: _EXEMPT_AGENTS must equal teachback_check._EXEMPT_AGENTS."""
        from teachback_check import _EXEMPT_AGENTS as CHECK_EXEMPT

        assert _EXEMPT_AGENTS == CHECK_EXEMPT


# ---------------------------------------------------------------------------
# _protocol_level
# ---------------------------------------------------------------------------

class TestProtocolLevel:
    def test_exempt_below_threshold(self):
        assert _protocol_level(5, []) == "exempt"

    def test_exempt_just_below_threshold(self):
        assert _protocol_level(6, []) == "exempt"

    def test_simplified_at_threshold_no_items(self):
        assert _protocol_level(7, []) == "simplified"

    def test_simplified_with_one_item(self):
        assert _protocol_level(8, ["item_a"]) == "simplified"

    def test_full_when_two_items(self):
        assert _protocol_level(7, ["a", "b"]) == "full"

    def test_full_at_variety_9(self):
        assert _protocol_level(9, []) == "full"

    def test_full_at_high_variety(self):
        assert _protocol_level(16, []) == "full"

    def test_none_items_tolerated(self):
        assert _protocol_level(8, None) == "simplified"

    def test_non_list_items_treated_as_zero(self):
        assert _protocol_level(8, "bad") == "simplified"  # type: ignore[arg-type]

    def test_bool_variety_rejected(self):
        # bool is int subclass but semantically wrong
        assert _protocol_level(True, []) == "exempt"  # type: ignore[arg-type]

    def test_non_int_variety_rejected(self):
        assert _protocol_level("seven", []) == "exempt"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _classify_task_state — precedence and state inference
# ---------------------------------------------------------------------------

def _simplified_submit():
    return {
        "understanding": "I'll implement the variety scoring primitives per the architect spec.",
        "first_action": {"action": "file.py:123", "expected_signal": "pytest passes"},
    }


def _full_submit():
    s = _simplified_submit()
    s["most_likely_wrong"] = {
        "assumption": "The variety scorer integrates cleanly without edge cases",
        "consequence": "If wrong, gate-threshold decisions produce wrong protocol level",
    }
    s["least_confident_item"] = {
        "item": "Exact semantics of bool-in-int rejection across dimensions",
        "current_plan": "Mirror session_journal isinstance check pattern",
        "failure_mode": "Schema silently accepts True as variety.total",
    }
    return s


class TestClassifyTaskState:
    def test_no_submit_pending(self):
        reason, state = _classify_task_state({}, "full")
        assert reason == "missing_submit"
        assert state == "teachback_pending"

    def test_valid_simplified_submit_under_review(self):
        meta = {"teachback_submit": _simplified_submit()}
        reason, state = _classify_task_state(meta, "simplified")
        assert reason == "awaiting_approval"
        assert state == "teachback_under_review"

    def test_valid_full_submit_under_review(self):
        meta = {"teachback_submit": _full_submit()}
        reason, state = _classify_task_state(meta, "full")
        assert reason == "awaiting_approval"
        assert state == "teachback_under_review"

    def test_invalid_submit_detected(self):
        meta = {"teachback_submit": {"understanding": "short"}}  # missing first_action
        reason, state = _classify_task_state(meta, "simplified")
        assert reason == "invalid_submit"
        assert state == "teachback_pending"

    def test_full_protocol_simplified_submit_is_invalid(self):
        # Simplified submit under full protocol — missing most_likely_wrong etc.
        meta = {"teachback_submit": _simplified_submit()}
        reason, state = _classify_task_state(meta, "full")
        assert reason == "invalid_submit"

    def test_approved_with_empty_unaddressed_active(self):
        meta = {
            "teachback_submit": _full_submit(),
            "teachback_approved": {
                "conditions_met": {"addressed": ["a"], "unaddressed": []},
            },
        }
        reason, state = _classify_task_state(meta, "full")
        assert reason == ""
        assert state == "active"

    def test_approved_missing_conditions_met_active(self):
        # approved present but no conditions_met key → treat as empty unaddressed → active
        meta = {"teachback_approved": {"verdict": "ok"}}
        reason, state = _classify_task_state(meta, "full")
        assert reason == ""
        assert state == "active"

    def test_approved_with_unaddressed_auto_downgrade(self):
        meta = {
            "teachback_approved": {
                "conditions_met": {"addressed": ["a"], "unaddressed": ["b", "c"]},
            },
        }
        reason, state = _classify_task_state(meta, "full")
        assert reason == "unaddressed_items"
        assert state == "teachback_correcting"

    def test_corrections_take_precedence_over_approved(self):
        # Cooperative-write invariant #2 — corrections wins
        meta = {
            "teachback_corrections": {"issues": ["fix thing"]},
            "teachback_approved": {"conditions_met": {"unaddressed": []}},
        }
        reason, state = _classify_task_state(meta, "full")
        assert reason == "corrections_pending"
        assert state == "teachback_correcting"

    def test_empty_corrections_dict_ignored(self):
        # An empty dict is falsy for corrections logic — falls through
        meta = {"teachback_corrections": {}}
        reason, state = _classify_task_state(meta, "simplified")
        assert reason == "missing_submit"

    def test_non_dict_submit_treated_as_invalid(self):
        meta = {"teachback_submit": "just a string"}
        reason, state = _classify_task_state(meta, "simplified")
        assert reason == "invalid_submit"


# ---------------------------------------------------------------------------
# scan_teachback_state — disk scan aggregation
# ---------------------------------------------------------------------------

def _write_task(tasks_dir: Path, task_id: str, owner: str, status: str = "in_progress",
                metadata: dict | None = None):
    data = {
        "id": task_id,
        "subject": f"backend-coder: task {task_id}",
        "owner": owner,
        "status": status,
        "metadata": metadata or {},
    }
    (tasks_dir / f"{task_id}.json").write_text(json.dumps(data), encoding="utf-8")


def _valid_variety(total=10):
    return {"total": total, "novelty": 2, "scope": 3, "uncertainty": 3, "risk": total - 8}


class TestScanTeachbackStateBasics:
    def test_missing_team_dir_fail_open(self, tmp_path):
        result = scan_teachback_state("coder-1", "pact-missing", tasks_base_dir=str(tmp_path))
        assert result["task_count"] == 0
        assert result["all_active"] is True

    def test_no_agent_or_team_fail_open(self, tmp_path):
        assert scan_teachback_state("", "pact-test", tasks_base_dir=str(tmp_path))["all_active"] is True
        assert scan_teachback_state("coder-1", "", tasks_base_dir=str(tmp_path))["all_active"] is True

    def test_no_in_progress_tasks(self, tmp_path):
        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        _write_task(team_dir, "1", "coder-1", status="completed")
        result = scan_teachback_state("coder-1", "pact-test", tasks_base_dir=str(tmp_path))
        assert result["task_count"] == 0

    def test_filters_by_owner(self, tmp_path):
        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        _write_task(team_dir, "1", "coder-2", metadata={"variety": _valid_variety()})
        _write_task(team_dir, "2", "coder-1", metadata={"variety": _valid_variety(),
                                                         "teachback_submit": _full_submit()})
        result = scan_teachback_state("coder-1", "pact-test", tasks_base_dir=str(tmp_path))
        assert result["task_count"] == 1


class TestScanTeachbackStateCarveOuts:
    def test_low_variety_task_bypasses(self, tmp_path):
        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        # variety=5 (below threshold 7) — carve-out; doesn't contribute to failing
        _write_task(team_dir, "1", "coder-1",
                     metadata={"variety": {"total": 5, "novelty": 1, "scope": 2, "uncertainty": 1, "risk": 1}})
        result = scan_teachback_state("coder-1", "pact-test", tasks_base_dir=str(tmp_path))
        assert result["task_count"] == 1
        assert result["all_active"] is True  # carve-out passes

    def test_blocker_type_bypasses(self, tmp_path):
        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        _write_task(team_dir, "1", "coder-1",
                     metadata={"type": "blocker", "variety": _valid_variety()})
        result = scan_teachback_state("coder-1", "pact-test", tasks_base_dir=str(tmp_path))
        assert result["all_active"] is True

    def test_skipped_bypasses(self, tmp_path):
        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        _write_task(team_dir, "1", "coder-1",
                     metadata={"skipped": True, "variety": _valid_variety()})
        assert scan_teachback_state("coder-1", "pact-test",
                                     tasks_base_dir=str(tmp_path))["all_active"] is True


class TestScanTeachbackStateAllMatch:
    """ALL-match semantics — one failing task taints the whole scan."""

    def test_all_active_passes(self, tmp_path):
        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        approved_meta = {
            "variety": _valid_variety(),
            "teachback_approved": {"conditions_met": {"unaddressed": []}},
        }
        _write_task(team_dir, "1", "coder-1", metadata=approved_meta)
        _write_task(team_dir, "2", "coder-1", metadata=approved_meta)

        result = scan_teachback_state("coder-1", "pact-test", tasks_base_dir=str(tmp_path))
        assert result["task_count"] == 2
        assert result["all_active"] is True
        assert result["first_failing_task_id"] == ""

    def test_one_failing_taints_all(self, tmp_path):
        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        approved_meta = {
            "variety": _valid_variety(),
            "teachback_approved": {"conditions_met": {"unaddressed": []}},
        }
        pending_meta = {"variety": _valid_variety()}  # no submit → pending
        _write_task(team_dir, "1", "coder-1", metadata=approved_meta)
        _write_task(team_dir, "2", "coder-1", metadata=pending_meta)

        result = scan_teachback_state("coder-1", "pact-test", tasks_base_dir=str(tmp_path))
        assert result["task_count"] == 2
        assert result["all_active"] is False
        # sorted iteration: task 2 is the failing one
        assert result["first_failing_task_id"] == "2"
        assert result["first_failing_reason"] == "missing_submit"

    def test_deterministic_first_failing_via_sort(self, tmp_path):
        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        pending_meta = {"variety": _valid_variety()}
        # Create 5 failing tasks — first_failing_task_id should be "1"
        for tid in ["3", "1", "5", "2", "4"]:
            _write_task(team_dir, tid, "coder-1", metadata=pending_meta)

        result = scan_teachback_state("coder-1", "pact-test", tasks_base_dir=str(tmp_path))
        assert result["first_failing_task_id"] == "1"


class TestScanTeachbackStateReasons:
    def _scan_single(self, tmp_path, metadata):
        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        _write_task(team_dir, "1", "coder-1", metadata=metadata)
        return scan_teachback_state("coder-1", "pact-test", tasks_base_dir=str(tmp_path))

    def test_pending_reason(self, tmp_path):
        result = self._scan_single(tmp_path, {"variety": _valid_variety()})
        assert result["first_failing_reason"] == "missing_submit"

    def test_invalid_submit_reason(self, tmp_path):
        meta = {"variety": _valid_variety(),
                "teachback_submit": {"understanding": "short"}}
        result = self._scan_single(tmp_path, meta)
        assert result["first_failing_reason"] == "invalid_submit"

    def test_awaiting_approval_reason(self, tmp_path):
        meta = {
            "variety": _valid_variety(),
            "teachback_submit": _full_submit(),
            "required_scope_items": ["a", "b"],
        }
        result = self._scan_single(tmp_path, meta)
        assert result["first_failing_reason"] == "awaiting_approval"
        assert result["first_failing_protocol_level"] == "full"

    def test_unaddressed_items_reason(self, tmp_path):
        meta = {
            "variety": _valid_variety(),
            "teachback_approved": {"conditions_met": {"unaddressed": ["x"]}},
        }
        result = self._scan_single(tmp_path, meta)
        assert result["first_failing_reason"] == "unaddressed_items"

    def test_corrections_pending_reason(self, tmp_path):
        meta = {
            "variety": _valid_variety(),
            "teachback_corrections": {"issues": ["fix"]},
        }
        result = self._scan_single(tmp_path, meta)
        assert result["first_failing_reason"] == "corrections_pending"


class TestScanTeachbackStateFailOpen:
    def test_corrupted_json_skipped(self, tmp_path):
        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / "bad.json").write_text("{{{not json")
        result = scan_teachback_state("coder-1", "pact-test", tasks_base_dir=str(tmp_path))
        # Corrupted file is skipped; no tasks found; allow
        assert result["task_count"] == 0
        assert result["all_active"] is True

    def test_non_dict_task_file_skipped(self, tmp_path):
        team_dir = tmp_path / "pact-test"
        team_dir.mkdir(parents=True)
        (team_dir / "1.json").write_text(json.dumps([1, 2, 3]))  # list not dict
        result = scan_teachback_state("coder-1", "pact-test", tasks_base_dir=str(tmp_path))
        assert result["task_count"] == 0


class TestScanTeachbackStateStructural:
    def test_default_summary_shape(self):
        result = scan_teachback_state("", "", tasks_base_dir="/nonexistent")
        assert set(result.keys()) == {
            "task_count",
            "first_failing_task_id",
            "first_failing_reason",
            "first_failing_metadata",
            "first_failing_protocol_level",
            "all_active",
        }
