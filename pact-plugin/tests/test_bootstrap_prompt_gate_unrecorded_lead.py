"""
Location: pact-plugin/tests/test_bootstrap_prompt_gate_unrecorded_lead.py
Summary: bootstrap_prompt_gate records a lead that session_init did not
         record, at the lead's first prompt.
Used by: pytest.

session_init records a lead at SessionStart: the project CLAUDE.md Current
Session block, the session_start journal event, the worktree identity and the
session values in the startup context. A lead it cannot recognise there (a fork
without `--agent`, or a resume without `--agent` whose session dir was reaped or
whose project moved) gets none of them. Its first prompt carries the lead
agent_type, so the gate records it then, once: it reads the old block (and the
previous session's pause or refresh claim when the block names another
session), replaces the block, records the worktree identity, and appends
session_start(source="prompt"). bootstrap_marker_writer leaves the block alone
until that session_start exists, and the gate suppresses only when the marker
is set AND session_start exists, so the hook order does not matter even when
the writer stamps the marker first.

Every test drives the real hooks in a fresh interpreter under tmp_path. HOME is
the sandbox and every CLAUDE_* var except the three set here is dropped, so no
run reaches the real config dir. Paused claims are dated 2020, so the claim
interpreter takes its stale branch and never calls `gh`.
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

import pytest

_HOOKS = Path(__file__).resolve().parents[1] / "hooks"
_PLUGIN_ROOT = _HOOKS.parent
LEAD = "PACT:pact-orchestrator"
_NOTE_MARK = "This session is the PACT team-lead."
_VALUES_MARK = "These replace any session values earlier in this conversation."
_STALE_MARK = "stale session block"
_ALIGNED_TEAM = "session-real0000"


def _sandbox(tmp_path):
    home = tmp_path / "home"
    proj = home / "proj"
    proj.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update(
        HOME=str(home),
        CLAUDE_PROJECT_DIR=str(proj),
        CLAUDE_PLUGIN_ROOT=str(_PLUGIN_ROOT),
    )
    return home, proj, env


def _sdir(home, sid, slug="proj"):
    return home / ".claude" / "pact-sessions" / slug / sid


def _run(hook, frame, home, env):
    proc = subprocess.run(
        [sys.executable, str(_HOOKS / hook)], input=json.dumps(frame),
        capture_output=True, text=True, env=env, cwd=str(home), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _start(sid, source, agent_type, home, env):
    frame = {"hook_event_name": "SessionStart", "session_id": sid,
             "source": source}
    if agent_type is not None:
        frame["agent_type"] = agent_type
    return _run("session_init.py", frame, home, env)


def _prompt_frame(sid, agent_type: Optional[str] = LEAD):
    frame = {"hook_event_name": "UserPromptSubmit", "session_id": sid,
             "prompt": "first prompt"}
    if agent_type is not None:
        frame["agent_type"] = agent_type
    return frame


def _gate(sid, home, env, agent_type: Optional[str] = LEAD):
    out = _run("bootstrap_prompt_gate.py", _prompt_frame(sid, agent_type), home, env)
    return out.get("hookSpecificOutput", {}).get("additionalContext", "")


def _block(proj):
    return (proj / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")


def _events(home, sid, event_type, slug="proj"):
    journal = _sdir(home, sid, slug) / "session-journal.jsonl"
    if not journal.exists():
        return []
    events = [json.loads(line) for line in journal.read_text().splitlines() if line]
    return [e for e in events if e.get("type") == event_type]


def _pause(session_dir, pr_number):
    session_dir.mkdir(parents=True, exist_ok=True)
    with (session_dir / "session-journal.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "v": 1, "type": "session_paused", "ts": "2020-01-01T00:00:00Z",
            "pr_number": pr_number, "pr_url": f"https://example.invalid/pull/{pr_number}",
            "branch": "feat/x", "worktree_path": "/tmp/nowhere",
            "consolidation_completed": True,
        }) + "\n")


def _diverge_team(home, sid, proj):
    """Give the marker writer a team-name write-back to do on this prompt.

    The platform team gets a name other than the computed session-<id8>, and a
    context file is already on disk with an older name (as when session_init
    persisted it and then failed before its journal write). The heal leaves an
    existing file alone, so the marker writer finds the persisted and aligned
    names differ, and reaches its CLAUDE.md write on the same prompt as the
    gate.
    """
    team = home / ".claude" / "teams" / _ALIGNED_TEAM
    team.mkdir(parents=True)
    (team / "config.json").write_text(
        json.dumps({"leadSessionId": sid, "members": []}), encoding="utf-8")
    _sdir(home, sid).mkdir(parents=True, exist_ok=True)
    (_sdir(home, sid) / "pact-session-context.json").write_text(json.dumps({
        "team_name": "session-persist0", "session_id": sid,
        "project_dir": str(proj), "plugin_root": str(_PLUGIN_ROOT),
        "started_at": "2026-01-01T00:00:00+00:00",
    }), encoding="utf-8")


def _block_text(sid, team, session_dir):
    return (
        "# Project\n\n<!-- SESSION_START -->\n## Current Session\n"
        f"- Resume: `claude --agent {LEAD} --resume {sid}`\n"
        f"- Team: `{team}`\n- Session dir: `{session_dir}`\n"
        "- Started: 2026-01-01 00:00:00 UTC\n<!-- SESSION_END -->\n"
    )


class TestForkedLeadIsRecorded:

    @pytest.mark.parametrize("order", [
        ("bootstrap_prompt_gate.py", "bootstrap_marker_writer.py"),
        ("bootstrap_marker_writer.py", "bootstrap_prompt_gate.py"),
    ], ids=["gate-first", "writer-first"])
    def test_first_prompt_records_the_fork_whatever_the_hook_order(
        self, tmp_path, order
    ):
        home, proj, env = _sandbox(tmp_path)
        parent, fork = str(uuid.uuid4()), str(uuid.uuid4())
        _start(parent, "startup", LEAD, home, env)
        assert f"--resume {parent}" in _block(proj), "control: the parent lead is recorded"
        _pause(_sdir(home, parent), 4242)
        started = _start(fork, "fork", None, home, env)
        assert "PACT cannot dispatch" in started["hookSpecificOutput"]["additionalContext"]
        _diverge_team(home, fork, proj)

        outputs = {hook: _run(hook, _prompt_frame(fork), home, env) for hook in order}
        context = outputs["bootstrap_prompt_gate.py"]["hookSpecificOutput"][
            "additionalContext"]

        block = _block(proj)
        assert f"--resume {fork}" in block and parent not in block
        assert f"- Team: `{_ALIGNED_TEAM}`" in block
        assert context.startswith(_NOTE_MARK)
        assert "PR #4242" in context, "the parent's pause must be surfaced"
        assert f"`{_ALIGNED_TEAM}`" in context and str(_sdir(home, fork)) in context
        assert _VALUES_MARK in context
        assert _STALE_MARK not in context
        assert [e.get("source") for e in _events(home, fork, "session_start")] == ["prompt"]
        assert len(_events(home, fork, "session_resumption_surfaced")) == 1

    def test_no_claim_means_no_resumption_marker(self, tmp_path):
        home, proj, env = _sandbox(tmp_path)
        parent, fork = str(uuid.uuid4()), str(uuid.uuid4())
        _start(parent, "startup", LEAD, home, env)
        _start(fork, "fork", None, home, env)

        context = _gate(fork, home, env)

        assert f"--resume {fork}" in _block(proj)
        assert "PR #" not in context
        assert _events(home, fork, "session_start"), "control: the fork was recorded"
        assert _events(home, fork, "session_resumption_surfaced") == []

    def test_the_branch_runs_once(self, tmp_path):
        home, proj, env = _sandbox(tmp_path)
        parent, fork = str(uuid.uuid4()), str(uuid.uuid4())
        _start(parent, "startup", LEAD, home, env)
        _start(fork, "fork", None, home, env)

        first = _gate(fork, home, env)
        second = _gate(fork, home, env)

        assert first.startswith(_NOTE_MARK) and _VALUES_MARK in first
        assert _NOTE_MARK not in second and _VALUES_MARK not in second
        assert len(_events(home, fork, "session_start")) == 1

    def test_a_linked_worktree_identity_is_recorded(self, tmp_path):
        home, proj, env = _sandbox(tmp_path)
        git_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        git_env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

        def git(*args, cwd):
            subprocess.run(
                ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t",
                 "-c", "init.defaultBranch=main", *args],
                cwd=str(cwd), env=git_env, capture_output=True, timeout=30, check=True,
            )

        git("init", "-q", ".", cwd=proj)
        git("commit", "-q", "--allow-empty", "-m", "seed", cwd=proj)
        worktree = home / "wt"
        git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=proj)
        env["CLAUDE_PROJECT_DIR"] = str(worktree)
        fork = str(uuid.uuid4())
        _start(fork, "fork", None, home, env)
        record = _sdir(home, fork, "wt") / "worktree-identity.json"
        assert not record.exists(), "control: an unknown frame records nothing"

        _gate(fork, home, env)

        assert json.loads(record.read_text())["session_id"] == fork


class TestBlockRewriteScope:

    def test_same_session_with_a_stale_team_is_rewritten_without_a_claim(self, tmp_path):
        """A lead resumed without --agent after its session dir was reaped:
        the block names this session, with a team the platform has since
        replaced. Its own journal holds a pause, which would surface if the
        claim were read for the same session."""
        home, proj, env = _sandbox(tmp_path)
        sid = str(uuid.uuid4())
        own = _sdir(home, sid)
        (proj / ".claude").mkdir()
        (proj / ".claude" / "CLAUDE.md").write_text(
            _block_text(sid, "session-old00000", own), encoding="utf-8")
        _pause(own, 5151)

        context = _gate(sid, home, env)

        assert f"- Team: `session-{sid[:8]}`" in _block(proj)
        assert "session-old00000" not in _block(proj)
        assert "PR #5151" not in context
        assert _events(home, sid, "session_resumption_surfaced") == []

    def test_a_moved_project_block_is_rewritten(self, tmp_path):
        """Same session id and team; the Session dir line still names the
        directory under the project's old slug, which still exists."""
        home, proj, env = _sandbox(tmp_path)
        sid = str(uuid.uuid4())
        old_dir = _sdir(home, sid, "oldname")
        old_dir.mkdir(parents=True)
        (proj / ".claude").mkdir()
        (proj / ".claude" / "CLAUDE.md").write_text(
            _block_text(sid, f"session-{sid[:8]}", old_dir), encoding="utf-8")

        _gate(sid, home, env)

        block = _block(proj)
        assert f"- Session dir: `{_sdir(home, sid)}`" in block
        assert str(old_dir) not in block


