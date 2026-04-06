"""
Tests for session_init.py — SessionStart hook.

Tests cover:
generate_team_name():
1. Happy path: session_id from input_data -> "pact-{first 8 chars}"
2. Random fallback: random hex suffix when input_data has no session_id
3. Short session_id: less than 8 chars used as-is
4. Empty session_id: treated as falsy, falls back to random
5. session_id from input_data used as sole source
6. Output format validation (pact- prefix, hex suffix)
7. None session_id: treated as falsy, falls back to random

Resume-aware team detection (main() integration):
9. Fresh session (no config file) → TeamCreate instruction emitted
10. Resume session (config file exists) → reuse instruction emitted
11. OSError fallback → TeamCreate instruction (fail-open)
12. Team instruction is first in context_parts (insert at position 0)

main() integration:
13. check_paused_state non-None result appears in additionalContext output

check_additional_directories():
14. Returns None when ~/.claude/teams is present (absolute path)
15. Returns None when ~/.claude/teams is present (tilde path)
16. Returns tip message when setting is missing from additionalDirectories
17. Returns tip message when additionalDirectories is empty
18. Returns None when settings.json does not exist (fail-open)
19. Returns None when settings.json contains malformed JSON (fail-open)
20. Returns tip when permissions key is missing (empty additionalDirectories)
21. Returns None when additionalDirectories is not a list (fail-open)
22. Returns None when Path.home() raises (fail-open)
23. Ignores non-string entries in additionalDirectories without crashing
24. main() integration: tip appears in systemMessage when setting is missing
25. main() integration: no tip when setting is present
26. main() integration: tip skipped on context reset (compact and clear sources)

Note: restore_last_session() and check_resumption_context() are tested
in test_session_resume.py (canonical location).
"""

import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestGenerateTeamName:
    """Tests for generate_team_name() -- session-unique team name generation."""

    def test_uses_session_id_from_input_data(self):
        """Should return pact- followed by first 8 chars of session_id."""
        from session_init import generate_team_name

        result = generate_team_name({"session_id": "0001639f-a74f-41c4-bd0b-93d9d206e7f7"})

        assert result == "pact-0001639f"

    def test_truncates_session_id_to_8_chars(self):
        """Should use only the first 8 characters of a long session_id."""
        from session_init import generate_team_name

        result = generate_team_name({"session_id": "abcdef1234567890"})

        assert result == "pact-abcdef12"

    def test_random_fallback_when_no_session_id_in_input(self):
        """No session_id in input_data should produce random hex suffix."""
        from session_init import generate_team_name

        result = generate_team_name({})

        assert result.startswith("pact-")
        suffix = result[len("pact-"):]
        assert len(suffix) == 8
        assert re.fullmatch(r"[a-f0-9]{8}", suffix)

    def test_random_fallback_when_session_id_key_missing(self):
        """Absent session_id key in input_data should produce random hex suffix."""
        from session_init import generate_team_name

        result = generate_team_name({"other_key": "value"})

        assert result.startswith("pact-")
        suffix = result[len("pact-"):]
        assert len(suffix) == 8
        assert re.fullmatch(r"[a-f0-9]{8}", suffix)

    def test_random_fallback_when_no_session_id_anywhere(self):
        """Should generate random hex suffix when neither source provides session_id."""
        from session_init import generate_team_name

        result = generate_team_name({})

        assert result.startswith("pact-")
        suffix = result[len("pact-"):]
        assert len(suffix) == 8
        assert re.fullmatch(r"[a-f0-9]{8}", suffix), f"Expected hex suffix, got: {suffix}"

    def test_random_fallback_produces_different_values(self):
        """Random fallback should produce different names across calls (probabilistic)."""
        from session_init import generate_team_name

        results = {generate_team_name({}) for _ in range(10)}

        assert len(results) > 1, "Expected different random names across 10 calls"

    def test_short_session_id_used_as_is(self):
        """Should use the full session_id when shorter than 8 chars."""
        from session_init import generate_team_name

        result = generate_team_name({"session_id": "abc"})

        assert result == "pact-abc"

    def test_empty_session_id_falls_back_to_random(self):
        """Empty string session_id should be treated as falsy, falling back to random hex."""
        from session_init import generate_team_name

        result = generate_team_name({"session_id": ""})

        assert result.startswith("pact-")
        suffix = result[len("pact-"):]
        assert len(suffix) == 8
        assert re.fullmatch(r"[a-f0-9]{8}", suffix)

    def test_session_id_from_input_data_used_directly(self):
        """session_id from input_data should be used as the sole source."""
        from session_init import generate_team_name

        result = generate_team_name({"session_id": "inputinp-aaaa-bbbb-cccc-ddddeeeeffff"})

        assert result == "pact-inputinp"

    def test_exactly_8_char_session_id(self):
        """Should handle a session_id that is exactly 8 characters."""
        from session_init import generate_team_name

        result = generate_team_name({"session_id": "a1b2c3d4"})

        assert result == "pact-a1b2c3d4"

    def test_none_session_id_falls_to_random(self):
        """None session_id in input_data should fall back to random."""
        from session_init import generate_team_name

        result = generate_team_name({"session_id": None})

        assert result.startswith("pact-")
        suffix = result[len("pact-"):]
        assert len(suffix) == 8
        assert re.fullmatch(r"[a-f0-9]{8}", suffix)

    def test_return_type_is_string(self):
        """Should always return a string."""
        from session_init import generate_team_name

        result = generate_team_name({"session_id": "test1234"})

        assert isinstance(result, str)


