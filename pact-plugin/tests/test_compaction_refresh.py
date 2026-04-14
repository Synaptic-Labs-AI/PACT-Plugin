"""
Integration tests for the SessionStart hook (compaction_refresh.py).

Tests refresh detection and instruction injection after compaction.
"""

import json
import os
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
# Add tests directory to path for helpers module
sys.path.insert(0, str(Path(__file__).parent))


class TestGetEncodedProjectPath:
    """Tests for refresh.checkpoint_builder.get_encoded_project_path.

    Post-#413: the deprecated precompact_refresh.py hook was the original
    consumer of this function. compaction_refresh.py no longer calls it
    (the TaskList-based primary path needs no project-path encoding).
    These tests verify the env-var-fallback behavior (empty transcript
    path), which is the only remaining invocation pattern in the codebase.
    Kept colocated with compaction_refresh tests for historical context;
    see test_checkpoint_builder.py for the full test suite on this helper.
    """

    def test_encodes_project_path_from_env(self):
        """Test encoding project path from environment when transcript path is empty."""
        from refresh.checkpoint_builder import get_encoded_project_path

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/Users/test/myproject"}):
            # Empty transcript path triggers the env var fallback
            encoded = get_encoded_project_path("")

        assert encoded == "-Users-test-myproject"

    def test_handles_nested_path(self):
        """Test encoding deeply nested project path."""
        from refresh.checkpoint_builder import get_encoded_project_path

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": "/home/user/code/org/repo"}):
            encoded = get_encoded_project_path("")

        assert encoded == "-home-user-code-org-repo"

    def test_returns_unknown_project_when_not_set(self):
        """Test returns 'unknown-project' when CLAUDE_PROJECT_DIR not set."""
        from refresh.checkpoint_builder import get_encoded_project_path

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
            encoded = get_encoded_project_path("")

        assert encoded == "unknown-project"


class TestCompactionRefreshMain:
    """Integration tests for the main() function.

    Post-#413: only the TaskList-based primary path remains. The checkpoint
    fallback (and all its edge-case tests) were removed when precompact_refresh
    was deleted; covered tests here are the source!=compact short-circuit,
    empty-tasks → suppressOutput, and defensive exception handling.
    """

    def test_main_non_compact_source(self, tmp_path: Path, pact_context):
        """Test that non-compact sessions are ignored."""
        pact_context(session_id="test-session-123")

        # Source is NOT "compact"
        input_data = json.dumps({"source": "new"})

        with patch("sys.stdin", StringIO(input_data)), \
             patch.dict(os.environ, {
                 "CLAUDE_PROJECT_DIR": "/test/project",
             }), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    main()

                # Should exit 0 without refresh (not a compact session)
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()
                # Bare exit path: suppressOutput to prevent false "hook error"
                assert json.loads(output.strip()) == {"suppressOutput": True}

    def test_main_tasks_empty_suppresses_output(self, tmp_path: Path, pact_context):
        """Post-#413: when get_task_list() returns None on compact source,
        emit suppressOutput (no stale checkpoint fallback)."""
        pact_context(session_id="test-session")

        input_data = json.dumps({"source": "compact"})
        with patch("sys.stdin", StringIO(input_data)), \
             patch("compaction_refresh.get_task_list", return_value=None), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main

            with patch("sys.stdout", new_callable=StringIO) as mock_stdout:
                with pytest.raises(SystemExit) as exc_info:
                    main()
                assert exc_info.value.code == 0
                output = mock_stdout.getvalue()

        assert json.loads(output.strip()) == {"suppressOutput": True}

    def test_main_never_raises(self, tmp_path: Path):
        """Test that main() never raises exceptions."""
        # Invalid JSON input
        with patch("sys.stdin", StringIO("invalid json {")), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main

            # Should not raise
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 0

    def test_main_with_invalid_json_input(self, tmp_path: Path):
        """Test handling of invalid JSON input."""
        with patch("sys.stdin", StringIO("not json")), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main

            with pytest.raises(SystemExit) as exc_info:
                main()

            # Should exit cleanly
            assert exc_info.value.code == 0