class TestNothingIsWrittenOutsideTheBranch:

    def test_a_plain_frame_writes_nothing(self, tmp_path):
        home, proj, env = _sandbox(tmp_path)
        parent, plain = str(uuid.uuid4()), str(uuid.uuid4())
        _start(parent, "startup", LEAD, home, env)
        before = _block(proj)
        _start(plain, "startup", None, home, env)

        out = _run("bootstrap_prompt_gate.py", _prompt_frame(plain, None), home, env)

        assert out.get("suppressOutput") is True
        assert _block(proj) == before
        assert not _sdir(home, plain).exists()
        # Control: a lead frame for the same session is recorded.
        _gate(plain, home, env)
        assert f"--resume {plain}" in _block(proj)

    def test_a_recorded_lead_keeps_a_foreign_block_and_is_warned(self, tmp_path):
        home, proj, env = _sandbox(tmp_path)
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        _start(first, "startup", LEAD, home, env)
        _start(second, "startup", LEAD, home, env)
        before = _block(proj)
        assert f"--resume {second}" in before

        context = _gate(first, home, env)

        assert _block(proj) == before
        assert _STALE_MARK in context and _NOTE_MARK not in context

    def test_no_claude_md_is_created(self, tmp_path):
        home, proj, env = _sandbox(tmp_path)
        fork = str(uuid.uuid4())
        _start(fork, "fork", None, home, env)

        context = _gate(fork, home, env)

        assert sorted(p.name for p in proj.rglob("*")) == []
        assert _VALUES_MARK in context, "control: the branch ran"
        assert _events(home, fork, "session_start")

    @pytest.mark.parametrize("content", [
        "# My project\n\nUser notes.\n",
        "# My project\n<!-- SESSION_START -->\n- Resume: `claude --resume abc`\n",
    ], ids=["no-block", "start-marker-only"])
    def test_a_file_without_a_whole_block_is_left_byte_identical(self, tmp_path, content):
        home, proj, env = _sandbox(tmp_path)
        (proj / "CLAUDE.md").write_text(content, encoding="utf-8")
        fork = str(uuid.uuid4())
        _start(fork, "fork", None, home, env)

        context = _gate(fork, home, env)

        assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == content
        assert sorted(p.name for p in proj.iterdir()) == ["CLAUDE.md"]
        assert _VALUES_MARK in context, "control: the branch ran"


