"""The resumption marker, written by the hook and read back the way the
secretary is told to read it — over the real journal, with nothing stubbed.

THE SEAM. `hooks/session_init.py` appends the marker; `agents/pact-secretary.md`
instructs the secretary to read it with the journal reader's `read-last` verb
against its own session directory. Both halves were covered, and each was
covered against a STUB of the other: `tests/test_session_init.py` patches
`session_init.append_event` and asserts the call, and
`tests/test_session_journal.py` exercises `read-last` on a hand-written journal
of synthetic types. Nothing joined them. A write that lands somewhere the
reader does not look, or under a shape the reader does not return, passes both
suites and fails in operation — and its failure is SILENT, because the reader
answering `null` is also the correct answer for a session that resumes nothing.

WHAT THIS FILE DOES NOT STUB, deliberately: `append_event`, the journal file,
its path resolution, the ADDRESS resolution, and the reader. The write goes
through the hook's own call site into a real per-test session directory, and
the read is the reader's real CLI in a subprocess — the same verb, flag for
flag, that the agent body names. A future edit that reintroduces an
`append_event` patch here removes the only reason this file exists.

THE ADDRESS IS HALF THE SEAM, and an earlier form of this file only had the
other half. It resolved the read directory with `get_session_dir()` — the
LEAD-frame call the hook itself uses — and then congratulated the reader for
finding a marker at an address the real reader cannot compute: the secretary
runs off-lead, where that call returns ''. So the resolution below goes through
`pact_harvest.py resolve-session-dir`, which is what the agent body now names,
and the write directory is asserted equal to what that resolution returns. A
value the reader can parse at an address it cannot reach fails exactly as
silently as a value it cannot parse.

THE TYPE IS NOT NAMED HERE. It is extracted from the hook by
`test_resumption_marker_mirror.marker_event_type`, so this file asks "does what
the hook writes come back to the reader" rather than "does a literal I typed
come back". The literal's agreement with the agent body is that module's job;
this one is about the path between them.

WHAT REMAINS UNTESTABLE, so a green here is not over-read: that the secretary
ACTS on what it reads. What an agent's context holds, and what it does with it,
is not observable from a test. This file proves the fact is available to be
read, never that reading it changed anything.
"""
from __future__ import annotations

import inspect
import io
import json
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.test_resumption_marker_mirror import AGENT_BODY, HOOK, marker_event_type
from tests.test_session_init import _with_lead_role

READER = str(Path(__file__).parent.parent / "hooks" / "shared" / "session_journal.py")
# The secretary is told to resolve its session directory with this subcommand
# before it reads anything. That resolution is HALF the seam: an address the
# reader cannot compute is as fatal as a value it cannot parse, and it fails
# the same silent way.
RESOLVER = str(Path(__file__).parent.parent / "hooks" / "shared" / "pact_harvest.py")

# The verb and flags this module actually runs. Named once and used BOTH to
# build the subprocess argv and to check the agent body, so the docstring's
# "flag for flag" claim is asserted rather than merely stated -- a true claim
# nothing pins goes false in silence the next time either side is edited.
_VERB, _DIR_FLAG, _TYPE_FLAG = "read-last", "--session-dir", "--type"

_SESSION_ID = "aabb1122-0000-0000-0000-000000000000"
_PROJECT_DIR = "/Users/example/Sites/test-project"

# The marker write sits inside the hook's lead-frame branch, so a stdin payload
# without a lead role skips step 8 entirely and every assertion below would be
# measuring a branch that never ran. The role shape comes from the hook suite's
# own helper rather than being restated here.
_STDIN = json.dumps(_with_lead_role({
    "session_id": _SESSION_ID,
    "cwd": _PROJECT_DIR,
    "hook_event_name": "SessionStart",
    "source": "startup",
}))

# The patches every driver shares. `append_event` is NOT among them, and that
# omission is the point of this module.
_PATCHED = (
    ("session_init.setup_plugin_symlinks", None),
    ("session_init.ensure_project_memory_md", None),
    ("session_init.check_pinned_staleness", None),
    ("session_init.update_session_info", None),
    ("session_init.get_task_list", None),
    ("session_init.restore_last_session", None),
)


