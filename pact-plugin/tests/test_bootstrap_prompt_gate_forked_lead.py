"""
Location: pact-plugin/tests/test_bootstrap_prompt_gate_forked_lead.py
Summary: The lead note bootstrap_prompt_gate prepends when a lead's session
         journal has no session_start event, driven here through a lead forked
         without `--agent`.
Used by: pytest.

A lead forked with `--resume <id> --fork-session` and no `--agent` starts with
no agent_type, so session_init gives it the no-role notice and writes no
session_start event. Its first prompt carries the lead agent_type. The gate
then tells it that the startup notice does not apply, keyed on the journal
having no session_start event. A lead whose session_init raised, had no session
id, or got input that did not parse also lacks the event; those routes are not
exercised here.

Every test drives the real hooks in a fresh interpreter under tmp_path, with
nothing stubbed on the heal path. HOME is the sandbox and every CLAUDE_* var
except the three set here is dropped, so no run reaches the real config dir.
"""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from shared.session_journal import make_event

_HOOKS = Path(__file__).resolve().parents[1] / "hooks"
_PLUGIN_ROOT = _HOOKS.parent
_NOTE = (
    "This session is the PACT team-lead. Any startup notice saying it has no "
    "recognized agent role, or that PACT cannot dispatch specialist agents "
    "in this session, does not apply.\n\n"
)
_NO_ROLE_MARK = "PACT cannot dispatch specialist agents in this session"
_SESSION_VALUES_MARK = "These replace any session values earlier in this conversation."
_SUPPRESS = {
    "suppressOutput": True,
    "hookSpecificOutput": {"hookEventName": "UserPromptSubmit"},
}


def _sandbox(tmp_path):
    """Return (home, env, session_id) for a fresh sandboxed session."""
    home = tmp_path / "home"
    (home / "proj").mkdir(parents=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update(
        HOME=str(home),
        CLAUDE_PROJECT_DIR=str(home / "proj"),
        CLAUDE_PLUGIN_ROOT=str(_PLUGIN_ROOT),
    )
    return home, env, str(uuid.uuid4())


def _session_dir(home, session_id):
    return home / ".claude" / "pact-sessions" / "proj" / session_id


def _run(hook, frame, home, env):
    proc = subprocess.run(
        [sys.executable, str(_HOOKS / hook)], input=json.dumps(frame),
        capture_output=True, text=True, env=env, cwd=str(home), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _prompt(session_id, agent_type):
    frame = {"hook_event_name": "UserPromptSubmit", "session_id": session_id,
             "prompt": "first prompt"}
    if agent_type is not None:
        frame["agent_type"] = agent_type
    return frame


def _gate_context(session_id, agent_type, home, env):
    out = _run("bootstrap_prompt_gate.py", _prompt(session_id, agent_type), home, env)
    return out["hookSpecificOutput"]["additionalContext"]


def _append_session_start(home, session_id):
    journal = _session_dir(home, session_id) / "session-journal.jsonl"
    event = make_event("session_start", team="session-test", session_id=session_id,
                       project_dir=str(home / "proj"), worktree="", source="startup")
    with journal.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


@pytest.mark.parametrize("lead", ["PACT:pact-orchestrator", "pact-orchestrator"])
def test_note_is_prepended_only_while_the_journal_has_no_session_start(tmp_path, lead):
    home, env, sid = _sandbox(tmp_path)
    context_file = _session_dir(home, sid) / "pact-session-context.json"
    assert not context_file.exists()

    without_anchor = _gate_context(sid, lead, home, env)
    assert without_anchor.startswith(_NOTE)
    assert context_file.exists(), "the heal must restore the missing context file"

    # Control: the same lead in the same session once SessionStart treated it
    # as the lead. The unrecorded prompt differs only by the note before the
    # instruction and the session values after it.
    _append_session_start(home, sid)
    with_anchor = _gate_context(sid, lead, home, env)
    assert with_anchor.startswith("REQUIRED: Before responding to this message")
    assert _NOTE.strip() not in with_anchor
    assert _SESSION_VALUES_MARK not in with_anchor
    assert without_anchor.startswith(_NOTE + with_anchor + "\n\n")
    values = without_anchor[len(_NOTE + with_anchor + "\n\n"):]
    assert values.startswith("Session placeholder variables")
    assert f"`session-{sid[:8]}`" in values
    assert values.endswith(_SESSION_VALUES_MARK)


def test_note_does_not_depend_on_which_hook_healed_the_context(tmp_path):
    """bootstrap_marker_writer heals the same file on the same prompt. When its
    write lands first, the gate's own heal finds the file present, and the note
    must still appear."""
    home, env, sid = _sandbox(tmp_path)
    context_file = _session_dir(home, sid) / "pact-session-context.json"

    _run("bootstrap_marker_writer.py", _prompt(sid, "PACT:pact-orchestrator"), home, env)
    assert context_file.exists(), "the marker writer must have healed first"

    assert _gate_context(sid, "PACT:pact-orchestrator", home, env).startswith(_NOTE)


@pytest.mark.parametrize("agent_type", ["pact-backend-coder", None])
def test_a_non_lead_frame_gets_no_note(tmp_path, agent_type):
    home, env, sid = _sandbox(tmp_path)

    out = _run("bootstrap_prompt_gate.py", _prompt(sid, agent_type), home, env)
    assert out == _SUPPRESS
    assert not _session_dir(home, sid).exists(), "a non-lead frame must not heal"

    # Control: a lead frame in the same sandbox does reach the note.
    assert _gate_context(sid, "PACT:pact-orchestrator", home, env).startswith(_NOTE)


@pytest.mark.parametrize("startup_agent_type, note_expected", [
    (None, True),
    ("PACT:pact-orchestrator", False),
])
def test_forked_lead_sequence_through_the_real_session_init(
    tmp_path, startup_agent_type, note_expected
):
    """SessionStart through the real session_init, then the lead's first prompt
    through the real gate. The no-role start is the forked lead; the lead start
    is its control."""
    home, env, sid = _sandbox(tmp_path)
    start = {"hook_event_name": "SessionStart", "session_id": sid, "source": "fork",
             "cwd": str(home / "proj")}
    if startup_agent_type is not None:
        start["agent_type"] = startup_agent_type

    started = _run("session_init.py", start, home, env)
    told_no_role = _NO_ROLE_MARK in started["hookSpecificOutput"]["additionalContext"]
    assert told_no_role is note_expected

    context = _gate_context(sid, "PACT:pact-orchestrator", home, env)
    assert context.startswith(_NOTE) is note_expected
    assert "PACT:bootstrap" in context