_INSTRUCTION_MARK = 'Skill("PACT:bootstrap")'
_FLAG = "lead-recorded"


def _secretary_team(home, sid):
    """A team for ``sid`` that already lists the secretary, as a resumed lead's
    surviving team does, so the marker writer's preconditions hold and it
    stamps bootstrap-complete on the first prompt."""
    team = home / ".claude" / "teams" / _ALIGNED_TEAM
    team.mkdir(parents=True)
    (team / "config.json").write_text(json.dumps({
        "name": _ALIGNED_TEAM, "leadSessionId": sid,
        "members": [{"name": "team-lead"},
                    {"name": "secretary", "agentType": "pact-secretary"}],
    }), encoding="utf-8")


def _unrecorded_lead_with_secretary(tmp_path):
    home, proj, env = _sandbox(tmp_path)
    parent, lead = str(uuid.uuid4()), str(uuid.uuid4())
    _start(parent, "startup", LEAD, home, env)
    _pause(_sdir(home, parent), 5151)
    _start(lead, "fork", None, home, env)
    _secretary_team(home, lead)
    return home, proj, env, parent, lead


def _prompt(order, lead, home, env, agent_type: Optional[str] = LEAD):
    outs = {hook: _run(hook, _prompt_frame(lead, agent_type), home, env) for hook in order}
    return outs["bootstrap_prompt_gate.py"].get("hookSpecificOutput", {}).get(
        "additionalContext", "")