class TestExceptionHandlingPaths:
    """Tests for exception handling and defensive paths in compaction_refresh."""

    def test_main_outer_exception_handling(self, tmp_path: Path):
        """Test that outer try/except in main() catches all exceptions.

        The main() function has a top-level try/except that should
        catch any unexpected exceptions and exit cleanly.
        """
        # Simulate an exception by patching stdin to raise
        class RaisingStdin:
            def read(self):
                raise RuntimeError("Simulated stdin error")

        with patch("sys.stdin", RaisingStdin()), \
             patch("pathlib.Path.home", return_value=tmp_path):

            from compaction_refresh import main

            # Should not raise, should exit 0
            with pytest.raises(SystemExit) as exc_info:
                main()

            assert exc_info.value.code == 0

    def test_get_encoded_project_path_empty_env_string(self):
        """Test handling of empty string CLAUDE_PROJECT_DIR returns unknown-project."""
        from refresh.checkpoint_builder import get_encoded_project_path

        with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": ""}):
            result = get_encoded_project_path("")

        # Empty env var triggers "unknown-project" fallback
        assert result == "unknown-project"


# =============================================================================
# Post-#413 Phantom Workflow State Regression Tests
# =============================================================================
#
# Issue #413 identified 4 bugs (session_id force-overwrite, project-scoped
# checkpoint filenames, unanchored TRIGGER_PATTERNS on user-turn content,
# deprecated hook still firing) that together fabricated phantom
# "Workflow: {name}" claims in post-compaction SessionStart output for
# sessions that never ran the claimed workflow. The fix was wholesale
# deletion of the checkpoint-fallback path; TaskList is now the only
# workflow-state source.
#
# These tests lock in the post-fix invariant: on source=compact, the hook
# either emits a TaskList-derived refresh message (when real in-progress
# tasks exist) or suppressOutput (when they don't). No transcript scan,
# no checkpoint read, no pattern match on user-turn content. Phantom
# workflow names CANNOT materialize from nothing.
# =============================================================================


@pytest.fixture
def _isolated_tasks_dir(tmp_path: Path, monkeypatch, pact_context):
    """Create mock ~/.claude/tasks/{session_id}/ under tmp_path.

    Matches the `mock_tasks_dir` fixture used by test_task_integration.py
    but scoped locally so this test file stays self-contained.
    """
    session_id = "test-session-413"
    tasks_dir = tmp_path / ".claude" / "tasks" / session_id
    tasks_dir.mkdir(parents=True)
    pact_context(session_id=session_id)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tasks_dir


def _run_compaction_refresh(source: str, monkeypatch) -> dict:
    """Drive compaction_refresh.main() with a synthetic stdin payload.

    Returns the parsed JSON written to stdout. Raises AssertionError if
    the hook exits non-zero (hook must be fail-open).
    """
    input_data = json.dumps({"source": source})
    stdin = StringIO(input_data)
    stdout = StringIO()
    monkeypatch.setattr("sys.stdin", stdin)
    monkeypatch.setattr("sys.stdout", stdout)

    from compaction_refresh import main

    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0, (
        f"compaction_refresh.main() must exit 0 (fail-open), got {exc_info.value.code}"
    )
    raw = stdout.getvalue().strip()
    return json.loads(raw) if raw else {}


