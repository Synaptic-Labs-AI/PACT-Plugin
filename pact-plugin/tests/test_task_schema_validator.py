"""Tests for pact-plugin/hooks/task_schema_validator.py (#401 Commit #5).

Covers: _is_agent_dispatch_task pass-through predicate, validate_task_schema
rules, stdin handling + disk-fallback read, exit-2 on reject, fail-open on
malformed stdin / JSON / exceptions.
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
# shared/ directory import path matches other test files
_SHARED_DIR = _HOOKS_DIR / "shared"
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))

import task_schema_validator as validator  # noqa: E402
from task_schema_validator import (  # noqa: E402
    _AGENT_PREFIXES,
    _SIGNAL_AGENT_PREFIXES,
    _is_agent_dispatch_task,
    _variety_missing_dimensions,
    validate_task_schema,
)


# ---------------------------------------------------------------------------
# _is_agent_dispatch_task
# ---------------------------------------------------------------------------

class TestIsAgentDispatchTask:
    """Cheap O(1) stdin+metadata predicate. No disk I/O."""

    @pytest.mark.parametrize("prefix", [
        "preparer:", "architect:",
        "backend-coder:", "frontend-coder:",
        "database-engineer:", "devops-engineer:", "n8n:",
        "test-engineer:", "security-engineer:", "qa-engineer:",
    ])
    def test_agent_prefix_subjects_pass(self, prefix):
        input_data = {"task_subject": f"{prefix} do the thing"}
        assert _is_agent_dispatch_task(input_data, metadata={}) is True

    @pytest.mark.parametrize("prefix", ["secretary:", "auditor:"])
    def test_signal_agent_subjects_bypass(self, prefix):
        input_data = {"task_subject": f"{prefix} observe"}
        assert _is_agent_dispatch_task(input_data, metadata={}) is False

    @pytest.mark.parametrize("subject", [
        "Implement user auth",          # feature-level
        "PREPARE: teachback-gate-401",   # phase-level
        "ARCHITECT: design ...",
        "CODE: implement ...",
        "TEST: verify ...",
        "random note",
        "",
    ])
    def test_non_agent_subjects_bypass(self, subject):
        input_data = {"task_subject": subject}
        assert _is_agent_dispatch_task(input_data, metadata={}) is False

    def test_mixed_case_prefix_bypasses(self):
        # Phase/user-authored subjects use ALL-CAPS or mixed-case labels
        # ("ARCHITECT:", "Backend-Coder:"). Agent dispatches use strict
        # lowercase ("architect:", "backend-coder:"). Only lowercase
        # leading tokens count as agent dispatches to avoid phase/agent
        # label collision (ARCHITECT phase vs architect agent).
        assert _is_agent_dispatch_task(
            {"task_subject": "Backend-Coder: do something"},
            metadata={},
        ) is False
        assert _is_agent_dispatch_task(
            {"task_subject": "ARCHITECT: design"},
            metadata={},
        ) is False
        # But lowercase form matches
        assert _is_agent_dispatch_task(
            {"task_subject": "architect: design"},
            metadata={},
        ) is True

    @pytest.mark.parametrize("flag", ["skipped", "stalled", "terminated"])
    def test_lifecycle_flags_bypass(self, flag):
        input_data = {"task_subject": "backend-coder: x"}
        metadata = {flag: True}
        assert _is_agent_dispatch_task(input_data, metadata) is False

    @pytest.mark.parametrize("type_value", ["blocker", "algedonic"])
    def test_signal_task_types_bypass(self, type_value):
        input_data = {"task_subject": "backend-coder: x"}
        metadata = {"type": type_value}
        assert _is_agent_dispatch_task(input_data, metadata) is False

    def test_completion_type_signal_bypasses(self):
        input_data = {"task_subject": "backend-coder: x"}
        metadata = {"completion_type": "signal"}
        assert _is_agent_dispatch_task(input_data, metadata) is False

    def test_non_string_subject_bypasses(self):
        input_data = {"task_subject": None}
        assert _is_agent_dispatch_task(input_data, metadata={}) is False

    def test_missing_subject_key_bypasses(self):
        assert _is_agent_dispatch_task({}, metadata={}) is False

    def test_non_dict_metadata_tolerated(self):
        input_data = {"task_subject": "backend-coder: x"}
        # Non-dict metadata is tolerated and treated as empty
        assert _is_agent_dispatch_task(input_data, metadata="not-a-dict") is True  # type: ignore[arg-type]

    def test_agent_prefixes_tuple_matches_findactiveagents(self):
        """Drift guard — keep _AGENT_PREFIXES aligned with
        shared.task_utils.find_active_agents:142-155."""
        from shared.task_utils import find_active_agents  # noqa: F401
        # The 10 non-signal agent-type prefixes (excluding auditor/secretary
        # which live in _SIGNAL_AGENT_PREFIXES) must match.
        assert _AGENT_PREFIXES == (
            "preparer:", "architect:",
            "backend-coder:", "frontend-coder:",
            "database-engineer:", "devops-engineer:", "n8n:",
            "test-engineer:", "security-engineer:", "qa-engineer:",
        )
        assert _SIGNAL_AGENT_PREFIXES == ("secretary:", "auditor:")


# ---------------------------------------------------------------------------
# _variety_missing_dimensions
# ---------------------------------------------------------------------------

class TestVarietyMissingDimensions:
    def test_full_variety_returns_empty(self):
        v = {"novelty": 2, "scope": 2, "uncertainty": 2, "risk": 1, "total": 7}
        assert _variety_missing_dimensions(v) == []

    def test_missing_novelty(self):
        v = {"scope": 2, "uncertainty": 2, "risk": 1, "total": 7}
        assert _variety_missing_dimensions(v) == ["novelty"]

    def test_missing_all_dimensions(self):
        v = {"total": 7}
        assert _variety_missing_dimensions(v) == [
            "novelty", "scope", "uncertainty", "risk"
        ]

    def test_none_valued_dimension_treated_as_missing(self):
        v = {"novelty": 2, "scope": None, "uncertainty": 2, "risk": 1, "total": 7}
        assert _variety_missing_dimensions(v) == ["scope"]


# ---------------------------------------------------------------------------
# validate_task_schema
# ---------------------------------------------------------------------------

def _valid_full_variety(total: int = 9) -> dict:
    # Make dimensions sum to `total` for consistency; validator doesn't
    # enforce sum here but keeps test data honest.
    return {
        "total": total,
        "novelty": max(total // 4, 1),
        "scope": max(total // 4, 1),
        "uncertainty": max(total // 4, 1),
        "risk": total - 3 * max(total // 4, 1),
    }


class TestValidateTaskSchema:
    """Validation rules — fail-open on malformed input, reject-only on failure."""

    def test_below_threshold_passes_without_schema(self):
        # variety.total=5 (below threshold 7) — no schema enforcement
        meta = {"variety": {"total": 5}}
        assert validate_task_schema(meta, "backend-coder: small task") is None

    def test_at_threshold_with_dims_passes(self):
        meta = {"variety": _valid_full_variety(7)}
        # variety=7 (>= threshold) but < full-protocol (9) → required_scope_items not required
        assert validate_task_schema(meta, "backend-coder: task") is None

    def test_full_protocol_with_scope_items_passes(self):
        meta = {
            "variety": _valid_full_variety(9),
            "required_scope_items": ["item_1", "item_2"],
        }
        assert validate_task_schema(meta, "backend-coder: task") is None

    def test_variety_missing_rejects(self):
        meta = {}  # no variety at all
        error = validate_task_schema(meta, "backend-coder: x", task_id="17")
        assert error is not None
        assert "metadata.variety.total" in error
        assert "17" in error

    def test_variety_total_missing_rejects(self):
        meta = {"variety": {"novelty": 2, "scope": 2}}
        error = validate_task_schema(meta, "backend-coder: x")
        assert error is not None
        assert "metadata.variety.total" in error

    def test_variety_total_bool_rejected(self):
        # bool is int subclass — reject explicitly (PR #416 pattern)
        meta = {"variety": {"total": True, "novelty": 2, "scope": 2, "uncertainty": 2, "risk": 1}}
        error = validate_task_schema(meta, "backend-coder: x")
        assert error is not None

    def test_variety_total_non_int_rejected(self):
        meta = {"variety": {"total": "seven"}}
        error = validate_task_schema(meta, "backend-coder: x")
        assert error is not None

    def test_missing_dimensions_rejects(self):
        meta = {"variety": {"total": 8, "novelty": 2}}
        error = validate_task_schema(meta, "backend-coder: x")
        assert error is not None
        assert "scope" in error
        assert "uncertainty" in error
        assert "risk" in error

    def test_full_protocol_empty_scope_items_rejects(self):
        meta = {
            "variety": _valid_full_variety(9),
            "required_scope_items": [],
        }
        error = validate_task_schema(meta, "backend-coder: x")
        assert error is not None
        assert "required_scope_items" in error

    def test_full_protocol_missing_scope_items_rejects(self):
        meta = {"variety": _valid_full_variety(10)}
        error = validate_task_schema(meta, "backend-coder: x")
        assert error is not None
        assert "required_scope_items" in error

    def test_full_protocol_non_list_scope_items_rejects(self):
        meta = {
            "variety": _valid_full_variety(9),
            "required_scope_items": "a,b,c",
        }
        error = validate_task_schema(meta, "backend-coder: x")
        assert error is not None

    def test_variety_non_dict_rejected(self):
        meta = {"variety": 9}  # int, not dict
        error = validate_task_schema(meta, "backend-coder: x")
        assert error is not None

    def test_malformed_metadata_fail_open(self):
        # validate_task_schema's internal try/except swallows unexpected
        # exceptions — caller exits 0. We simulate an exception by passing
        # a dict-like that raises on .get().
        class Explode:
            def get(self, *_args, **_kwargs):
                raise RuntimeError("boom")

        assert validate_task_schema(Explode(), "backend-coder: x") is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# main() — stdin handling, exit codes
# ---------------------------------------------------------------------------

def _run_main_with_stdin(
    monkeypatch,
    capsys,
    stdin_payload,
    *,
    task_on_disk: dict | None = None,
):
    """Helper to run validator.main() with synthetic stdin and an
    optional task_data returned from _read_task_json."""
    if isinstance(stdin_payload, (dict, list)):
        raw = json.dumps(stdin_payload)
    else:
        raw = stdin_payload  # str passthrough for malformed tests

    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))

    read_mock = patch.object(
        validator, "_read_task_json",
        return_value=(task_on_disk or {}),
    )
    with read_mock, pytest.raises(SystemExit) as exc:
        validator.main()
    captured = capsys.readouterr()
    return exc.value.code, captured.out, captured.err


class TestMainStdinHandling:
    def test_malformed_stdin_fail_open(self, monkeypatch, capsys):
        code, out, err = _run_main_with_stdin(
            monkeypatch, capsys, stdin_payload="{not-json}",
        )
        assert code == 0
        assert '"suppressOutput": true' in out

    def test_empty_stdin_fail_open(self, monkeypatch, capsys):
        code, out, err = _run_main_with_stdin(
            monkeypatch, capsys, stdin_payload="",
        )
        assert code == 0

    def test_non_dict_stdin_fail_open(self, monkeypatch, capsys):
        code, out, err = _run_main_with_stdin(
            monkeypatch, capsys, stdin_payload=["not", "a", "dict"],
        )
        assert code == 0


class TestMainPassThrough:
    def test_non_agent_subject_passes(self, monkeypatch, capsys):
        code, out, err = _run_main_with_stdin(
            monkeypatch, capsys,
            stdin_payload={"task_id": "1", "task_subject": "Implement auth"},
            task_on_disk={"metadata": {}},
        )
        assert code == 0
        assert err == ""

    def test_secretary_subject_passes(self, monkeypatch, capsys):
        code, out, err = _run_main_with_stdin(
            monkeypatch, capsys,
            stdin_payload={"task_id": "1", "task_subject": "secretary: harvest"},
            task_on_disk={"metadata": {}},
        )
        assert code == 0

    def test_blocker_type_passes(self, monkeypatch, capsys):
        code, out, err = _run_main_with_stdin(
            monkeypatch, capsys,
            stdin_payload={"task_id": "1", "task_subject": "backend-coder: x"},
            task_on_disk={"metadata": {"type": "blocker"}},
        )
        assert code == 0


class TestMainRejection:
    def test_agent_task_missing_variety_rejects_exit_2(self, monkeypatch, capsys):
        code, out, err = _run_main_with_stdin(
            monkeypatch, capsys,
            stdin_payload={"task_id": "17", "task_subject": "backend-coder: implement"},
            task_on_disk={"metadata": {}},
        )
        assert code == 2
        assert "metadata.variety.total" in err
        assert "17" in err

    def test_agent_task_full_protocol_missing_scope_items_rejects(
        self, monkeypatch, capsys
    ):
        meta = {"variety": _valid_full_variety(10)}
        code, out, err = _run_main_with_stdin(
            monkeypatch, capsys,
            stdin_payload={"task_id": "20", "task_subject": "backend-coder: big task"},
            task_on_disk={"metadata": meta},
        )
        assert code == 2
        assert "required_scope_items" in err

    def test_agent_task_well_formed_passes(self, monkeypatch, capsys):
        meta = {
            "variety": _valid_full_variety(9),
            "required_scope_items": ["scope_a", "scope_b"],
        }
        code, out, err = _run_main_with_stdin(
            monkeypatch, capsys,
            stdin_payload={"task_id": "25", "task_subject": "backend-coder: fine"},
            task_on_disk={"metadata": meta},
        )
        assert code == 0
        assert '"suppressOutput": true' in out

    def test_agent_task_below_threshold_passes(self, monkeypatch, capsys):
        meta = {"variety": {"total": 5}}  # below threshold 7
        code, out, err = _run_main_with_stdin(
            monkeypatch, capsys,
            stdin_payload={"task_id": "30", "task_subject": "backend-coder: small"},
            task_on_disk={"metadata": meta},
        )
        assert code == 0


class TestMainExceptionFailOpen:
    def test_unhandled_exception_in_validation_fails_open(
        self, monkeypatch, capsys
    ):
        # Force _read_task_json to raise, exercising the outer try/except
        def boom(*args, **kwargs):
            raise RuntimeError("disk exploded")

        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
            {"task_id": "1", "task_subject": "backend-coder: x"}
        )))
        monkeypatch.setattr(validator, "_read_task_json", boom)

        with pytest.raises(SystemExit) as exc:
            validator.main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "task_schema_validator" in captured.err


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------

class TestModuleSurface:
    def test_main_is_public(self):
        assert callable(validator.main)

    def test_validate_task_schema_is_public(self):
        assert callable(validate_task_schema)

    def test_is_agent_dispatch_task_is_public(self):
        assert callable(_is_agent_dispatch_task)

    def test_probe_module_deleted(self):
        """Regression: _task_created_probe.py must not ship in #5+."""
        probe = Path(__file__).resolve().parent.parent / "hooks" / "_task_created_probe.py"
        assert not probe.exists(), (
            "Commit #5 must delete _task_created_probe.py per "
            "COMMIT-SEQUENCE.md §Commit #5 (probe lifecycle)"
        )
