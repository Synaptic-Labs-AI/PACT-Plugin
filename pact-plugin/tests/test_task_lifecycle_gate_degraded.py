"""
Degraded-mode subprocess coverage for task_lifecycle_gate.py — the
crash-path health marker (#951).

Sibling to the in-process suite in test_task_lifecycle_gate.py (which
carries the runtime-stage marker legs, the journal-event pin, and the
error-bounding pin). This module exercises the IMPORT-stage crash path
the only way it can be exercised honestly: a real subprocess whose
`shared` package is deliberately broken, so the module-level
`except BaseException` gauntlet guard actually fires.

Contract under test (raw stdout, never a platform-relayed channel — the
platform's non-strict output schema strips unknown top-level keys):

  - On EVERY breakage vector the gate exits 0 (PostToolUse cannot deny)
    and the first stdout line is intact JSON carrying:
      * a top-level ``pactGateHealth`` machine marker
        {v, hook, status, stage, error} with the vector's exception
        type name inside the bounded ``error`` text,
      * ``hookSpecificOutput.hookEventName == "PostToolUse"`` (kept
        intact on every path — the schema-rejection defense),
      * a ``systemMessage`` mirroring the advisory text,
    plus a non-empty stderr diagnostic.
  - On a HEALTHY scaffold the output shapes are byte-identical to the
    pre-marker gate: no ``pactGateHealth``, no ``systemMessage``.

Deliberately NOT asserted here: the best-effort ``gate_health`` journal
event. All three breakage vectors break ``shared/__init__``-level
imports, which also kills the late lazy import inside the journal
emitter — those vectors are stdout-marker-only by design. The journal
channel's one pin lives in the in-process suite, on the runtime path
where the channel is contractually expected to work.

Scaffold/vector machinery is imported from tests.test_bootstrap_gate
(the #942/#950 degraded-mode suite) so the breakage-vector family stays
single-source. Both subprocess env vars (CLAUDE_CONFIG_DIR /
CLAUDE_PROJECT_DIR) are pointed inside tmp_path so no leg can touch the
real config tree, mirroring the live-probe containment recipe.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_bootstrap_gate import _BREAKAGE_VECTORS, _find_python39


# =============================================================================
# Helpers
# =============================================================================


def _make_gate_input(tool_name="TaskUpdate", tool_input=None,
                     session_id="lifecycle-degraded-session"):
    """PostToolUse-shaped stdin for the lifecycle gate (vs bootstrap_gate's
    PreToolUse shape): tool_name + tool_input + tool_response + session_id."""
    return {
        "tool_name": tool_name,
        "tool_input": tool_input or {"taskId": "1", "status": "in_progress"},
        "tool_response": {},
        "session_id": session_id,
    }


def _scaffold_env(tmp_path):
    """Subprocess env with both config-tree roots contained inside tmp_path
    so neither pact_context.init nor a journal write can ever resolve a
    path under the real ~/.claude."""
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(tmp_path / "scratch-config")
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path / "scratch-project")
    return env


def _run_degraded_subprocess(tmp_path, stdin_text, interpreter=None,
                             vector="syntax-broken-init"):
    """Run task_lifecycle_gate.py as a subprocess inside a scaffold whose
    `shared` package is deliberately broken per ``vector`` (a
    _BREAKAGE_VECTORS key; default = the canonical syntax-broken
    __init__.py), forcing the import-stage crash path. Returns the
    CompletedProcess.

    ``interpreter`` defaults to the dev interpreter (sys.executable); the
    py3.9-floor test passes a discovered 3.9 binary to exercise the
    stdlib-only crash path on the production system interpreter
    (GUI-launched macOS sessions run hooks on /usr/bin/python3 = 3.9.x).
    """
    hook_src = Path(__file__).parent.parent / "hooks" / "task_lifecycle_gate.py"
    scaffold = tmp_path / "scaffold"
    scaffold.mkdir(parents=True)
    (scaffold / "task_lifecycle_gate.py").write_text(
        hook_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    _BREAKAGE_VECTORS[vector][0](scaffold)
    return subprocess.run(
        [interpreter or sys.executable,
         str(scaffold / "task_lifecycle_gate.py")],
        input=stdin_text,
        capture_output=True,
        text=True,
        cwd=str(scaffold),
        timeout=10,
        env=_scaffold_env(tmp_path),
    )


def _run_healthy_subprocess(tmp_path, stdin_text):
    """Run task_lifecycle_gate.py as a subprocess inside a scaffold whose
    `shared` package is a full copy of the real one — the healthy-path
    twin of _run_degraded_subprocess, for the key-ABSENCE pins."""
    hooks_src = Path(__file__).parent.parent / "hooks"
    scaffold = tmp_path / "scaffold"
    scaffold.mkdir(parents=True)
    (scaffold / "task_lifecycle_gate.py").write_text(
        (hooks_src / "task_lifecycle_gate.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # shared/__init__.py eagerly imports the hooks/-level sibling pin_caps
    # (resolvable because the hook's own directory is on sys.path), so the
    # healthy scaffold needs both the package and that sibling module.
    shutil.copytree(hooks_src / "shared", scaffold / "shared")
    (scaffold / "pin_caps.py").write_text(
        (hooks_src / "pin_caps.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, str(scaffold / "task_lifecycle_gate.py")],
        input=stdin_text,
        capture_output=True,
        text=True,
        cwd=str(scaffold),
        timeout=10,
        env=_scaffold_env(tmp_path),
    )


def _assert_crash_marker_shape(result, expected_exc, stage="module imports"):
    """Shared content assertions for a crash-path subprocess result.

    rc==0 is asserted ONLY as the emit-contract precondition (stdout JSON
    is honored on exit 0) — always paired with content asserts, never rc
    alone: the health contract itself is content, by acceptance criterion.
    """
    assert result.returncode == 0, (
        f"stderr={result.stderr!r} stdout={result.stdout!r}"
    )
    out = json.loads(result.stdout.strip().splitlines()[0])

    # Machine marker: exact key set + field-by-field content.
    marker = out["pactGateHealth"]
    assert set(marker) == {"v", "hook", "status", "stage", "error"}
    assert marker["v"] == 1
    assert marker["hook"] == "task_lifecycle_gate"
    assert marker["status"] == "failed"
    assert marker["stage"] == stage
    assert expected_exc in marker["error"], (
        f"marker error must name the exception type ({expected_exc}) "
        f"so the failure is diagnosable; got {marker['error']!r}"
    )

    # Platform-facing keys stay intact (schema-rejection defense) and the
    # human channel mirrors the model channel.
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PostToolUse"
    assert stage in hso["additionalContext"]
    assert expected_exc in hso["additionalContext"]
    assert out["systemMessage"] == hso["additionalContext"]

    assert result.stderr.strip(), "stderr diagnostic line expected"
    return out


# =============================================================================
# Import-stage crash matrix — pactGateHealth PRESENT on every vector
# =============================================================================


class TestDegradedLifecycleGate:

    @pytest.mark.parametrize("vector", sorted(_BREAKAGE_VECTORS))
    def test_subprocess_breakage_vectors_emit_health_marker(
        self, tmp_path, vector,
    ):
        """The import gauntlet guard catches BaseException, so the crash
        output must be IDENTICAL in shape no matter HOW shared/ broke:
        syntax-broken __init__.py (SyntaxError), package absent
        (ModuleNotFoundError), or a failing from-import inside an
        otherwise-parseable __init__.py (ImportError). Every vector gets
        the full machine-recognizable pactGateHealth marker with the
        vector's exception type named in the bounded error text.

        NO journal assert here: all three vectors break shared/__init__-
        level imports, which also kills the journal emitter's late lazy
        import — these are the stdout-marker-only rows by design.
        """
        result = _run_degraded_subprocess(
            tmp_path, json.dumps(_make_gate_input()), vector=vector
        )
        _assert_crash_marker_shape(result, _BREAKAGE_VECTORS[vector][1])

    def test_subprocess_crash_output_first_line_is_complete_json(
        self, tmp_path,
    ):
        """Single-print discipline: the marker, advisory, and systemMessage
        all travel in ONE stdout JSON document — the first line parses and
        already carries every contract key, so a "first stdout line"
        consumer never needs to scan further."""
        result = _run_degraded_subprocess(
            tmp_path, json.dumps(_make_gate_input())
        )
        assert result.returncode == 0, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        first_line = result.stdout.strip().splitlines()[0]
        out = json.loads(first_line)
        assert {"hookSpecificOutput", "systemMessage", "pactGateHealth"} <= set(out)


# =============================================================================
# Healthy scaffold — pactGateHealth ABSENT (byte-identity pins)
# =============================================================================


class TestHealthyLifecycleGateUnchanged:

    def test_subprocess_healthy_suppress_output_is_byte_identical(
        self, tmp_path,
    ):
        """Healthy full path (imports OK, init runs, zero advisories) →
        stdout is EXACTLY {"suppressOutput": true}. Byte-identity is the
        strongest absence pin: it forbids pactGateHealth, systemMessage,
        and any other sibling key in one equality."""
        result = _run_healthy_subprocess(
            tmp_path, json.dumps(_make_gate_input())
        )
        assert result.returncode == 0, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        assert json.loads(result.stdout.strip()) == {"suppressOutput": True}

    def test_subprocess_healthy_short_circuit_suppress_is_byte_identical(
        self, tmp_path,
    ):
        """Non-Task tool short-circuit (deliberate by-design no-op, not a
        failure) keeps the same byte-identical suppress shape — the
        marker never decorates a healthy or skipped path."""
        result = _run_healthy_subprocess(
            tmp_path, json.dumps(_make_gate_input(tool_name="Read"))
        )
        assert result.returncode == 0, (
            f"stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        assert json.loads(result.stdout.strip()) == {"suppressOutput": True}


# =============================================================================
# py3.9 floor exercise (conditional on an available interpreter)
# =============================================================================


class TestPython39Floor:

    def test_crash_marker_emits_on_python39_floor(self, tmp_path):
        """The crash path (stdlib preamble + advisory helper + marker
        emit) must execute on the production system interpreter
        (GUI-launched macOS sessions run hooks on /usr/bin/python3 =
        3.9.x). Exercise the canonical syntax vector under a REAL 3.9
        interpreter when one is discoverable; the static AST floor guard
        (test_py39_annotation_compat.py) remains the unconditional gate
        when none is."""
        py39 = _find_python39()
        if py39 is None:
            pytest.skip(
                "no Python 3.9 interpreter discoverable (python3.9 on PATH "
                "or /usr/bin/python3 reporting 3.9.x); static floor guard "
                "test_py39_annotation_compat.py covers the syntax floor"
            )
        result = _run_degraded_subprocess(
            tmp_path, json.dumps(_make_gate_input()), interpreter=py39
        )
        _assert_crash_marker_shape(result, "SyntaxError")