class TestTeamResumeDetection:
    """Tests for resume-aware team detection in session_init.main().

    The hook checks whether ~/.claude/teams/{team_name}/config.json exists
    to determine if this is a fresh session (TeamCreate instruction) or a
    resumed session (reuse instruction).
    """

    def _run_main_with_team_detection(self, monkeypatch, tmp_path, stdin_data=None):
        """Helper: run main() with Path.home() pointed at tmp_path.

        Returns the additionalContext string from the hook output.
        """
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/mj/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        if stdin_data is None:
            stdin_data = json.dumps({"session_id": "aabb1122-0000-0000-0000-000000000000"})

        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.update_claude_md", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(mock_stdout.getvalue())
        return output["hookSpecificOutput"]["additionalContext"]

    def test_fresh_session_emits_team_create(self, monkeypatch, tmp_path):
        """When no team config exists on disk, should emit TeamCreate instruction."""
        additional = self._run_main_with_team_detection(monkeypatch, tmp_path)

        assert 'TeamCreate(team_name="pact-aabb1122")' in additional
        assert "Do not call TeamCreate" not in additional

    def test_resume_session_emits_reuse_instruction(self, monkeypatch, tmp_path):
        """When team config exists on disk, should emit reuse instruction."""
        # Create the team config file to simulate a resumed session
        team_dir = tmp_path / ".claude" / "teams" / "pact-aabb1122"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text('{"members": []}')

        additional = self._run_main_with_team_detection(monkeypatch, tmp_path)

        assert "existing — resumed session" in additional
        assert "Do not call TeamCreate" in additional
        assert "pact-aabb1122" in additional

    def test_oserror_falls_back_to_team_create(self, monkeypatch, tmp_path):
        """When filesystem check raises OSError, should fall back to TeamCreate."""
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/mj/Sites/test-project")

        # Make Path.home() raise OSError indirectly by patching Path.exists
        original_exists = Path.exists

        def exists_that_raises(self):
            if "config.json" in str(self) and "teams" in str(self):
                raise OSError("Simulated filesystem error")
            return original_exists(self)

        monkeypatch.setattr(Path, "exists", exists_that_raises)

        stdin_data = json.dumps({"session_id": "aabb1122-0000-0000-0000-000000000000"})

        from session_init import main

        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.update_claude_md", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(mock_stdout.getvalue())
        additional = output["hookSpecificOutput"]["additionalContext"]
        assert 'TeamCreate(team_name="pact-aabb1122")' in additional

    def test_team_instruction_is_first_in_context(self, monkeypatch, tmp_path):
        """Team instruction should be inserted at position 0 (first in context)."""
        additional = self._run_main_with_team_detection(monkeypatch, tmp_path)

        # The team instruction uses insert(0, ...) so it should be first
        # additionalContext is " | ".join(context_parts), so team instruction
        # should be at the start
        assert additional.startswith("Your FIRST action must be")