_WRITER_FIRST = ("bootstrap_marker_writer.py", "bootstrap_prompt_gate.py")
_GATE_FIRST = ("bootstrap_prompt_gate.py", "bootstrap_marker_writer.py")


class TestTheMarkerWriterCannotPreemptTheRecording:
    """The marker writer runs beside the gate on every prompt and can stamp
    bootstrap-complete first. The gate suppresses only when the journal also
    holds session_start, so an unrecorded lead is recorded either way."""

    def test_writer_first_records_the_lead_without_the_instruction(self, tmp_path):
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)

        _run("bootstrap_marker_writer.py", _prompt_frame(lead), home, env)
        assert (_sdir(home, lead) / "bootstrap-complete").exists(), (
            "control: the writer stamped before the gate ran")
        context = _gate(lead, home, env)

        block = _block(proj)
        assert f"--resume {lead}" in block and parent not in block
        assert context.startswith(_NOTE_MARK) and _VALUES_MARK in context
        assert _INSTRUCTION_MARK not in context, "bootstrap is already complete"
        assert "PR #5151" in context, "the previous session's pause must be surfaced"
        assert [e.get("source") for e in _events(home, lead, "session_start")] == ["prompt"]
        assert len(_events(home, lead, "session_resumption_surfaced")) == 1

        assert _prompt(_WRITER_FIRST, lead, home, env) == ""
        assert len(_events(home, lead, "session_start")) == 1

    def test_gate_first_records_the_lead_and_the_writer_stamps(self, tmp_path):
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)

        context = _prompt(_GATE_FIRST, lead, home, env)

        assert f"--resume {lead}" in _block(proj)
        assert context.startswith(_NOTE_MARK) and _INSTRUCTION_MARK in context
        assert "PR #5151" in context
        assert (_sdir(home, lead) / "bootstrap-complete").exists()
        assert _prompt(_GATE_FIRST, lead, home, env) == ""
        assert len(_events(home, lead, "session_start")) == 1
        assert len(_events(home, lead, "session_resumption_surfaced")) == 1

    def test_a_failed_recording_is_retried_once_and_not_duplicated(self, tmp_path):
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)

        # The gate's read of CLAUDE.md raises EIO: the recording fails before
        # any write, in a way that may clear.
        failed = _prompt(_WRITER_FIRST, lead, home,
                         _with_site(tmp_path, env, _EIO_ON_GATE_READ))

        assert failed.startswith(_NOTE_MARK) and _VALUES_MARK not in failed
        assert "PR #5151" not in failed, "the claim waits for the prompt that records"
        assert _events(home, lead, "session_start") == []
        assert not (_sdir(home, lead) / _FLAG).exists(), (
            "a failure before the rewrite leaves no flag, so the next prompt retries")

        retried = _prompt(_WRITER_FIRST, lead, home, env)
        third = _prompt(_WRITER_FIRST, lead, home, env)

        assert retried.count("PR #5151") == 1 and _VALUES_MARK in retried
        assert third == ""
        assert _block(proj).count("<!-- SESSION_START -->") == 1
        assert f"--resume {lead}" in _block(proj)
        assert len(_events(home, lead, "session_start")) == 1
        assert len(_events(home, lead, "session_resumption_surfaced")) == 1

    def test_a_soft_failed_append_is_recorded_once_and_retried_quietly(self, tmp_path):
        """The session_start append fails soft: the note, values and claim go
        out once and the flag stands in for session_start. Once the journal is
        writable again, the next prompt appends session_start silently."""
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)
        journal = _sdir(home, lead) / "session-journal.jsonl"
        journal.mkdir(parents=True)  # every append fails soft: it returns False

        failed = _prompt(_WRITER_FIRST, lead, home, env)

        assert failed.count("PR #5151") == 1, "the claim read before the rewrite is shown"
        assert "RESUMPTION MARKER MISSING" in failed
        assert f"--resume {lead}" in _block(proj)
        assert (_sdir(home, lead) / _FLAG).exists()
        recorded = _block(proj)
        journal.rmdir()

        retried = _prompt(_WRITER_FIRST, lead, home, env)

        assert retried == "", "no R1 output: the note and rewrite happened once"
        assert _block(proj) == recorded
        assert [e.get("source") for e in _events(home, lead, "session_start")] == ["prompt"]

        assert _prompt(_WRITER_FIRST, lead, home, env) == ""
        assert len(_events(home, lead, "session_start")) == 1
        assert _events(home, lead, "session_resumption_surfaced") == []

    def test_a_journal_that_stays_unwritable_records_the_lead_once(self, tmp_path):
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)
        (_sdir(home, lead) / "session-journal.jsonl").mkdir(parents=True)

        first = _prompt(_WRITER_FIRST, lead, home, env)
        recorded = _block(proj)

        assert _VALUES_MARK in first and first.count("PR #5151") == 1
        assert _prompt(_WRITER_FIRST, lead, home, env) == ""
        assert _prompt(_WRITER_FIRST, lead, home, env) == ""
        assert _block(proj) == recorded, "the Started line must not move each prompt"

    def test_a_lead_without_the_marker_gets_the_note_once(self, tmp_path):
        """No secretary, so no marker: later prompts keep the bootstrap
        instruction but not the note, the values or the block rewrite."""
        home, proj, env = _sandbox(tmp_path)
        parent, lead = str(uuid.uuid4()), str(uuid.uuid4())
        _start(parent, "startup", LEAD, home, env)
        _start(lead, "fork", None, home, env)
        (_sdir(home, lead) / "session-journal.jsonl").mkdir(parents=True)

        first = _gate(lead, home, env)
        recorded = _block(proj)
        second = _gate(lead, home, env)

        assert first.startswith(_NOTE_MARK) and _VALUES_MARK in first
        assert _INSTRUCTION_MARK in second
        assert _NOTE_MARK not in second and _VALUES_MARK not in second
        assert _block(proj) == recorded

    @pytest.mark.parametrize("agent_type", [None, "pact-backend-coder"],
                             ids=["no-role", "teammate"])
    def test_a_non_lead_frame_stamps_nothing_and_writes_nothing(self, tmp_path, agent_type):
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)
        before = _block(proj)

        context = _prompt(_WRITER_FIRST, lead, home, env, agent_type=agent_type)

        assert context == ""
        assert not (_sdir(home, lead) / "bootstrap-complete").exists()
        assert _events(home, lead, "session_start") == []
        assert _block(proj) == before

    @pytest.mark.parametrize("agent_type", [None, "pact-backend-coder"],
                             ids=["no-role", "teammate"])
    def test_a_non_lead_frame_after_a_lead_stamped_the_marker_writes_nothing(
        self, tmp_path, agent_type
    ):
        # The marker writer is lead-gated, so the arm above never has the marker
        # set. Stamp it with a lead frame so a non-lead frame meets the state the
        # recording branch keys on: marker set, no session_start.
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)
        _run("bootstrap_marker_writer.py", _prompt_frame(lead), home, env)
        assert (_sdir(home, lead) / "bootstrap-complete").exists(), "control: marker set"
        assert _events(home, lead, "session_start") == [], "control: not recorded"
        before = _block(proj)

        context = _gate(lead, home, env, agent_type=agent_type)

        assert context == ""
        assert _events(home, lead, "session_start") == []
        assert _block(proj) == before


