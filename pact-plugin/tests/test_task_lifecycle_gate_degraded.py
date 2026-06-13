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

Journal-channel split across this module: the three breakage vectors in
the crash matrix all break ``shared/__init__``-level imports, which also
kills the late lazy import inside the journal emitter — those vectors
are stdout-marker-only by design. The import-stage journal-SUCCESS
shape (a gauntlet-only module is broken while pact_context /
session_journal stay importable, and a prior hook already wrote the
session context file) is pinned separately in
TestImportStageJournalSuccess — the one subprocess leg that exercises
the crash handler's late stdin read on its success path. The runtime-path journal pin lives in the in-process suite, where
the channel is contractually expected to work.

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
# Import-stage crash with a LIVE journal channel — partial breakage
# =============================================================================


def _build_partial_breakage_scaffold(tmp_path):
    """Scaffold for the import-stage-crash-with-live-journal family:
    ``shared`` is intact EXCEPT for one gauntlet-only module
    (agent_handoff_marker) — the gauntlet from-import crashes
    (ModuleNotFoundError) while pact_context/session_journal stay
    importable — and a prior hook in the session already wrote the
    context file (the production precondition for the import-stage
    durable channel). Returns (scaffold, env, session_dir, session_id).

    Includes the premise assert (import-graph-rot guard): the journal
    channel must import WITHOUT agent_handoff_marker, or this vector no
    longer isolates "gauntlet broken, journal alive" and the family
    needs a different gauntlet-only module.
    """
    hooks_src = Path(__file__).parent.parent / "hooks"
    scaffold = tmp_path / "scaffold"
    scaffold.mkdir(parents=True)
    (scaffold / "task_lifecycle_gate.py").write_text(
        (hooks_src / "task_lifecycle_gate.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    shutil.copytree(hooks_src / "shared", scaffold / "shared")
    (scaffold / "pin_caps.py").write_text(
        (hooks_src / "pin_caps.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (scaffold / "shared" / "agent_handoff_marker.py").unlink()

    premise = subprocess.run(
        [sys.executable, "-c",
         "import shared.pact_context, shared.session_journal"],
        capture_output=True,
        text=True,
        cwd=str(scaffold),
        timeout=10,
    )
    assert premise.returncode == 0, (
        "vector premise broken: shared.pact_context/session_journal "
        "no longer import without agent_handoff_marker — pick a "
        f"different gauntlet-only module. {premise.stderr!r}"
    )

    # Context-file path mirrors init(): slug from CLAUDE_PROJECT_DIR's
    # basename + the stdin session_id.
    env = _scaffold_env(tmp_path)
    session_id = "lifecycle-degraded-session"
    session_dir = (
        Path(env["CLAUDE_CONFIG_DIR"]) / "pact-sessions"
        / Path(env["CLAUDE_PROJECT_DIR"]).name / session_id
    )
    session_dir.mkdir(parents=True)
    (session_dir / "pact-session-context.json").write_text(
        json.dumps({
            "team_name": "degraded-team",
            "session_id": session_id,
            "project_dir": env["CLAUDE_PROJECT_DIR"],
            "plugin_root": "",
            "started_at": "2026-01-01T00:00:00Z",
        }),
        encoding="utf-8",
    )
    return scaffold, env, session_dir, session_id


class TestImportStageJournalSuccess:

    def test_partial_breakage_writes_import_stage_journal_event(
        self, tmp_path,
    ):
        """Partial-breakage shape: the gauntlet crashes but the crash
        handler's lazy import of pact_context/session_journal succeeds,
        the handler reads the still-unconsumed stdin itself, and the
        durable gate_health event lands in the session journal at stage
        "module imports" — alongside the full stdout marker.

        This is the only leg exercising the late-stdin-read SUCCESS
        branch: every crash-matrix vector dies before the read, and the
        in-process runtime legs always hand input_data in directly.
        """
        scaffold, env, session_dir, session_id = (
            _build_partial_breakage_scaffold(tmp_path)
        )
        result = subprocess.run(
            [sys.executable, str(scaffold / "task_lifecycle_gate.py")],
            input=json.dumps(_make_gate_input(session_id=session_id)),
            capture_output=True,
            text=True,
            cwd=str(scaffold),
            timeout=10,
            env=env,
        )

        # Stdout marker contract — identical to the crash matrix.
        out = _assert_crash_marker_shape(result, "ModuleNotFoundError")

        # Durable channel: exactly one gate_health event, full field
        # set, same bounded error rendering as the stdout marker.
        journal = session_dir / "session-journal.jsonl"
        assert journal.exists(), (
            f"journal not written; stderr={result.stderr!r}"
        )
        events = [
            json.loads(line)
            for line in journal.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        health_events = [
            e for e in events if e.get("type") == "gate_health"
        ]
        assert len(health_events) == 1, (
            f"exactly one gate_health event expected: {health_events!r}"
        )
        event = health_events[0]
        assert set(event) == {
            "v", "type", "ts", "hook", "status", "stage", "error",
            "tool_name",
        }
        assert event["v"] == 1
        assert event["hook"] == "task_lifecycle_gate"
        assert event["status"] == "failed"
        assert event["stage"] == "module imports"
        assert "ModuleNotFoundError" in event["error"]
        assert event["tool_name"] == "TaskUpdate"
        assert event["ts"]
        assert event["error"] == out["pactGateHealth"]["error"]

        # Neither degradation disposition may fire on the success path.
        assert "gate_health journal emit" not in result.stderr, (
            "no skipped/unavailable disposition on the working "
            f"import-stage journal path: {result.stderr!r}"
        )

    def test_oversized_stdin_degrades_to_marker_only_without_hang(
        self, tmp_path,
    ):
        """The crash handler's late stdin read is capped: an over-cap
        frame truncates mid-JSON inside the handler, degrades to the
        "unavailable" stderr disposition, and the stdout floor marker
        stays fully intact — no hang, no raise, no journal write, even
        though the journal channel itself is alive (same live-channel
        scaffold as the success leg, so the cap — not a dead channel —
        is what stops the write)."""
        scaffold, env, session_dir, session_id = (
            _build_partial_breakage_scaffold(tmp_path)
        )
        frame = _make_gate_input(session_id=session_id)
        # Pad well past the read cap with valid JSON so truncation —
        # not malformed input — is what breaks the parse.
        frame["pad"] = "a" * (9 * 1024 * 1024)
        result = subprocess.run(
            [sys.executable, str(scaffold / "task_lifecycle_gate.py")],
            input=json.dumps(frame),
            capture_output=True,
            text=True,
            cwd=str(scaffold),
            timeout=30,
            env=env,
        )

        # Floor marker contract fully intact despite the oversized frame.
        _assert_crash_marker_shape(result, "ModuleNotFoundError")

        # Best-effort channel degrades inside its guard: unavailable
        # disposition on stderr, and nothing reaches the journal.
        assert "gate_health journal emit unavailable" in result.stderr, (
            f"over-cap frame must degrade in-guard: {result.stderr!r}"
        )
        assert not (session_dir / "session-journal.jsonl").exists(), (
            "truncated frame must not produce a journal event"
        )


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


# =============================================================================
# BaseException-breadth regression: a module-level sys.exit() / KeyboardInterrupt
# AT IMPORT time is a BaseException that is NOT an Exception subclass. The
# import-gauntlet guard (task_lifecycle_gate.py `except BaseException`) and the
# crash handler's own `_emit_gate_health_event` guard are deliberately broad so
# such a vector is caught and the floor marker still prints at exit 0. None of
# the _BREAKAGE_VECTORS exercises this (all three raise Exception subclasses),
# so narrowing either guard to `except Exception` would silently re-introduce
# the #951 masking (SystemExit/KeyboardInterrupt escapes → nonzero exit →
# stdout JSON ignored → no health marker). These pins lock the breadth.
#
# Counter-test: narrow the import-gauntlet guard to `except Exception` → the
# crash escapes the gauntlet, nothing prints, exit nonzero. Separately narrow
# the `_emit_gate_health_event` guard to `except Exception` → the marker prints
# but the lazy re-import's SystemExit escapes before sys.exit(0), so the
# process exits nonzero and the stdout marker is ignored. Either narrowing
# fails the `returncode == 0` + marker-present assertions below.
# =============================================================================


def _break_shared_sys_exit(scaffold):
    """shared/__init__.py calls sys.exit(1) at import time → SystemExit (a
    BaseException, NOT an Exception) propagates out of the gauntlet import."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "import sys\nsys.exit(1)\n", encoding="utf-8"
    )


def _break_shared_keyboard_interrupt(scaffold):
    """shared/__init__.py raises KeyboardInterrupt at import time → a
    BaseException that `except Exception` would NOT catch."""
    (scaffold / "shared").mkdir(parents=True)
    (scaffold / "shared" / "__init__.py").write_text(
        "raise KeyboardInterrupt('simulated at import')\n", encoding="utf-8"
    )


def _run_with_breaker(tmp_path, stdin_text, breaker):
    """Copy the lifecycle hook into a scaffold, apply an arbitrary breaker
    (not constrained to the _BREAKAGE_VECTORS dict), run as a subprocess with
    both config roots sandboxed inside tmp_path."""
    hook_src = Path(__file__).parent.parent / "hooks" / "task_lifecycle_gate.py"
    scaffold = tmp_path / "scaffold"
    scaffold.mkdir(parents=True)
    (scaffold / "task_lifecycle_gate.py").write_text(
        hook_src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    breaker(scaffold)
    return subprocess.run(
        [sys.executable, str(scaffold / "task_lifecycle_gate.py")],
        input=stdin_text,
        capture_output=True,
        text=True,
        cwd=str(scaffold),
        timeout=10,
        env=_scaffold_env(tmp_path),
    )


class TestBaseExceptionBreadthAtImport:

    @pytest.mark.parametrize(
        "breaker,expected_exc",
        [
            (_break_shared_sys_exit, "SystemExit"),
            (_break_shared_keyboard_interrupt, "KeyboardInterrupt"),
        ],
        ids=["import-sys-exit", "import-keyboard-interrupt"],
    )
    def test_non_exception_baseexception_at_import_still_emits_marker(
        self, tmp_path, breaker, expected_exc,
    ):
        """A module-level sys.exit(1) / KeyboardInterrupt at import is caught
        by the gauntlet's `except BaseException`, so the full pactGateHealth
        marker still emits at exit 0 with the BaseException type named in the
        bounded error text. Narrowing the gauntlet (or the
        _emit_gate_health_event) guard to `except Exception` re-masks: the
        crash escapes → nonzero exit → the stdout marker is ignored."""
        result = _run_with_breaker(
            tmp_path, json.dumps(_make_gate_input()), breaker
        )
        _assert_crash_marker_shape(result, expected_exc)
