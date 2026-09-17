"""L2 seam test: who receives the background-launch advisory, and who is recorded.

Location: pact-plugin/tests/test_launch_advisory_population.py
Summary: runs the real wait_filler_gate (PreToolUse) and track_files
         (PostToolUse) hooks as subprocesses against a real config root: team
         config, session context, session registry and task store. Pins that
         an Agent-tool subagent gets no advisory, and that its launch is
         recorded under its OWN id rather than against a teammate, while
         in-process and separate-process teammates keep both.
Used by: hook_infra_classifier's COVERED_L2 mapping for `wait_filler_gate`.

Nothing is monkeypatched. Every subprocess gets HOME and CLAUDE_CONFIG_DIR
pointed at the test's own root, so no arm can read the real ~/.claude.

REVERT-CARDINALITY NON-VACUITY GATE, MEASURED. Run against the hooks as they
were before the teammate predicate and the team resolver existed, this file
reports 9 failed, 3 passed. The three that pass are the guards that a teammate
keeps its advisory (in-process, separate-process through the registry, and
`name@team` in `agent_id`), which held before the change as well. The nine
that fail are the subagent, non-member and lead-session advisories, the
subagent record, the separate-process record, and the four direct-call arms,
which fail on import. If that revert ever reports 0 failed, this file has
stopped measuring the seam.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fixtures.role_frames import (
    captured_posttooluse_teammate_inprocess_bash_background,
    captured_pretooluse_lead_inprocess,
    captured_pretooluse_teammate_inprocess_subagent,
    captured_pretooluse_teammate_tmux,
)

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
GATE = HOOKS / "wait_filler_gate.py"
TRACKER = HOOKS / "track_files.py"
TEAM = "session-advisory"
PROJECT_DIR = "/advisory/project"
IN_PROCESS_MEMBER = "probe-coder"
SEPARATE_MEMBER = "tmux-coder"
# The captured in-process frames share the lead's session; the captured tmux
# teammate has its own.
LEAD_SESSION = captured_pretooluse_teammate_inprocess_subagent()["session_id"]
SEPARATE_SESSION = captured_pretooluse_teammate_tmux()["session_id"]


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _context_file(root: Path, session_id: str) -> Path:
    from shared.pact_context import project_slug

    return (
        root / ".claude" / "pact-sessions" / project_slug(PROJECT_DIR)
        / session_id / "pact-session-context.json"
    )


@pytest.fixture
def seam(tmp_path):
    """A real config root. Only the lead's session has a context file, as in
    production: in-process frames share it, a separate-process teammate has none."""
    assert LEAD_SESSION != SEPARATE_SESSION
    config = tmp_path / ".claude"
    _write(config / "teams" / TEAM / "config.json", {
        "leadSessionId": LEAD_SESSION,
        "members": [
            {"name": IN_PROCESS_MEMBER, "agentId": f"{IN_PROCESS_MEMBER}@{TEAM}",
             "agentType": "pact-backend-coder", "backendType": "in-process"},
            {"name": SEPARATE_MEMBER, "agentId": f"{SEPARATE_MEMBER}@{TEAM}",
             "agentType": "pact-test-engineer"},
            # No backendType: these arms pin identity routing, and an absent
            # signal keeps the advisory. The tmux split is pinned in
            # test_teammate_process_mode.py.
        ],
    })
    _write(_context_file(tmp_path, LEAD_SESSION),
           {"session_id": LEAD_SESSION, "project_dir": PROJECT_DIR, "team_name": TEAM})
    _write(config / "tasks" / TEAM / "13.json",
           {"id": "13", "status": "in_progress", "owner": IN_PROCESS_MEMBER})
    _write(config / "tasks" / TEAM / "21.json",
           {"id": "21", "status": "in_progress", "owner": SEPARATE_MEMBER})
    return tmp_path


@pytest.fixture
def in_this_process(seam, monkeypatch):
    """Point this process at the seam, for arms that call the resolver directly."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(seam / ".claude"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", PROJECT_DIR)
    return seam