def _run_hook(monkeypatch, resume_msg):
    """Drive the hook's session-start path and return its real session dir.

    Everything heavy or environment-touching is patched EXCEPT the journal
    write, which is the subject. `check_resume_state` is patched because it is
    the INPUT to the branch under test — the resolver has its own suite — but
    its verdict then travels the real code path from there.
    """
    import session_init
    import shared.pact_context as ctx

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)

    with ExitStack() as stack:
        for target, value in _PATCHED:
            stack.enter_context(patch(target, return_value=value))
        stack.enter_context(
            patch("session_init.check_resume_state", return_value=resume_msg)
        )
        stack.enter_context(patch("sys.stdin", io.StringIO(_STDIN)))
        stack.enter_context(patch("sys.stdout", new_callable=io.StringIO))
        with pytest.raises(SystemExit) as exc:
            session_init.main()

    assert exc.value.code == 0
    session_dir = ctx.get_session_dir()
    assert session_dir, "the hook resolved no session directory to write into"
    return session_dir


def _documented_read_dir(write_dir, home):
    """The read directory, resolved the way the SECRETARY is told to resolve it.

    NOT `get_session_dir()`. That call is what the hook uses and it works only
    in a LEAD frame — off-lead it returns '' — so a test that resolved the read
    side with it would be handing the reader an address the real reader cannot
    compute, and would go green on the one failure it exists to catch.

    `write_dir` is used ONLY to locate the context file the hook just wrote; the
    directory itself comes back out of the resolver, and the caller asserts the
    two agree. That is the check: the write lands where the documented,
    off-lead resolution says to look.
    """
    context_file = str(Path(write_dir) / "pact-session-context.json")
    result = subprocess.run(
        [sys.executable, RESOLVER, "resolve-session-dir",
         "--context-file", context_file],
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(home)},
    )
    assert result.returncode == 0, (
        f"the secretary's own session-dir resolution failed on the context "
        f"file the hook wrote ({context_file}): {result.stderr}. The secretary "
        "would then have no address to read the marker from, and its "
        "instructions send it to report a gap rather than rebuild."
    )
    resolved = result.stdout.strip()
    assert resolved, "resolve-session-dir exited 0 with no directory on stdout"
    return resolved


def _read_last(session_dir, event_type, home):
    """The secretary's own read, as a subprocess.

    `HOME` is set explicitly because `monkeypatch.setattr` on `Path.home` does
    not cross into a child process — the established convention in this suite.
    """
    result = subprocess.run(
        [
            sys.executable, READER, _VERB,
            _DIR_FLAG, session_dir,
            _TYPE_FLAG, event_type,
        ],
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(home)},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def event_type():
    return marker_event_type(HOOK.read_text(encoding="utf-8"))


