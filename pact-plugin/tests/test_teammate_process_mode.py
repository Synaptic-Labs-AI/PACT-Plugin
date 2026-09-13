"""Pins for skipping the stall layers for a separate-process (tmux) teammate.

Location: pact-plugin/tests/test_teammate_process_mode.py
Summary: a separate-process teammate is woken by its own background completion,
         so the launch advisory, Layer 2's idle-count advisory and Layer 3's
         unflagged lead surface skip it. The skip keys on the member's
         team-config `backendType` being exactly "tmux"; every other value, and
         every missing or unreadable signal, keeps the in-process behaviour.
Used by: the suite. Path setup is conftest-owned.

An arm marked REVERT PROOF fails against the hooks before the split existed. An
arm marked GUARD passes there too; it pins the fail direction or a property the
split must keep.
"""

from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from fixtures.role_frames import (
    captured_lead_userpromptsubmit_qualified,
    captured_posttooluse_teammate_inprocess_bash_background,
    captured_pretooluse_lead_inprocess,
    captured_pretooluse_teammate_inprocess_subagent,
    captured_pretooluse_teammate_tmux,
)

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
GATE = HOOKS / "wait_filler_gate.py"
TEAM = "session-procmode"
PROJECT_DIR = "/procmode/project"
MEMBER = "mode-coder"
LEAD_SESSION = captured_pretooluse_lead_inprocess()["session_id"]
SEPARATE_SESSION = captured_pretooluse_teammate_tmux()["session_id"]
NOW = datetime(2031, 1, 1, tzinfo=timezone.utc)
ABSENT = object()  # the member carries no backendType key


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(text, encoding="utf-8")


def _team_config(config: Path, backend_type=ABSENT, lead_session=LEAD_SESSION) -> None:
    member = {"name": MEMBER, "agentId": f"{MEMBER}@{TEAM}", "agentType": "pact-backend-coder"}
    if backend_type is not ABSENT:
        member["backendType"] = backend_type
    _write(config / "teams" / TEAM / "config.json",
           {"leadSessionId": lead_session, "members": [member]})


def _register(config: Path, session_id: str) -> None:
    """Append one session-registry line, in the shape `session_registry.register` writes."""
    path = config / "pact-sessions" / ".teammate-registry.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"session_id": session_id, "value": f"{MEMBER}@{TEAM}"}) + "\n")


@pytest.fixture
def config(tmp_path, monkeypatch):
    root = tmp_path / ".claude"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", PROJECT_DIR)
    return root


# ------------------------------------------------------------------ the signal


@pytest.mark.parametrize(
    "backend_type, expected",
    [("tmux", True), ("in-process", False), ("iterm2", False), ("TMUX", False),
     ("", False), (None, False), (ABSENT, False)],
    ids=["tmux", "in-process", "iterm2", "TMUX", "empty", "null", "absent"],
)
def test_separate_process_is_backend_type_tmux_only(config, backend_type, expected):
    """REVERT PROOF for the tmux case: the helper does not exist before the split."""
    from shared.background_work import teammate_is_separate_process

    _team_config(config, backend_type)
    assert teammate_is_separate_process(TEAM, MEMBER) is expected


def test_a_missing_member_or_an_unreadable_config_is_not_separate_process(config):
    from shared.background_work import teammate_is_separate_process

    _team_config(config, "tmux")
    assert teammate_is_separate_process(TEAM, MEMBER) is True, "control: a tmux member"
    assert teammate_is_separate_process(TEAM, "someone-else") is False
    assert teammate_is_separate_process("no-such-team", MEMBER) is False
    _write(config / "teams" / TEAM / "config.json", "{not json")
    assert teammate_is_separate_process(TEAM, MEMBER) is False


def test_teammate_launch_name_resolves_both_paths(config):
    from shared.background_work import teammate_launch_name

    _team_config(config, "tmux")
    in_process = captured_posttooluse_teammate_inprocess_bash_background()
    in_process.update(agent_type=MEMBER, session_id=LEAD_SESSION)
    assert teammate_launch_name(in_process, TEAM) == MEMBER, "step 2: agent_type names a member"
    _register(config, SEPARATE_SESSION)
    tmux = captured_pretooluse_teammate_tmux()
    assert teammate_launch_name(tmux, TEAM) == MEMBER, "step 4: the session registry"
    tmux["agent_id"] = f"{MEMBER}@{TEAM}"
    assert teammate_launch_name(tmux, TEAM) == MEMBER, "step 3: name@team"
    assert teammate_launch_name(captured_pretooluse_lead_inprocess(), TEAM) == ""
    assert teammate_launch_name(captured_pretooluse_teammate_inprocess_subagent(), TEAM) == ""
    assert teammate_launch_name({"session_id": SEPARATE_SESSION, "tool_name": "Bash"}, TEAM) == ""


