"""
Tests for hooks/precompact_state_reminder.py — PreCompact hook that gathers
mechanical state from disk and emits custom_instructions for the compaction
model. Per #444 Tertiary, the previously-emitted systemMessage channel was
removed (it fired too late in the compaction flow to be actioned).

Tests cover:
1. State summary formatting
2. Custom instructions composition
3. Full hook output (single-field contract)
4. Subprocess integration (JSON output, exit code)
5. Fail-open on malformed input, missing dirs, bad JSON files
6. Outer exception handler (hook_error_json output on unexpected errors)

Note: Disk state gathering (task analysis, team scanning) is tested in
test_session_state.py since those functions now live in shared/session_state.py.
"""
import json
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


HOOK_PATH = str(Path(__file__).parent.parent / "hooks" / "precompact_state_reminder.py")


def run_hook(
    stdin_data: str | None = None,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run the hook as a subprocess and return the result.

    env: when provided, REPLACES the subprocess environment (pass a merged
    {**os.environ, ...} to keep PATH etc.). Used by the context-init wiring
    test to override HOME + CLAUDE_PROJECT_DIR so the hook resolves
    on-disk fixtures under a temp tree instead of the real ~/.claude. When
    None, the subprocess inherits the current environment (prior behavior).
    """
    return subprocess.run(
        [sys.executable, HOOK_PATH],
        input=stdin_data or "",
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


# ---------------------------------------------------------------------------
# Helpers for creating fake task/team directories
# ---------------------------------------------------------------------------


def _create_task_file(task_dir: Path, task_id: str, data: dict) -> None:
    """Write a task JSON file into the given directory."""
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / f"{task_id}.json").write_text(
        json.dumps(data), encoding="utf-8"
    )


def _create_team_config(
    teams_dir: Path, team_name: str, members: list[dict], name: str | None = None
) -> None:
    """Write a team config.json with the given members list."""
    team_dir = teams_dir / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    config = {"members": members}
    if name is not None:
        config["name"] = name
    (team_dir / "config.json").write_text(
        json.dumps(config), encoding="utf-8"
    )


# TestBuildStateSummary class removed in PR #447 cleanup:
# _build_state_summary had zero production call sites after #444's Tertiary
# removed the systemMessage composition that consumed it. The 4 self-coverage
# tests were only testing a function that nothing else called; function +
# tests deleted together per user-authorized LOW-1 remediation.


# ---------------------------------------------------------------------------
# Unit tests: build_custom_instructions
# ---------------------------------------------------------------------------


class TestBuildCustomInstructions:
    """Test custom_instructions composition for compaction model."""

    def test_full_instructions(self):
        from precompact_state_reminder import build_custom_instructions
        state = {
            "feature_subject": "Add auth", "feature_id": "5",
            "current_phase": "Phase: CODE", "variety_score": 9,
            "teammates": ["coder", "tester"], "team_names": ["pact-abc"],
        }
        result = build_custom_instructions(state)
        assert "CRITICAL CONTEXT TO PRESERVE" in result
        assert "Add auth" in result
        assert "task #5" in result
        assert "Phase: CODE" in result
        assert "coder, tester" in result
        assert "Variety score: 9" in result
        assert "pact-abc" in result
        assert "Preserve task IDs and agent names exactly" in result

    def test_minimal_state(self):
        from precompact_state_reminder import build_custom_instructions
        state = {
            "feature_subject": None, "feature_id": None,
            "current_phase": None, "variety_score": None,
            "teammates": [], "team_names": [],
        }
        result = build_custom_instructions(state)
        assert "CRITICAL CONTEXT" in result
        assert "unknown" in result  # phase unknown
        assert "none found" in result  # agents none found
        assert "Preserve task IDs" in result

    def test_no_variety_omits_variety_line(self):
        from precompact_state_reminder import build_custom_instructions
        state = {
            "feature_subject": "X", "feature_id": "1",
            "current_phase": "Phase: TEST", "variety_score": None,
            "teammates": ["a"], "team_names": ["t"],
        }
        result = build_custom_instructions(state)
        assert "Variety" not in result

    def test_out_of_range_bare_int_omits_variety_line(self):
        """A bare int below the valid score range (0 < MIN_SCORE=4) is an
        impossible variety total — the line is omitted rather than rendering
        "Variety score: 0", per the range-gate on the bare-int branch."""
        from precompact_state_reminder import build_custom_instructions
        state = {
            "feature_subject": "X", "feature_id": "1",
            "current_phase": None, "variety_score": 0,
            "teammates": [], "team_names": [],
        }
        result = build_custom_instructions(state)
        assert "Variety" not in result


# ---------------------------------------------------------------------------
# Unit tests: _extract_variety_total
# ---------------------------------------------------------------------------


class TestExtractVarietyTotal:
    """Direct tests for the _extract_variety_total helper.

    Defensive code rejects bool because Python's bool is a subclass of
    int — `isinstance(True, int) is True`. The bare-int branch carries an
    explicit `not isinstance(_, bool)` guard so a `variety_score: True`
    cannot render as "Variety score: 1" in the compaction-model context;
    the dict path delegates bool rejection to the shared resolver, which
    rejects `{"total": False}` for the same reason. Removing the bare-int
    guard makes the bool cases below fail the `is None` assertion."""

    def test_bool_true_at_top_level_rejected(self):
        from precompact_state_reminder import _extract_variety_total
        assert _extract_variety_total(True) is None

    def test_bool_false_at_top_level_rejected(self):
        from precompact_state_reminder import _extract_variety_total
        assert _extract_variety_total(False) is None

    def test_dict_with_bool_total_rejected(self):
        from precompact_state_reminder import _extract_variety_total
        assert _extract_variety_total({"total": True}) is None
        assert _extract_variety_total({"total": False}) is None

    # ----- dict path delegates to the shared resolver -------------------

    def test_dict_with_canonical_total(self):
        from precompact_state_reminder import _extract_variety_total
        assert _extract_variety_total({"total": 8}) == 8

    def test_dict_with_score_only_resolves_via_shared_resolver(self):
        """NEW behavior: a non-canonical `score`-only stamp now renders a
        total instead of silently dropping the line. Before convergence on
        the shared resolver, precompact only read `total` and dropped this."""
        from precompact_state_reminder import _extract_variety_total
        assert _extract_variety_total({"score": 8}) == 8

    def test_dict_with_dimension_scores_resolves_via_shared_resolver(self):
        from precompact_state_reminder import _extract_variety_total
        v = {"novelty": 2, "scope": 3, "uncertainty": 1, "risk": 4}
        assert _extract_variety_total(v) == 10

    def test_dict_with_out_of_range_total_no_fallback_returns_none(self):
        from precompact_state_reminder import _extract_variety_total
        assert _extract_variety_total({"total": 99}) is None

    # ----- bare-int render affordance, range-gated to [MIN_SCORE, MAX_SCORE] -

    def test_bare_int_in_range_passes_through(self):
        from precompact_state_reminder import _extract_variety_total
        assert _extract_variety_total(8) == 8

    def test_bare_int_at_range_bounds_passes_through(self):
        """The inclusive [4, 16] bounds (MIN_SCORE/MAX_SCORE) both pass."""
        from precompact_state_reminder import _extract_variety_total
        assert _extract_variety_total(4) == 4
        assert _extract_variety_total(16) == 16

    def test_out_of_range_bare_int_returns_none(self):
        """The bare-int branch is range-gated to match the resolver's
        no-clamp/no-fabricate [4, 16] policy: an out-of-range bare int is
        dropped (line omitted), not rendered verbatim. Above the max (99)
        and below the min (0, 3) both return None."""
        from precompact_state_reminder import _extract_variety_total
        assert _extract_variety_total(99) is None
        assert _extract_variety_total(17) is None
        assert _extract_variety_total(3) is None
        assert _extract_variety_total(0) is None

    # ----- junk → None --------------------------------------------------

    @pytest.mark.parametrize(
        "junk",
        [None, 8.0, 8.5, "8", "high", [8], {"total": "twelve"}, {}],
    )
    def test_junk_returns_none(self, junk):
        from precompact_state_reminder import _extract_variety_total
        assert _extract_variety_total(junk) is None


class TestExtractVarietyTotalRenders:
    """build_custom_instructions renders or omits the variety line per the
    resolver's verdict — the consumer-side of the precompact convergence."""

    def _state(self, variety_score):
        return {
            "feature_subject": "X", "feature_id": "1",
            "current_phase": "Phase: TEST", "variety_score": variety_score,
            "teammates": ["a"], "team_names": ["t"],
        }

    def test_renders_line_for_score_only_dict(self):
        from precompact_state_reminder import build_custom_instructions
        result = build_custom_instructions(self._state({"score": 9}))
        assert "Variety score: 9" in result

    def test_renders_line_for_dimension_sum_dict(self):
        from precompact_state_reminder import build_custom_instructions
        v = {"novelty": 2, "scope": 3, "uncertainty": 1, "risk": 4}
        result = build_custom_instructions(self._state(v))
        assert "Variety score: 10" in result

    def test_omits_line_for_unresolvable_dict(self):
        from precompact_state_reminder import build_custom_instructions
        result = build_custom_instructions(self._state({"total": 99}))
        assert "Variety" not in result

    def test_renders_line_for_bare_int(self):
        from precompact_state_reminder import build_custom_instructions
        result = build_custom_instructions(self._state(12))
        assert "Variety score: 12" in result

    def test_omits_line_for_junk(self):
        from precompact_state_reminder import build_custom_instructions
        result = build_custom_instructions(self._state("not-a-number"))
        assert "Variety" not in result


# ---------------------------------------------------------------------------
# Unit tests: build_hook_output (full composition)
# ---------------------------------------------------------------------------


class TestBuildHookOutput:
    """Test complete hook output.

    Per #444 Tertiary: build_hook_output returns only custom_instructions —
    no systemMessage. The previously-emitted "Compaction imminent" message
    fired as part of the compaction event, too late to be actioned before
    the context cut.
    """

    def test_contains_only_custom_instructions_key(self, tmp_path):
        """Output dict must contain custom_instructions and MUST NOT
        contain systemMessage (the latter was removed in #444)."""
        from precompact_state_reminder import build_hook_output
        tasks_dir = tmp_path / "tasks"
        teams_dir = tmp_path / "teams"
        team_task_dir = tasks_dir / "pact-test"
        _create_task_file(team_task_dir, "t1", {
            "id": "7",
            "status": "in_progress",
            "subject": "Build dashboard",
        })
        _create_team_config(teams_dir, "pact-test", [
            {"name": "frontend-coder"},
        ], name="pact-test")

        result = build_hook_output(str(tasks_dir), str(teams_dir))
        assert "custom_instructions" in result
        assert "systemMessage" not in result
        assert set(result.keys()) == {"custom_instructions"}

    def test_custom_instructions_has_feature(self, tmp_path, monkeypatch):
        """Feature surfaces when a journal event names the feature task
        and the team's task file is reachable via session-scoped disk read.

        Exercises the new journal-based code path: variety_assessed in the
        journal identifies feature_id=3; with no matching agent_handoff,
        session_state reads ~/.claude/tasks/pact-t/3.json for the subject.
        build_hook_output accepts only tasks/teams base dirs, so session_dir
        and team_name are threaded in via monkeypatched pact_context."""
        from shared.session_journal import make_event
        import shared.pact_context as ctx_module
        from precompact_state_reminder import build_hook_output

        tasks_dir = tmp_path / "tasks"
        teams_dir = tmp_path / "teams"
        session_dir = tmp_path / "session-abc"

        # Journal event: feature_id=3; no handoff → disk fallback supplies subject
        session_dir.mkdir(parents=True)
        (session_dir / "session-journal.jsonl").write_text(
            json.dumps(make_event(
                "variety_assessed", task_id="3",
                variety={"score": 6, "level": "MEDIUM"},
                ts="2026-04-14T00:00:01Z",
            )) + "\n",
            encoding="utf-8",
        )

        _create_task_file(tasks_dir / "pact-t", "3", {
            "id": "3",
            "status": "in_progress",
            "subject": "Auth feature",
        })
        _create_team_config(teams_dir, "pact-t", [{"name": "coder"}], name="pact-t")

        # Thread session_dir + team_name via pact_context (build_hook_output
        # does not accept them directly)
        monkeypatch.setattr(ctx_module, "get_session_dir", lambda: str(session_dir))
        monkeypatch.setattr(ctx_module, "get_team_name", lambda: "pact-t")

        result = build_hook_output(str(tasks_dir), str(teams_dir))
        assert "Auth feature" in result["custom_instructions"]
        assert "task #3" in result["custom_instructions"]

    # test_system_message_has_brain_dump removed in #444:
    # BRAIN_DUMP_INSTRUCTIONS constant and systemMessage composition were
    # deleted. custom_instructions remains the only output channel.

    def test_empty_dirs_produces_valid_output(self, tmp_path):
        from precompact_state_reminder import build_hook_output
        result = build_hook_output(
            str(tmp_path / "no-tasks"),
            str(tmp_path / "no-teams"),
        )
        assert "custom_instructions" in result
        assert "systemMessage" not in result
        assert "CRITICAL CONTEXT" in result["custom_instructions"]


# ---------------------------------------------------------------------------
# Integration tests: subprocess
# ---------------------------------------------------------------------------


class TestPrecompactSubprocess:
    """Verify the hook emits expected JSON via subprocess.

    Per #444: only custom_instructions is emitted. The previously-emitted
    systemMessage channel was removed — it fired too late to be actioned
    before the context cut.
    """

    def test_emits_only_custom_instructions(self):
        """Subprocess output contains custom_instructions and MUST NOT
        contain systemMessage."""
        result = run_hook(json.dumps({"transcript_path": "/tmp/test.jsonl"}))
        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        assert "custom_instructions" in output
        assert "systemMessage" not in output

    def test_custom_instructions_has_critical_context(self):
        result = run_hook(json.dumps({}))
        output = json.loads(result.stdout.strip())
        assert "CRITICAL CONTEXT" in output["custom_instructions"]
        assert "Preserve task IDs" in output["custom_instructions"]


# ---------------------------------------------------------------------------
# Context-init wiring (regression: hook must call pact_context.init)
# ---------------------------------------------------------------------------


class TestPrecompactContextInitWiring:
    """Pin the pact_context.init() wiring in main().

    Regression guard: main() must call pact_context.init(input_data) after
    parsing stdin and before build_hook_output(). Without it,
    build_hook_output() -> summarize_session_state() (invoked with no
    session_dir/team_name overrides) resolves scope from an uninitialized
    pact_context (empty team_name/session_dir), so the compaction reminder
    ships blank ("Current phase: unknown / Active agents: none found") on
    every compaction even with live teammates and an active phase.

    This exercises the REAL main() -> pact_context.init -> summarize_session_state
    path via subprocess: stdin carries session_id, and HOME +
    CLAUDE_PROJECT_DIR are overridden so the hook resolves on-disk fixtures
    under a temp tree (build_hook_output does not override the home-relative
    teams/tasks base dirs, so the fixtures must live under the temp ~/.claude).
    Neutering the init() call makes the teammate/phase assertions below fail.
    """

    def _build_session_fixture(self, home: Path, proj: Path) -> tuple[str, str]:
        """Lay down the on-disk state the hook reads once init() resolves it.

        Returns (session_id, team_name). Both use allowlist-only characters
        so pact_context's sanitize-substitute leaves them unchanged and the
        directories we create match the paths the hook resolves.
        """
        from shared.session_journal import make_event

        slug = proj.name
        session_id = "sess-913-wiring"
        team_name = "pact-wiring913"

        # Session-scoped context file: init() resolves _context_path to
        # ~/.claude/pact-sessions/{slug}/{session_id}/pact-session-context.json
        # from stdin session_id + CLAUDE_PROJECT_DIR; get_pact_context() then
        # reads team_name/session_id from here.
        session_dir = home / ".claude" / "pact-sessions" / slug / session_id
        session_dir.mkdir(parents=True)
        (session_dir / "pact-session-context.json").write_text(
            json.dumps({
                "team_name": team_name,
                "session_id": session_id,
                "project_dir": str(proj),
                "plugin_root": "",
                "started_at": "",
            }),
            encoding="utf-8",
        )

        # Session journal with an active (started, not completed) phase —
        # summarize_session_state reads it from get_session_dir().
        (session_dir / "session-journal.jsonl").write_text(
            json.dumps(make_event(
                "phase_transition", phase="CODE", status="started",
                ts="2026-06-06T00:00:01Z",
            )) + "\n",
            encoding="utf-8",
        )

        # Team config with several members — summarize_session_state reads
        # ~/.claude/teams/{team_name}/config.json (home-relative default).
        team_dir = home / ".claude" / "teams" / team_name
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({
                "name": team_name,
                "members": [
                    {"name": "frontend"},
                    {"name": "backend"},
                    {"name": "tester"},
                ],
            }),
            encoding="utf-8",
        )

        return session_id, team_name

    def test_init_wiring_surfaces_teammates_and_phase(self, tmp_path):
        """custom_instructions must LIST the live teammates and active phase.

        Asserts the reminder is NOT the empty-state default. This fails if
        main() omits the pact_context.init(input_data) call (the #913 bug).
        """
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        proj.mkdir(parents=True)
        session_id, team_name = self._build_session_fixture(home, proj)

        env = {
            **os.environ,
            "HOME": str(home),
            "CLAUDE_PROJECT_DIR": str(proj),
        }
        result = run_hook(json.dumps({"session_id": session_id}), env=env)

        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        ci = output["custom_instructions"]

        # The active phase from the journal is surfaced (not "unknown").
        assert "Current phase: CODE" in ci
        assert "Current phase: unknown" not in ci

        # Every live teammate is listed (not "none found").
        assert "frontend" in ci
        assert "backend" in ci
        assert "tester" in ci
        assert "none found" not in ci

        # The resolved team name surfaces too — corroborates that
        # get_team_name() (hence init()) resolved real context.
        assert team_name in ci

    def test_empty_stdin_with_init_still_fails_open(self, tmp_path):
        """The new init() call must preserve fail-open on empty/no-context
        stdin: no session_id -> _context_path stays None -> empty-state
        reminder, exit 0, custom_instructions still emitted.

        Runs under a temp HOME with NO fixtures so the hook cannot resolve
        any context — the canonical "init() ran but session is unresolved"
        path that must degrade gracefully rather than crash.
        """
        home = tmp_path / "home"
        proj = tmp_path / "proj"
        proj.mkdir(parents=True)
        env = {
            **os.environ,
            "HOME": str(home),
            "CLAUDE_PROJECT_DIR": str(proj),
        }
        # Empty JSON object: parses fine, init({}) finds no session_id and
        # leaves _context_path None (no raise) -> empty-state reminder.
        result = run_hook(json.dumps({}), env=env)

        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        ci = output["custom_instructions"]
        assert "CRITICAL CONTEXT" in ci
        assert "none found" in ci  # no teammates resolvable
        assert "systemMessage" not in output


