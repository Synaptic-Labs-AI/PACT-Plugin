"""session_init per-write role-suppression matrix (#877) — end-to-end.

The comprehensive complement to the coder's function-level verification in
test_session_init.py. Drives the REAL session_init.main() end-to-end via
synthetic stdin (under the established Path.home/tmp_path isolation) so the
actual ``frame_is_lead = is_lead(input_data)`` computation and the gating
branches execute — not a mock of them.

THE MATRIX: each of session_init's 4 Class-A lead-only writes ×
{lead / teammate / plain}:

    write                         | lead | teammate | plain
    ------------------------------|------|----------|------
    write_context disk-write      | runs | SUPPRESS | SUPPRESS
    append_event(session_start)   | runs | SUPPRESS | SUPPRESS
    update_session_info           | runs | SUPPRESS | SUPPRESS
    check_paused_state            | runs | SUPPRESS | SUPPRESS

CRUCIAL split-correctness property (the one non-mechanical site, #877):
``write_context`` populates the in-process ``_cache`` / ``_context_path``
UNCONDITIONALLY (every frame) so ``get_session_dir()`` / append_event's
path-resolution keep working — but gates ONLY the on-disk write on is_lead.
A teammate/plain frame must get the cache populated AND no disk file written.
``TestWriteContextSplit`` exercises the REAL write_context (not a mock) to pin
this.

Lead path is the coder's existing coverage (test_session_init.py
TestWriteContextIntegration etc.); this file is the SUPPRESSION-side
comprehensiveness add: teammate + plain rows for every write, plus the split.
"""

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures.role_frames import (
    lead_frame_qualified,
    lead_frame_unqualified,
    plain_frame,
    teammate_frame,
)


_SESSION_ID = "aabb1122-0000-0000-0000-000000000000"
_PROJECT_DIR = "/Users/example/Sites/test-project"


def _stdin_for(frame: dict) -> str:
    """Build a SessionStart stdin payload carrying ``frame``'s role
    discriminator + a valid session_id (so the writes are reached and only
    the is_lead gate decides suppression)."""
    payload = {"session_id": _SESSION_ID, **frame}
    return json.dumps(payload)


def _run_main_with(stdin_data: str, monkeypatch, tmp_path):
    """Run session_init.main() end-to-end with the heavy collaborators
    patched out, returning the four gated-write mocks for assertion.

    Patches the SAME collaborator set the coder's integration tests use so we
    isolate the is_lead gating decision. Returns a dict of the 4 write mocks.
    """
    from session_init import main

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    with patch("session_init.setup_plugin_symlinks", return_value=None), \
         patch("session_init.ensure_project_memory_md", return_value=None), \
         patch("session_init.check_pinned_staleness", return_value=None), \
         patch("session_init.get_task_list", return_value=None), \
         patch("session_init.restore_last_session", return_value=None), \
         patch("session_init.write_context") as mock_write_ctx, \
         patch("session_init.append_event") as mock_append, \
         patch("session_init.update_session_info", return_value=None) as mock_update, \
         patch("session_init.check_paused_state", return_value=None) as mock_paused, \
         patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO):
        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code == 0
    return {
        "write_context": mock_write_ctx,
        "append_event": mock_append,
        "update_session_info": mock_update,
        "check_paused_state": mock_paused,
    }


def _session_start_calls(mock_append):
    """The session_start append_event calls (filtering out any other event
    types the hook may append)."""
    return [
        c for c in mock_append.call_args_list
        if c.args and c.args[0].get("type") == "session_start"
    ]


# ===========================================================================
# LEAD rows — every write RUNS (both spellings).
# ===========================================================================