class TestPhantomWorkflowRegression:
    """Regression tests guarding against phantom workflow state fabrication.

    SACROSANCT: the hook must never inject a 'Workflow: {name}' claim (or
    equivalent 'Feature: {name}' / 'Current Phase: {name}' content) unless
    a real in-progress task with that identity exists on disk.
    """

    def test_compact_with_no_tasks_dir_emits_suppress(
        self, tmp_path: Path, monkeypatch, pact_context
    ):
        """Source=compact with ZERO tasks dir on disk emits suppressOutput.

        This is the bare repro scenario from issue #413: bootstrap + /compact
        with no PACT workflow actually started. Pre-fix, transcript scanning +
        stale checkpoint could fabricate a phantom 'Workflow: peer-review'.
        Post-fix, empty TaskList means suppressOutput — no fabrication path.
        """
        pact_context(session_id="bare-repro-session")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        output = _run_compaction_refresh("compact", monkeypatch)

        assert output == {"suppressOutput": True}
        assert "hookSpecificOutput" not in output
        assert "additionalContext" not in output

    def test_compact_with_empty_tasks_dir_emits_suppress(
        self, _isolated_tasks_dir, monkeypatch
    ):
        """Source=compact with tasks dir present but empty emits suppressOutput."""
        output = _run_compaction_refresh("compact", monkeypatch)

        assert output == {"suppressOutput": True}

    def test_compact_with_only_completed_tasks_emits_suppress(
        self, _isolated_tasks_dir, monkeypatch
    ):
        """Tasks exist but all completed → no in_progress → suppressOutput.

        This catches a subtle phantom class: stale completed-task data
        must not leak into additionalContext.
        """
        completed_feature = {
            "id": "task-old",
            "subject": "Ancient completed feature",
            "status": "completed",
        }
        (_isolated_tasks_dir / "task-old.json").write_text(
            json.dumps(completed_feature)
        )

        output = _run_compaction_refresh("compact", monkeypatch)

        assert output == {"suppressOutput": True}

    def test_compact_with_malformed_json_files_emits_suppress(
        self, _isolated_tasks_dir, monkeypatch
    ):
        """Malformed JSON task files (syntactically invalid or empty)
        must not produce phantom state.

        Pre-fix fallback path could interpret corrupted/stale checkpoint
        JSON and fabricate workflow names. Post-fix, get_task_list()
        skips syntactically-malformed files via JSONDecodeError; if
        nothing usable remains, suppressOutput.

        NOTE: This test does NOT cover JSON values that parse successfully
        to non-dict types (null, true, numbers, strings) — those expose a
        pre-existing degradation path in task_utils.get_task_list() where
        None leaks downstream and triggers the outer exception handler.
        See test_compact_with_null_json_never_leaks_phantom below for the
        fail-open boundary on that path.
        """
        (_isolated_tasks_dir / "malformed1.json").write_text("{ not json")
        (_isolated_tasks_dir / "malformed2.json").write_text("")

        output = _run_compaction_refresh("compact", monkeypatch)

        assert output == {"suppressOutput": True}

    def test_compact_with_null_json_never_leaks_phantom(
        self, _isolated_tasks_dir, monkeypatch
    ):
        """A task file containing JSON literal 'null' must NOT produce
        phantom workflow state — even if downstream processing raises.

        Exposes a pre-existing edge case: json.loads('null') returns None,
        which bypasses the JSONDecodeError catch in get_task_list() and
        reaches compaction_refresh.main() where `.get()` on None raises.
        The outer try/except catches it and emits hook_error_json, which
        contains NO workflow identity. Weaker than suppressOutput but
        still satisfies the SACROSANCT phantom-state invariant.

        Regression intent: if a future refactor ever adds transcript-
        scanning or filesystem-glob 'workflow recovery', it MUST NOT
        leak workflow names through the error path.
        """
        (_isolated_tasks_dir / "null.json").write_text("null")

        input_data = json.dumps({"source": "compact"})
        stdin = StringIO(input_data)
        stdout = StringIO()
        monkeypatch.setattr("sys.stdin", stdin)
        monkeypatch.setattr("sys.stdout", stdout)

        from compaction_refresh import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0  # fail-open preserved

        raw = stdout.getvalue()
        # Whatever the output shape, phantom workflow names must NOT appear
        assert "Workflow:" not in raw
        assert "POST-COMPACTION CHECKPOINT" not in raw
        assert "peer-review" not in raw
        assert "orchestrate" not in raw
        assert "comPACT" not in raw

    def test_compact_output_never_mentions_workflow_literal_when_no_tasks(
        self, tmp_path: Path, monkeypatch, pact_context
    ):
        """Byte-level assertion: 'Workflow:' literal is never in output on
        a bare source=compact session.

        The pre-fix bug surface injected 'Workflow: peer-review' or similar
        into additionalContext. Post-fix, the output is exactly the
        suppressOutput sentinel. A stronger form of the suppress assertion:
        even if a future refactor changes the sentinel shape, the literal
        phantom-bug string 'Workflow:' must never appear.
        """
        pact_context(session_id="bare-bytes-session")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        input_data = json.dumps({"source": "compact"})
        stdin = StringIO(input_data)
        stdout = StringIO()
        monkeypatch.setattr("sys.stdin", stdin)
        monkeypatch.setattr("sys.stdout", stdout)

        from compaction_refresh import main

        with pytest.raises(SystemExit):
            main()

        raw_output = stdout.getvalue()
        assert "Workflow:" not in raw_output
        assert "POST-COMPACTION CHECKPOINT" not in raw_output
        assert "peer-review" not in raw_output
        assert "orchestrate" not in raw_output

    def test_non_compact_source_never_mentions_workflow_even_with_tasks(
        self, _isolated_tasks_dir, monkeypatch
    ):
        """Non-compact source + real in-progress tasks still suppresses output.

        The hook only acts on source=='compact'. Any other source value
        (startup, resume, clear, ...) MUST short-circuit to suppressOutput
        BEFORE reading TaskList. Locks the primary guard.
        """
        feature = {
            "id": "f-1",
            "subject": "Implement X",
            "status": "in_progress",
        }
        (_isolated_tasks_dir / "f-1.json").write_text(json.dumps(feature))

        output = _run_compaction_refresh("startup", monkeypatch)

        assert output == {"suppressOutput": True}


