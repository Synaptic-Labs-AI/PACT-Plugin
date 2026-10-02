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
        md = proj / ".claude" / "CLAUDE.md"
        saved = md.read_text(encoding="utf-8")
        md.unlink()
        md.mkdir()  # reading it raises: the recording fails before any write

        failed = _prompt(_WRITER_FIRST, lead, home, env)

        assert failed.startswith(_NOTE_MARK) and _VALUES_MARK not in failed
        assert _events(home, lead, "session_start") == []
        assert not (_sdir(home, lead) / _FLAG).exists(), (
            "a failure before the rewrite leaves no flag, so the next prompt retries")
        md.rmdir()
        md.write_text(saved, encoding="utf-8")

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