def _register(root: Path, session_id: str, member: str) -> None:
    """Append one session-registry line, in the shape `session_registry.register` writes."""
    path = root / ".claude" / "pact-sessions" / ".teammate-registry.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"session_id": session_id, "value": f"{member}@{TEAM}"}) + "\n")


def _env(root: Path) -> dict:
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR", "CLAUDE_CODE_SESSION_ID")
    }
    env.update(HOME=str(root), CLAUDE_CONFIG_DIR=str(root / ".claude"),
               CLAUDE_PROJECT_DIR=PROJECT_DIR)
    return env


def _launch(frame: dict, event: str = "PreToolUse") -> dict:
    frame = {k: v for k, v in frame.items() if k != "_meta"}
    frame.update(hook_event_name=event, tool_name="Bash",
                 tool_input={"command": "echo hi", "run_in_background": True})
    return frame


def _subagent() -> dict:
    """The captured Agent-tool subagent: `general-purpose`, a hex `agent_id`, the lead's session."""
    return captured_pretooluse_teammate_inprocess_subagent()


def _in_process_teammate() -> dict:
    """The captured in-process teammate: its own name in `agent_type`, a hex `agent_id`."""
    frame = captured_posttooluse_teammate_inprocess_bash_background()
    frame.update(agent_type=IN_PROCESS_MEMBER, session_id=LEAD_SESSION)
    return frame


def _advisory(root: Path, frame: dict) -> bool:
    proc = subprocess.run(
        [sys.executable, str(GATE)], input=json.dumps(frame),
        capture_output=True, text=True, timeout=30, env=_env(root),
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout or "{}")
    return bool(out.get("hookSpecificOutput", {}).get("additionalContext"))


def _records_after(root: Path, frame: dict) -> list:
    proc = subprocess.run(
        [sys.executable, str(TRACKER)], input=json.dumps(frame),
        capture_output=True, text=True, timeout=30, env=_env(root),
    )
    assert proc.returncode == 0, proc.stderr
    registry = root / ".claude" / "teams" / TEAM / "background_work.json"
    if not registry.exists():
        return []
    return json.loads(registry.read_text(encoding="utf-8")).get("records", [])


# ------------------------------------------------------------------ advisory


def test_an_in_process_subagent_launch_gets_no_advisory(seam):
    assert _advisory(seam, _launch(_subagent())) is False, (
        "an Agent-tool subagent drew the teammate launch advisory"
    )


def test_an_in_process_teammate_launch_still_gets_the_advisory(seam):
    assert _advisory(seam, _launch(_in_process_teammate())) is True


def test_a_tmux_teammate_launch_still_gets_the_advisory(seam):
    _register(seam, SEPARATE_SESSION, SEPARATE_MEMBER)
    assert not _context_file(seam, SEPARATE_SESSION).exists()
    assert _advisory(seam, _launch(captured_pretooluse_teammate_tmux())) is True


def test_a_frame_whose_agent_id_is_name_at_this_team_gets_the_advisory(seam):
    """The inferred separate-process shape: launched with `--agent-id name@team`."""
    frame = captured_pretooluse_teammate_tmux()
    frame["agent_id"] = f"{SEPARATE_MEMBER}@{TEAM}"
    assert _advisory(seam, _launch(frame)) is True


def test_a_hex_agent_id_that_is_not_a_member_name_gets_no_advisory(seam):
    """A subagent spawned under a non-platform type name, with the live hex id shape."""
    frame = _subagent()
    frame["agent_type"] = "helper-agent"
    assert _advisory(seam, _launch(frame)) is False


def test_a_frame_in_the_lead_session_with_no_agent_id_gets_no_advisory(seam):
    """The lead under an `agent_type` that is not a lead spelling, with a registry
    entry for its session. The session is the lead's, so the frame is not a
    teammate whatever the registry says."""
    _register(seam, LEAD_SESSION, IN_PROCESS_MEMBER)
    frame = captured_pretooluse_lead_inprocess()
    frame.update(agent_type="custom-orchestrator", session_id=LEAD_SESSION)
    assert "agent_id" not in frame
    assert _advisory(seam, _launch(frame)) is False