class TestSourceAwareness:
    """Tests for session source detection in session_init.main().

    The hook reads input_data["source"] which has 4 values:
    - "startup": fresh session (full init)
    - "resume": resumed session (model retains context)
    - "compact": context window compacted (model lost context)
    - "clear": /clear command (intentional context reset)

    Tests cover all 8 combinations (4 sources x 2 team states) plus edge cases.
    """

    def _run_main_with_source(
        self, monkeypatch, tmp_path, source, team_exists=False
    ):
        """Helper: run main() with given source and team state.

        Returns (additionalContext, mock_symlinks_called, mock_claude_md_called).
        """
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/mj/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        if team_exists:
            team_dir = tmp_path / ".claude" / "teams" / "pact-aabb1122"
            team_dir.mkdir(parents=True)
            (team_dir / "config.json").write_text('{"members": []}')

        stdin_data = json.dumps({
            "session_id": "aabb1122-0000-0000-0000-000000000000",
            "source": source,
        })

        with patch("session_init.setup_plugin_symlinks", return_value=None) as mock_symlinks, \
             patch("session_init.update_claude_md", return_value=None) as mock_claude_md, \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(mock_stdout.getvalue())
        additional = output["hookSpecificOutput"]["additionalContext"]
        return additional, mock_symlinks.called, mock_claude_md.called

    # --- Path 1: startup + no team (fresh session) ---

    def test_startup_no_team_creates_team(self, monkeypatch, tmp_path):
        """startup + no team: should emit TeamCreate instruction."""
        additional, _, _ = self._run_main_with_source(
            monkeypatch, tmp_path, source="startup", team_exists=False
        )

        assert 'TeamCreate(team_name="pact-aabb1122")' in additional
        assert "Do not call TeamCreate" not in additional
        assert "WARNING" not in additional

    def test_startup_calls_symlinks_and_claude_md(self, monkeypatch, tmp_path):
        """startup should run full init (symlinks + CLAUDE.md)."""
        _, symlinks_called, claude_md_called = self._run_main_with_source(
            monkeypatch, tmp_path, source="startup", team_exists=False
        )

        assert symlinks_called
        assert claude_md_called

    # --- Path 2: resume + team exists (normal resume) ---

    def test_resume_team_exists_reuse(self, monkeypatch, tmp_path):
        """resume + team exists: should emit reuse instruction with paused-state hint."""
        additional, _, _ = self._run_main_with_source(
            monkeypatch, tmp_path, source="resume", team_exists=True
        )

        assert "existing — resumed session" in additional
        assert "Do not call TeamCreate" in additional
        assert "pact-aabb1122" in additional
        assert "paused state" in additional
        # Should NOT have recovery instructions for context resets
        assert "compact-summary.txt" not in additional
        assert "CONTEXT CLEARED" not in additional
        assert "POST-COMPACTION" not in additional

    def test_resume_calls_symlinks_and_claude_md(self, monkeypatch, tmp_path):
        """resume should run full init (symlinks + CLAUDE.md)."""
        _, symlinks_called, claude_md_called = self._run_main_with_source(
            monkeypatch, tmp_path, source="resume", team_exists=True
        )

        assert symlinks_called
        assert claude_md_called

    # --- Path 3: compact + team exists (post-compaction recovery) ---

    def test_compact_team_exists_recovery(self, monkeypatch, tmp_path):
        """compact + team exists: should emit POST-COMPACTION recovery instructions."""
        additional, _, _ = self._run_main_with_source(
            monkeypatch, tmp_path, source="compact", team_exists=True
        )

        assert "existing — resumed session" in additional
        assert "Do not call TeamCreate" in additional
        assert "POST-COMPACTION" in additional
        assert "compact-summary.txt" in additional
        assert "TaskList" in additional
        assert "secretary" in additional

    def test_compact_skips_symlinks(self, monkeypatch, tmp_path):
        """compact should skip symlink setup (already done)."""
        _, symlinks_called, _ = self._run_main_with_source(
            monkeypatch, tmp_path, source="compact", team_exists=True
        )

        assert not symlinks_called

    def test_compact_skips_claude_md_update(self, monkeypatch, tmp_path):
        """compact should skip CLAUDE.md update (already installed)."""
        _, _, claude_md_called = self._run_main_with_source(
            monkeypatch, tmp_path, source="compact", team_exists=True
        )

        assert not claude_md_called

    # --- Path 4: clear + team exists (context intentionally cleared) ---

    def test_clear_team_exists_context_cleared(self, monkeypatch, tmp_path):
        """clear + team exists: should emit CONTEXT CLEARED with recovery."""
        additional, _, _ = self._run_main_with_source(
            monkeypatch, tmp_path, source="clear", team_exists=True
        )

        assert "existing — resumed session" in additional
        assert "Do not call TeamCreate" in additional
        assert "CONTEXT CLEARED" in additional
        assert "TaskList" in additional
        assert "secretary" in additional
        # Should NOT reference compact-summary (no file created on /clear)
        assert "compact-summary.txt" not in additional

    def test_clear_skips_symlinks(self, monkeypatch, tmp_path):
        """clear should skip symlink setup (already done)."""
        _, symlinks_called, _ = self._run_main_with_source(
            monkeypatch, tmp_path, source="clear", team_exists=True
        )

        assert not symlinks_called

    def test_clear_skips_claude_md_update(self, monkeypatch, tmp_path):
        """clear should skip CLAUDE.md update (already installed)."""
        _, _, claude_md_called = self._run_main_with_source(
            monkeypatch, tmp_path, source="clear", team_exists=True
        )

        assert not claude_md_called

    # --- Path 5: anomalous combinations ---

    def test_startup_team_exists_anomalous(self, monkeypatch, tmp_path):
        """startup + team exists: anomalous — should reuse team with note."""
        additional, _, _ = self._run_main_with_source(
            monkeypatch, tmp_path, source="startup", team_exists=True
        )

        assert "existing — resumed session" in additional
        assert "Do not call TeamCreate" in additional
        assert "Unexpected" in additional or "Note" in additional
        assert "TaskList" in additional

    def test_resume_no_team_anomalous(self, monkeypatch, tmp_path):
        """resume + no team: anomalous — should create team with warning."""
        additional, _, _ = self._run_main_with_source(
            monkeypatch, tmp_path, source="resume", team_exists=False
        )

        assert 'TeamCreate(team_name="pact-aabb1122")' in additional
        assert "WARNING" in additional

    def test_compact_no_team_anomalous(self, monkeypatch, tmp_path):
        """compact + no team: anomalous — should create team with warning."""
        additional, _, _ = self._run_main_with_source(
            monkeypatch, tmp_path, source="compact", team_exists=False
        )

        assert 'TeamCreate(team_name="pact-aabb1122")' in additional
        assert "WARNING" in additional
        assert "team not found" in additional.lower()

    def test_clear_no_team_anomalous(self, monkeypatch, tmp_path):
        """clear + no team: anomalous — should create team with warning."""
        additional, _, _ = self._run_main_with_source(
            monkeypatch, tmp_path, source="clear", team_exists=False
        )

        assert 'TeamCreate(team_name="pact-aabb1122")' in additional
        assert "WARNING" in additional

    # --- Edge cases ---

    def test_missing_source_defaults_to_startup(self, monkeypatch, tmp_path):
        """Missing source field should default to startup behavior."""
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/mj/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # stdin_data without "source" key
        stdin_data = json.dumps({
            "session_id": "aabb1122-0000-0000-0000-000000000000",
        })

        with patch("session_init.setup_plugin_symlinks", return_value=None) as mock_symlinks, \
             patch("session_init.update_claude_md", return_value=None) as mock_claude_md, \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # Should behave like startup: full init, TeamCreate
        assert mock_symlinks.called
        assert mock_claude_md.called
        output = json.loads(mock_stdout.getvalue())
        additional = output["hookSpecificOutput"]["additionalContext"]
        assert 'TeamCreate(team_name="pact-aabb1122")' in additional
        assert "POST-COMPACTION" not in additional
        assert "CONTEXT CLEARED" not in additional

    def test_unknown_source_with_team_is_anomalous(self, monkeypatch, tmp_path):
        """Unknown source value + team exists: should reuse team with note."""
        additional, _, _ = self._run_main_with_source(
            monkeypatch, tmp_path, source="unknown_value", team_exists=True
        )

        assert "existing — resumed session" in additional
        assert "Unexpected" in additional or "Note" in additional

    def test_unknown_source_without_team_creates_with_warning(self, monkeypatch, tmp_path):
        """Unknown source value + no team: should create team with warning."""
        additional, _, _ = self._run_main_with_source(
            monkeypatch, tmp_path, source="unknown_value", team_exists=False
        )

        assert 'TeamCreate(team_name="pact-aabb1122")' in additional
        assert "WARNING" in additional


class TestMainPausedStateIntegration:
    """Integration test: check_paused_state wiring in session_init.main()."""

    def test_paused_state_appears_in_additional_context(self, monkeypatch):
        """Non-None check_paused_state result should appear in additionalContext output."""
        from session_init import main

        paused_msg = "Paused work detected: PR #42 (feat/login) — awaiting merge."

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/mj/Sites/test-project")

        # Provide valid JSON on stdin with a session_id
        stdin_data = json.dumps({"session_id": "aabb1122-0000-0000-0000-000000000000"})

        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.update_claude_md", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=paused_msg) as mock_paused, \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        mock_paused.assert_called_once_with(prev_session_dir=None)

        output = json.loads(mock_stdout.getvalue())
        additional = output["hookSpecificOutput"]["additionalContext"]
        assert paused_msg in additional

    def test_none_paused_state_excluded_from_output(self, monkeypatch):
        """None check_paused_state result should not appear in additionalContext."""
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/mj/Sites/test-project")

        stdin_data = json.dumps({"session_id": "aabb1122-0000-0000-0000-000000000000"})

        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.update_claude_md", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit):
                main()

        output = json.loads(mock_stdout.getvalue())
        additional = output["hookSpecificOutput"]["additionalContext"]
        assert "Paused work" not in additional


