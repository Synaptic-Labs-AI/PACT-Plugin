"""
Tests for hooks/postcompact_archive.py — PostCompact hook that writes the
compact summary to disk for the secretary.

Per #444 Tertiary, this hook no longer emits systemMessage — the previous
"critical context preserved" reassurance surface was removed to avoid
suppressing orchestrator self-check. The surviving responsibilities are:
- Write compact_summary to disk for the secretary
- Emit {"suppressOutput": true} on clean exits (matches the pre-#444
  compaction_refresh.py output pattern for clean paths)
- Emit hook_error_json on unexpected failure (unchanged)

Tests cover:
1. Compact summary file writing (path, permissions, content)
2. Subprocess integration (suppressOutput emission, no systemMessage)
3. Fail-open on malformed input and errors
4. Outer exception handler (hook_error_json output)
5. Module constants
"""
import json
import stat
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


HOOK_PATH = str(Path(__file__).parent.parent / "hooks" / "postcompact_archive.py")


def run_hook(stdin_data: str | None = None) -> subprocess.CompletedProcess:
    """Run the hook as a subprocess and return the result."""
    return subprocess.run(
        [sys.executable, HOOK_PATH],
        input=stdin_data or "",
        capture_output=True,
        text=True,
        timeout=10,
    )


# ---------------------------------------------------------------------------
# Unit tests: write_compact_summary
# ---------------------------------------------------------------------------


class TestWriteCompactSummary:
    """Test compact summary file writing."""

    def test_writes_file(self, tmp_path):
        from postcompact_archive import write_compact_summary
        result = write_compact_summary("Test summary", str(tmp_path))
        assert result is True
        path = tmp_path / "compact-summary.txt"
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "Test summary"

    def test_creates_parent_dirs(self, tmp_path):
        from postcompact_archive import write_compact_summary
        deep_dir = str(tmp_path / "a" / "b" / "c")
        result = write_compact_summary("content", deep_dir)
        assert result is True
        assert (Path(deep_dir) / "compact-summary.txt").exists()

    def test_secure_permissions(self, tmp_path):
        from postcompact_archive import write_compact_summary
        write_compact_summary("secure content", str(tmp_path))
        path = tmp_path / "compact-summary.txt"
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_overwrites_existing_file(self, tmp_path):
        from postcompact_archive import write_compact_summary
        write_compact_summary("first", str(tmp_path))
        write_compact_summary("second", str(tmp_path))
        path = tmp_path / "compact-summary.txt"
        assert path.read_text(encoding="utf-8") == "second"

    def test_returns_false_on_error(self, tmp_path):
        from postcompact_archive import write_compact_summary
        # Point at a file path where parent can't be created
        fake_file = tmp_path / "blocker"
        fake_file.write_text("x", encoding="utf-8")
        result = write_compact_summary("test", str(fake_file / "nested"))
        assert result is False

    def test_empty_summary_writes_empty_file(self, tmp_path):
        from postcompact_archive import write_compact_summary
        write_compact_summary("", str(tmp_path))
        path = tmp_path / "compact-summary.txt"
        assert path.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# Integration tests: subprocess
# ---------------------------------------------------------------------------


class TestPostcompactSubprocess:
    """Verify hook output via subprocess.

    Per #444: output is {"suppressOutput": true} on clean paths. No
    systemMessage — the previously-emitted "critical context preserved"
    message was a reassurance surface that could suppress orchestrator
    self-check.
    """

    def test_emits_suppress_output_not_system_message(self):
        """Clean path: subprocess emits {"suppressOutput": true} with
        no systemMessage key."""
        result = run_hook(json.dumps({"compact_summary": "Test summary"}))
        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        assert output == {"suppressOutput": True}
        assert "systemMessage" not in output

    def test_exits_zero_with_empty_summary(self):
        result = run_hook(json.dumps({"compact_summary": ""}))
        assert result.returncode == 0

    def test_exits_zero_with_no_summary_field(self):
        result = run_hook(json.dumps({"other_field": "data"}))
        assert result.returncode == 0

    def test_empty_summary_still_emits_suppress_output(self):
        """Even when compact_summary is empty, clean path returns
        {"suppressOutput": true} — no systemMessage."""
        result = run_hook(json.dumps({"compact_summary": ""}))
        output = json.loads(result.stdout.strip())
        assert output == {"suppressOutput": True}
        assert "systemMessage" not in output


