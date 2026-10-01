"""A frame with no PACT role gets one notice and writes nothing into its project.

``classify_session_role`` returns "unknown" when ``agent_type`` is ABSENT: plain
``claude``, ``claude -p``, every ``claude plugin eval`` run. session_init gives
that frame exactly ``_UNKNOWN_FRAME_CONTEXT`` and returns before any project
write, unless its own session folder holds a lead's context file (a lead
resumed without ``--agent``). The lead, teammate and unclassified (None)
frames are unchanged; a SessionStart whose stdin was not valid JSON is
unclassified.

The arms below pin the whole output by EXACT EQUALITY, so an emission added to
main() later that reaches an unknown frame fails them. The no-write arms each
run a lead control on the same layout, so an absence cannot pass because the
layout never reached the writer.
"""
import io
import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import session_init  # noqa: E402
from session_init import (  # noqa: E402
    _UNKNOWN_FRAME_CONTEXT,
    _UNKNOWN_ROLE_NOTICE,
    _UNRESOLVED_ROLE_CUE,
    _build_safety_net_context,
    _unknown_frame_output,
)
from shared import BOOTSTRAP_MARKER_NAME, compaction_owner  # noqa: E402
import shared.pact_context as pact_context  # noqa: E402
import shared.session_resume as session_resume  # noqa: E402
from shared.constants import COMPACT_SUMMARY_NAME, get_compact_summary_path  # noqa: E402
from shared.pact_context import _build_session_path, project_slug  # noqa: E402
from shared.paths import get_claude_config_dir  # noqa: E402

LADDER = "YOUR PACT ROLE: orchestrator."
BOOTSTRAP = 'Skill("PACT:bootstrap")'
TEAMMATE_MARKER = "YOUR PACT ROLE: teammate."
LEAD = {"agent_type": "PACT:pact-orchestrator"}
_SESSION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_PROJECT_DIR = "/tmp/pact-unknown-frame-inert"
USER_MD = "# My project\n\nBuild with make.\n"

# The four lifecycle sources plus one the source ladder does not recognize.
SOURCES = ("startup", "resume", "compact", "clear", "a-source-nobody-has-added-yet")