_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

# sitecustomize sources, loaded by every hook process _with_site starts.
# This one: the rename that would put a new CLAUDE.md in place raises EIO.
_EIO_ON_REPLACE = """
import errno, os
_replace = os.replace
def replace(src, dst, *args, **kwargs):
    if str(dst).endswith("CLAUDE.md"):
        raise OSError(errno.EIO, "injected")
    return _replace(src, dst, *args, **kwargs)
os.replace = replace
"""

# This one: the rename into CLAUDE.md succeeds, and the next close (the
# parent directory's) raises EIO after closing the descriptor.
_EIO_ON_CLOSE_AFTER_REPLACE = """
import errno, os
_replace, _close = os.replace, os.close
_armed = []
def replace(src, dst, *args, **kwargs):
    result = _replace(src, dst, *args, **kwargs)
    if str(dst).endswith("CLAUDE.md"):
        _armed.append(True)
    return result
def close(fd):
    _close(fd)
    if _armed:
        _armed.clear()
        raise OSError(errno.EIO, "injected")
os.replace, os.close = replace, close
"""


# Shared by the two flock sources below: is `fd` the project's CLAUDE.md lock
# file? Compared by inode, not by patching os.open: Python 3.9's pathlib binds
# os.open as a method, so a replacement function there breaks every Path.open.
_IS_CLAUDE_MD_LOCK = """
import errno, fcntl, os
_flock = fcntl.flock
def _is_claude_md_lock(fd):
    lock = os.path.join(os.environ["CLAUDE_PROJECT_DIR"], ".claude", ".CLAUDE.md.lock")
    try:
        held, named = os.fstat(fd), os.stat(lock)
    except OSError:
        return False
    return (held.st_dev, held.st_ino) == (named.st_dev, named.st_ino)
"""

