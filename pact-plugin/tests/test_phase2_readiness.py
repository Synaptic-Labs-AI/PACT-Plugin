"""
Smoke tests for scripts/check_teachback_phase2_readiness.py (#401 Commit #13).

This is an observational diagnostic, not a gate. The tests verify:
- Script is importable
- Script produces valid JSON output shape (Q5 schema)
- Script handles empty / missing sessions gracefully (fail-safe to
  not-ready, exit 0 since no false-positives were found)

Rich scenario coverage (true-positive vs false-positive classification)
is deferred to a follow-up once teachback_gate_advisory events are
flowing through real workflows in Phase 1.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_teachback_phase2_readiness.py"


def test_script_exists_and_executable() -> None:
    assert _SCRIPT.exists(), f"Script missing at {_SCRIPT}"
    assert _SCRIPT.stat().st_mode & 0o111, "Script is not executable (chmod +x)"


def test_script_runs_against_empty_sessions_dir(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--sessions-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"Expected exit 0 on empty sessions dir (no false-positives found); "
        f"got {result.returncode}. Stderr: {result.stderr}"
    )
    payload = json.loads(result.stdout)
    assert payload["ready"] is False, "Empty sessions dir is not-ready"
    assert payload["workflows_observed"] == 0
    assert payload["workflows_clean"] == 0
    assert payload["false_positives"] == []
    assert payload["criterion"].startswith("F10_")


def test_script_output_has_required_keys(tmp_path: Path) -> None:
    """Drift test: the Q5 output shape is locked in CONTENT-SCHEMAS.md.
    If the script drifts from the shape, downstream automation breaks.
    """
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--sessions-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=30,
    )
    payload = json.loads(result.stdout)
    for key in ("ready", "workflows_observed", "workflows_clean",
                "false_positives", "criterion"):
        assert key in payload, f"Missing required output key {key!r}"
    assert isinstance(payload["ready"], bool)
    assert isinstance(payload["workflows_observed"], int)
    assert isinstance(payload["workflows_clean"], int)
    assert isinstance(payload["false_positives"], list)
    assert isinstance(payload["criterion"], str)


def test_script_importable_as_module() -> None:
    """Ensure the script is importable without invoking main(), so unit-
    test authors can exercise assess_readiness() directly later.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "check_teachback_phase2_readiness", _SCRIPT
    )
    assert spec is not None, "Script is not importable as a module"
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert callable(getattr(module, "assess_readiness", None))
    assert callable(getattr(module, "main", None))
    assert getattr(module, "CRITERION_NAME", "").startswith("F10_")


def test_project_scope_with_missing_project_returns_empty(tmp_path: Path) -> None:
    """The --project flag restricts scope; if the project has no sessions,
    the script returns empty (exit 0)."""
    result = subprocess.run(
        [
            sys.executable, str(_SCRIPT),
            "--sessions-dir", str(tmp_path),
            "--project", "nonexistent-project",
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["workflows_observed"] == 0