def _run_main(frame, source, monkeypatch, tmp_path):
    """Drive ``session_init.main()`` with its heavy collaborators stubbed and
    return the additionalContext. THE ROLE GATE IS NOT STUBBED."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    stdin_data = json.dumps({"session_id": _SESSION_ID, "source": source, **frame})
    with patch("session_init.setup_plugin_symlinks", return_value=None), \
         patch("session_init.ensure_project_memory_md", return_value=None), \
         patch("session_init.check_pinned_staleness", return_value=None), \
         patch("session_init.get_task_list", return_value=None), \
         patch("session_init.restore_last_session", return_value=None), \
         patch("session_init.build_context_cache",
               return_value=(Path("/tmp/ctx.json"), {})), \
         patch("session_init.persist_context", return_value=None), \
         patch("session_init.append_event"), \
         patch("session_init.update_session_info", return_value=None), \
         patch("session_init.check_resume_state", return_value=None), \
         patch("session_init._registry_resolve", return_value=None), \
         patch("session_init.get_peer_context", return_value=None), \
         patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    raw = mock_stdout.getvalue().strip()
    if not raw:
        return ""
    return json.loads(raw).get("hookSpecificOutput", {}).get("additionalContext", "")


def _run_real(monkeypatch, project_dir, stdin_data):
    """Drive ``session_init.main()`` with NOTHING stubbed and return the output.

    The conftest autouse fixtures already point the config root at tmp_path and
    scrub CLAUDE_PLUGIN_ROOT, so every write lands under tmp_path. Each call
    starts from a clean pact_context cache, as a fresh hook process does.
    """
    pact_context.reset_for_tests()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    monkeypatch.chdir(project_dir)
    with patch("sys.stdin", io.StringIO(stdin_data)), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        with pytest.raises(SystemExit) as exc:
            session_init.main()
    assert exc.value.code == 0
    return json.loads(mock_stdout.getvalue())


def _frame(source="startup", session_id=_SESSION_ID, **extra):
    return json.dumps({"session_id": session_id, "source": source, **extra})


def _session_dir(project_dir, session_id=_SESSION_ID):
    return _build_session_path(project_slug(str(project_dir)), session_id)


class TestUnknownFrameIsInert:
    """The live emission path, driven through ``main()``."""

    @pytest.mark.parametrize("source", SOURCES)
    def test_unknown_frame_gets_only_the_inert_context(
        self, source, monkeypatch, tmp_path
    ):
        out = _run_main({}, source, monkeypatch, tmp_path)
        assert out == _UNKNOWN_FRAME_CONTEXT, (
            f"an unknown frame (source={source!r}) must receive exactly "
            f"_UNKNOWN_FRAME_CONTEXT, with no orchestrator ladder. got: {out[:200]!r}"
        )

    def test_lead_frame_is_unchanged(self, monkeypatch, tmp_path):
        """THE ARM THAT BOUNDS THE FIX. A lead frame must not move."""
        out = _run_main(LEAD, "startup", monkeypatch, tmp_path)
        assert out, "a lead frame emitted NO additionalContext at all"
        assert LADDER in out, "a LEAD frame lost its orchestrator instructions"
        assert _UNKNOWN_ROLE_NOTICE not in out, (
            "a lead frame received the unknown-role notice, so the lead branch "
            "now falls through to the unknown-frame branch"
        )

    def test_teammate_frame_is_unchanged(self, monkeypatch, tmp_path):
        """THE OTHER BOUND. A teammate must gain neither the ladder nor the
        notice."""
        out = _run_main(
            {"agent_type": "some-teammate-name"}, "startup", monkeypatch, tmp_path
        )
        assert LADDER not in out, (
            "a teammate frame received the orchestrator instructions"
        )
        assert _UNKNOWN_ROLE_NOTICE not in out, (
            "a teammate frame received the unknown-role notice, so the teammate "
            "branch now falls through to the unknown-frame branch"
        )


class TestSafetyNetUnknownFrame:
    """The exception path. ``_build_safety_net_context`` is pure, so each case
    is driven directly."""

    def test_unknown_gets_only_the_inert_context(self):
        out = _build_safety_net_context("session-x", "unknown")
        assert out == _UNKNOWN_FRAME_CONTEXT, (
            "the safety-net unknown branch must give the same text as the "
            "normal path, with no orchestrator marker or bootstrap directive"
        )

    def test_unresolved_role_keeps_the_ladder_and_its_own_diagnostic(self):
        """A frame that never classified STILL gets the ladder.

        `frame_role is None` means the classifier DID NOT RUN, because the
        raise fired above the capture. Its population includes real leads, so
        it keeps the ladder, plus a sentence that keeps it separable from a
        resolved-empty frame for a reader who debugs the early window.
        """
        out = _build_safety_net_context("session-x", None)
        assert out, "the safety net returned an empty string for an unresolved frame"
        assert LADDER in out, "an unresolved frame lost the orchestrator marker"
        assert BOOTSTRAP in out, (
            "an unresolved frame kept the marker and lost the bootstrap directive"
        )
        assert "could not determine this session's role" in out, (
            "the unresolved-frame case lost its distinguishing sentence, so a "
            "reader can no longer tell it from the resolved-empty case"
        )

    def test_unresolved_does_not_claim_the_unknown_role_fact(self):
        """None and 'unknown' stay DIFFERENT. The unknown-role notice asserts
        that no `--agent` flag was recognized, a classifier result that was
        never computed for an unresolved frame."""
        out = _build_safety_net_context("session-x", None)
        assert LADDER in out, (
            "the ladder is missing, so this arm cannot show what rides beside it"
        )
        assert _UNKNOWN_ROLE_NOTICE not in out, (
            "an unresolved frame received the unknown-role notice, which "
            "asserts a classifier result that was never computed"
        )
        assert _build_safety_net_context("session-x", None) != \
            _build_safety_net_context("session-x", "unknown"), (
            "the None case and the 'unknown' case now emit identical text, so "
            "the two have been collapsed into one"
        )

    def test_lead_is_unchanged(self):
        out = _build_safety_net_context("session-x", "lead")
        assert out, "the safety net returned an empty string for a lead frame"
        assert LADDER in out, "the safety net stopped delivering the lead ladder"
        assert _UNKNOWN_ROLE_NOTICE not in out, (
            "a lead frame received the unknown-role notice from the safety net"
        )

    def test_teammate_is_unchanged(self):
        out = _build_safety_net_context("session-x", "teammate")
        assert TEAMMATE_MARKER in out, "the teammate safety-net marker is gone"
        assert LADDER not in out, "a teammate frame received the lead ladder"
        assert _UNKNOWN_ROLE_NOTICE not in out, (
            "a teammate frame received the unknown-role notice"
        )


class TestUnknownFrameOutputIsExact:
    """The WHOLE output, in a clean sandbox with nothing stubbed. Exact
    equality on both channels is what makes the helper's allowlist a pin."""

    @pytest.mark.parametrize("source", SOURCES)
    def test_both_channels_are_exact(self, source, monkeypatch, tmp_path):
        project = tmp_path / "plain"
        project.mkdir()
        output = _run_real(monkeypatch, project, _frame(source))
        assert output["hookSpecificOutput"] == {
            "hookEventName": "SessionStart",
            "additionalContext": _UNKNOWN_FRAME_CONTEXT,
        }
        if source in ("startup", "resume"):
            assert output.get("systemMessage") == _UNKNOWN_ROLE_NOTICE
        else:
            assert "systemMessage" not in output, output.get("systemMessage")


