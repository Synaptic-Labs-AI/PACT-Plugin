"""
Tests for file_tracker.py — PostToolUse hook matching Edit|Write that tracks
which agent edits which files and warns on conflicts.

Tests cover:
1. Records file edit to tracking JSON
2. Detects conflict when different agent edits same file
3. No conflict when same agent edits same file again
4. Creates tracking file if missing
5. No-op when no agent name set
6. main() entry point: stdin JSON parsing, exit codes, output format
7. Corrupted tracking JSON treated as empty list
8. Path normalization: different representations of same file match
"""
import io
import json
import os
import sys
from unittest.mock import patch

import pytest


class TestFileTracker:
    """Tests for file_tracker.track_edit() and file_tracker.check_conflict()."""

    def test_records_edit(self, tmp_path):
        from file_tracker import track_edit

        tracking_file = tmp_path / "file-edits.json"

        # Use an absolute path to avoid cwd-dependent normalization
        abs_path = str(tmp_path / "src" / "auth.ts")
        track_edit(
            file_path=abs_path,
            agent_name="backend-coder",
            tool_name="Edit",
            tracking_path=str(tracking_file)
        )

        entries = json.loads(tracking_file.read_text())
        assert len(entries) == 1
        assert entries[0]["file"] == os.path.realpath(abs_path)
        assert entries[0]["agent"] == "backend-coder"

    def test_detects_conflict(self, tmp_path):
        from file_tracker import track_edit, check_conflict

        tracking_file = tmp_path / "file-edits.json"

        # First edit by backend-coder
        track_edit("src/auth.ts", "backend-coder", "Edit", str(tracking_file))

        # Check conflict for frontend-coder editing same file
        conflict = check_conflict("src/auth.ts", "frontend-coder", str(tracking_file))

        assert conflict is not None
        assert "backend-coder" in conflict

    def test_no_conflict_same_agent(self, tmp_path):
        from file_tracker import track_edit, check_conflict

        tracking_file = tmp_path / "file-edits.json"

        track_edit("src/auth.ts", "backend-coder", "Edit", str(tracking_file))
        conflict = check_conflict("src/auth.ts", "backend-coder", str(tracking_file))

        assert conflict is None

    def test_creates_tracking_file(self, tmp_path):
        from file_tracker import track_edit

        tracking_file = tmp_path / "file-edits.json"
        assert not tracking_file.exists()

        track_edit("src/auth.ts", "backend-coder", "Edit", str(tracking_file))

        assert tracking_file.exists()

    def test_noop_when_no_agent_name(self, tmp_path):
        from file_tracker import check_conflict

        tracking_file = tmp_path / "file-edits.json"
        conflict = check_conflict("src/auth.ts", "", str(tracking_file))

        assert conflict is None

    def test_corrupted_tracking_json_treated_as_empty(self, tmp_path):
        """Corrupted tracking file should be treated as empty list."""
        from file_tracker import track_edit

        tracking_file = tmp_path / "file-edits.json"
        tracking_file.write_text("not valid json{{{")

        abs_path = str(tmp_path / "src" / "auth.ts")
        # track_edit should overwrite with a fresh single-entry list
        track_edit(abs_path, "backend-coder", "Edit", str(tracking_file))

        entries = json.loads(tracking_file.read_text())
        assert len(entries) == 1
        assert entries[0]["file"] == os.path.realpath(abs_path)

    def test_corrupted_tracking_json_no_conflict(self, tmp_path):
        """check_conflict with corrupted tracking file should return None."""
        from file_tracker import check_conflict

        tracking_file = tmp_path / "file-edits.json"
        tracking_file.write_text("not valid json{{{")

        conflict = check_conflict("src/auth.ts", "backend-coder", str(tracking_file))

        assert conflict is None


