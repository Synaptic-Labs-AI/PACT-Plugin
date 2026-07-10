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
    check_resume_state            | runs | SUPPRESS | SUPPRESS

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
         patch("session_init.build_context_cache",
               return_value=(Path("/tmp/ctx.json"), {})) as mock_build_ctx, \
         patch("session_init.persist_context", return_value=None) as mock_persist, \
         patch("session_init.append_event") as mock_append, \
         patch("session_init.update_session_info", return_value=None) as mock_update, \
         patch("session_init.check_resume_state", return_value=None) as mock_paused, \
         patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO):
        with pytest.raises(SystemExit) as exc_info:
            main()

    assert exc_info.value.code == 0
    return {
        # #878 SHAPE-2: the disk/cache seam is two functions. build_context_cache
        # runs for EVERY frame (cache always populated); persist_context (the disk
        # side-effect) runs ONLY for a lead frame.
        "build_context_cache": mock_build_ctx,
        "persist_context": mock_persist,
        "append_event": mock_append,
        "update_session_info": mock_update,
        "check_resume_state": mock_paused,
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

        # #878 SHAPE-2: cache is built for every frame; the lead ALSO persists to
        # disk (persist_context fires).
        mocks["build_context_cache"].assert_called_once()
        mocks["persist_context"].assert_called_once()
        # session_start journal anchor appended.
        assert _session_start_calls(mocks["append_event"]), (
            "lead frame must append the session_start journal anchor"
        )
        # CLAUDE.md Current Session block written.
        mocks["update_session_info"].assert_called_once()
        # paused-state surface checked.
        mocks["check_resume_state"].assert_called_once()

    def test_lead_with_missing_session_id_suppresses_all_writes(
        self, monkeypatch, tmp_path
    ):
        """#9: a LEAD frame with NO session_id suppresses ALL Class-A writes —
        including update_session_info. The is_lead gate is necessary but not
        sufficient: the `session_id_was_missing` guard (R3) independently skips
        every persistence call so a malformed-stdin lead can't leak an
        unreapable `pact-sessions/.../unknown-xxxx/` dir or interpolate a junk
        session_id into the CLAUDE.md Current Session block. This pins that the
        two gates compose (lead AND valid-session_id), not lead alone."""
        # Lead frame (qualified) but OMIT session_id → session_id_was_missing.
        stdin = json.dumps({**lead_frame_qualified()})  # no session_id key
        mocks = _run_main_with(stdin, monkeypatch, tmp_path)

        # The whole `if not session_id_was_missing` block is skipped: neither
        # half of the build/persist seam, nor the journal anchor, runs.
        mocks["build_context_cache"].assert_not_called()
        mocks["persist_context"].assert_not_called()
        assert _session_start_calls(mocks["append_event"]) == [], (
            "missing-session_id lead must NOT append the session_start anchor"
        )
        # update_session_info is gated by `frame_is_lead AND not
        # _is_unknown_or_missing_session` — the missing-session_id arm suppresses
        # it even though the frame IS the lead.
        mocks["update_session_info"].assert_not_called()


# ===========================================================================
# TEAMMATE + PLAIN rows — every write is SUPPRESSED.
# ===========================================================================

class TestNonLeadRowsAllWritesSuppressed:
    """For a teammate or plain frame, all 4 Class-A writes are suppressed.

    build_context_cache is still CALLED (the cache must be populated — see
    TestWriteContextSplit) but persist_context (the disk write) is NOT called;
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

        # #878 SHAPE-2: build_context_cache IS called (cache population is
        # unconditional) but persist_context (the disk write) is NOT — the disk
        # side-effect is gated on is_lead.
        mocks["build_context_cache"].assert_called_once()
        mocks["persist_context"].assert_not_called()
        # The other 3 lead-only writes are NOT called at all.
        assert _session_start_calls(mocks["append_event"]) == [], (
            f"{role} frame must NOT append the session_start journal anchor"
        )
        mocks["update_session_info"].assert_not_called()
        mocks["check_resume_state"].assert_not_called()

    def test_teammate_specific_agent_types_all_suppress(self, monkeypatch, tmp_path):
        """A range of specialist agent_types all suppress (not just the
        default). Guards against a literal-coupling bug where only one
        teammate spelling is treated as non-lead."""
        for at in ("pact-backend-coder", "pact-secretary", "pact-test-engineer"):
            mocks = _run_main_with(
                _stdin_for(teammate_frame(agent_type=at)), monkeypatch, tmp_path
            )
            # #878 SHAPE-2: cache built, but the disk persist is suppressed.
            mocks["build_context_cache"].assert_called_once()
            mocks["persist_context"].assert_not_called()
            mocks["update_session_info"].assert_not_called()


# ===========================================================================
# write_context SPLIT — the load-bearing correctness property (REAL function).
# ===========================================================================

class TestWriteContextSplit:
    """#878 SHAPE-2 seam: build_context_cache populates _cache/_context_path
    UNCONDITIONALLY (NO disk I/O); persist_context is the separate is_lead-gated
    disk write. The #877 split's correctness property. Exercises the REAL
    functions (not mocks). The autouse _reset_pact_context_state fixture
    (conftest.py) gives each test a clean cache.
    """

    def test_non_lead_populates_cache_but_writes_no_disk_file(self, monkeypatch, tmp_path):
        """Non-lead = build_context_cache ONLY (no persist): _cache +
        _context_path are set (get_session_dir works) and NO on-disk
        pact-session-context.json is created."""
        import shared.pact_context as ctx

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # Pre-state: autouse reset leaves both None.
        assert ctx._cache is None and ctx._context_path is None

        result = ctx.build_context_cache(
            "pact-aabb1122", _SESSION_ID, str(tmp_path / "proj"), "",
        )
        # (no persist_context call — this is the non-lead path)

        assert result is not None
        # Cache + path populated unconditionally by the builder.
        assert ctx._cache is not None, "non-lead frame must still populate _cache"
        assert ctx._cache["session_id"] == _SESSION_ID
        assert ctx._context_path is not None, "non-lead frame must set _context_path"
        # get_session_dir() (the downstream consumer) resolves off the cache.
        assert ctx.get_session_dir(), "get_session_dir must work for a non-lead frame"
        # But NO disk file was written (persist_context was never called).
        assert not ctx._context_path.exists(), (
            "skipping persist must NOT create the on-disk session-context file "
            "(the lead-only artifact a teammate frame must not clobber)"
        )

    def test_lead_populates_cache_and_writes_disk_file(self, monkeypatch, tmp_path):
        """Lead = build_context_cache THEN persist_context: _cache populated AND
        the on-disk file exists with the expected content — byte-for-byte
        historical behavior."""
        import shared.pact_context as ctx

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert ctx._cache is None and ctx._context_path is None

        result = ctx.build_context_cache(
            "pact-aabb1122", _SESSION_ID, str(tmp_path / "proj"), "",
        )
        assert result is not None
        ctx.persist_context(*result)

        assert ctx._cache is not None
        assert ctx._context_path is not None
        assert ctx._context_path.exists(), (
            "lead path (build + persist) must create the on-disk session-context file"
        )
        written = json.loads(ctx._context_path.read_text(encoding="utf-8"))
        assert written["session_id"] == _SESSION_ID
        assert written["team_name"] == "pact-aabb1122"

    def test_cache_identical_between_disk_and_no_disk(self, monkeypatch, tmp_path):
        """The in-memory cache content is IDENTICAL whether or not persist runs
        — the seam changes only the disk side-effect, never the cache the
        downstream consumers read. (started_at differs by clock, so compare the
        stable fields.)"""
        import shared.pact_context as ctx

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        ctx.build_context_cache("pact-x", _SESSION_ID, "/p", "plug")  # no persist
        no_disk = {k: ctx._cache[k] for k in
                   ("team_name", "session_id", "project_dir", "plugin_root")}

        ctx.reset_for_tests()
        result = ctx.build_context_cache("pact-x", _SESSION_ID, "/p", "plug")
        ctx.persist_context(*result)
        with_disk = {k: ctx._cache[k] for k in
                     ("team_name", "session_id", "project_dir", "plugin_root")}

        assert no_disk == with_disk, (
            "the in-memory cache must be identical regardless of whether persist "
            "runs — the seam gates ONLY the disk side-effect"
        )


# ===========================================================================
# UNKNOWN-ROLE STARTUP WARNING — fires for unknown role ONLY.
# ===========================================================================

# The live plugin root (this file is tests/…, parent.parent is pact-plugin/,
# which carries the real agents/pact-*.md registry). #1 resolves the recognized-
# specialist set from CLAUDE_PLUGIN_ROOT at the 0c site, so the notice tests
# point that env at the live root — exercising the REAL specialist registry
# (the SSOT), not a hand-seeded fixture.
_REAL_PLUGIN_ROOT = str(Path(__file__).resolve().parent.parent)


class TestUnknownRoleStartupWarning:
    """#1 (#878): the unknown-role notice fires when a frame has NO recognized
    role — agent_type ABSENT, OR present-but-unrecognized (typo'd) — and NOT for
    a lead frame or a RECOGNIZED specialist. Recognized = the live
    agents/pact-*.md registry, resolved at 0c from CLAUDE_PLUGIN_ROOT (env), with
    a PACT:-strip for spelling-symmetry and is_lead checked FIRST.
    """

    def _run_capture_systemmessage(
        self, stdin_data, monkeypatch, tmp_path, plugin_root=_REAL_PLUGIN_ROOT,
    ):
        from session_init import main

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
        # #1: the specialist-registry resolution at 0c reads CLAUDE_PLUGIN_ROOT
        # from env. Default to the live plugin root so a recognized specialist
        # type actually resolves; pass "" to exercise the unresolvable-registry
        # fail-OPEN residual.
        if plugin_root:
            monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", plugin_root)
        else:
            monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        with patch("session_init.setup_plugin_symlinks", return_value=None), \
             patch("session_init.ensure_project_memory_md", return_value=None), \
             patch("session_init.check_pinned_staleness", return_value=None), \
             patch("session_init.get_task_list", return_value=None), \
             patch("session_init.restore_last_session", return_value=None), \
             patch("session_init.persist_context", return_value=None), \
             patch("session_init.append_event", return_value=None), \
             patch("session_init.update_session_info", return_value=None), \
             patch("session_init.check_resume_state", return_value=None), \
             patch("sys.stdin", io.StringIO(stdin_data)), \
             patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            with pytest.raises(SystemExit):
                main()
        out = json.loads(mock_stdout.getvalue())
        return json.dumps(out)  # whole output blob; notice may be systemMessage

    def _notice_text(self):
        import session_init
        return session_init._UNKNOWN_ROLE_NOTICE

    # ---- FIRES: no recognized role ----

    def test_absent_agent_type_fires(self, monkeypatch, tmp_path):
        """(d) plain frame — agent_type ABSENT → notice fires."""
        stdin = json.dumps({"session_id": _SESSION_ID, "source": "startup",
                            **plain_frame()})
        blob = self._run_capture_systemmessage(stdin, monkeypatch, tmp_path)
        assert self._notice_text() in blob, (
            "absent agent_type on startup must emit the unknown-role notice"
        )

    def test_present_but_unrecognized_agent_type_fires(self, monkeypatch, tmp_path):
        """(c) a present-but-unrecognized / typo'd agent_type (not in the live
        registry) → notice fires. This is the #1 broadening over the prior
        absent-only check."""
        stdin = json.dumps({"session_id": _SESSION_ID, "source": "startup",
                            "agent_type": "pact-architct"})  # typo: not in registry
        blob = self._run_capture_systemmessage(stdin, monkeypatch, tmp_path)
        assert self._notice_text() in blob, (
            "a present-but-unrecognized agent_type must emit the notice "
            "(the typo'd-orchestrator case #1 exists to catch)"
        )

    def test_unresolvable_registry_present_unrecognized_fires(self, monkeypatch, tmp_path):
        """(e) fail-OPEN residual: env CLAUDE_PLUGIN_ROOT empty → registry
        unresolvable → a present-but-(unverifiable) agent_type fires. An install
        with no resolvable plugin_root is broken; a spurious advisory is harmless."""
        stdin = json.dumps({"session_id": _SESSION_ID, "source": "startup",
                            "agent_type": "pact-backend-coder"})
        blob = self._run_capture_systemmessage(
            stdin, monkeypatch, tmp_path, plugin_root="",
        )
        assert self._notice_text() in blob, (
            "unresolvable registry (empty env plugin_root) must FAIL-OPEN — "
            "the notice fires rather than being suppressed"
        )

    # ---- DOES NOT FIRE: recognized role ----

    @pytest.mark.parametrize("frame_builder", [
        lead_frame_qualified, lead_frame_unqualified,
    ], ids=["lead-qualified", "lead-unqualified"])
    def test_lead_does_not_fire(self, frame_builder, monkeypatch, tmp_path):
        stdin = json.dumps({"session_id": _SESSION_ID, "source": "startup",
                            **frame_builder()})
        blob = self._run_capture_systemmessage(stdin, monkeypatch, tmp_path)
        assert self._notice_text() not in blob, (
            "a lead frame must NOT emit the unknown-role notice"
        )

    def test_recognized_teammate_does_not_fire(self, monkeypatch, tmp_path):
        """(a) THE REGRESSION PIN: a recognized specialist teammate at 0c (with
        env plugin_root set) must NOT fire. The timing-blocker fix exists so the
        registry resolves at 0c — without it, the empty pre-cache registry would
        false-fire for every teammate."""
        stdin = json.dumps({"session_id": _SESSION_ID, "source": "startup",
                            **teammate_frame()})  # agent_type=pact-backend-coder
        blob = self._run_capture_systemmessage(stdin, monkeypatch, tmp_path)
        assert self._notice_text() not in blob, (
            "a RECOGNIZED specialist teammate must NOT fire the unknown-role "
            "notice — this is the false-fire regression the #1 timing fix prevents"
        )

    def test_qualified_specialist_does_not_fire(self, monkeypatch, tmp_path):
        """(b) the PACT:-strip pin: a qualified specialist spelling
        (PACT:pact-backend-coder) is recognized just like is_lead accepts both
        lead spellings → NO notice."""
        stdin = json.dumps({"session_id": _SESSION_ID, "source": "startup",
                            "agent_type": "PACT:pact-backend-coder"})
        blob = self._run_capture_systemmessage(stdin, monkeypatch, tmp_path)
        assert self._notice_text() not in blob, (
            "a qualified specialist spelling must be recognized (PACT:-strip) "
            "and NOT fire the unknown-role notice"
        )