class TestLeadRowsAllWritesRun:
    """For a lead frame (both spellings), all 4 Class-A writes fire."""

    @pytest.mark.parametrize("frame_builder", [
        lead_frame_qualified,
        lead_frame_unqualified,
    ], ids=["qualified", "unqualified"])
    def test_lead_runs_all_four_writes(self, frame_builder, monkeypatch, tmp_path):
        mocks = _run_main_with(_stdin_for(frame_builder()), monkeypatch, tmp_path)

        # write_context invoked with write_disk=True (the disk write happens).
        mocks["write_context"].assert_called_once()
        assert mocks["write_context"].call_args.kwargs.get("write_disk") is True, (
            "lead frame must call write_context with write_disk=True"
        )
        # session_start journal anchor appended.
        assert _session_start_calls(mocks["append_event"]), (
            "lead frame must append the session_start journal anchor"
        )
        # CLAUDE.md Current Session block written.
        mocks["update_session_info"].assert_called_once()
        # paused-state surface checked.
        mocks["check_paused_state"].assert_called_once()


# ===========================================================================
# TEAMMATE + PLAIN rows — every write is SUPPRESSED.
# ===========================================================================

class TestNonLeadRowsAllWritesSuppressed:
    """For a teammate or plain frame, all 4 Class-A writes are suppressed.

    write_context is still CALLED (the cache must be populated — see
    TestWriteContextSplit) but with write_disk=False so no disk write occurs;
    the other 3 writes are not called at all.
    """

    @pytest.mark.parametrize("frame_builder, role", [
        (teammate_frame, "teammate"),
        (plain_frame, "plain"),
    ])
    def test_non_lead_suppresses_disk_write_and_other_writes(
        self, frame_builder, role, monkeypatch, tmp_path
    ):
        mocks = _run_main_with(_stdin_for(frame_builder()), monkeypatch, tmp_path)

        # write_context IS called (cache population is unconditional) but the
        # disk write is gated OFF (write_disk=False).
        mocks["write_context"].assert_called_once()
        assert mocks["write_context"].call_args.kwargs.get("write_disk") is False, (
            f"{role} frame must call write_context with write_disk=False "
            f"(disk write suppressed, cache still populated)"
        )
        # The other 3 lead-only writes are NOT called at all.
        assert _session_start_calls(mocks["append_event"]) == [], (
            f"{role} frame must NOT append the session_start journal anchor"
        )
        mocks["update_session_info"].assert_not_called()
        mocks["check_paused_state"].assert_not_called()

    def test_teammate_specific_agent_types_all_suppress(self, monkeypatch, tmp_path):
        """A range of specialist agent_types all suppress (not just the
        default). Guards against a literal-coupling bug where only one
        teammate spelling is treated as non-lead."""
        for at in ("pact-backend-coder", "pact-secretary", "pact-test-engineer"):
            mocks = _run_main_with(
                _stdin_for(teammate_frame(agent_type=at)), monkeypatch, tmp_path
            )
            assert mocks["write_context"].call_args.kwargs.get("write_disk") is False
            mocks["update_session_info"].assert_not_called()


# ===========================================================================
# write_context SPLIT — the load-bearing correctness property (REAL function).
# ===========================================================================