class TestTheMarkerSurvivesTheTripFromHookToReader:

    def test_a_surfaced_claim_is_readable_by_the_verb_the_secretary_runs(
        self, monkeypatch, tmp_path, event_type
    ):
        """Detects: a write that never lands where the documented read looks.

        The consequential direction. A marker the reader cannot return is
        indistinguishable from no marker, so the secretary rebuilds and the
        agents respawned to judge the arc are handed the arc's own conclusions
        — while both halves' own suites stay green against their stubs.
        """
        write_dir = _run_hook(
            monkeypatch, resume_msg="Paused work detected: feat/login."
        )
        read_dir = _documented_read_dir(write_dir, Path.home())

        assert read_dir == write_dir, (
            f"the hook wrote the marker into {write_dir} and the secretary's "
            f"own resolution points at {read_dir}. The reader would find "
            "nothing, which is the same answer as a session that resumes "
            "nothing, so the rebuild runs."
        )

        event = _read_last(read_dir, event_type, Path.home())

        assert event is not None, (
            f"the hook wrote {event_type!r} but read-last returned null for it "
            f"in {read_dir} — the write and the documented read disagree "
            "about where the marker lives."
        )
        assert event["type"] == event_type

    def test_no_claim_leaves_the_reader_answering_null(
        self, monkeypatch, tmp_path, event_type
    ):
        """Detects: a marker written unconditionally.

        This is the arm that makes the sibling above mean something. Presence
        IS the signal, so a marker written when nothing surfaced would freeze
        the Working Memory block of an ordinary session forever — and the
        sibling test would still pass, because it only asks whether the event
        can be read back.
        """
        write_dir = _run_hook(monkeypatch, resume_msg=None)
        read_dir = _documented_read_dir(write_dir, Path.home())

        assert _read_last(read_dir, event_type, Path.home()) is None

    def test_the_driver_does_not_stub_the_write(self):
        """Detects: a later edit adding the writer to this module's patch table.

        The value of this module is that ONE seam is real. A patch on the writer
        would leave both assertions above passing while measuring nothing —
        the shape this file exists to catch. Scoped to the shared driver and its
        patch table rather than to the whole file, because the loud-failure test
        below patches the writer legitimately and a file-wide ban would forbid
        that too.
        """
        assert not [t for t, _ in _PATCHED if "append_event" in t]
        assert "append_event" not in inspect.getsource(_run_hook)

    def test_the_agent_body_names_the_verb_and_flags_this_module_runs(self):
        """Detects: the documented read and this module's read drifting apart.

        The docstring above claims the subprocess runs the verb and flags the
        agent body names. That was TRUE and pinned by nothing: this module never
        opened the agent body, so an edit to either side would leave the claim
        quietly false and the green here would stop meaning what it says. The
        tokens come from the same constants the subprocess is built from, so the
        pin cannot drift from what actually runs.

        SCOPED TO THE COMMAND, NOT TO THE STEP, and the difference is not
        pedantry -- the first draft of this pin searched the whole spawn step
        and a mutation walked straight through it. The step's prose NAMES the
        verb ("the journal reader's `read-last` verb") as well as running it, so
        a body whose command said `read` while its prose still said `read-last`
        satisfied a step-wide token search. Measured, not reasoned: that mutant
        passed 5/5 before this was narrowed. A token search answers "does this
        word appear somewhere near", which is not the question.
        """
        body = AGENT_BODY.read_text(encoding="utf-8")
        start = body.find("1. **Rebuild Working Memory from the store**")
        assert start != -1, (
            "the spawn step's opening marker is gone from the agent body -- "
            "re-anchor this pin, do not delete it."
        )
        end = body.find("\n2. **Search pact-memory**", start)
        assert end != -1, "the spawn step has no terminating numbered line"
        spawn_step = body[start:end]

        # The invocation itself: from the reader's filename to the end of the
        # backtick span holding it. Exactly one, or the anchor is ambiguous and
        # this pin would be reading some other command.
        anchor = "session_journal.py"
        assert spawn_step.count(anchor) == 1, (
            f"expected exactly one {anchor!r} in the spawn step, found "
            f"{spawn_step.count(anchor)}. Re-anchor this pin on the invocation "
            "the secretary is told to run; do not widen it back to the step."
        )
        at = spawn_step.index(anchor)
        close = spawn_step.find("`", at)
        assert close != -1, "the spawn step's read command is not in a backtick span"
        command = spawn_step[at:close]

        for token in (_VERB, _DIR_FLAG, _TYPE_FLAG):
            assert token in command, (
                f"this module runs {token!r} but the command the secretary's "
                f"spawn step tells it to run does not carry it: {command!r}. "
                "The seam is then tested with an invocation the secretary was "
                "never told to make."
            )


class TestTheHookStillAnnouncesAFailedWrite:

    def test_a_failed_write_reaches_both_channels(self, monkeypatch, tmp_path):
        """Detects: the loud fail direction going quiet.

        The write's failure cannot be made safe — no marker means the secretary
        rebuilds — so it is made loud instead, on both channels. Covered here
        rather than only against a mocked writer because this file is where the
        real writer runs: the mock below replaces it for this one test on
        purpose, since a genuine write failure cannot be provoked otherwise.
        """
        import session_init

        failing = MagicMock(return_value=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", _PROJECT_DIR)

        with ExitStack() as stack:
            for target, value in _PATCHED:
                stack.enter_context(patch(target, return_value=value))
            stack.enter_context(patch(
                "session_init.check_resume_state",
                return_value="Paused work detected: feat/login.",
            ))
            stack.enter_context(patch("session_init.append_event", failing))
            stack.enter_context(patch("sys.stdin", io.StringIO(_STDIN)))
            out = stack.enter_context(
                patch("sys.stdout", new_callable=io.StringIO)
            )
            with pytest.raises(SystemExit) as exc:
                session_init.main()

        assert exc.value.code == 0, "a failed marker write must never block start"
        payload = json.loads(out.getvalue())
        assert "not recorded" in payload["systemMessage"]
        assert "RESUMPTION MARKER MISSING" in (
            payload["hookSpecificOutput"]["additionalContext"]
        )