# This one: unlocking the CLAUDE.md lock file unlocks it, then raises EIO.
_EIO_ON_LOCK_RELEASE = _IS_CLAUDE_MD_LOCK + """
def flock(fd, operation):
    _flock(fd, operation)
    if operation & fcntl.LOCK_UN and _is_claude_md_lock(fd):
        raise OSError(errno.EIO, "injected")
fcntl.flock = flock
"""


# This one: the gate's own read of CLAUDE.md raises EIO (other hooks read it).
_EIO_ON_GATE_READ = """
import errno, pathlib, sys
_read_text = pathlib.Path.read_text
def read_text(self, *args, **kwargs):
    if self.name == "CLAUDE.md" and sys.argv[0].endswith("bootstrap_prompt_gate.py"):
        raise OSError(errno.EIO, "injected")
    return _read_text(self, *args, **kwargs)
pathlib.Path.read_text = read_text
"""

# This one: taking the CLAUDE.md lock raises ENOTSUP, as on a filesystem
# without flock.
_ENOTSUP_ON_LOCK = _IS_CLAUDE_MD_LOCK + """
def flock(fd, operation):
    if operation & fcntl.LOCK_EX and _is_claude_md_lock(fd):
        raise OSError(errno.ENOTSUP, "injected")
    return _flock(fd, operation)
fcntl.flock = flock
"""


def _with_site(tmp_path, env, source):
    site = tmp_path / "site"
    site.mkdir(exist_ok=True)
    (site / "sitecustomize.py").write_text(source, encoding="utf-8")
    return {**env, "PYTHONPATH": str(site)}