class TestMainPrevSessionDirOrdering:
    """Regression: _extract_prev_session_dir must run BEFORE update_session_info.

    Bug: in an earlier revision, main() called update_session_info() (which
    overwrites the Current Session block in CLAUDE.md with THIS session's info)
    before calling _extract_prev_session_dir(). That caused _extract_prev_session_dir
    to read back the just-written current session dir, silently breaking:
      - restore_last_session(prev_session_dir=...) -> reads current (empty) journal
      - check_paused_state(prev_session_dir=...)   -> never finds paused events

    Invariant: READ prior CLAUDE.md BEFORE OVERWRITING it.

    This test drives main() end-to-end (no mocking of update_session_info,
    _extract_prev_session_dir, restore_last_session, or check_paused_state).
    It pre-seeds a prior session with a session_paused event in its journal and
    asserts the paused-state message appears in the hook output — which can
    only happen if prev_session_dir was captured before the block rewrite.
    """

    def test_prev_session_dir_captured_before_claude_md_rewrite(
        self, monkeypatch, tmp_path
    ):
        """End-to-end: paused state from prior session is detected despite
        update_session_info() overwriting CLAUDE.md in the same main() call."""
        from session_init import main

        # --- Arrange: tmp home + tmp project dir ---
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))

        # --- Arrange: prior session dir with a session_paused event in its journal ---
        prior_session_id = "deadbeef-1111-2222-3333-444455556666"
        prior_session_dir = (
            tmp_path / ".claude" / "pact-sessions" / "project" / prior_session_id
        )
        prior_session_dir.mkdir(parents=True)
        prior_journal = prior_session_dir / "session-journal.jsonl"
        paused_event = {
            "v": 1,
            "type": "session_paused",
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pr_number": 9999,
            "pr_url": "https://github.com/example/repo/pull/9999",
            "branch": "feat/prior-session-branch",
            "worktree_path": str(tmp_path / "wt" / "prior"),
            "consolidation_completed": True,
            "team_name": "pact-deadbeef",
        }
        prior_journal.write_text(json.dumps(paused_event) + "\n", encoding="utf-8")

        # --- Arrange: project CLAUDE.md with a Session dir line pointing at the prior dir ---
        # Use an absolute path (not ~) so _extract_prev_session_dir returns it verbatim
        # and we can assert against it below.
        prior_session_dir_str = str(prior_session_dir)
        claude_md_dir = project_dir / ".claude"
        claude_md_dir.mkdir()
        claude_md = claude_md_dir / "CLAUDE.md"
        claude_md.write_text(
            "# Project Memory\n"
            "\n"
            "<!-- SESSION_START -->\n"
            "## Current Session\n"
            f"- Resume: `claude --resume {prior_session_id}`\n"
            "- Team: `pact-deadbeef`\n"
            f"- Session dir: `{prior_session_dir_str}`\n"
            "- Started: 2025-01-01 00:00:00 UTC\n"
            "<!-- SESSION_END -->\n",
            encoding="utf-8",
        )

        # --- Arrange: current session id (different from the prior one) ---
        current_session_id = "aabb1122-0000-0000-0000-000000000000"
        stdin_data = json.dumps({"session_id": current_session_id})

        # Spies so we can assert exactly which prev_session_dir the downstream
        # calls received. We wrap rather than replace so the real implementations
        # still run (end-to-end coverage).
        from session_init import (
            check_paused_state as real_check_paused_state,
            restore_last_session as real_restore_last_session,
        )

        restore_calls: list[str | None] = []
        paused_calls: list[str | None] = []

        def spy_restore_last_session(prev_session_dir=None):
            restore_calls.append(prev_session_dir)
            return real_restore_last_session(prev_session_dir=prev_session_dir)

        def spy_check_paused_state(prev_session_dir=None):
            paused_calls.append(prev_session_dir)
            return real_check_paused_state(prev_session_dir=prev_session_dir)

        # Patch boundary side effects that are unrelated to the ordering invariant:
        #   - setup_plugin_symlinks / update_claude_md / ensure_project_memory_md /
        #     check_pinned_staleness: touch user home / plugin root — not under test
        #   - _check_pr_state: shells out to `gh pr view`; patch to OPEN so the
        #     paused_msg path is exercised instead of being suppressed.
        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.update_claude_md", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch(
                 "shared.session_resume._check_pr_state",
                 return_value="OPEN",
             ), \
             patch(
                 "session_init.restore_last_session",
                 side_effect=spy_restore_last_session,
             ), \
             patch(
                 "session_init.check_paused_state",
                 side_effect=spy_check_paused_state,
             ), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

        # --- Assert: both downstream calls saw the PRIOR session dir, not the current one ---
        assert restore_calls == [prior_session_dir_str], (
            f"restore_last_session should have received the prior session dir "
            f"({prior_session_dir_str!r}), but got {restore_calls!r}. "
            f"This indicates update_session_info() ran before "
            f"_extract_prev_session_dir() and clobbered the CLAUDE.md block."
        )
        assert paused_calls == [prior_session_dir_str], (
            f"check_paused_state should have received the prior session dir "
            f"({prior_session_dir_str!r}), but got {paused_calls!r}."
        )

        # --- Assert: the paused-work message made it into the hook output ---
        output = json.loads(mock_stdout.getvalue())
        additional = output["hookSpecificOutput"]["additionalContext"]
        assert "Paused work detected: PR #9999" in additional
        assert "feat/prior-session-branch" in additional

        # --- Assert: update_session_info DID in fact rewrite the block to the current session ---
        # (confirms the ordering fix didn't accidentally skip the write)
        rewritten = claude_md.read_text(encoding="utf-8")
        assert f"claude --resume {current_session_id}" in rewritten
        assert f"claude --resume {prior_session_id}" not in rewritten