class TestPathNormalization:
    """Tests for _normalize_path and its effect on conflict detection."""

    def test_relative_path_normalized_to_absolute(self, tmp_path, monkeypatch):
        """Relative paths are resolved to absolute before recording."""
        from file_tracker import track_edit

        tracking_file = tmp_path / "file-edits.json"

        # Use a real directory as cwd so os.path.realpath can resolve
        monkeypatch.chdir(tmp_path)
        track_edit("src/auth.ts", "backend-coder", "Edit", str(tracking_file))

        entries = json.loads(tracking_file.read_text())
        assert len(entries) == 1
        # The stored path should be absolute (resolved from cwd)
        assert entries[0]["file"] == str(tmp_path / "src" / "auth.ts")

    def test_dotslash_and_plain_paths_match(self, tmp_path, monkeypatch):
        """'./src/auth.ts' and 'src/auth.ts' should detect as same file."""
        from file_tracker import track_edit, check_conflict

        tracking_file = tmp_path / "file-edits.json"

        monkeypatch.chdir(tmp_path)
        track_edit("./src/auth.ts", "backend-coder", "Edit", str(tracking_file))

        conflict = check_conflict("src/auth.ts", "frontend-coder", str(tracking_file))
        assert conflict is not None
        assert "backend-coder" in conflict

    def test_dotdot_paths_normalized(self, tmp_path, monkeypatch):
        """Paths with '../' components are resolved correctly."""
        from file_tracker import track_edit, check_conflict

        tracking_file = tmp_path / "file-edits.json"

        monkeypatch.chdir(tmp_path)
        track_edit("src/../src/auth.ts", "backend-coder", "Edit", str(tracking_file))

        conflict = check_conflict("src/auth.ts", "frontend-coder", str(tracking_file))
        assert conflict is not None
        assert "backend-coder" in conflict

    def test_normalize_path_helper(self):
        """_normalize_path produces absolute, resolved paths."""
        from file_tracker import _normalize_path

        result = _normalize_path("/tmp/foo/../bar/baz.ts")
        assert result == os.path.join(os.path.realpath("/tmp"), "bar", "baz.ts")
        assert ".." not in result


