"""Phase A env-file export pins for session_init's CLAUDE_ENV_FILE channel.

Pins the contract of ``session_init._persist_project_dir_env`` and its call
site in ``main()``:

- export written when BOTH $CLAUDE_ENV_FILE and $CLAUDE_PROJECT_DIR are present
- no-op when CLAUDE_ENV_FILE is unset (older platform versions fail open)
- no append when CLAUDE_PROJECT_DIR is unset (the structural guard: an
  env-absent frame's cwd-fallback value is never exported)
- producer-side shlex.quote for paths with spaces / ``$`` (the env file is
  sourced by a shell)
- re-fire dedupe: an identical export line is never appended twice
  (resume/compact/clear re-fires stay idempotent)
- exported == recorded invariant: the value appended to the env file is the
  same resolve-once value passed to build_context_cache (env verbatim when
  present; absolute cwd on the env-absent leg, never the retired "." default)

The main()-driven rows use the same injection-orthogonal stub set as
test_config_injection_both_modes.py — the env-file write happens at main()
top, before any stubbed collaborator runs.
"""
import io
import json
import os
import shlex
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import session_init

_PROJECT_DIR = "/Users/example/Sites/test-project"


def _run_main(monkeypatch, tmp_path, frame=None):
    """Drive real session_init.main() with heavy collaborators stubbed.
    Returns (stdout additionalContext, recorded project_dir captured from the
    build_context_cache call)."""
    recorded = {}

    def _capture_context_cache(*args, **kwargs):
        # build_context_cache(team_name, session_id, project_dir, plugin_root, ...)
        recorded["project_dir"] = args[2] if len(args) > 2 else kwargs.get("project_dir")
        return (Path("/tmp/ctx.json"), {})

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # A lead agent_type, so the run reaches build_context_cache: a frame with
    # no agent_type returns before it.
    stdin_data = json.dumps({
        "source": "startup",
        "session_id": "33334444-0000-0000-0000-000000000000",
        "agent_type": "PACT:pact-orchestrator",
        **(frame or {}),
    })
    with patch("session_init.setup_plugin_symlinks", return_value=None), \
         patch("session_init.ensure_project_memory_md", return_value=None), \
         patch("session_init.check_pinned_staleness", return_value=None), \
         patch("session_init.get_task_list", return_value=None), \
         patch("session_init.restore_last_session", return_value=None), \
         patch("session_init.build_context_cache", side_effect=_capture_context_cache), \
         patch("session_init.persist_context", return_value=None), \
         patch("session_init.append_event"), \
         patch("session_init.update_session_info", return_value=None), \
         patch("session_init.check_resume_state", return_value=None), \
         patch("session_init._registry_resolve", return_value=None), \
         patch("session_init.get_peer_context", return_value=None), \
         patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO):
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    return recorded.get("project_dir")