class TestCompactSummaryCleanup:
    """Tests for stale compact-summary.txt cleanup in session_init.main()."""

    def _run_main_with_source_and_summary(self, monkeypatch, tmp_path, source):
        """Helper: run main() with compact-summary.txt present.

        Returns whether compact-summary.txt still exists after main().
        """
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/mj/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Create compact-summary.txt
        sessions_dir = tmp_path / ".claude" / "pact-sessions"
        sessions_dir.mkdir(parents=True)
        summary_file = sessions_dir / "compact-summary.txt"
        summary_file.write_text("Prior compaction context")

        # Patch COMPACT_SUMMARY_PATH to point to our tmp_path version
        patched_path = summary_file

        stdin_data = json.dumps({
            "session_id": "aabb1122-0000-0000-0000-000000000000",
            "source": source,
        })

        with patch("session_init.COMPACT_SUMMARY_PATH", patched_path), \
             patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.update_claude_md", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        return summary_file.exists()

    @pytest.mark.parametrize("source", ["startup", "resume", "clear"])
    def test_non_compact_source_deletes_stale_summary(self, monkeypatch, tmp_path, source):
        """Non-compact sources (startup, resume, clear) should delete compact-summary.txt."""
        still_exists = self._run_main_with_source_and_summary(
            monkeypatch, tmp_path, source
        )
        assert not still_exists, (
            f"compact-summary.txt should be deleted for source='{source}'"
        )

    def test_compact_source_preserves_summary(self, monkeypatch, tmp_path):
        """Compact source should preserve compact-summary.txt (it was just written)."""
        still_exists = self._run_main_with_source_and_summary(
            monkeypatch, tmp_path, "compact"
        )
        assert still_exists, (
            "compact-summary.txt should be preserved for source='compact'"
        )


class TestCheckAdditionalDirectories:
    """Tests for check_additional_directories() — ~/.claude/teams tip.

    The function reads ~/.claude/settings.json and checks if ~/.claude/teams
    (or its absolute equivalent) is listed in permissions.additionalDirectories.
    Returns a tip message if missing, None if present. Fail-open on all errors.
    """

    def _write_settings(self, tmp_path, settings_data):
        """Helper: write settings.json under tmp_path/.claude/."""
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir(parents=True, exist_ok=True)
        settings_file = settings_dir / "settings.json"
        settings_file.write_text(json.dumps(settings_data), encoding="utf-8")
        return settings_file

    def test_returns_none_when_absolute_path_present(self, monkeypatch, tmp_path):
        """Should return None when absolute path to teams dir is in additionalDirectories."""
        from session_init import check_additional_directories

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        teams_abs = str(tmp_path / ".claude" / "teams")
        self._write_settings(tmp_path, {
            "permissions": {"additionalDirectories": [teams_abs]}
        })

        result = check_additional_directories()

        assert result is None

    def test_returns_none_when_tilde_path_present(self, monkeypatch, tmp_path):
        """Should return None when ~/.claude/teams (tilde form) is in additionalDirectories."""
        from session_init import check_additional_directories

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._write_settings(tmp_path, {
            "permissions": {"additionalDirectories": ["~/.claude/teams"]}
        })

        result = check_additional_directories()

        assert result is None

    def test_returns_tip_when_setting_missing(self, monkeypatch, tmp_path):
        """Should return tip message when teams dir is not in additionalDirectories."""
        from session_init import check_additional_directories

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._write_settings(tmp_path, {
            "permissions": {"additionalDirectories": ["/some/other/path"]}
        })

        result = check_additional_directories()

        assert result is not None
        assert "additionalDirectories" in result
        assert "~/.claude/teams" in result

    def test_returns_tip_when_additional_directories_empty(self, monkeypatch, tmp_path):
        """Should return tip when additionalDirectories is an empty list."""
        from session_init import check_additional_directories

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._write_settings(tmp_path, {
            "permissions": {"additionalDirectories": []}
        })

        result = check_additional_directories()

        assert result is not None
        assert "~/.claude/teams" in result

    def test_returns_none_when_settings_file_missing(self, monkeypatch, tmp_path):
        """Should return None (fail-open) when settings.json does not exist."""
        from session_init import check_additional_directories

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Do NOT create settings.json

        result = check_additional_directories()

        assert result is None

    def test_returns_none_on_malformed_json(self, monkeypatch, tmp_path):
        """Should return None (fail-open) when settings.json is malformed."""
        from session_init import check_additional_directories

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir(parents=True)
        (settings_dir / "settings.json").write_text("{invalid json!!!", encoding="utf-8")

        result = check_additional_directories()

        assert result is None

    def test_returns_tip_when_permissions_key_missing(self, monkeypatch, tmp_path):
        """Should return tip when permissions key is missing (empty additionalDirectories)."""
        from session_init import check_additional_directories

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._write_settings(tmp_path, {"env": {"FOO": "bar"}})

        result = check_additional_directories()

        # permissions missing → .get("permissions", {}).get("additionalDirectories", [])
        # returns [] → no matching entry → tip message returned
        assert result is not None
        assert "~/.claude/teams" in result

    def test_returns_none_when_additional_dirs_not_list(self, monkeypatch, tmp_path):
        """Should return None (fail-open) when additionalDirectories is not a list."""
        from session_init import check_additional_directories

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        self._write_settings(tmp_path, {
            "permissions": {"additionalDirectories": "not-a-list"}
        })

        result = check_additional_directories()

        assert result is None

    def test_returns_none_when_path_home_raises(self, monkeypatch):
        """Should return None (fail-open) when Path.home() raises an exception."""
        from session_init import check_additional_directories

        def home_that_raises():
            raise RuntimeError("Simulated home dir error")

        monkeypatch.setattr(Path, "home", home_that_raises)

        result = check_additional_directories()

        assert result is None

    def test_ignores_non_string_entries(self, monkeypatch, tmp_path):
        """Should skip non-string entries in additionalDirectories without crashing."""
        from session_init import check_additional_directories

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        teams_abs = str(tmp_path / ".claude" / "teams")
        self._write_settings(tmp_path, {
            "permissions": {"additionalDirectories": [42, None, teams_abs]}
        })

        result = check_additional_directories()

        assert result is None  # Found the valid path after skipping non-strings