# =============================================================================
# Post-#413 Primary-Path E2E (replaces deleted test_workflow_e2e.py coverage)
# =============================================================================
#
# Architect's §3.1 D3 deleted test_workflow_e2e.py wholesale (596 LOC). The
# coder's §Q2 open question flagged that the 2 existing primary-path tests
# in test_task_integration.py may leave coverage holes. This class fills
# them with focused assertions on the post-compaction contract: when real
# in-progress tasks exist + source=compact, the hook emits a structurally
# valid hookSpecificOutput containing the feature, phase, and blocker
# identities that actually exist in TaskList.
#
# Scope is deliberately narrow (~5 tests, ~130 LOC) vs the original 596 LOC.
# Broader task-utility behavior is covered in test_task_integration.py.
# =============================================================================


class TestCompactionRefreshPrimaryPathE2E:
    """E2E coverage for the TaskList-based primary path."""

    def test_feature_plus_phase_plus_blockers_render_into_additional_context(
        self, _isolated_tasks_dir, monkeypatch
    ):
        """End-to-end: realistic task list → structurally-correct refresh.

        Verifies the contract the orchestrator depends on post-compaction:
        hookSpecificOutput.hookEventName == 'SessionStart' AND
        hookSpecificOutput.additionalContext contains feature, phase,
        agent, and blocker info derived from on-disk task files.

        Note: the blocker task has blockedBy=["f-001"] so find_feature_task
        correctly picks "Fix regression in payment flow" as the feature
        rather than the blocker. This mirrors the canonical orchestration
        shape from test_task_integration.py:sample_task_list.
        """
        tasks = [
            {
                "id": "f-001",
                "subject": "Fix regression in payment flow",
                "status": "in_progress",
            },
            {
                "id": "p-002",
                "subject": "CODE: payment-regression",
                "status": "in_progress",
                "blockedBy": ["f-001"],
            },
            {
                "id": "a-003",
                "subject": "backend-coder: fix stripe adapter",
                "status": "in_progress",
                "blockedBy": ["p-002"],
            },
            {
                "id": "b-004",
                "subject": "Missing API credentials",
                "status": "in_progress",
                "blockedBy": ["f-001"],
                "metadata": {"type": "blocker", "level": "HALT"},
            },
        ]
        for t in tasks:
            (_isolated_tasks_dir / f"{t['id']}.json").write_text(json.dumps(t))

        output = _run_compaction_refresh("compact", monkeypatch)

        # Structural contract
        assert "hookSpecificOutput" in output
        hso = output["hookSpecificOutput"]
        assert hso["hookEventName"] == "SessionStart"
        ctx = hso["additionalContext"]

        # Content contract — every identity must trace back to on-disk tasks
        assert "[POST-COMPACTION CHECKPOINT]" in ctx
        assert "Fix regression in payment flow" in ctx
        assert "CODE: payment-regression" in ctx
        assert "backend-coder: fix stripe adapter" in ctx
        assert "Missing API credentials" in ctx
        assert "BLOCKERS DETECTED" in ctx

    def test_feature_only_no_phase_emits_feature_without_phantom_phase(
        self, _isolated_tasks_dir, monkeypatch
    ):
        """Feature in_progress but no phase task: output must not fabricate
        a phase name. The code's 'None detected' branch is the load-bearing
        anti-phantom guard for phase identity."""
        feature = {
            "id": "f-only",
            "subject": "Solo feature",
            "status": "in_progress",
        }
        (_isolated_tasks_dir / "f-only.json").write_text(json.dumps(feature))

        output = _run_compaction_refresh("compact", monkeypatch)

        ctx = output["hookSpecificOutput"]["additionalContext"]
        assert "Solo feature" in ctx
        assert "None detected" in ctx  # the honest marker for absent phase
        # No phase literal should appear
        assert "CODE:" not in ctx
        assert "ARCHITECT:" not in ctx
        assert "PREPARE:" not in ctx
        assert "TEST:" not in ctx

    def test_phase_only_no_feature_emits_identification_fallback(
        self, _isolated_tasks_dir, monkeypatch
    ):
        """Phase in_progress but no identifiable feature task. Output must
        honestly declare 'Unable to identify feature task' — NOT invent
        one from thin air."""
        phase = {
            "id": "p-orphan",
            "subject": "CODE: orphan-feature",
            "status": "in_progress",
        }
        (_isolated_tasks_dir / "p-orphan.json").write_text(json.dumps(phase))

        output = _run_compaction_refresh("compact", monkeypatch)

        ctx = output["hookSpecificOutput"]["additionalContext"]
        assert "Unable to identify feature task" in ctx
        assert "CODE: orphan-feature" in ctx

    def test_pending_tasks_only_emits_suppress(
        self, _isolated_tasks_dir, monkeypatch
    ):
        """Tasks exist but status=pending (not in_progress): nothing
        running, suppressOutput. Contract: 'in_progress' is the only
        status that triggers a refresh message."""
        pending = {
            "id": "p-pending",
            "subject": "Pending feature",
            "status": "pending",
        }
        (_isolated_tasks_dir / "p-pending.json").write_text(json.dumps(pending))

        output = _run_compaction_refresh("compact", monkeypatch)

        assert output == {"suppressOutput": True}

    def test_mixed_in_progress_and_malformed_emits_refresh_skipping_malformed(
        self, _isolated_tasks_dir, monkeypatch
    ):
        """Valid in_progress task + malformed task JSON co-exist.
        Primary path must: (a) skip the malformed file, (b) still emit
        refresh from the valid one. No phantom from the malformed path."""
        valid = {
            "id": "v-1",
            "subject": "Valid feature",
            "status": "in_progress",
        }
        (_isolated_tasks_dir / "v-1.json").write_text(json.dumps(valid))
        (_isolated_tasks_dir / "broken.json").write_text("{ not json }")

        output = _run_compaction_refresh("compact", monkeypatch)

        hso = output["hookSpecificOutput"]
        assert hso["hookEventName"] == "SessionStart"
        assert "Valid feature" in hso["additionalContext"]