class TestEnvFileExport:
    """main()-driven contract rows (call-site gate + helper)."""

    def test_export_written_when_both_present(self, monkeypatch, tmp_path):
        env_file = tmp_path / "session-env.sh"  # deliberately not pre-created
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        recorded = _run_main(monkeypatch, tmp_path)
        assert env_file.read_text(encoding="utf-8") == (
            f"export CLAUDE_PROJECT_DIR={shlex.quote(_PROJECT_DIR)}\n"
        )
        assert recorded == _PROJECT_DIR

    def test_noop_when_env_file_unset(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
        monkeypatch.delenv("CLAUDE_ENV_FILE", raising=False)
        # No env-file channel -> clean no-op, exit 0, record side unaffected.
        recorded = _run_main(monkeypatch, tmp_path)
        assert recorded == _PROJECT_DIR

    def test_no_append_when_project_dir_unset(self, monkeypatch, tmp_path):
        env_file = tmp_path / "session-env.sh"
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        recorded = _run_main(monkeypatch, tmp_path)
        # Structural guard: env-absent frame never reaches the append...
        assert not env_file.exists() or "CLAUDE_PROJECT_DIR" not in env_file.read_text(encoding="utf-8")
        # ...and the record side gets the absolute cwd, never the retired "." default.
        assert recorded == os.getcwd()
        assert Path(recorded).is_absolute()

    def test_spacey_dollar_path_is_quoted(self, monkeypatch, tmp_path):
        spacey = "/Users/example/My Proj$ect/work"
        env_file = tmp_path / "session-env.sh"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", spacey)
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        recorded = _run_main(monkeypatch, tmp_path)
        assert env_file.read_text(encoding="utf-8") == (
            f"export CLAUDE_PROJECT_DIR={shlex.quote(spacey)}\n"
        )
        assert recorded == spacey  # verbatim, not resolved


class TestPersistProjectDirEnvHelper:
    """Helper-level guards, exercised directly (no main() drive)."""

    def test_refire_dedupe_appends_nothing_twice(self, monkeypatch, tmp_path):
        env_file = tmp_path / "session-env.sh"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        session_init._persist_project_dir_env(_PROJECT_DIR)
        session_init._persist_project_dir_env(_PROJECT_DIR)
        lines = env_file.read_text(encoding="utf-8").splitlines()
        assert lines == [f"export CLAUDE_PROJECT_DIR={shlex.quote(_PROJECT_DIR)}"]

    def test_non_absolute_value_skipped(self, monkeypatch, tmp_path):
        env_file = tmp_path / "session-env.sh"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        session_init._persist_project_dir_env("relative/dir")
        assert not env_file.exists()

    def test_env_var_unset_skips_even_with_arg(self, monkeypatch, tmp_path):
        env_file = tmp_path / "session-env.sh"
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        session_init._persist_project_dir_env(_PROJECT_DIR)
        assert not env_file.exists()

    def test_env_file_unset_skips(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
        monkeypatch.delenv("CLAUDE_ENV_FILE", raising=False)
        session_init._persist_project_dir_env(_PROJECT_DIR)  # must not raise

    # -- Fail-open pins (the never-raises contract on the SessionStart hot path)
    def test_env_file_in_nonexistent_directory_fails_open(self, monkeypatch, tmp_path):
        """The append's open() raises FileNotFoundError when the parent is
        missing; the helper must swallow it (OSError arm) — no raise, no file."""
        env_file = tmp_path / "no-such-dir" / "session-env.sh"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        session_init._persist_project_dir_env(_PROJECT_DIR)  # must not raise
        assert not env_file.exists()

    def test_non_utf8_env_file_fails_open_without_appending(self, monkeypatch, tmp_path):
        """A non-UTF-8 pre-existing env file raises UnicodeDecodeError on
        read_text — a ValueError, NOT an OSError. The widened catch must
        swallow it locally: no raise, and NO append (the whole body is
        skipped, so the foreign bytes are preserved byte-identical)."""
        env_file = tmp_path / "session-env.sh"
        bad = b"\xff\xfe\x00not-utf8"
        env_file.write_bytes(bad)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        session_init._persist_project_dir_env(_PROJECT_DIR)  # must not raise
        assert env_file.read_bytes() == bad, (
            "a failed read must skip the append — foreign content is preserved"
        )

    # -- Foreign-content append shape (the channel file can carry other hooks' lines)
    def test_append_after_unterminated_last_line_starts_on_its_own_line(
        self, monkeypatch, tmp_path
    ):
        """An unterminated foreign last line gets its newline written FIRST —
        the export never glues onto it, and the prior variable survives a real
        shell source intact."""
        env_file = tmp_path / "session-env.sh"
        env_file.write_text("export PRIOR=1", encoding="utf-8")  # no trailing newline
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        session_init._persist_project_dir_env(_PROJECT_DIR)
        assert env_file.read_text(encoding="utf-8") == (
            f"export PRIOR=1\nexport CLAUDE_PROJECT_DIR={shlex.quote(_PROJECT_DIR)}\n"
        )
        probe = subprocess.run(
            ["bash", "-c", 'source "$1" && printf "%s|%s" "$PRIOR" "$CLAUDE_PROJECT_DIR"',
             "_", str(env_file)],
            capture_output=True, text=True, timeout=30,
        )
        assert probe.returncode == 0, f"source failed: {probe.stderr!r}"
        assert probe.stdout == f"1|{_PROJECT_DIR}", (
            f"the glued-line corruption would read PRIOR=1export...; got {probe.stdout!r}"
        )

    def test_append_after_terminated_last_line_adds_no_blank_line(
        self, monkeypatch, tmp_path
    ):
        env_file = tmp_path / "session-env.sh"
        env_file.write_text("export PRIOR=1\n", encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        session_init._persist_project_dir_env(_PROJECT_DIR)
        assert env_file.read_text(encoding="utf-8") == (
            f"export PRIOR=1\nexport CLAUDE_PROJECT_DIR={shlex.quote(_PROJECT_DIR)}\n"
        )

    def test_newline_bearing_value_refire_appends_no_duplicate(self, monkeypatch, tmp_path):
        """A path containing a literal newline quotes to a MULTI-LINE
        single-quoted string (valid when sourced). The dedupe must self-match
        it on re-fire — raw-text match of the terminated logical line, not
        splitlines() membership, which false-misses and accumulates a
        duplicate on every resume/compact/clear re-fire."""
        env_file = tmp_path / "session-env.sh"
        newline_dir = "/Users/example/My\nProject"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", newline_dir)
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        session_init._persist_project_dir_env(newline_dir)
        after_first = env_file.read_text(encoding="utf-8")
        assert "CLAUDE_PROJECT_DIR" in after_first
        session_init._persist_project_dir_env(newline_dir)
        assert env_file.read_text(encoding="utf-8") == after_first, (
            "re-fire appended a duplicate — the dedupe does not self-match a "
            "newline-bearing value"
        )
        assert after_first.count("CLAUDE_PROJECT_DIR") == 1