class TestWriteContextSplit:
    """write_context populates _cache/_context_path UNCONDITIONALLY but gates
    the on-disk write on write_disk. Exercises the REAL write_context (not a
    mock) — this is the #877 split's correctness property. The autouse
    _reset_pact_context_state fixture (conftest.py) gives each test a clean
    cache.
    """

    def test_non_lead_populates_cache_but_writes_no_disk_file(self, monkeypatch, tmp_path):
        """write_disk=False: _cache + _context_path are set (get_session_dir
        works) and NO on-disk pact-session-context.json is created."""
        import shared.pact_context as ctx

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Pre-state: autouse reset leaves both None.
        assert ctx._cache is None and ctx._context_path is None

        ctx.write_context(
            "pact-aabb1122", _SESSION_ID, str(tmp_path / "proj"),
            "", write_disk=False,
        )

        # Cache + path populated unconditionally.
        assert ctx._cache is not None, "non-lead frame must still populate _cache"
        assert ctx._cache["session_id"] == _SESSION_ID
        assert ctx._context_path is not None, "non-lead frame must set _context_path"
        # get_session_dir() (the downstream consumer) resolves off the cache.
        assert ctx.get_session_dir(), "get_session_dir must work for a non-lead frame"
        # But NO disk file was written.
        assert not ctx._context_path.exists(), (
            "write_disk=False must NOT create the on-disk session-context file "
            "(the lead-only artifact a teammate frame must not clobber)"
        )

    def test_lead_populates_cache_and_writes_disk_file(self, monkeypatch, tmp_path):
        """write_disk=True (lead): _cache populated AND the on-disk file
        exists with the expected content — byte-for-byte historical behavior."""
        import shared.pact_context as ctx

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert ctx._cache is None and ctx._context_path is None

        ctx.write_context(
            "pact-aabb1122", _SESSION_ID, str(tmp_path / "proj"),
            "", write_disk=True,
        )

        assert ctx._cache is not None
        assert ctx._context_path is not None
        assert ctx._context_path.exists(), (
            "write_disk=True (lead) must create the on-disk session-context file"
        )
        written = json.loads(ctx._context_path.read_text(encoding="utf-8"))
        assert written["session_id"] == _SESSION_ID
        assert written["team_name"] == "pact-aabb1122"

    def test_cache_identical_between_disk_and_no_disk(self, monkeypatch, tmp_path):
        """The in-memory cache content is IDENTICAL whether or not the disk
        write happens — the split changes only the disk side-effect, never the
        cache the downstream consumers read. (started_at differs by clock, so
        compare the stable fields.)"""
        import shared.pact_context as ctx

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        ctx.write_context("pact-x", _SESSION_ID, "/p", "plug", write_disk=False)
        no_disk = {k: ctx._cache[k] for k in
                   ("team_name", "session_id", "project_dir", "plugin_root")}

        ctx.reset_for_tests()
        ctx.write_context("pact-x", _SESSION_ID, "/p", "plug", write_disk=True)
        with_disk = {k: ctx._cache[k] for k in
                     ("team_name", "session_id", "project_dir", "plugin_root")}

        assert no_disk == with_disk, (
            "the in-memory cache must be identical regardless of write_disk — "
            "the split gates ONLY the disk side-effect"
        )


# ===========================================================================
# UNKNOWN-ROLE STARTUP WARNING — fires for unknown role ONLY.
# ===========================================================================

class TestUnknownRoleStartupWarning:
    """The unknown-role notice fires for a plain (agent_type-absent) frame on
    startup/resume, and NOT for lead or teammate frames."""

    def _run_capture_systemmessage(self, stdin_data, monkeypatch, tmp_path):
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.write_context", return_value=None), \
             patch("session_init.append_event", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.check_paused_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit):
                main()
        out = json.loads(mock_stdout.getvalue())
        return json.dumps(out)  # whole output blob; notice may be systemMessage

    def _notice_text(self):
        import session_init
        return session_init._UNKNOWN_ROLE_NOTICE

    def test_plain_frame_startup_fires_warning(self, monkeypatch, tmp_path):
        stdin = json.dumps({"session_id": _SESSION_ID, "source": "startup",
                            **plain_frame()})
        blob = self._run_capture_systemmessage(stdin, monkeypatch, tmp_path)
        assert self._notice_text() in blob, (
            "unknown-role (plain) frame on startup must emit the notice"
        )

    @pytest.mark.parametrize("frame_builder", [
        lead_frame_qualified, lead_frame_unqualified, teammate_frame,
    ], ids=["lead-qualified", "lead-unqualified", "teammate"])
    def test_known_role_does_not_fire_warning(self, frame_builder, monkeypatch, tmp_path):
        stdin = json.dumps({"session_id": _SESSION_ID, "source": "startup",
                            **frame_builder()})
        blob = self._run_capture_systemmessage(stdin, monkeypatch, tmp_path)
        assert self._notice_text() not in blob, (
            "a recognized (lead/teammate) role must NOT emit the unknown-role notice"
        )