class TestUnknownFrameWritesNothing:
    """No PACT write lands in a plain session's project or session dir. Each
    arm runs a lead control on an identical layout first, so the absence is
    measured on a layout that provably reaches the writer."""

    def test_no_project_claude_md_is_created(self, monkeypatch, tmp_path):
        control = tmp_path / "control"
        control.mkdir()
        _run_real(monkeypatch, control, _frame(**LEAD))
        assert (control / ".claude" / "CLAUDE.md").exists(), (
            "control: a lead start did not create the project CLAUDE.md, so "
            "this layout never reaches the writer"
        )

        plain = tmp_path / "plain"
        plain.mkdir()
        _run_real(monkeypatch, plain, _frame())
        assert sorted(p.name for p in plain.rglob("*")) == [], (
            "an unknown frame wrote into its project"
        )
        assert not _session_dir(plain).exists(), (
            "an unknown frame created a pact-sessions dir for itself"
        )

    def test_a_user_claude_md_is_left_byte_identical(self, monkeypatch, tmp_path):
        control = tmp_path / "control"
        control.mkdir()
        (control / "CLAUDE.md").write_text(USER_MD)
        _run_real(monkeypatch, control, _frame(**LEAD))
        assert (control / "CLAUDE.md").read_text() != USER_MD, (
            "control: a lead start did not migrate a marker-less CLAUDE.md, so "
            "this layout never reaches the migration"
        )

        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "CLAUDE.md").write_text(USER_MD)
        _run_real(monkeypatch, plain, _frame())
        assert (plain / "CLAUDE.md").read_text() == USER_MD, (
            "an unknown frame rewrote the user's own CLAUDE.md"
        )
        assert sorted(p.name for p in plain.rglob("*")) == ["CLAUDE.md"]

    def test_a_root_compact_summary_stays_in_place(self, monkeypatch, tmp_path):
        root_summary = get_compact_summary_path()
        root_summary.parent.mkdir(parents=True, exist_ok=True)

        control = tmp_path / "control"
        control.mkdir()
        root_summary.write_text("stale summary")
        _run_real(monkeypatch, control, _frame(**LEAD))
        assert not root_summary.exists(), (
            "control: a lead start did not drain the root compact summary, so "
            "this layout never reaches the drain"
        )

        plain = tmp_path / "plain"
        plain.mkdir()
        root_summary.write_text("stale summary")
        _run_real(monkeypatch, plain, _frame())
        assert root_summary.read_text() == "stale summary", (
            "an unknown frame moved another session's compact summary"
        )
        assert not _session_dir(plain).exists(), (
            "an unknown frame created a pact-sessions dir to drain a summary into"
        )

    def test_a_linked_worktree_gets_no_identity_record(self, monkeypatch, tmp_path):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

        def git(*args, cwd):
            subprocess.run(
                ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
                 "-c", "init.defaultBranch=main", *args],
                cwd=str(cwd), env=env, capture_output=True, text=True,
                timeout=30, check=True,
            )

        repo = tmp_path / "repo"
        repo.mkdir()
        git("init", "-q", ".", cwd=repo)
        (repo / "README").write_text("seed\n")
        git("add", "README", cwd=repo)
        git("commit", "-qm", "seed", cwd=repo)
        control = tmp_path / "control-wt"
        plain = tmp_path / "plain-wt"
        git("worktree", "add", "-q", str(control), "-b", "control", cwd=repo)
        git("worktree", "add", "-q", str(plain), "-b", "plain", cwd=repo)

        _run_real(monkeypatch, control, _frame(**LEAD))
        assert (_session_dir(control) / "worktree-identity.json").exists(), (
            "control: a lead start inside a linked worktree wrote no identity "
            "record, so this layout never reaches the writer"
        )

        _run_real(monkeypatch, plain, _frame())
        assert not _session_dir(plain).exists(), (
            "an unknown frame inside a linked worktree created a pact-sessions dir"
        )