class TestATransientRewriteFailureLeavesTheRecordingOpen:
    """When the block rewrite fails for a reason that may clear (the lock held
    past its timeout, an I/O error), nothing is recorded: the note goes out,
    and the values and the claim wait for the prompt that records. A skip by
    design still closes the recording."""

    @staticmethod
    def _assert_open(out, home, lead, proj, before):
        assert out.startswith(_NOTE_MARK)
        assert _VALUES_MARK not in out and "PR #5151" not in out
        assert _events(home, lead, "session_start") == []
        assert not (_sdir(home, lead) / _FLAG).exists()
        assert _block(proj) == before

    @staticmethod
    def _assert_recorded_once(out, home, lead, proj):
        assert _VALUES_MARK in out and out.count("PR #5151") == 1
        assert f"--resume {lead}" in _block(proj)

    def test_a_lock_held_past_its_timeout_retries_on_the_next_prompt(self, tmp_path):
        import fcntl
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)
        before = _block(proj)
        fd = os.open(str(proj / ".claude" / ".CLAUDE.md.lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            held = _prompt(_WRITER_FIRST, lead, home, env)
        finally:
            os.close(fd)

        self._assert_open(held, home, lead, proj, before)
        self._assert_recorded_once(_prompt(_WRITER_FIRST, lead, home, env), home, lead, proj)
        assert len(_events(home, lead, "session_start")) == 1
        assert len(_events(home, lead, "session_resumption_surfaced")) == 1
        assert _prompt(_WRITER_FIRST, lead, home, env) == ""

    @pytest.mark.parametrize("journal_works", [True, False], ids=["journal", "no-journal"])
    def test_an_io_failure_retries_on_the_next_prompt(self, tmp_path, journal_works):
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)
        if not journal_works:
            (_sdir(home, lead) / "session-journal.jsonl").mkdir(parents=True)
        before = _block(proj)

        failed = _prompt(_WRITER_FIRST, lead, home, _with_site(tmp_path, env, _EIO_ON_REPLACE))

        if journal_works:
            self._assert_open(failed, home, lead, proj, before)
        else:
            assert failed.startswith(_NOTE_MARK) and _VALUES_MARK not in failed
            assert not (_sdir(home, lead) / _FLAG).exists()
            assert _block(proj) == before
        recorded = _prompt(_WRITER_FIRST, lead, home, env)
        self._assert_recorded_once(recorded, home, lead, proj)
        if journal_works:
            assert len(_events(home, lead, "session_start")) == 1
        else:
            assert (_sdir(home, lead) / _FLAG).exists()
        assert _prompt(_WRITER_FIRST, lead, home, env) == ""

    def test_a_close_that_fails_after_the_rename_records_on_the_same_prompt(self, tmp_path):
        """The new block is in place once the rename succeeds, so a failure
        closing the directory afterwards is no failed write: the lead is
        recorded, claim and all, on this prompt."""
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)

        out = _prompt(_WRITER_FIRST, lead, home,
                      _with_site(tmp_path, env, _EIO_ON_CLOSE_AFTER_REPLACE))

        assert out.startswith(_NOTE_MARK)
        self._assert_recorded_once(out, home, lead, proj)
        assert "Session info failed" not in out
        assert len(_events(home, lead, "session_start")) == 1
        assert len(_events(home, lead, "session_resumption_surfaced")) == 1
        assert _prompt(_WRITER_FIRST, lead, home, env) == ""

    def test_a_lock_release_that_fails_after_the_write_records_on_the_same_prompt(
        self, tmp_path
    ):
        """The block is replaced before the lock is released, so a failure
        releasing it is no failed write: the lead is recorded, claim and all,
        on this prompt."""
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)

        out = _prompt(_WRITER_FIRST, lead, home,
                      _with_site(tmp_path, env, _EIO_ON_LOCK_RELEASE))

        assert out.startswith(_NOTE_MARK)
        self._assert_recorded_once(out, home, lead, proj)
        assert "lock" not in out.split(_VALUES_MARK, 1)[1].lower()
        assert len(_events(home, lead, "session_start")) == 1
        assert len(_events(home, lead, "session_resumption_surfaced")) == 1
        assert _prompt(_WRITER_FIRST, lead, home, env) == ""

    @pytest.mark.parametrize("journal_works", [True, False], ids=["journal", "no-journal"])
    def test_a_failure_that_never_clears_records_at_the_bound(self, tmp_path, journal_works):
        """EIO on every rename into CLAUDE.md: two prompts send only the note,
        and the third records the lead with the failure shown."""
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)
        if not journal_works:
            (_sdir(home, lead) / "session-journal.jsonl").mkdir(parents=True)
        before = _block(proj)
        failing = _with_site(tmp_path, env, _EIO_ON_REPLACE)

        first = _prompt(_WRITER_FIRST, lead, home, failing)
        second = _prompt(_WRITER_FIRST, lead, home, failing)
        third = _prompt(_WRITER_FIRST, lead, home, failing)

        for out in (first, second):
            assert out.startswith(_NOTE_MARK)
            assert _VALUES_MARK not in out and "PR #5151" not in out
        assert _VALUES_MARK in third and third.count("PR #5151") == 1
        assert "Session info failed: OSError (EIO)" in third
        assert _block(proj) == before
        if journal_works:
            assert len(_events(home, lead, "session_start")) == 1
        else:
            assert (_sdir(home, lead) / _FLAG).exists()
        assert _prompt(_WRITER_FIRST, lead, home, failing) == ""

    def test_a_count_that_cannot_be_kept_records_at_once(self, tmp_path):
        """The attempt count fails toward recording: when it cannot be read or
        written, the first transient failure records the lead."""
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)
        (_sdir(home, lead) / "lead-record-attempts").mkdir(parents=True)

        out = _prompt(_WRITER_FIRST, lead, home, _with_site(tmp_path, env, _EIO_ON_REPLACE))

        assert _VALUES_MARK in out and out.count("PR #5151") == 1
        assert "Session info failed: OSError (EIO)" in out
        assert len(_events(home, lead, "session_start")) == 1

    def test_a_filesystem_without_flock_closes_the_recording(self, tmp_path):
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)
        before = _block(proj)
        failing = _with_site(tmp_path, env, _ENOTSUP_ON_LOCK)

        out = _prompt(_WRITER_FIRST, lead, home, failing)

        assert _VALUES_MARK in out and out.count("PR #5151") == 1
        assert "Could not acquire lock on project CLAUDE.md" in out
        assert _prompt(_WRITER_FIRST, lead, home, failing) == ""
        assert len(_events(home, lead, "session_start")) == 1
        assert _block(proj) == before

    def test_a_directory_at_the_lock_file_closes_the_recording(self, tmp_path):
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)
        before = _block(proj)
        lock = proj / ".claude" / ".CLAUDE.md.lock"
        lock.unlink(missing_ok=True)
        lock.mkdir()

        out = _prompt(_WRITER_FIRST, lead, home, env)

        assert _VALUES_MARK in out and out.count("PR #5151") == 1
        assert "Could not acquire lock on project CLAUDE.md" in out
        assert _prompt(_WRITER_FIRST, lead, home, env) == ""
        assert len(_events(home, lead, "session_start")) == 1
        assert _block(proj) == before

    def test_a_directory_at_claude_md_closes_the_recording(self, tmp_path):
        """The gate's own read raises EISDIR before any rewrite: the path is
        unusable, so the lead is recorded at once with the failure shown."""
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)
        md = proj / ".claude" / "CLAUDE.md"
        md.unlink()
        md.mkdir()

        out = _prompt(_WRITER_FIRST, lead, home, env)

        assert _VALUES_MARK in out
        assert "Session info failed: IsADirectoryError (EISDIR)" in out
        assert _prompt(_WRITER_FIRST, lead, home, env) == ""
        assert len(_events(home, lead, "session_start")) == 1
        assert md.is_dir()

    @pytest.mark.skipif(_ROOT, reason="root ignores directory permissions")
    def test_a_directory_that_refuses_the_write_closes_the_recording(self, tmp_path):
        """EACCES from the write itself, with the lock file already there: the
        same refusal as a lock that cannot be created, so the same outcome."""
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)
        before = _block(proj)
        dot_claude = proj / ".claude"
        (dot_claude / ".CLAUDE.md.lock").touch()
        dot_claude.chmod(0o500)
        try:
            out = _prompt(_WRITER_FIRST, lead, home, env)
            again = _prompt(_WRITER_FIRST, lead, home, env)
        finally:
            dot_claude.chmod(0o700)

        assert _VALUES_MARK in out and out.count("PR #5151") == 1
        assert "Session info failed: PermissionError (EACCES)" in out
        assert again == ""
        assert len(_events(home, lead, "session_start")) == 1
        assert _block(proj) == before

    @pytest.mark.skipif(_ROOT, reason="root ignores directory permissions")
    def test_a_lock_that_cannot_be_created_closes_the_recording(self, tmp_path):
        """EACCES creating the lock file: the path itself is unusable, so the
        skip stands and the lead is recorded without the rewrite."""
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)
        before = _block(proj)
        dot_claude = proj / ".claude"
        (dot_claude / ".CLAUDE.md.lock").unlink(missing_ok=True)
        dot_claude.chmod(0o500)
        try:
            out = _prompt(_WRITER_FIRST, lead, home, env)
            again = _prompt(_WRITER_FIRST, lead, home, env)
        finally:
            dot_claude.chmod(0o700)

        assert _VALUES_MARK in out and out.count("PR #5151") == 1
        assert "Could not acquire lock on project CLAUDE.md" in out
        assert again == ""
        assert len(_events(home, lead, "session_start")) == 1
        assert _block(proj) == before

    def test_a_file_that_is_not_utf8_closes_the_recording(self, tmp_path):
        home, proj, env, parent, lead = _unrecorded_lead_with_secretary(tmp_path)
        md = proj / ".claude" / "CLAUDE.md"
        data = md.read_bytes() + b"caf\xe9\n"
        md.write_bytes(data)

        out = _prompt(_WRITER_FIRST, lead, home, env)

        assert _VALUES_MARK in out and "not valid UTF-8" in out
        assert _prompt(_WRITER_FIRST, lead, home, env) == ""
        assert len(_events(home, lead, "session_start")) == 1
        assert md.read_bytes() == data
