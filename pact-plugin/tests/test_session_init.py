"""
Tests for session_init.py — SessionStart hook.

Tests cover:
generate_team_name():
1. Happy path: session_id from input_data -> "pact-{first 8 chars}"
2. Env var fallback: CLAUDE_SESSION_ID used when input_data has no session_id
3. Random fallback: random hex suffix when neither source available
4. Short session_id: less than 8 chars used as-is
5. Empty session_id: treated as falsy, falls back to env or random
6. Input_data session_id takes precedence over env var
7. Output format validation (pact- prefix, hex suffix)
8. None session_id: treated as falsy, falls back to random

Resume-aware team detection (main() integration):
9. Fresh session (no config file) → TeamCreate instruction emitted
10. Resume session (config file exists) → reuse instruction emitted
11. OSError fallback → TeamCreate instruction (fail-open)
12. Team instruction is first in context_parts (insert at position 0)

main() integration:
13. check_paused_state non-None result appears in additionalContext output

Note: restore_last_session() and check_resumption_context() are tested
in test_session_resume.py (canonical location).
"""

import io
import json
import re
import sys
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

    def test_env_var_fallback_when_no_session_id_in_input(self, monkeypatch):
        """Should fall back to CLAUDE_SESSION_ID env var when input_data lacks session_id."""
        from session_init import generate_team_name

        monkeypatch.setenv("CLAUDE_SESSION_ID", "deadbeef-1234-5678-9abc-def012345678")

        result = generate_team_name({})

        assert result == "pact-deadbeef"

    def test_env_var_fallback_when_session_id_key_missing(self, monkeypatch):
        """Should fall back to env var when session_id key is absent from input_data."""
        from session_init import generate_team_name

        monkeypatch.setenv("CLAUDE_SESSION_ID", "cafebabe-0000-1111-2222-333344445555")

        result = generate_team_name({"other_key": "value"})

        assert result == "pact-cafebabe"

    def test_random_fallback_when_no_session_id_anywhere(self, monkeypatch):
        """Should generate random hex suffix when neither source provides session_id."""
        from session_init import generate_team_name

        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

        result = generate_team_name({})

        assert result.startswith("pact-")
        suffix = result[len("pact-"):]
        assert len(suffix) == 8
        assert re.fullmatch(r"[a-f0-9]{8}", suffix), f"Expected hex suffix, got: {suffix}"

    def test_random_fallback_produces_different_values(self, monkeypatch):
        """Random fallback should produce different names across calls (probabilistic)."""
        from session_init import generate_team_name

        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

        results = {generate_team_name({}) for _ in range(10)}

        assert len(results) > 1, "Expected different random names across 10 calls"

    def test_short_session_id_used_as_is(self):
        """Should use the full session_id when shorter than 8 chars."""
        from session_init import generate_team_name

        result = generate_team_name({"session_id": "abc"})

        assert result == "pact-abc"

    def test_empty_session_id_falls_back_to_env(self, monkeypatch):
        """Empty string session_id should be treated as falsy, falling back to env var."""
        from session_init import generate_team_name

        monkeypatch.setenv("CLAUDE_SESSION_ID", "feedface-0000-1111-2222-333344445555")

        result = generate_team_name({"session_id": ""})

        assert result == "pact-feedface"

    def test_empty_session_id_falls_back_to_random(self, monkeypatch):
        """Empty string session_id with no env var should fall back to random."""
        from session_init import generate_team_name

        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

        result = generate_team_name({"session_id": ""})

        assert result.startswith("pact-")
        suffix = result[len("pact-"):]
        assert len(suffix) == 8
        assert re.fullmatch(r"[a-f0-9]{8}", suffix)

    def test_input_data_takes_precedence_over_env_var(self, monkeypatch):
        """session_id from input_data should take priority over CLAUDE_SESSION_ID env var."""
        from session_init import generate_team_name

        monkeypatch.setenv("CLAUDE_SESSION_ID", "envenvev-0000-1111-2222-333344445555")

        result = generate_team_name({"session_id": "inputinp-aaaa-bbbb-cccc-ddddeeeeffff"})

        assert result == "pact-inputinp"

    def test_exactly_8_char_session_id(self):
        """Should handle a session_id that is exactly 8 characters."""
        from session_init import generate_team_name

        result = generate_team_name({"session_id": "a1b2c3d4"})

        assert result == "pact-a1b2c3d4"

    def test_none_session_id_falls_to_random(self, monkeypatch):
        """None session_id in input_data should fall back to random."""
        from session_init import generate_team_name

        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

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
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
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
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

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
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
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
        assert "paused-state.json" in additional
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
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
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
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

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
        mock_paused.assert_called_once_with(project_slug="test-project")

        output = json.loads(mock_stdout.getvalue())
        additional = output["hookSpecificOutput"]["additionalContext"]
        assert paused_msg in additional

    def test_none_paused_state_excluded_from_output(self, monkeypatch):
        """None check_paused_state result should not appear in additionalContext."""
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/Users/mj/Sites/test-project")
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)

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