class TestUnknownFrameKeepsFaultReports:
    """The helper copies three routing predicates from main(). Each kept fault
    report must still reach systemMessage, and each success line must not."""

    def test_each_fault_reaches_system_message(self):
        with patch("session_init.setup_plugin_symlinks",
                   return_value="PACT: 2 agents failed"), \
             patch("session_init.strip_orphan_kernel_block",
                   return_value="Migration skipped: orphan PACT_START"), \
             patch("session_init.check_settings_well_formed",
                   return_value="PACT: settings.json is not valid JSON"), \
             patch("session_init._cleanup_orphan_tokens"):
            output = _unknown_frame_output("startup")
        assert output["systemMessage"] == " | ".join([
            _UNKNOWN_ROLE_NOTICE,
            "PACT: 2 agents failed",
            "Migration skipped: orphan PACT_START",
            "PACT: settings.json is not valid JSON",
        ])
        assert output["hookSpecificOutput"]["additionalContext"] == _UNKNOWN_FRAME_CONTEXT

    def test_success_lines_do_not_reach_either_channel(self):
        with patch("session_init.setup_plugin_symlinks",
                   return_value="PACT: agents linked"), \
             patch("session_init.strip_orphan_kernel_block",
                   return_value="Stripped obsolete PACT kernel block"), \
             patch("session_init.check_settings_well_formed", return_value=None), \
             patch("session_init._cleanup_orphan_tokens"):
            output = _unknown_frame_output("compact")
        assert output == {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": _UNKNOWN_FRAME_CONTEXT,
            }
        }

    def test_real_malformed_settings_and_kernel_markers_are_reported(
        self, monkeypatch, tmp_path
    ):
        config = get_claude_config_dir()
        config.mkdir(parents=True, exist_ok=True)
        (config / "settings.json").write_text("{not json")
        (config / "CLAUDE.md").write_text("<!-- PACT_START: v3 -->\norphan\n")
        plain = tmp_path / "plain"
        plain.mkdir()
        output = _run_real(monkeypatch, plain, _frame())
        message = output.get("systemMessage", "")
        assert message.startswith(_UNKNOWN_ROLE_NOTICE)
        assert "Migration skipped" in message
        assert "is not valid JSON" in message

    def test_a_kernel_block_is_stripped(self, monkeypatch, tmp_path):
        config = get_claude_config_dir()
        config.mkdir(parents=True, exist_ok=True)
        home_md = config / "CLAUDE.md"
        home_md.write_text(
            "keep above\n<!-- PACT_START: v3 -->\norchestrator persona\n"
            "<!-- PACT_END -->\nkeep below\n"
        )
        plain = tmp_path / "plain"
        plain.mkdir()
        _run_real(monkeypatch, plain, _frame())
        text = home_md.read_text()
        assert "PACT_START" not in text and "orchestrator persona" not in text
        assert "keep above" in text and "keep below" in text