class TestCheckAdditionalDirectoriesMainIntegration:
    """Integration tests: check_additional_directories wiring in session_init.main()."""

    def test_tip_appears_in_system_message_when_missing(self, monkeypatch, tmp_path):
        """Tip should appear in systemMessage when teams dir not configured."""
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/mj/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Create settings.json WITHOUT ~/.claude/teams in additionalDirectories
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir(parents=True)
        (settings_dir / "settings.json").write_text(
            json.dumps({"permissions": {"additionalDirectories": []}}),
            encoding="utf-8",
        )

        stdin_data = json.dumps({"session_id": "aabb1122-0000-0000-0000-000000000000"})

        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.update_claude_md", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(mock_stdout.getvalue())
        system_msg = output.get("systemMessage", "")
        assert "additionalDirectories" in system_msg
        assert "~/.claude/teams" in system_msg

    def test_no_tip_when_setting_present(self, monkeypatch, tmp_path):
        """No tip should appear when teams dir is already configured."""
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/mj/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Create settings.json WITH ~/.claude/teams in additionalDirectories
        teams_abs = str(tmp_path / ".claude" / "teams")
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir(parents=True)
        (settings_dir / "settings.json").write_text(
            json.dumps({"permissions": {"additionalDirectories": [teams_abs]}}),
            encoding="utf-8",
        )

        stdin_data = json.dumps({"session_id": "aabb1122-0000-0000-0000-000000000000"})

        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.update_claude_md", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(mock_stdout.getvalue())
        # systemMessage should not contain the tip (or should be absent entirely)
        system_msg = output.get("systemMessage", "")
        assert "PACT tip" not in system_msg

    @pytest.mark.parametrize("source", ["compact", "clear"])
    def test_tip_skipped_on_context_reset(self, monkeypatch, tmp_path, source):
        """Tip should NOT be checked on compact/clear sources (context resets)."""
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/mj/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Create settings.json WITHOUT ~/.claude/teams — tip would fire on startup
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir(parents=True)
        (settings_dir / "settings.json").write_text(
            json.dumps({"permissions": {"additionalDirectories": []}}),
            encoding="utf-8",
        )

        # Create team config so compact path doesn't hit the anomalous branch
        team_dir = tmp_path / ".claude" / "teams" / "pact-aabb1122"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text('{"members": []}')

        stdin_data = json.dumps({
            "session_id": "aabb1122-0000-0000-0000-000000000000",
            "source": source,
        })

        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.update_claude_md", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        output = json.loads(mock_stdout.getvalue())
        system_msg = output.get("systemMessage", "")
        assert "PACT tip" not in system_msg


class TestWriteContextIntegration:
    """Integration tests: write_context() call wiring in session_init.main().

    Verifies that main() calls write_context() with the correct arguments
    derived from the session's team_name, session_id, and project_dir.
    """

    def test_write_context_called_with_correct_args(self, monkeypatch, tmp_path):
        """write_context should be called with team_name, session_id, project_dir."""
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/mj/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        stdin_data = json.dumps({
            "session_id": "aabb1122-0000-0000-0000-000000000000",
        })

        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.update_claude_md", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch("session_init.write_context") as mock_write_ctx, \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        mock_write_ctx.assert_called_once_with(
            "pact-aabb1122",
            "aabb1122-0000-0000-0000-000000000000",
            "/Users/mj/Sites/test-project",
            "",  # plugin_root: CLAUDE_PLUGIN_ROOT not set in this test
        )

    def test_write_context_gets_empty_session_id_when_stdin_lacks_it(self, monkeypatch, tmp_path):
        """write_context should receive empty session_id when stdin has none."""
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/mj/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        stdin_data = json.dumps({})  # No session_id in stdin

        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.update_claude_md", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch("session_init.write_context") as mock_write_ctx, \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO):
            with pytest.raises(SystemExit):
                main()

        # session_id should be empty string — no env var fallback
        call_args = mock_write_ctx.call_args[0]
        assert call_args[1] == ""

    def test_write_context_failure_does_not_block_session(self, monkeypatch, tmp_path):
        """write_context failure should not prevent session_init from completing."""
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/mj/Sites/test-project")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        stdin_data = json.dumps({
            "session_id": "aabb1122-0000-0000-0000-000000000000",
        })

        def write_that_raises(*args, **kwargs):
            raise OSError("Simulated write failure")

        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.update_claude_md", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch("session_init.write_context", side_effect=write_that_raises), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit) as exc_info:
                main()

        # Should still exit cleanly (fail-open)
        assert exc_info.value.code == 0
        output = json.loads(mock_stdout.getvalue())
        # Should still have team instruction in output
        assert "pact-aabb1122" in output["hookSpecificOutput"]["additionalContext"]


# =============================================================================
# _extract_prev_session_dir() Dual-Location Tests
# =============================================================================