# ------------------------------------------------------------------ launch advisory


def _advisory(tmp_path: Path, frame: dict) -> bool:
    """Run the real gate as a subprocess on a background launch; True if it advised."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR", "CLAUDE_CODE_SESSION_ID")}
    env.update(HOME=str(tmp_path), CLAUDE_CONFIG_DIR=str(tmp_path / ".claude"),
               CLAUDE_PROJECT_DIR=PROJECT_DIR)
    frame = {k: v for k, v in frame.items() if k != "_meta"}
    frame.update(hook_event_name="PreToolUse", tool_name="Bash",
                 tool_input={"command": "echo hi", "run_in_background": True})
    proc = subprocess.run([sys.executable, str(GATE)], input=json.dumps(frame),
                          capture_output=True, text=True, timeout=30, env=env)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout or "{}")
    return bool(out.get("hookSpecificOutput", {}).get("additionalContext"))


def test_a_tmux_teammate_launch_gets_no_pact_advisory(config, tmp_path):
    """REVERT PROOF. The captured separate-process PreToolUse shape: no agent_id, a
    real type, its own session with no context file, resolved through the registry."""
    _team_config(config, "tmux")
    _register(config, SEPARATE_SESSION)
    assert _advisory(tmp_path, captured_pretooluse_teammate_tmux()) is False


@pytest.mark.parametrize("backend_type", ["in-process", "iterm2", "TMUX", ABSENT],
                         ids=["in-process", "iterm2", "TMUX", "absent"])
def test_a_teammate_that_is_not_tmux_still_gets_the_advisory(config, tmp_path, backend_type):
    """GUARD, the fail direction. Same frame and registry entry as the arm above;
    only the signal differs, so the advisory is attributable to it."""
    _team_config(config, backend_type)
    _register(config, SEPARATE_SESSION)
    assert _advisory(tmp_path, captured_pretooluse_teammate_tmux()) is True


# ------------------------------------------------------------------ Layer 2


class TestLayer2SkipsATmuxTeammate:
    SESSION_ID = "procmode-idle-session"
    TASK_ID = "13"

    @pytest.fixture
    def store(self, config):
        from shared import background_work as bw
        from shared.pact_context import project_slug

        _write(config / "pact-sessions" / project_slug(PROJECT_DIR) / self.SESSION_ID
               / "pact-session-context.json",
               {"session_id": self.SESSION_ID, "project_dir": PROJECT_DIR, "team_name": TEAM})

        def seed(backend_type, covering_wait=False):
            _team_config(config, backend_type, lead_session=self.SESSION_ID)
            registered_at = bw.iso_now()
            task = {"id": self.TASK_ID, "status": "in_progress", "owner": MEMBER,
                    "subject": "CODE: mode arm"}
            if covering_wait:
                task["metadata"] = {"intentional_wait": {
                    "reason": "awaiting_blocker_resolution", "expected_resolver": "lead",
                    "since": registered_at, "covers_since": registered_at,
                }}
            _write(config / "tasks" / TEAM / f"{self.TASK_ID}.json", task)
            assert bw.save_records([{
                "agent_name": MEMBER, "session_id": self.SESSION_ID,
                "task_ids": [self.TASK_ID], "registered_at": registered_at,
            }], team_name=TEAM) is True

        return seed

    def _idle_once(self, capsys) -> bool:
        """One TeammateIdle tick through `main()`. True if the unflagged advisory fired."""
        import teammate_idle

        frame = {"hook_event_name": "TeammateIdle", "session_id": self.SESSION_ID,
                 "teammate_name": MEMBER}
        capsys.readouterr()
        with patch("sys.stdin", io.StringIO(json.dumps(frame))):
            with pytest.raises(SystemExit) as exc:
                teammate_idle.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out.strip()
        payload = json.loads(out) if out else {}
        return teammate_idle.UNFLAGGED_ADVISORY in payload.get("systemMessage", "")

    def test_a_tmux_teammate_never_reaches_the_unflagged_advisory(self, store, capsys):
        """REVERT PROOF. Three unflagged idles: no advisory, and no count kept."""
        from shared.background_work import load_unflagged_idle_counts

        store("tmux")
        assert [self._idle_once(capsys) for _ in range(3)] == [False, False, False]
        assert MEMBER not in load_unflagged_idle_counts(TEAM)

    @pytest.mark.parametrize("backend_type", ["in-process", "iterm2", ABSENT],
                             ids=["in-process", "iterm2", "absent"])
    def test_a_teammate_that_is_not_tmux_still_gets_it_on_the_third_idle(
        self, store, capsys, backend_type
    ):
        """GUARD, the fail direction."""
        store(backend_type)
        assert [self._idle_once(capsys) for _ in range(3)] == [False, False, True]

    def test_the_discharge_pass_still_runs_for_a_tmux_teammate(self, store, capsys, config):
        """GUARD. The skip sits after the discharge, so a covering wait still
        retires the record."""
        store("tmux", covering_wait=True)
        self._idle_once(capsys)
        registry = config / "teams" / TEAM / "background_work.json"
        assert json.loads(registry.read_text(encoding="utf-8"))["records"] == []


# ------------------------------------------------------------------ Layer 3


@pytest.fixture
def lead(tmp_path, monkeypatch):
    """Run `run_surface` as the lead at NOW, with journal writes captured."""
    import missed_wake_scan as mw
    from shared import pact_context

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    events: list = []
    monkeypatch.setattr(pact_context, "get_team_name", lambda: TEAM)
    monkeypatch.setattr(mw, "read_events", lambda event_type: [])
    monkeypatch.setattr(mw, "append_event", lambda event: events.append(event) or True)
    monkeypatch.setattr(mw, "get_journal_path", lambda: str(tmp_path / "journal.jsonl"))

    def run(members: list) -> "tuple[str, list]":
        _write(tmp_path / "teams" / TEAM / "config.json",
               {"leadSessionId": LEAD_SESSION, "members": members})
        registered = (NOW - timedelta(minutes=40)).isoformat()
        names = [m["name"] for m in members]
        _write(tmp_path / "teams" / TEAM / "background_work.json", {"records": [
            {"agent_name": name, "session_id": "s", "task_ids": [str(i)],
             "registered_at": registered, "command": "./gate.sh &"}
            for i, name in enumerate(names, 1)
        ]})
        tasks = [{"id": str(i), "owner": name, "subject": "s", "status": "in_progress"}
                 for i, name in enumerate(names, 1)]
        monkeypatch.setattr(mw, "get_task_list", lambda: tasks)
        surface = mw.run_surface(captured_lead_userpromptsubmit_qualified(), now=NOW) or ""
        return surface, events

    return run


@pytest.mark.parametrize("other_backend", ["in-process", ABSENT], ids=["in-process", "absent"])
def test_the_lead_surface_omits_a_tmux_teammates_record(lead, other_backend):
    """REVERT PROOF. Two stale unflagged records: only the non-tmux teammate is
    listed, and only it reaches the forensic event."""
    other = {"name": "inproc-coder", "agentType": "pact-backend-coder"}
    if other_backend is not ABSENT:
        other["backendType"] = other_backend
    tmux = {"name": "tmux-coder", "agentType": "pact-backend-coder", "backendType": "tmux"}
    surface, events = lead([tmux, other])
    assert "UNFLAGGED BACKGROUND WORK" in surface
    assert "inproc-coder" in surface
    assert "tmux-coder" not in surface
    import missed_wake_scan as mw

    forensic = [e for e in events if mw._UNFLAGGED_EVENT in json.dumps(e)]
    assert len(forensic) == 1, events
    assert "tmux-coder" not in json.dumps(forensic)


def test_the_lead_surface_says_listed_teammates_are_not_woken_by_their_job():
    """REVERT PROOF. True only while tmux teammates are filtered out above."""
    import missed_wake_scan as mw

    surface = mw.build_unflagged_surface(
        [{"agent_name": "inproc-coder", "task_ids": ["2"]}]
    )
    assert "These teammates are not woken by their own job finishing." in surface


# ------------------------------------------------------------------ structure


LAYER_HOOKS = ("wait_filler_gate.py", "teammate_idle.py", "missed_wake_scan.py")


def test_role_split_layers_use_the_one_backend_helper():
    """REVERT PROOF. Each stall layer calls the helper, and nothing under hooks/
    except the helper's module reads backendType."""
    for name in LAYER_HOOKS:
        tree = ast.parse((HOOKS / name).read_text(encoding="utf-8"))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", None))
            == "teammate_is_separate_process"
        ]
        assert calls, f"{name} does not call teammate_is_separate_process"
    readers = sorted(
        path.relative_to(HOOKS).as_posix() for path in HOOKS.rglob("*.py")
        if "__pycache__" not in path.parts and "backendType" in path.read_text(encoding="utf-8")
    )
    assert readers == ["shared/background_work.py"], readers