class TestLockRelease:
    """Tests that fcntl lock is released even on exception."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="fcntl not available on Windows"
    )
    def test_lock_released_on_write_exception(self, tmp_path):
        """Lock must be released if an exception occurs during write operations."""
        import fcntl
        from file_tracker import track_edit

        tracking_file = tmp_path / "file-edits.json"
        tracking_file.write_text("[]")

        # Patch json.dumps to raise during the write phase (after lock acquired)
        with patch("file_tracker.json.dumps", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                track_edit("/tmp/test.ts", "agent-a", "Edit", str(tracking_file))

        # If the sidecar lock was released, it can be taken without blocking
        with open(tmp_path / "file-edits.json.lock", "r") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)


class TestMainEntryPoint:
    """Tests for file_tracker.main() stdin/stdout/exit behavior."""

    def test_main_exits_0_when_no_team_name(self):
        from file_tracker import main

        input_data = json.dumps({"tool_name": "Edit"})

        with patch("file_tracker.frame_team_and_name", return_value=("", "")), \
             patch("file_tracker.pact_context.init"), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_on_valid_edit(self):
        from file_tracker import main

        input_data = json.dumps({
            "tool_input": {"file_path": "src/auth.ts"},
            "tool_name": "Edit",
        })

        with patch("file_tracker.frame_team_and_name", return_value=("pact-test", "")), \
             patch("file_tracker.pact_context.init"), \
             patch("file_tracker.resolve_agent_name", return_value="backend-coder"), \
             patch("file_tracker.check_conflict", return_value=None), \
             patch("file_tracker.track_edit"), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_on_invalid_json(self):
        from file_tracker import main

        with patch("sys.stdin", io.StringIO("not json")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_exits_0_when_no_file_path(self):
        from file_tracker import main

        input_data = json.dumps({"tool_input": {}})

        with patch("file_tracker.frame_team_and_name", return_value=("pact-test", "")), \
             patch("file_tracker.pact_context.init"), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

    def test_main_outputs_warning_on_conflict(self, capsys):
        from file_tracker import main

        input_data = json.dumps({
            "tool_input": {"file_path": "src/auth.ts"},
            "tool_name": "Edit",
        })

        conflict_msg = "File conflict: src/auth.ts was also edited by backend-coder."
        with patch("file_tracker.frame_team_and_name", return_value=("pact-test", "")), \
             patch("file_tracker.pact_context.init"), \
             patch("file_tracker.resolve_agent_name", return_value="frontend-coder"), \
             patch("file_tracker.check_conflict", return_value=conflict_msg), \
             patch("file_tracker.track_edit"), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "additionalContext" in output["hookSpecificOutput"]
        assert "conflict" in output["hookSpecificOutput"]["additionalContext"].lower()
        # Issue #658: hookEventName is required by the harness schema; missing
        # it causes the harness to silently fail open (additionalContext dropped).
        assert output["hookSpecificOutput"]["hookEventName"] == "PostToolUse"


class TestFileTrackerCompositeKey:
    """NEW-1 (#878): the editor key is the composite (agent_name, session_id),
    so same-agent_type instances are distinguished under tmux.

    Smoke tests for the functional fix; comprehensive matrix is the TEST phase.
    """

    def test_same_agent_type_distinct_sessions_conflict_detected(self, tmp_path):
        """Two backend-coder instances (same agent_name, DIFFERENT session_id)
        editing one file → conflict DETECTED. The prior agent-name-only key
        false-negatived this under tmux. Only ONE other instance exists here,
        so the label is the bare name (no session suffix — disambiguation only
        kicks in when a name is shared across multiple OTHER editors)."""
        from file_tracker import track_edit, check_conflict

        tracking_file = str(tmp_path / "file-edits.json")
        abs_path = str(tmp_path / "src" / "auth.ts")

        # Instance A edits.
        track_edit(abs_path, "backend-coder", "Edit", tracking_file, session_id="sess-aaaa")
        # Instance B (same agent_name, different session) checks before editing.
        conflict = check_conflict(abs_path, "backend-coder", tracking_file, session_id="sess-bbbb")
        assert conflict is not None
        assert "auth.ts" in conflict
        assert "backend-coder" in conflict

    def test_two_same_name_other_instances_label_disambiguated(self, tmp_path):
        """When TWO other editors share an agent_name (different sessions), the
        labels are disambiguated with a session suffix so the message names two
        distinct editors rather than a confusing repeated bare name."""
        from file_tracker import track_edit, check_conflict

        tracking_file = str(tmp_path / "file-edits.json")
        abs_path = str(tmp_path / "src" / "auth.ts")

        track_edit(abs_path, "backend-coder", "Edit", tracking_file, session_id="sess-aaaa")
        track_edit(abs_path, "backend-coder", "Edit", tracking_file, session_id="sess-bbbb")
        # A third instance checks.
        conflict = check_conflict(abs_path, "backend-coder", tracking_file, session_id="sess-cccc")
        assert conflict is not None
        assert "session sess-aaa" in conflict
        assert "session sess-bbb" in conflict

    def test_same_instance_twice_no_false_positive(self, tmp_path):
        """The SAME instance (same agent_name AND session_id) editing twice is
        NOT a conflict."""
        from file_tracker import track_edit, check_conflict

        tracking_file = str(tmp_path / "file-edits.json")
        abs_path = str(tmp_path / "src" / "auth.ts")

        track_edit(abs_path, "backend-coder", "Edit", tracking_file, session_id="sess-aaaa")
        conflict = check_conflict(abs_path, "backend-coder", tracking_file, session_id="sess-aaaa")
        assert conflict is None

    def test_in_process_distinct_agent_names_shared_session_detected(self, tmp_path):
        """In-process model (one process → one shared session_id, distinct
        agent_names) → conflict still DETECTED via the agent_name half of the
        composite."""
        from file_tracker import track_edit, check_conflict

        tracking_file = str(tmp_path / "file-edits.json")
        abs_path = str(tmp_path / "src" / "auth.ts")

        track_edit(abs_path, "backend-coder", "Edit", tracking_file, session_id="sess-shared")
        conflict = check_conflict(abs_path, "frontend-coder", tracking_file, session_id="sess-shared")
        assert conflict is not None
        assert "backend-coder" in conflict

    def test_track_edit_records_session_id(self, tmp_path):
        """The composite-key session_id component is persisted in the entry."""
        from file_tracker import track_edit

        tracking_file = tmp_path / "file-edits.json"
        abs_path = str(tmp_path / "src" / "auth.ts")
        track_edit(abs_path, "backend-coder", "Edit", str(tracking_file), session_id="sess-xyz")

        entries = json.loads(tracking_file.read_text())
        assert entries[0]["session_id"] == "sess-xyz"
        assert entries[0]["agent"] == "backend-coder"  # label retained

    def test_single_other_editor_label_not_disambiguated(self, tmp_path):
        """When only ONE other editor instance exists for a name, the label is
        the bare agent_name (no noisy session suffix)."""
        from file_tracker import track_edit, check_conflict

        tracking_file = str(tmp_path / "file-edits.json")
        abs_path = str(tmp_path / "src" / "auth.ts")

        track_edit(abs_path, "frontend-coder", "Edit", tracking_file, session_id="sess-aaaa")
        conflict = check_conflict(abs_path, "backend-coder", tracking_file, session_id="sess-bbbb")
        assert conflict is not None
        assert "frontend-coder" in conflict
        assert "session" not in conflict  # unambiguous single editor → no suffix


# ---------------------------------------------------------------------------
# A separate-process teammate's own process: no PACT context
# ---------------------------------------------------------------------------


class TestFileTrackerInASeparateProcess:
    """`python3 hooks/file_tracker.py` with no pact-session-context.json.

    A separate-process teammate's own process has no PACT context, so its team
    and member name come from its session-registry entry, and the session half
    of the editor key comes from the frame's own `session_id`.
    """

    TEAM = "session-ftframe"
    PROJECT = "/ft-frame/project"

    def _root(self, tmp_path, members):
        config = tmp_path / ".claude"
        (config / "teams" / self.TEAM).mkdir(parents=True)
        (config / "teams" / self.TEAM / "config.json").write_text(json.dumps({
            "leadSessionId": "ft-lead-session",
            "members": [{"name": m, "agentId": f"{m}@{self.TEAM}",
                         "agentType": "pact-backend-coder"} for m in members],
        }), encoding="utf-8")
        return config

    def _register(self, config, session_id, member):
        registry = config / "pact-sessions" / ".teammate-registry.jsonl"
        registry.parent.mkdir(parents=True, exist_ok=True)
        with registry.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"session_id": session_id, "value": f"{member}@{self.TEAM}"}) + "\n")

    def _edit(self, tmp_path, session_id, file_path) -> str:
        """One PostToolUse Edit through the real hook process; returns additionalContext."""
        import os
        import subprocess
        import sys
        from pathlib import Path

        hook = Path(__file__).resolve().parents[1] / "hooks" / "file_tracker.py"
        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR", "CLAUDE_CODE_SESSION_ID")}
        env.update(HOME=str(tmp_path), CLAUDE_CONFIG_DIR=str(tmp_path / ".claude"),
                   CLAUDE_PROJECT_DIR=self.PROJECT)
        frame = {"hook_event_name": "PostToolUse", "session_id": session_id,
                 "agent_type": "pact-backend-coder", "tool_name": "Edit",
                 "tool_input": {"file_path": file_path}}
        proc = subprocess.run([sys.executable, str(hook)], input=json.dumps(frame),
                              capture_output=True, text=True, timeout=30, env=env)
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout or "{}")
        return out.get("hookSpecificOutput", {}).get("additionalContext", "")

    def test_a_separate_process_teammate_edit_is_tracked_and_warned(self, tmp_path):
        """REVERT PROOF. The first editor's edit lands in the team's file-edits.json
        under its member name, and a second editor's edit to the same file draws
        the conflict warning naming it."""
        config = self._root(tmp_path, ["tmux-editor", "other-editor"])
        self._register(config, "ft-session-a", "tmux-editor")
        self._register(config, "ft-session-b", "other-editor")
        target = str(tmp_path / "shared.py")
        assert self._edit(tmp_path, "ft-session-a", target) == ""
        tracking = config / "teams" / self.TEAM / "file-edits.json"
        assert tracking.exists(), "the separate-process teammate's edit was not tracked"
        edits = json.loads(tracking.read_text())
        assert [(e["agent"], e["session_id"]) for e in edits] == [("tmux-editor", "ft-session-a")]
        warning = self._edit(tmp_path, "ft-session-b", target)
        assert "File conflict" in warning and "tmux-editor" in warning, warning

    def test_two_same_name_separate_process_editors_draw_the_conflict_warning(self, tmp_path):
        """REVERT PROOF. Two instances with one member name are separate editors
        only through their sessions; without the frame's session_id both keys
        would be (name, "") and the second edit would look like the first."""
        config = self._root(tmp_path, ["tmux-editor"])
        self._register(config, "ft-session-a", "tmux-editor")
        self._register(config, "ft-session-b", "tmux-editor")
        target = str(tmp_path / "shared.py")
        assert self._edit(tmp_path, "ft-session-a", target) == ""
        warning = self._edit(tmp_path, "ft-session-b", target)
        assert "File conflict" in warning, warning