class TestMalformedStdin:
    """Stdin that does not parse leaves nothing to classify, so the frame is
    unresolved (None), like the safety net's None case: its population includes
    real leads, so it keeps the ladder plus the unresolved-role cue, with no
    no-role notice and no lead writes. It stays observable in the failure log."""

    @pytest.mark.parametrize("stdin_data", ["not json{", ""], ids=["garbage", "empty"])
    def test_non_json_stdin_gets_the_ladder_and_the_cue_and_is_logged_once(
        self, stdin_data, monkeypatch, tmp_path
    ):
        plain = tmp_path / "plain"
        plain.mkdir()
        recorder = MagicMock()
        with patch("session_init.append_failure", recorder):
            output = _run_real(monkeypatch, plain, stdin_data)
        parts = output["hookSpecificOutput"]["additionalContext"].split(" | ")
        assert parts[0].startswith(LADDER) and BOOTSTRAP in parts[0], parts[0][:200]
        assert parts[1] == _UNRESOLVED_ROLE_CUE, parts[1][:200]
        assert "fail" not in parts[1], (
            "the cue says session_init failed, which is false on this path"
        )
        assert _UNKNOWN_ROLE_NOTICE not in json.dumps(output)
        assert "partially failed" not in json.dumps(output), (
            "the malformed-stdin path raised into the safety net"
        )
        assert recorder.call_count == 1, recorder.call_args_list
        assert recorder.call_args.kwargs["classification"] == "malformed_json"
        assert sorted(p.name for p in plain.rglob("*")) == [], (
            "the unresolved frame wrote into its project"
        )

    @pytest.mark.parametrize("stdin_data", [
        _frame(),
        json.dumps({"source": "startup"}),
    ], ids=["with-session-id", "without-session-id"])
    def test_valid_json_is_not_logged(self, stdin_data, monkeypatch, tmp_path):
        """Only malformed stdin is logged for an unknown frame. The lead control
        sends the same stdin without a session id and IS logged, so the layout
        reaches the failure log."""
        control = tmp_path / "control"
        control.mkdir()
        recorder = MagicMock()
        with patch("session_init.append_failure", recorder):
            _run_real(monkeypatch, control, json.dumps({"source": "startup", **LEAD}))
        assert recorder.call_count == 1, (
            "control: a lead frame without a session id wrote no failure-log "
            "entry, so this layout never reaches the log"
        )

        plain = tmp_path / "plain"
        plain.mkdir()
        recorder = MagicMock()
        with patch("session_init.append_failure", recorder):
            _run_real(monkeypatch, plain, stdin_data)
        assert recorder.call_count == 0, recorder.call_args_list


class TestUnknownFrameKeepsTheEnvFileExport:
    """The CLAUDE_ENV_FILE export runs before the role is classified, so an
    unknown frame still gets it: skill-spawned CLIs in any session read it."""

    def test_the_export_lands(self, monkeypatch, tmp_path):
        env_file = tmp_path / "session-env.sh"
        monkeypatch.setenv("CLAUDE_ENV_FILE", str(env_file))
        plain = tmp_path / "plain"
        plain.mkdir()
        _run_real(monkeypatch, plain, _frame())
        assert env_file.read_text(encoding="utf-8") == (
            f"export CLAUDE_PROJECT_DIR={shlex.quote(str(plain))}\n"
        )


def _seed_lead_session_dir(project_dir):
    """A lead's session folder as a compaction and a /clear leave it: the
    bootstrap marker, the session's own compact summary, and one staged summary
    old enough that a settle pass resolves it."""
    folder = _session_dir(project_dir)
    folder.mkdir(parents=True)
    (folder / BOOTSTRAP_MARKER_NAME).write_text("")
    (folder / COMPACT_SUMMARY_NAME).write_text("the lead's own summary")
    staged_at = datetime.now(timezone.utc) - timedelta(
        seconds=compaction_owner.EXPIRE_S * 5
    )
    assert compaction_owner.stage_summary(
        {"compact_summary": "s" * 300, "session_id": _SESSION_ID},
        str(folder),
        now=lambda: staged_at,
    ), "the staged summary was not written"
    return folder


def _snapshot(folder):
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir())}


class TestUnknownFrameLeavesALeadSessionDirAlone:
    """A session folder under this session id that holds no lead context file
    does not make the frame a lead: it stays unknown and the folder stays as it
    was. The lead control, on the same seed, must clear the marker, archive the
    own-dir summary and settle the staged one, so each writer is reachable from
    this layout. TestResumedLeadIsRecognised covers the folder that does hold
    the context file."""

    @pytest.mark.parametrize("source", ["resume", "clear"])
    def test_the_folder_is_byte_identical(self, source, monkeypatch, tmp_path):
        control = tmp_path / "control"
        control.mkdir()
        folder = _seed_lead_session_dir(control)
        _run_real(monkeypatch, control, _frame(source="clear", **LEAD))
        names = {p.name for p in folder.iterdir()}
        assert BOOTSTRAP_MARKER_NAME not in names, (
            "control: a lead /clear kept the bootstrap marker"
        )
        assert COMPACT_SUMMARY_NAME not in names, (
            "control: a lead /clear did not archive the own-dir summary"
        )
        assert not any(n.startswith("compact-summary.pending-") for n in names), (
            "control: a lead start did not settle the staged summary"
        )

        plain = tmp_path / "plain"
        plain.mkdir()
        folder = _seed_lead_session_dir(plain)
        before = _snapshot(folder)
        _run_real(monkeypatch, plain, _frame(source=source))
        assert _snapshot(folder) == before, (
            f"an unknown frame (source={source!r}) changed the lead's session folder"
        )