# ---------------------------------------------------------------------------
# Fail-open tests
# ---------------------------------------------------------------------------


class TestPrecompactFailOpen:
    """Verify fail-open behavior."""

    def test_empty_stdin_exits_zero(self):
        result = run_hook("")
        assert result.returncode == 0

    def test_malformed_json_exits_zero(self):
        result = run_hook("not json at all")
        assert result.returncode == 0

    def test_malformed_json_still_emits_custom_instructions(self):
        result = run_hook("not json at all")
        output = json.loads(result.stdout.strip())
        assert "custom_instructions" in output
        assert "systemMessage" not in output

    def test_null_input_exits_zero(self):
        result = run_hook("null")
        assert result.returncode == 0
        # Non-dict valid JSON coerces to {} -> empty-state reminder, NOT the
        # fail-open error channel. (Pre-guard: init(None) raised -> systemMessage.)
        output = json.loads(result.stdout.strip())
        assert "custom_instructions" in output
        assert "systemMessage" not in output

    def test_array_input_exits_zero(self):
        result = run_hook("[]")
        assert result.returncode == 0
        # Non-dict valid JSON coerces to {} -> empty-state reminder, NOT the
        # fail-open error channel. (Pre-guard: init([]) raised -> systemMessage.)
        output = json.loads(result.stdout.strip())
        assert "custom_instructions" in output
        assert "systemMessage" not in output

    def test_disk_read_error_fails_open(self, tmp_path):
        """Unreadable tasks/teams dirs must not raise — build_hook_output
        degrades gracefully and still emits custom_instructions."""
        from precompact_state_reminder import build_hook_output
        fake_file = tmp_path / "not-a-dir"
        fake_file.write_text("x", encoding="utf-8")
        result = build_hook_output(str(fake_file), str(fake_file))
        assert "custom_instructions" in result
        assert "none found" in result["custom_instructions"]
        assert "systemMessage" not in result


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# TestConstants class removed in #444:
# BRAIN_DUMP_INSTRUCTIONS constant was deleted along with the systemMessage
# composition. No other module-level constants require testing here.


