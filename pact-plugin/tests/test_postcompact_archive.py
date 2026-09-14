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

from __future__ import annotations
import json
import os
import stat
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest


HOOK_PATH = str(Path(__file__).parent.parent / "hooks" / "postcompact_archive.py")


def run_hook(
    stdin_data: str | None = None,
    env_root: str | None = None,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run the hook as a subprocess and return the result.

    Config-root isolation (#1191): the postcompact child resolves its write
    target via ``get_claude_config_dir()`` (precedence-1 ``$CLAUDE_CONFIG_DIR``,
    else ``$HOME/.claude``). The autouse ``_isolate_config_root_to_tmp`` fixture
    redirects ``Path.home`` IN-PROCESS ONLY — that ``setattr`` does NOT cross to
    this subprocess child, and the fixture DELIBERATELY does not set the HOME env
    var (see conftest's "WHY NOT ALSO SET HOME ENV" note: a global HOME override
    breaks the telegram ``cwd_is_home`` tests). Without an explicit env pin here,
    a LEAD-frame input (``agent_type`` in ``LEAD_AGENT_TYPES`` + truthy
    ``compact_summary``) opens the ``is_lead`` gate (postcompact_archive.py) and
    the child writes ``compact-summary.txt`` to the operator's REAL
    ``~/.claude/pact-sessions/``.

    Pin BOTH ``HOME`` and ``CLAUDE_CONFIG_DIR`` to a tmp root so the child
    resolves the tmp, never real home — matching the suite's per-test
    subprocess-isolation convention (Form A: ``monkeypatch.setenv`` of
    ``CLAUDE_CONFIG_DIR`` in test_pact_harvest_cli / test_config_dir_*; Form B:
    ``env={**os.environ, "HOME": tmp}`` in test_session_journal). Setting both is
    belt-and-suspenders: ``CLAUDE_CONFIG_DIR`` precedence-1 does the resolution
    work; ``HOME`` is a harmless backup covering any HOME-fallthrough path.
    ``env_root`` defaults to a fresh ``mkdtemp`` so EVERY caller is isolated —
    the latent #1191 gap is closed universally, not just for callers that pass a
    root.
    """
    if env_root is None:
        env_root = tempfile.mkdtemp(prefix="postcompact-hook-test-")
    # ``extra_env`` carries FRAME-CONTRACT env (e.g. CLAUDE_PROJECT_DIR for the
    # #1504 session-scoped arm), NOT isolation: the HOME/CLAUDE_CONFIG_DIR pin
    # above stays the sole isolation provider and wins the merge order below
    # only for keys extra_env does not name.
    return subprocess.run(
        [sys.executable, HOOK_PATH],
        input=stdin_data or "",
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "HOME": env_root, "CLAUDE_CONFIG_DIR": env_root,
             **(extra_env or {})},
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
        from shared.constants import get_compact_summary_path
        p = get_compact_summary_path()
        assert p.name == "compact-summary.txt"
        assert "pact-sessions" in str(p)

    def test_bare_write_targets_the_shared_root_accessor(self, tmp_path, monkeypatch):
        """#1504: the seam takes a FULLY RESOLVED session dir; a bare call
        falls back to the shared root accessor's path — the degradation
        destination, not a locally re-spelled filename."""
        from postcompact_archive import write_compact_summary

        target = tmp_path / "compact-summary.txt"
        monkeypatch.setattr(
            "postcompact_archive.get_compact_summary_path", lambda: target
        )
        assert write_compact_summary("bare")
        assert target.read_text(encoding="utf-8") == "bare"

    def test_seam_write_targets_the_given_directory(self, tmp_path):
        """write_compact_summary(summary, session_dir) writes
        COMPACT_SUMMARY_NAME inside the fully-resolved dir, nowhere else."""
        from postcompact_archive import write_compact_summary

        session_dir = tmp_path / "proj" / "sid"
        assert write_compact_summary("scoped", str(session_dir))
        assert (session_dir / "compact-summary.txt").read_text(encoding="utf-8") == "scoped"


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
    """The compact-summary write is gated behind is_lead (#881, re-scoped #1504).

    The write is O_TRUNC, and in-process identity collapse resolves a teammate
    frame into the LEAD's session directory — so a teammate/plain frame's
    PostCompact must NOT write at all. The destination itself is the TOTAL
    resolver's job (patched to a fixed path here; its own arms live in
    test_compact_summary_session_scope.py). These are smoke tests (call /
    no-call of write_compact_summary by role); comprehensive per-role
    suppression coverage is the TEST phase.
    """

    def _run_main_with(self, frame, tmp_path):
        from postcompact_archive import main

        self._dest = tmp_path / "pact-sessions" / "proj" / "sid" / "compact-summary.txt"
        stdin_data = json.dumps(frame)
        with patch("sys.stdin", StringIO(stdin_data)), \
             patch("postcompact_archive.resolve_compact_summary_path",
                   return_value=self._dest), \
             patch("postcompact_archive.write_compact_summary") as mock_write:
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
            return mock_write

    def test_lead_qualified_writes_to_resolved_destination(self, tmp_path):
        from fixtures.role_frames import postcompact_frame
        mock_write = self._run_main_with(
            postcompact_frame("PACT:pact-orchestrator", compact_summary="x"),
            tmp_path,
        )
        mock_write.assert_called_once_with("x", str(self._dest.parent))

    def test_lead_unqualified_writes_to_resolved_destination(self, tmp_path):
        from fixtures.role_frames import postcompact_frame
        mock_write = self._run_main_with(
            postcompact_frame("pact-orchestrator", compact_summary="x"),
            tmp_path,
        )
        mock_write.assert_called_once_with("x", str(self._dest.parent))

    def test_teammate_suppressed(self, tmp_path):
        from fixtures.role_frames import postcompact_frame
        mock_write = self._run_main_with(
            postcompact_frame("pact-backend-coder", compact_summary="x"),
            tmp_path,
        )
        mock_write.assert_not_called()

    def test_plain_frame_suppressed(self, tmp_path):
        """No agent_type (no --agent) → not lead → write suppressed."""
        from fixtures.role_frames import postcompact_frame
        mock_write = self._run_main_with(
            postcompact_frame(None, compact_summary="x"),
            tmp_path,
        )
        mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# #4 (#883 fold-in): real-disk defense-in-depth for the #881 lead-gate.
# ---------------------------------------------------------------------------


class TestPostcompactLeadGateRealDisk:
    """Defense-in-depth complement to TestPostcompactLeadGate (which mocks
    write_compact_summary and asserts call/no-call). Here the REAL writer runs
    against REAL files on disk: a teammate/plain frame through main() must NOT
    truncate the lead's compact summary (#881's O_TRUNC clobber).

    #1504 changed where the resolution lives: the destination comes from
    pact_context's TOTAL resolver, so no postcompact_archive-local accessor
    patch can steer it. Instead the autouse config-root fixture (Path.home ->
    tmp) carries every real path under tmp. The root singleton is planted via
    the shared accessor; the identified-lead arm asserts the SESSION-SCOPED
    landing and that the root sentinel SURVIVES (only degraded writes feed the
    root now).
    """

    _SENTINEL = "PRIOR LEAD SUMMARY — must survive a teammate PostCompact"
    _SID = "aabb1122-0000-0000-0000-00000000cafe"

    def _root_summary_path(self):
        from shared.constants import get_compact_summary_path
        return get_compact_summary_path()

    def _run_main_realdisk(self, frame):
        from postcompact_archive import main

        summary_path = self._root_summary_path()
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(self._SENTINEL, encoding="utf-8")

        with patch("sys.stdin", StringIO(json.dumps(frame))):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        return summary_path

    def test_teammate_frame_does_not_truncate_real_file(self):
        """A teammate PostCompact must leave the lead's on-disk compact-summary
        UNTOUCHED — the #881 is_lead gate suppresses the real O_TRUNC write."""
        from fixtures.role_frames import postcompact_frame
        summary_path = self._run_main_realdisk(
            postcompact_frame("pact-backend-coder", compact_summary="TEAMMATE CLOBBER")
        )
        assert summary_path.read_text(encoding="utf-8") == self._SENTINEL, (
            "a teammate PostCompact truncated the lead's compact-summary file — "
            "the #881 lead-gate failed to suppress the real O_TRUNC write"
        )

    def test_plain_frame_does_not_truncate_real_file(self):
        """A plain (no-agent_type) PostCompact must also leave the file intact."""
        from fixtures.role_frames import postcompact_frame
        summary_path = self._run_main_realdisk(
            postcompact_frame(None, compact_summary="PLAIN CLOBBER")
        )
        assert summary_path.read_text(encoding="utf-8") == self._SENTINEL

    def test_identified_lead_frame_writes_session_scoped_not_root(self, tmp_path, monkeypatch):
        """Positive symmetry, session-scoped (#1504): an IDENTIFIED lead
        PostCompact writes into the session's own directory, and the root
        sentinel SURVIVES — only degraded writes may feed the root now."""
        from fixtures.role_frames import postcompact_frame
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/test/project")
        summary_path = self._run_main_realdisk(
            postcompact_frame("PACT:pact-orchestrator",
                              compact_summary="NEW LEAD SUMMARY",
                              session_id=self._SID)
        )
        assert summary_path.read_text(encoding="utf-8") == self._SENTINEL, (
            "an identified lead PostCompact must not touch the root singleton"
        )
        scoped = (
            tmp_path / ".claude" / "pact-sessions" / "project" / self._SID
            / "compact-summary.txt"
        )
        assert scoped.read_text(encoding="utf-8") == "NEW LEAD SUMMARY", (
            "an identified lead PostCompact must archive into the session dir"
        )


# ---------------------------------------------------------------------------
# #1191: config-root isolation pin for the run_hook subprocess spawn.
# ---------------------------------------------------------------------------


class TestPostcompactRunHookConfigRootIsolation:
    """Delete-the-fix counter-test for run_hook's config-root env pin (#1191).

    ``run_hook`` spawns the postcompact child as a subprocess. The child's
    compact-summary write resolves through ``get_claude_config_dir()``
    (precedence-1 ``$CLAUDE_CONFIG_DIR``, else ``$HOME/.claude``). The autouse
    ``_isolate_config_root_to_tmp`` fixture redirects ``Path.home`` IN-PROCESS
    ONLY — that ``setattr`` does NOT cross to the subprocess child, and the
    fixture deliberately does NOT set the HOME env var (see conftest's "WHY NOT
    ALSO SET HOME ENV"). Without run_hook's env= pin, a LEAD-frame input opens
    the ``is_lead`` gate and the child writes ``compact-summary.txt`` to the
    operator's REAL ``~/.claude/pact-sessions/``. ``run_hook`` pins BOTH ``HOME``
    and ``CLAUDE_CONFIG_DIR`` to a tmp root so the child resolves the tmp.

    SOLE-PROVIDER DOCTRINE (#1189 / test_conftest_config_root_isolation.py):
    this test does NOT self-provide HOME/CLAUDE_CONFIG_DIR isolation (no
    ``monkeypatch.setenv``, no in-body env override). It passes
    ``env_root=str(tmp_path)`` only to SELECT the tmp target the pin should
    resolve to; the ISOLATION mechanism is run_hook's env= pin itself. The test
    therefore stays green solely because run_hook applies the pin — the #1189
    "the test's pass depends on the fix's correctness" shape.

    COUNTER-TEST PROPERTY (the pinning property this class exists for): if the
    ``env=env`` pin is deleted from run_hook (``env_root`` threaded but unused),
    the child inherits the test process's ``os.environ`` — HOME=real-operator-
    home, CLAUDE_CONFIG_DIR absent (scrubbed by the autouse fixture) — resolves
    the REAL ``~/.claude``, and writes ``compact-summary.txt`` there. The
    ``tmp_path`` target this test asserts would be MISSING, so the containment
    assertion FAILS.

    Verified by guarded-mutation (per the #1189 precedent's "Verified by local
    fixture-disable, reverted before staging"): with the fix committed, delete
    ONLY the ``env=env`` line from run_hook, run THIS test under a guarded HOME
    (``HOME=/tmp/<throwaway>``) so the mutation's real-home-resolved write lands
    in the throwaway and NOT the operator's real ``~/.claude``, observe the
    ``tmp_path``-containment assertion FAIL, then restore via
    ``git restore -- pact-plugin/tests/test_postcompact_archive.py`` (recovers
    the committed pinned version byte-identically; ``git diff --quiet -- <file>``
    exits 0). The guard prevents the leak DURING the probe; the POSITIVE
    ``tmp_path``-containment assertion is the load-bearing signal the delete
    breaks (a not-at-real-home negative is optional belt-and-suspenders and does
    NOT replace the guard — it would only fire AFTER a leak had already fired).
    """

    def test_lead_frame_write_lands_under_pinned_tmp_not_real_home(self, tmp_path):
        """A LEAD-frame PostCompact through run_hook writes compact-summary.txt
        under the run_hook env-pin tmp root, NOT the operator's real home.

        Drives the REAL write path: the LEAD frame (``agent_type`` a lead
        spelling + truthy ``compact_summary``) opens the ``is_lead`` gate, so
        ``write_compact_summary -> get_compact_summary_path ->
        get_claude_config_dir`` fires in the child. With the pin, the child
        resolves ``tmp_path``; without it (delete-the-fix), the child resolves
        the real home and the ``tmp_path`` target is empty.
        """
        from fixtures.role_frames import postcompact_frame

        lead_frame = postcompact_frame(
            "PACT:pact-orchestrator", compact_summary="LEAD COUNTER-TEST SUMMARY"
        )
        result = run_hook(json.dumps(lead_frame), env_root=str(tmp_path))
        assert result.returncode == 0, (
            f"postcompact child exited {result.returncode}; stderr={result.stderr!r}"
        )

        # POSITIVE tmp-target assertion (load-bearing). Robust to the Form A vs
        # Form B leaf shape: CCD=tmp -> tmp/pact-sessions/compact-summary.txt;
        # HOME=tmp -> tmp/.claude/pact-sessions/compact-summary.txt. A recursive
        # glob catches either. This arm's frame carries NO session_id, so it is
        # simultaneously the #1504 DEGRADATION arm: an unidentified lead frame
        # lands at the root singleton under the pinned root, loss-free.
        written = list(tmp_path.glob("**/compact-summary.txt"))
        assert len(written) == 1, (
            f"expected exactly one compact-summary.txt under the pinned tmp root "
            f"{tmp_path}, found {written}. If run_hook's env= pin is absent, the "
            f"child resolved the REAL ~/.claude (HOME fallthrough — the autouse "
            f"Path.home setattr does not cross to subprocesses) and wrote "
            f"elsewhere: the #1191 latent leak is OPEN. (delete-the-fix: this "
            f"assertion is what the env= pin's absence breaks.)"
        )
        assert written[0].read_text(encoding="utf-8") == "LEAD COUNTER-TEST SUMMARY"

    def test_identified_lead_frame_lands_session_scoped_under_pinned_tmp(self, tmp_path):
        """#1504 session-scoped arm: an IDENTIFIED lead frame (session_id in
        stdin, CLAUDE_PROJECT_DIR in env — both ride the real captured frame,
        tests/fixtures/role_frames.py ``postcompact_lead_manual``) lands at
        {env_root}/pact-sessions/{slug}/{sid}/compact-summary.txt, and the
        root singleton stays EMPTY: identified writes never feed the drain.
        """
        from fixtures.role_frames import postcompact_frame

        sid = "aabb1122-0000-0000-0000-00000000cafe"
        lead_frame = postcompact_frame(
            "PACT:pact-orchestrator", compact_summary="SCOPED LEAD SUMMARY",
            session_id=sid,
        )
        result = run_hook(
            json.dumps(lead_frame), env_root=str(tmp_path),
            extra_env={"CLAUDE_PROJECT_DIR": "/test/project"},
        )
        assert result.returncode == 0, (
            f"postcompact child exited {result.returncode}; stderr={result.stderr!r}"
        )
        scoped = tmp_path / "pact-sessions" / "project" / sid / "compact-summary.txt"
        assert scoped.is_file(), (
            f"identified lead frame must land session-scoped under the pinned "
            f"tmp root; found instead: {list(tmp_path.glob('**/compact-summary.txt'))}"
        )
        assert scoped.read_text(encoding="utf-8") == "SCOPED LEAD SUMMARY"
        assert not (tmp_path / "pact-sessions" / "compact-summary.txt").exists(), (
            "an identified write fed the root singleton — the resolver's "
            "session leg did not fire"
        )