class TestExtractPrevSessionDirDualLocation:
    """Tests for _extract_prev_session_dir() honoring both project CLAUDE.md
    locations.

    Claude Code accepts the project memory file at either:
      - $CLAUDE_PROJECT_DIR/.claude/CLAUDE.md   (preferred / new default)
      - $CLAUDE_PROJECT_DIR/CLAUDE.md           (legacy)

    _extract_prev_session_dir must locate the previous session's journal
    regardless of which location is in use, with .claude/CLAUDE.md taking
    priority when both exist.
    """

    _CONTENT_TEMPLATE = (
        "# Project\n"
        "<!-- SESSION_START -->\n"
        "## Current Session\n"
        "- Resume: `claude --resume {sid}`\n"
        "- Team: `pact-{sid_short}`\n"
        "- Session dir: `{session_dir}`\n"
        "<!-- SESSION_END -->\n"
    )

    def _make_content(self, session_id: str, session_dir: str) -> str:
        return self._CONTENT_TEMPLATE.format(
            sid=session_id,
            sid_short=session_id[:8],
            session_dir=session_dir,
        )

    def test_returns_none_when_neither_location_exists(self, tmp_path):
        """Returns None when neither .claude/CLAUDE.md nor ./CLAUDE.md exists."""
        from session_init import _extract_prev_session_dir

        result = _extract_prev_session_dir(str(tmp_path))

        assert result is None

    def test_returns_none_when_project_dir_empty(self):
        """Returns None when project_dir is the empty string."""
        from session_init import _extract_prev_session_dir

        assert _extract_prev_session_dir("") is None

    def test_reads_dot_claude_when_only_dot_claude_exists(self, tmp_path):
        """Reads .claude/CLAUDE.md when it is the only location present."""
        from session_init import _extract_prev_session_dir

        dot_claude_dir = tmp_path / ".claude"
        dot_claude_dir.mkdir()
        expected = "/tmp/sessions/dot-claude-only"
        (dot_claude_dir / "CLAUDE.md").write_text(
            self._make_content("aaaaaaaa-1111-2222-3333-444444444444", expected),
            encoding="utf-8",
        )
        # Legacy must NOT exist for this case
        assert not (tmp_path / "CLAUDE.md").exists()

        result = _extract_prev_session_dir(str(tmp_path))

        assert result == expected

    def test_reads_legacy_when_only_legacy_exists(self, tmp_path):
        """Reads ./CLAUDE.md when it is the only location present."""
        from session_init import _extract_prev_session_dir

        expected = "/tmp/sessions/legacy-only"
        (tmp_path / "CLAUDE.md").write_text(
            self._make_content("bbbbbbbb-1111-2222-3333-444444444444", expected),
            encoding="utf-8",
        )
        # .claude/ must NOT exist for this case
        assert not (tmp_path / ".claude").exists()

        result = _extract_prev_session_dir(str(tmp_path))

        assert result == expected

    def test_prefers_dot_claude_when_both_exist(self, tmp_path):
        """When both files exist, .claude/CLAUDE.md is the source of truth."""
        from session_init import _extract_prev_session_dir

        dot_claude_dir = tmp_path / ".claude"
        dot_claude_dir.mkdir()
        preferred = "/tmp/sessions/dot-claude-preferred"
        legacy = "/tmp/sessions/legacy-ignored"

        (dot_claude_dir / "CLAUDE.md").write_text(
            self._make_content(
                "cccccccc-1111-2222-3333-444444444444", preferred
            ),
            encoding="utf-8",
        )
        (tmp_path / "CLAUDE.md").write_text(
            self._make_content(
                "dddddddd-1111-2222-3333-444444444444", legacy
            ),
            encoding="utf-8",
        )

        result = _extract_prev_session_dir(str(tmp_path))

        assert result == preferred
        assert result != legacy

    def test_resume_line_fallback_when_session_dir_missing(self, tmp_path, monkeypatch):
        """M3: derives session dir from Resume line + project basename when
        the `- Session dir:` line is absent.

        Targets the fallback branch at session_init.py:207-218. Backward-compat
        path for sessions written before the Session dir line existed: when only
        the `- Resume:` line is present, the function reconstructs the path as
        ~/.claude/pact-sessions/<project-basename>/<session-id>.
        """
        from session_init import _extract_prev_session_dir

        # Pin Path.home() so the asserted path is deterministic.
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        # Build a project dir whose basename is the slug used in the fallback.
        project_dir = tmp_path / "MyProject"
        project_dir.mkdir()
        dot_claude = project_dir / ".claude"
        dot_claude.mkdir()

        session_id = "12345678-1234-1234-1234-123456789abc"
        # No "- Session dir:" line — only the Resume line.
        (dot_claude / "CLAUDE.md").write_text(
            "# Project\n"
            "<!-- SESSION_START -->\n"
            "## Current Session\n"
            f"- Resume: `claude --resume {session_id}`\n"
            "- Team: `pact-12345678`\n"
            "- Started: 2026-01-01 00:00:00 UTC\n"
            "<!-- SESSION_END -->\n",
            encoding="utf-8",
        )

        result = _extract_prev_session_dir(str(project_dir))

        expected = str(
            (tmp_path / "home") / ".claude" / "pact-sessions"
            / "MyProject" / session_id
        )
        assert result == expected

    def test_regex_miss_on_existing_claude_md_logs_warning(
        self, tmp_path, monkeypatch, capsys,
    ):
        """A1: log a stderr warning when CLAUDE.md exists but the primary regex misses.

        The fallback-to-Resume-line path is intentional and benign for older
        sessions that never wrote the `- Session dir:` line. But it is also
        how a silent format regression would present — future changes to
        session_resume.update_session_info could drop or rename the Session
        dir line and the test suite would not notice because the fallback
        quietly succeeds. The fix adds a one-line stderr warning on the
        regex-miss path so any format drift becomes visible.

        This test:
          (a) writes a CLAUDE.md where the primary `- Session dir:` regex
              will miss (only a Resume line is present),
          (b) asserts the fallback still succeeds (behavior unchanged), and
          (c) asserts the stderr warning fired with the diagnostic message.
        """
        from session_init import _extract_prev_session_dir

        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        project_dir = tmp_path / "MyProject"
        project_dir.mkdir()
        dot_claude = project_dir / ".claude"
        dot_claude.mkdir()

        session_id = "abcdef01-2345-6789-abcd-ef0123456789"
        # CLAUDE.md exists but lacks the `- Session dir:` line — the primary
        # regex must miss while the Resume-line fallback still resolves.
        (dot_claude / "CLAUDE.md").write_text(
            "# Project\n"
            "<!-- SESSION_START -->\n"
            "## Current Session\n"
            f"- Resume: `claude --resume {session_id}`\n"
            "- Team: `pact-abcdef01`\n"
            "<!-- SESSION_END -->\n",
            encoding="utf-8",
        )

        result = _extract_prev_session_dir(str(project_dir))

        # (a) Fallback path still resolves the session dir — behavior unchanged.
        expected = str(
            (tmp_path / "home") / ".claude" / "pact-sessions"
            / "MyProject" / session_id
        )
        assert result == expected

        # (b) The stderr warning message fires so format drift is visible.
        captured = capsys.readouterr()
        assert "_extract_prev_session_dir regex failed" in captured.err
        assert "falling back to Resume-line" in captured.err

    def test_regex_match_does_not_log_warning(
        self, tmp_path, monkeypatch, capsys,
    ):
        """A1 happy path: no warning when the primary regex matches.

        Negative companion to the regex-miss test above. Pins that the
        warning is not spuriously emitted on the common case where the
        Session dir line IS present — a regression there would add noise
        to every SessionStart hook run.
        """
        from session_init import _extract_prev_session_dir

        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        project_dir = tmp_path / "MyProject"
        project_dir.mkdir()
        dot_claude = project_dir / ".claude"
        dot_claude.mkdir()

        expected = "/tmp/sessions/happy"
        (dot_claude / "CLAUDE.md").write_text(
            "# Project\n"
            "<!-- SESSION_START -->\n"
            "## Current Session\n"
            "- Resume: `claude --resume aaaaaaaa-0000-0000-0000-000000000000`\n"
            "- Team: `pact-aaaaaaaa`\n"
            f"- Session dir: `{expected}`\n"
            "<!-- SESSION_END -->\n",
            encoding="utf-8",
        )

        result = _extract_prev_session_dir(str(project_dir))

        assert result == expected
        captured = capsys.readouterr()
        # The warning must NOT fire on the happy path.
        assert "_extract_prev_session_dir regex failed" not in captured.err