# ---------------------------------------------------------------------------
# Fail-open tests
# ---------------------------------------------------------------------------


class TestPostcompactFailOpen:
    """Verify fail-open behavior."""

    def test_empty_stdin_exits_zero(self):
        result = run_hook("")
        assert result.returncode == 0

    def test_malformed_json_exits_zero(self):
        result = run_hook("not json")
        assert result.returncode == 0

    def test_null_input_exits_zero(self):
        result = run_hook("null")
        assert result.returncode == 0

    def test_array_input_exits_zero(self):
        result = run_hook("[]")
        assert result.returncode == 0

    def test_malformed_json_still_emits_suppress_output(self):
        """Malformed stdin still goes through the happy path (empty
        summary) and emits {"suppressOutput": true}."""
        result = run_hook("not json")
        output = json.loads(result.stdout.strip())
        assert output == {"suppressOutput": True}


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify module-level constants."""

    def test_compact_summary_path_from_shared_constants(self):
        from shared.constants import COMPACT_SUMMARY_PATH
        assert COMPACT_SUMMARY_PATH.name == "compact-summary.txt"
        assert "pact-sessions" in str(COMPACT_SUMMARY_PATH)

    def test_postcompact_uses_shared_path(self):
        """Verify postcompact_archive imports COMPACT_SUMMARY_PATH from shared."""
        from postcompact_archive import _get_summary_path
        from shared.constants import COMPACT_SUMMARY_PATH
        # Default path (no override) should match the shared constant
        assert _get_summary_path() == COMPACT_SUMMARY_PATH


# ---------------------------------------------------------------------------
# Outer exception handler tests
# ---------------------------------------------------------------------------


class TestPostcompactOuterExceptionHandler:
    """Verify that main() catches unexpected exceptions, exits 0,
    emits hook_error_json on stdout and error info on stderr.

    Post-#444: the target function for the simulated failure changes
    from the deleted build_verification_message to write_compact_summary
    (the only external call remaining in main()'s happy path).
    """

    # #881: the compact-summary write is now gated behind is_lead, so these
    # outer-exception-handler tests must present a LEAD frame (agent_type) for
    # the patched write_compact_summary side-effect to actually fire.
    def test_exits_zero_on_unexpected_error(self):
        """main() must exit 0 even when write_compact_summary raises."""
        from postcompact_archive import main

        stdin_data = json.dumps(
            {"compact_summary": "test", "agent_type": "pact-orchestrator"}
        )
        with patch("sys.stdin", StringIO(stdin_data)), \
             patch("postcompact_archive.write_compact_summary",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_stderr_contains_error_info(self, capsys):
        """Error details must appear on stderr for logging."""
        from postcompact_archive import main

        stdin_data = json.dumps(
            {"compact_summary": "test", "agent_type": "pact-orchestrator"}
        )
        with patch("sys.stdin", StringIO(stdin_data)), \
             patch("postcompact_archive.write_compact_summary",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert "postcompact_archive" in captured.err
        assert "test error" in captured.err

    def test_stdout_contains_hook_error_json(self, capsys):
        """Stdout must contain structured JSON from hook_error_json."""
        from postcompact_archive import main

        stdin_data = json.dumps(
            {"compact_summary": "test", "agent_type": "pact-orchestrator"}
        )
        with patch("sys.stdin", StringIO(stdin_data)), \
             patch("postcompact_archive.write_compact_summary",
                   side_effect=RuntimeError("test error")):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        assert "systemMessage" in output
        assert "PACT hook warning" in output["systemMessage"]
        assert "postcompact_archive" in output["systemMessage"]
        assert "test error" in output["systemMessage"]


# ---------------------------------------------------------------------------
# #881: lead-only gate on the global-singleton compact-summary write
# ---------------------------------------------------------------------------


class TestPostcompactLeadGate:
    """The compact-summary write is gated behind is_lead (#881).

    COMPACT_SUMMARY_PATH is a GLOBAL SINGLETON the lead reads on resume, and
    the write is O_TRUNC. A teammate/plain frame's PostCompact must NOT clobber
    it. These are smoke tests (call / no-call of write_compact_summary by
    role); comprehensive per-role suppression coverage is the TEST phase.
    """

    def _run_main_with(self, frame):
        from postcompact_archive import main

        stdin_data = json.dumps(frame)
        with patch("sys.stdin", StringIO(stdin_data)), \
             patch("postcompact_archive.write_compact_summary") as mock_write:
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
            return mock_write

    def test_lead_qualified_writes(self):
        from fixtures.role_frames import postcompact_frame
        mock_write = self._run_main_with(
            postcompact_frame("PACT:pact-orchestrator", compact_summary="x")
        )
        mock_write.assert_called_once_with("x")

    def test_lead_unqualified_writes(self):
        from fixtures.role_frames import postcompact_frame
        mock_write = self._run_main_with(
            postcompact_frame("pact-orchestrator", compact_summary="x")
        )
        mock_write.assert_called_once_with("x")

    def test_teammate_suppressed(self):
        from fixtures.role_frames import postcompact_frame
        mock_write = self._run_main_with(
            postcompact_frame("pact-backend-coder", compact_summary="x")
        )
        mock_write.assert_not_called()

    def test_plain_frame_suppressed(self):
        """No agent_type (no --agent) → not lead → write suppressed."""
        from fixtures.role_frames import postcompact_frame
        mock_write = self._run_main_with(
            postcompact_frame(None, compact_summary="x")
        )
        mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# #4 (#883 fold-in): real-disk defense-in-depth for the #881 lead-gate.
# ---------------------------------------------------------------------------


class TestPostcompactLeadGateRealDisk:
    """Defense-in-depth complement to TestPostcompactLeadGate (which mocks
    write_compact_summary and asserts call/no-call). Here the REAL function runs
    against a REAL file on disk: a teammate/plain frame through main() must NOT
    truncate the global-singleton compact-summary file (#881's O_TRUNC clobber).

    COMPACT_SUMMARY_PATH is computed at IMPORT in shared.constants
    (Path.home()/...), so a post-import Path.home monkeypatch does NOT redirect
    it; we monkeypatch the name postcompact_archive binds (it does
    `from shared.constants import COMPACT_SUMMARY_PATH`) to a tmp file. main()
    calls write_compact_summary(summary) with no base-dir → _get_summary_path()
    returns this redirected path.
    """

    _SENTINEL = "PRIOR LEAD SUMMARY — must survive a teammate PostCompact"

    def _run_main_realdisk(self, frame, monkeypatch, tmp_path):
        from postcompact_archive import main

        summary_path = tmp_path / "compact-summary.txt"
        summary_path.write_text(self._SENTINEL, encoding="utf-8")
        # Redirect the global-singleton path (import-frozen constant) to tmp.
        monkeypatch.setattr(
            "postcompact_archive.COMPACT_SUMMARY_PATH", summary_path
        )

        with patch("sys.stdin", StringIO(json.dumps(frame))):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        return summary_path

    def test_teammate_frame_does_not_truncate_real_file(self, monkeypatch, tmp_path):
        """A teammate PostCompact must leave the lead's on-disk compact-summary
        UNTOUCHED — the #881 is_lead gate suppresses the real O_TRUNC write."""
        from fixtures.role_frames import postcompact_frame
        summary_path = self._run_main_realdisk(
            postcompact_frame("pact-backend-coder", compact_summary="TEAMMATE CLOBBER"),
            monkeypatch, tmp_path,
        )
        assert summary_path.read_text(encoding="utf-8") == self._SENTINEL, (
            "a teammate PostCompact truncated the global compact-summary file — "
            "the #881 lead-gate failed to suppress the real O_TRUNC write"
        )

    def test_plain_frame_does_not_truncate_real_file(self, monkeypatch, tmp_path):
        """A plain (no-agent_type) PostCompact must also leave the file intact."""
        from fixtures.role_frames import postcompact_frame
        summary_path = self._run_main_realdisk(
            postcompact_frame(None, compact_summary="PLAIN CLOBBER"),
            monkeypatch, tmp_path,
        )
        assert summary_path.read_text(encoding="utf-8") == self._SENTINEL

    def test_lead_frame_does_overwrite_real_file(self, monkeypatch, tmp_path):
        """Positive symmetry: a LEAD PostCompact DOES write the file (the gate
        suppresses only NON-lead frames; the lead's archival must still work)."""
        from fixtures.role_frames import postcompact_frame
        summary_path = self._run_main_realdisk(
            postcompact_frame("PACT:pact-orchestrator", compact_summary="NEW LEAD SUMMARY"),
            monkeypatch, tmp_path,
        )
        assert summary_path.read_text(encoding="utf-8") == "NEW LEAD SUMMARY", (
            "a lead PostCompact must still archive the compact summary"
        )