def _normalise(output, root, session_id):
    text = json.dumps(output, sort_keys=True)
    return text.replace(str(root), "<R>").replace(session_id, "<S>").replace(
        session_id[:8], "<S8>"
    )


class _PinnedDatetime(datetime):
    """datetime with a fixed now(), so both arms stamp the same second."""

    @classmethod
    def now(cls, tz=None):
        pinned = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        return pinned.astimezone(tz) if tz else pinned.replace(tzinfo=None)


class TestResumedLeadIsRecognised:
    """A lead resumed without `--agent` carries no agent_type at SessionStart
    only. Its own session folder holds pact-session-context.json, which only a
    lead writes, so session_init treats it as the lead. The control repeats the
    same history with the lead's agent_type on the resume frame: the two must
    emit the same output and leave the same folder."""

    @pytest.mark.parametrize("source", ["resume", "clear", "compact"])
    def test_it_gets_the_same_output_as_a_lead_with_the_flag(
        self, source, monkeypatch, tmp_path
    ):
        # The output names second-resolution stamps (the archived summary, the
        # session block's start); pin the clocks that write them.
        monkeypatch.setattr(session_init, "datetime", _PinnedDatetime)
        monkeypatch.setattr(session_resume, "datetime", _PinnedDatetime)
        runs = {}
        for arm, session_id, extra in (
            ("flag", "aaaa1111-0000-0000-0000-000000000001", LEAD),
            ("noflag", "bbbb2222-0000-0000-0000-000000000002", {}),
        ):
            root = tmp_path / arm
            monkeypatch.setattr(Path, "home", lambda root=root: root / "home")
            project = root / "proj"
            project.mkdir(parents=True)
            _run_real(monkeypatch, project, _frame(session_id=session_id, **LEAD))
            folder = _session_dir(project, session_id)
            assert (folder / "pact-session-context.json").is_file(), (
                "the lead start persisted no context file"
            )
            (folder / BOOTSTRAP_MARKER_NAME).write_text("")
            (folder / COMPACT_SUMMARY_NAME).write_text("the lead's own summary")
            # Backdate the recorded start, so the resume rewrites the session
            # block and reports it in both arms.
            claude_md = project / ".claude" / "CLAUDE.md"
            claude_md.write_text(re.sub(
                r"- Started: [^\n]*", "- Started: 2000-01-01 00:00:00 UTC",
                claude_md.read_text(),
            ))
            output = _run_real(
                monkeypatch, project, _frame(source=source, session_id=session_id, **extra)
            )
            journal = folder / "session-journal.jsonl"
            events = [json.loads(line).get("type") for line in journal.read_text().splitlines()]
            runs[arm] = (
                _normalise(output, root, session_id),
                sorted(p.name.split("-2")[0] for p in folder.iterdir()),
                output,
                events,
            )

        flag, noflag = runs["flag"], runs["noflag"]
        additional = noflag[2]["hookSpecificOutput"]["additionalContext"]
        assert additional.startswith(LADDER), additional[:200]
        assert _UNKNOWN_ROLE_NOTICE not in json.dumps(noflag[2])
        if source != "compact":
            assert "Session info updated in project CLAUDE.md" in additional, (
                "the recovered lead did not rewrite its session block"
            )
        assert noflag[0] == flag[0], "the recovered lead's output differs from the flagged lead's"
        assert noflag[1] == flag[1], "the recovered lead left a different session folder"
        assert noflag[3] == flag[3], (
            "the recovered lead's lead-only writes differ from the flagged lead's "
            f"(journal events {noflag[3]} vs {flag[3]})"
        )

    def test_a_resumed_session_without_the_context_file_stays_inert(
        self, monkeypatch, tmp_path
    ):
        control = tmp_path / "control"
        control.mkdir()
        _run_real(monkeypatch, control, _frame(**LEAD))
        assert (_session_dir(control) / "pact-session-context.json").is_file()
        output = _run_real(monkeypatch, control, _frame(source="resume"))
        assert output["hookSpecificOutput"]["additionalContext"].startswith(LADDER), (
            "control: the context file did not make the resumed frame a lead"
        )

        plain = tmp_path / "plain"
        plain.mkdir()
        _session_dir(plain).mkdir(parents=True)
        output = _run_real(monkeypatch, plain, _frame(source="resume"))
        assert output["hookSpecificOutput"]["additionalContext"] == _UNKNOWN_FRAME_CONTEXT