# =============================================================================
# CLAUDE_PLUGIN_ROOT env-set wiring tests (M6)
# =============================================================================


class TestPluginRootEnvWiring:
    """M6: Verify that main() honors CLAUDE_PLUGIN_ROOT when already set in env.

    The happy-path tests (TestWriteContextIntegration) cover the case where
    CLAUDE_PLUGIN_ROOT is unset (empty string is passed through). These tests
    cover the complementary case where the Claude Code plugin loader sets
    CLAUDE_PLUGIN_ROOT BEFORE session_init runs. session_init.py:317 reads
    the env var directly:

        plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")

    So the test merely needs to assert that when the env var is set, the
    value flows through to:
      (a) write_context() -> pact-session-context.json
      (b) update_session_info() -> `- Plugin root:` line in CLAUDE.md
    """

    @pytest.fixture(autouse=True)
    def _reset_pact_context_cache(self, monkeypatch):
        """T2: reset pact_context module state before every test in this class.

        `write_context()` caches the computed session directory path in
        `pact_context._cache` and `pact_context._context_path` on first
        call. Because pytest loads `shared.pact_context` once per process
        and tests in this class exercise `main()` against different
        project dirs / tmp_paths, a stale cache from a prior test can
        leak into the next one — the second test's `write_context` short-
        circuits on the cached state and writes to the old path.

        Previously only M6c reset these via inline `monkeypatch.setattr`;
        M6a and M6b were incidentally safe because their assertions were
        on mock call args or CLAUDE.md file contents rather than on
        `pact-session-context.json` contents. Promoting the reset to a
        class-scoped autouse fixture hardens all three tests and any
        future additions to the class against cache leakage.
        """
        import shared.pact_context as pact_context
        monkeypatch.setattr(pact_context, "_context_path", None)
        monkeypatch.setattr(pact_context, "_cache", None)

    def test_plugin_root_env_flows_to_write_context(self, monkeypatch, tmp_path):
        """M6a: CLAUDE_PLUGIN_ROOT in env is passed as write_context's 4th arg."""
        from session_init import main

        plugin_root_value = "/some/custom/plugin/root"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", plugin_root_value)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        stdin_data = json.dumps({
            "session_id": "aabb1122-0000-0000-0000-000000000000",
        })

        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.update_claude_md", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch("session_init.write_context") as mock_write_ctx, \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # 4th positional arg of write_context is plugin_root
        mock_write_ctx.assert_called_once_with(
            "pact-aabb1122",
            "aabb1122-0000-0000-0000-000000000000",
            str(tmp_path / "proj"),
            plugin_root_value,
        )

    def test_plugin_root_env_flows_to_update_session_info(
        self, monkeypatch, tmp_path
    ):
        """M6b: CLAUDE_PLUGIN_ROOT value is passed to update_session_info
        and lands in the `- Plugin root:` line of the CLAUDE.md SESSION_START
        block.

        This test does NOT mock update_session_info so the real function
        actually writes CLAUDE.md -- the assertion is on file contents.
        """
        from session_init import main

        plugin_root_value = "/opt/pact-plugin/installed/2.0.0"

        # Set up a real project dir so update_session_info can write to it.
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", plugin_root_value)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        stdin_data = json.dumps({
            "session_id": "aabb1122-0000-0000-0000-000000000000",
        })

        # Intentionally NOT mocking update_session_info — we want it to run
        # for real against the tmp_path project dir. Everything else that
        # touches ~/.claude or the plugin root is mocked.
        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.update_claude_md", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

        # update_session_info should have created/updated the project CLAUDE.md
        # at the preferred .claude/CLAUDE.md location with the Plugin root line.
        claude_md = project_dir / ".claude" / "CLAUDE.md"
        assert claude_md.exists(), (
            "update_session_info should have created .claude/CLAUDE.md"
        )
        content = claude_md.read_text(encoding="utf-8")
        assert "<!-- SESSION_START -->" in content
        assert f"- Plugin root: `{plugin_root_value}`" in content, (
            f"CLAUDE_PLUGIN_ROOT env value should have been written to the "
            f"Plugin root line, but got:\n{content}"
        )

        # Also assert the Plugin root line is inside the SESSION_START block
        # (not appended elsewhere).
        start = content.index("<!-- SESSION_START -->")
        end = content.index("<!-- SESSION_END -->")
        session_block = content[start:end]
        assert f"- Plugin root: `{plugin_root_value}`" in session_block

    def test_plugin_root_env_flows_to_pact_session_context_json(
        self, monkeypatch, tmp_path
    ):
        """M6c: CLAUDE_PLUGIN_ROOT lands in pact-session-context.json on disk.

        Unlike M6a (which mocks write_context to assert call args), this test
        lets write_context actually run and reads the JSON back off disk to
        verify the plugin_root field is persisted.
        """
        from session_init import main

        plugin_root_value = "/my/plugin/root/for/persistence/check"

        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
        monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", plugin_root_value)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        session_id = "ccdd3344-0000-0000-0000-000000000000"
        stdin_data = json.dumps({"session_id": session_id})

        # pact_context._cache / _context_path reset handled by the class's
        # autouse _reset_pact_context_cache fixture (T2) — no inline reset
        # needed here.

        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.update_claude_md", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

        # Context JSON lives at
        # ~/.claude/pact-sessions/<project-basename>/<session-id>/pact-session-context.json
        context_file = (
            tmp_path / ".claude" / "pact-sessions" / "proj" / session_id
            / "pact-session-context.json"
        )
        assert context_file.exists(), (
            f"pact-session-context.json not found at {context_file}"
        )
        data = json.loads(context_file.read_text(encoding="utf-8"))
        assert data["plugin_root"] == plugin_root_value
        assert data["session_id"] == session_id
        assert data["team_name"] == "pact-ccdd3344"