# ---------------------------------------------------------------------------
# Outer exception handler tests
# ---------------------------------------------------------------------------


class TestPrecompactOuterExceptionHandler:
    """Verify that main() catches unexpected exceptions, exits 0,
    emits hook_error_json on stdout and error info on stderr."""

    def test_exits_zero_on_unexpected_error(self):
        """main() must exit 0 even when build_hook_output raises."""
        from precompact_state_reminder import main

        with patch("sys.stdin", StringIO("{}")), \
             patch("precompact_state_reminder.build_hook_output",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_stderr_contains_error_info(self, capsys):
        """Error details must appear on stderr for logging."""
        from precompact_state_reminder import main

        with patch("sys.stdin", StringIO("{}")), \
             patch("precompact_state_reminder.build_hook_output",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "precompact_state_reminder" in captured.err
        assert "test error" in captured.err

    def test_stdout_contains_hook_error_json(self, capsys):
        """Stdout must contain structured JSON from hook_error_json."""
        from precompact_state_reminder import main

        with patch("sys.stdin", StringIO("{}")), \
             patch("precompact_state_reminder.build_hook_output",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert "systemMessage" in output
        assert "PACT hook warning" in output["systemMessage"]
        assert "precompact_state_reminder" in output["systemMessage"]
        assert "test error" in output["systemMessage"]