# ------------------------------------------------------------------ Layer 1


def test_a_subagent_launch_is_recorded_under_its_own_id_not_a_teammates(seam):
    """The registry holds an entry for the lead's session, as a registration leaves
    when the lead's session id could not be read at register time. The HAZARD is
    that a subagent reaches THAT entry and is recorded against the teammate it
    names — a launch attributed to a teammate who did not make it.

    THAT HAZARD IS STILL WHAT THIS ARM GUARDS, and it is still closed. What
    changed is the blanket refusal around it: a shell launched inside a subagent
    IS recorded now, because unrecorded it sits in the LEAD's job list with no
    owner and refuses the lead its own turn end over work it did not start. It
    is recorded under the subagent's OWN `agent_id` and carries no task ids, so
    it never reaches a teammate's task surfaces.
    """
    _register(seam, LEAD_SESSION, IN_PROCESS_MEMBER)
    records = _records_after(seam, _launch(_subagent(), "PostToolUse"))
    assert len(records) == 1, "the subagent's launch was not recorded at all"
    record = records[0]
    assert record["agent_name"] != IN_PROCESS_MEMBER, (
        "an Agent-tool subagent's launch was recorded against a teammate"
    )
    assert record["agent_name"] == _subagent()["agent_id"]
    assert record["owner_role"] == "subagent"
    assert record["task_ids"] == []


def test_a_separate_process_teammate_launch_is_recorded_with_no_context_file(seam):
    _register(seam, SEPARATE_SESSION, SEPARATE_MEMBER)
    assert not _context_file(seam, SEPARATE_SESSION).exists()
    records = _records_after(seam, _launch(captured_pretooluse_teammate_tmux(), "PostToolUse"))
    assert [(r["agent_name"], r["task_ids"]) for r in records] == [(SEPARATE_MEMBER, ["21"])], (
        "a separate-process teammate's launch was not recorded: its team was not "
        "resolved without a session context"
    )


# ------------------------------------------------------------------ resolver


def test_a_separate_process_teammate_resolves_its_team_with_no_context_file(in_this_process):
    from shared.background_work import frame_team_and_name

    _register(in_this_process, SEPARATE_SESSION, SEPARATE_MEMBER)
    assert not _context_file(in_this_process, SEPARATE_SESSION).exists()
    assert frame_team_and_name(captured_pretooluse_teammate_tmux()) == (TEAM, SEPARATE_MEMBER)


def test_an_agent_id_route_resolves_only_a_member(in_this_process):
    from shared.background_work import frame_team_and_name

    frame = captured_pretooluse_teammate_tmux()
    frame["agent_id"] = f"{SEPARATE_MEMBER}@{TEAM}"
    assert frame_team_and_name(frame) == (TEAM, SEPARATE_MEMBER)
    frame["agent_id"] = f"stranger@{TEAM}"
    assert frame_team_and_name(frame) == ("", "")


def test_the_lead_session_resolves_its_team_from_its_context(in_this_process):
    from shared.background_work import frame_team_and_name

    frame = captured_pretooluse_lead_inprocess()
    frame["session_id"] = LEAD_SESSION
    assert frame_team_and_name(frame) == (TEAM, "")


def test_a_registry_entry_for_another_team_does_not_make_a_teammate(in_this_process):
    """Step 4 checks the registry entry's team against the team being asked about,
    so a caller that brings its own team never gets a teammate from another
    team's registration. The same frame asked about the registry's own team is
    the positive control."""
    from shared.background_work import is_teammate_launch_frame

    _write(in_this_process / ".claude" / "teams" / "other-team" / "config.json",
           {"leadSessionId": "other-lead-session", "members": []})
    _register(in_this_process, SEPARATE_SESSION, SEPARATE_MEMBER)
    frame = _launch(captured_pretooluse_teammate_tmux())
    assert "agent_id" not in frame
    assert is_teammate_launch_frame(frame, TEAM) is True
    assert is_teammate_launch_frame(frame, "other-team") is False, (
        "a registry entry for one team made the frame a teammate of another team"
    )
