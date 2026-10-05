"""
Location: pact-plugin/tests/test_claude_md_drift.py

Pin growth that the pin-cap gate does not see is reported, never refused:
`shared.claude_md_drift`, driven through the real hooks that host it.
`track_files.py` compares the project CLAUDE.md with the session's last-seen
record after every Bash call (PostToolUse and PostToolUseFailure) and records
it after an Edit or Write; `missed_wake_scan.py` compares it with the
project's baseline at each prompt and session start, in lead frames only.

Every row runs the hooks as real subprocesses against a tmp_path config root
and a tmp_path git repository; nothing touches the user's own files.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from clock_shift.clock_shift_env import carry_clock_shift

from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
)

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
TRACK = HOOKS / "track_files.py"
MISSED = HOOKS / "missed_wake_scan.py"
SID = "drift-session"
TEAM = "drift-team"
LEAD = "PACT:pact-orchestrator"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


# ---------------------------------------------------------------------------
# A world: a config root, a git repository holding the project CLAUDE.md, and
# the lead's session context
# ---------------------------------------------------------------------------

def _pins(count, *, first=0, body="secret body"):
    return "".join(
        f"<!-- pinned: 2026-04-21 -->\n### Pin {i}\n{body} {i}\n\n"
        for i in range(first, first + count)
    )


def _doc(pins, *, markers=True, working="## Working Memory\n\n### 2026-01-02\nentry\n"):
    inner = f"## Retrieved Context\n\n## Pinned Context\n\n{pins}{working}"
    if markers:
        inner = f"{MEMORY_START_MARKER}\n{inner}{MEMORY_END_MARKER}\n"
    return (
        "# Notes\n\n"
        f"{MANAGED_START_MARKER}\n# PACT Framework and Managed Project Memory\n\n"
        f"{inner}{MANAGED_END_MARKER}\n"
    )


@pytest.fixture
def world(tmp_path):
    from shared.pact_context import project_slug

    cfg = tmp_path / "cfg"
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(cfg),
        "CLAUDE_CONFIG_DIR": str(cfg),
        "CLAUDE_PROJECT_DIR": str(repo),
        **GIT_ENV,
    }
    w = SimpleNamespace(cfg=cfg, repo=repo, env=env, claude_md=repo / "CLAUDE.md")
    _sh(w, "git init -q -b main")
    w.claude_md.write_text(_doc(_pins(12)), encoding="utf-8")
    _sh(w, "git add CLAUDE.md && git commit -q -m base")
    w.project_dir = cfg / "pact-sessions" / project_slug(str(repo))
    w.session_dir = w.project_dir / SID
    w.session_dir.mkdir(parents=True)
    (w.session_dir / "pact-session-context.json").write_text(
        json.dumps({"session_id": SID, "project_dir": str(repo), "team_name": TEAM}),
        encoding="utf-8")
    # The lead's team, so an in-process teammate's frame resolves membership.
    team_config = cfg / "teams" / TEAM / "config.json"
    team_config.parent.mkdir(parents=True)
    team_config.write_text(json.dumps({"leadSessionId": SID, "members": [
        {"name": "probe-coder", "agentId": f"probe-coder@{TEAM}",
         "agentType": "pact-backend-coder", "backendType": "in-process"},
        {"name": "tmux-coder", "agentId": f"tmux-coder@{TEAM}",
         "agentType": "pact-backend-coder", "backendType": "tmux"}]}), encoding="utf-8")
    # A separate-process teammate's own session, registered as it does at spawn.
    (cfg / "pact-sessions" / ".teammate-registry.jsonl").write_text(
        json.dumps({"session_id": "teammate-session", "value": f"tmux-coder@{TEAM}"}) + "\n",
        encoding="utf-8")
    return w


def _sh(w, command):
    result = subprocess.run(["bash", "-c", command], cwd=w.repo, env=w.env,
                            capture_output=True, text=True, timeout=60)
    return result.returncode


def _hook(w, script, frame, env=None):
    """Run a hook on `frame`; a key set to None is left out of the frame."""
    frame = {key: value for key, value in frame.items() if value is not None}
    result = subprocess.run([sys.executable, str(script)], input=json.dumps(frame),
                            capture_output=True, text=True, timeout=60,
                            env=carry_clock_shift(dict(env or w.env)), cwd=w.repo)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else {}
    return out


def _context(out):
    return (out.get("hookSpecificOutput") or {}).get("additionalContext")


def _bash(w, command, *, event="PostToolUse", run=True, **frame):
    """Run `command` in the repository (unless `run` is False), then fire
    track_files.py with the Bash frame for it; return the hook's output."""
    rc = _sh(w, command) if run else 0
    out = _hook(w, TRACK, {"hook_event_name": event, "tool_name": "Bash",
                           "tool_input": {"command": command}, "session_id": SID,
                           "agent_type": LEAD, **frame})
    out["_rc"] = rc
    return out


def _prompt(w, **frame):
    base = {"hook_event_name": "UserPromptSubmit", "session_id": SID,
            "agent_type": LEAD, "prompt": "hello"}
    base.update(frame)
    return _context(_hook(w, MISSED, base))


def _edit(w, text, **frame):
    """An in-tool edit: write the file, then fire track_files.py's Edit leg."""
    w.claude_md.write_text(text, encoding="utf-8")
    return _hook(w, TRACK, {"hook_event_name": "PostToolUse", "tool_name": "Edit",
                            "tool_input": {"file_path": str(w.claude_md),
                                           "old_string": "x", "new_string": "y"},
                            "session_id": SID, "agent_type": LEAD, **frame})


def _edit_other(w, path):
    """An in-tool edit of a file that is not the project CLAUDE.md."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("unrelated\n", encoding="utf-8")
    return _hook(w, TRACK, {"hook_event_name": "PostToolUse", "tool_name": "Write",
                            "tool_input": {"file_path": str(path), "content": "unrelated\n"},
                            "session_id": SID, "agent_type": LEAD})

def _append_pin_script(index):
    return (f"python3 - <<'EOF'\nimport pathlib\np = pathlib.Path('CLAUDE.md')\n"
            f"s = p.read_text()\nmark = '## Working Memory'\n"
            f"s = s.replace(mark, '<!-- pinned: 2026-04-21 -->\\n### Pin {index}\\nsecret body {index}\\n\\n' + mark, 1)\n"
            f"p.write_text(s)\nEOF")


# Pushes pin 4's body past the per-pin size cap; the pin count is unchanged.
_GROW_PIN_4 = ("python3 - <<'EOF'\nimport pathlib\np = pathlib.Path('CLAUDE.md')\n"
               "p.write_text(p.read_text().replace('secret body 4', 'secret body 4 ' + 'w' * 1600, 1))\nEOF")


_PRUNE_STEP = "If the growth was not intended, run /PACT:prune-memory to demote pins."
_MEMBER_STEP = "Do not change CLAUDE.md yourself, by any route; tell the team-lead."


def _count_report(w, step):
    return (f"The project CLAUDE.md ({w.claude_md}) has grown past the pin count cap since "
            "the last check: 12 pins then, 13 now. The file was left as it is; nothing was "
            f"refused or reverted. {step}")


def _no_decision(out):
    spec = out.get("hookSpecificOutput") or {}
    assert "permissionDecision" not in spec and "decision" not in out


# ---------------------------------------------------------------------------
# Pin growth through Bash is reported, never refused
# ---------------------------------------------------------------------------

class TestBashGrowthIsReported:

    def _seeded(self, w, count=12):
        if count != 12:
            w.claude_md.write_text(_doc(_pins(count)), encoding="utf-8")
            _sh(w, "git add CLAUDE.md && git commit -q -m pins")
        out = _bash(w, "true")
        assert _context(out) is None  # no record yet: record one, report nothing
        return out

    def _unchanged_by_hook(self, w, command, **frame):
        """Run the command, snapshot the file and git state, fire the hook,
        and require the hook left both exactly as they were."""
        rc = _sh(w, command)
        before = (w.claude_md.read_bytes(), w.claude_md.stat().st_mtime_ns,
                  subprocess.run(["git", "status", "--porcelain"], cwd=w.repo,
                                 capture_output=True, text=True).stdout)
        out = _bash(w, command, run=False, **frame)
        after = (w.claude_md.read_bytes(), w.claude_md.stat().st_mtime_ns,
                 subprocess.run(["git", "status", "--porcelain"], cwd=w.repo,
                                capture_output=True, text=True).stdout)
        assert before == after
        _no_decision(out)
        out["_rc"] = rc
        return out

    def test_a_sed_typo_fix_at_13_pins_reports_nothing(self, world):
        self._seeded(world, 13)
        out = self._unchanged_by_hook(
            world, "sed -i.bak 's/secret body 3/secret bodies 3/' CLAUDE.md && rm CLAUDE.md.bak")
        assert out["_rc"] == 0 and _context(out) is None
        assert "secret bodies 3" in world.claude_md.read_text()

    def test_a_python_heredoc_adding_a_pin_at_the_cap_is_reported(self, world):
        self._seeded(world)
        out = self._unchanged_by_hook(world, _append_pin_script(12))
        report = _context(out)
        assert report is not None
        assert "count cap" in report and "since the last check" in report
        assert report == _count_report(world, _PRUNE_STEP)  # the lead keeps the pin command
        assert "secret" not in report and "Pin 12" not in report
        assert "command" not in report.lower()

    def test_a_git_checkout_bringing_one_more_pin_is_reported(self, world):
        world.claude_md.write_text(_doc(_pins(13)), encoding="utf-8")
        _sh(world, "git commit -qam thirteen && git tag thirteen && git reset -q --hard HEAD~1")
        self._seeded(world)
        out = self._unchanged_by_hook(world, "git checkout thirteen -- CLAUDE.md && git add CLAUDE.md")
        assert out["_rc"] == 0 and "count cap" in (_context(out) or "")

    def test_a_fast_forward_merge_adding_a_pin_is_reported(self, world):
        _sh(world, "git checkout -q -b more")
        world.claude_md.write_text(_doc(_pins(13)), encoding="utf-8")
        _sh(world, "git commit -qam more && git checkout -q main")
        self._seeded(world)
        out = self._unchanged_by_hook(world, "git merge -q --ff-only more")
        assert out["_rc"] == 0 and "count cap" in (_context(out) or "")

    def test_a_failed_call_that_adds_a_pin_is_reported_through_the_failure_event(self, world):
        self._seeded(world)
        (world.repo / "add_pin.py").write_text(
            "import pathlib\np = pathlib.Path('CLAUDE.md')\ns = p.read_text()\n"
            "mark = '## Working Memory'\n"
            "p.write_text(s.replace(mark, '### Pin 12\\nsecret body\\n\\n' + mark, 1))\n")
        out = self._unchanged_by_hook(world, "python3 add_pin.py && false",
                                      hook_event_name="PostToolUseFailure")
        assert out["_rc"] != 0
        assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUseFailure"
        assert "count cap" in out["hookSpecificOutput"]["additionalContext"]

    def test_a_pact_specialist_with_no_team_keeps_the_pin_command(self, world):
        """A solo --agent session of a PACT specialist type: checked by the gate,
        in no team, so it can run the pin commands itself."""
        frame: dict = {"agent_type": "pact-backend-coder", "session_id": "solo-session"}
        _bash(world, "true", **frame)
        assert _context(_bash(world, _append_pin_script(12), **frame)) == _count_report(
            world, _PRUNE_STEP)

    def test_size_only_growth_is_reported_as_a_size_denial_with_equal_counts(self, world):
        self._seeded(world)
        out = _bash(world, _GROW_PIN_4)
        report = _context(out)
        assert report is not None and "size cap" in report and "count" not in report
        assert "12 pins then, 12 now" in report

    def test_count_and_size_growth_name_both_caps(self, world):
        self._seeded(world)
        _sh(world, _append_pin_script(12))
        report = _context(_bash(world, _GROW_PIN_4))
        assert report is not None and "count and size caps" in report
        assert "12 pins then, 13 now" in report


# ---------------------------------------------------------------------------
# The last-seen record
# ---------------------------------------------------------------------------

class TestLastSeenRecord:

    def test_moving_the_file_to_dot_claude_with_the_same_pins_reports_nothing(self, world):
        _bash(world, "true")
        out = _bash(world, "mkdir -p .claude && git mv CLAUDE.md .claude/CLAUDE.md")
        assert _context(out) is None

    def test_a_draft_copied_over_the_resolved_file_is_reported(self, world):
        _bash(world, "true")
        (world.repo / "draft.md").write_text(_doc(_pins(20)), encoding="utf-8")
        out = _bash(world, "mkdir -p .claude && cp draft.md .claude/CLAUDE.md")
        assert "12 pins then, 20 now" in (_context(out) or "")

    def test_a_rerun_of_the_same_call_reports_once(self, world):
        _bash(world, "true")
        command = _append_pin_script(12)
        assert _context(_bash(world, command)) is not None
        assert _context(_bash(world, command, run=False)) is None

    def test_an_in_tool_edit_moves_the_record_so_bash_reports_nothing(self, world):
        _bash(world, "true")
        _edit(world, _doc(_pins(13)))
        assert _context(_bash(world, "true")) is None

    def test_an_outside_edit_between_calls_lands_on_the_next_bash_call(self, world):
        """Accepted cost, pinned so it stays deliberate: the report says the
        file grew since the last check, never that the command grew it."""
        _bash(world, "true")
        world.claude_md.write_text(_doc(_pins(13)), encoding="utf-8")
        _edit_other(world, world.repo / "notes.md")
        _edit_other(world, world.repo / "pkg" / "CLAUDE.md")  # not the project file
        report = _context(_bash(world, "ls"))
        assert report is not None and "since the last check" in report

    def test_records_are_private_files_in_private_directories(self, world):
        _bash(world, "true")
        record = world.session_dir / "claude-md-last-seen" / "lead.json"
        assert stat.S_IMODE(record.stat().st_mode) == 0o600
        assert stat.S_IMODE(record.parent.stat().st_mode) == 0o700
        _prompt(world)
        baseline = world.project_dir / "claude-md-baseline.json"
        assert stat.S_IMODE(baseline.stat().st_mode) == 0o600


# ---------------------------------------------------------------------------
# The drift check at each prompt and session start
# ---------------------------------------------------------------------------

class TestDriftCheck:

    def _baselined(self, w):
        assert _prompt(w) is None  # no baseline: record one, say nothing
        return w

    def test_a_background_append_is_reported_once_at_the_next_prompt(self, world):
        self._baselined(world)
        _bash(world, "true")
        # PostToolUse fires at launch, before the background job writes.
        assert _context(_bash(world, "python3 add_pin.py &", run=False)) is None
        _sh(world, _append_pin_script(12))  # the background write lands later
        advisory = _prompt(world)
        assert advisory is not None and "13 pins" in advisory and "held 12" in advisory
        assert "Nothing was refused or changed" in advisory and "secret" not in advisory
        assert _prompt(world) is None

    def test_outside_growth_is_reported_once_and_again_only_when_it_grows(self, world):
        self._baselined(world)
        world.claude_md.write_text(_doc(_pins(13)), encoding="utf-8")
        assert _prompt(world) is not None
        assert _prompt(world) is None
        world.claude_md.write_text(_doc(_pins(14)), encoding="utf-8")
        assert "14 pins" in (_prompt(world) or "")
        world.claude_md.write_text(_doc(_pins(14, body="changed body")), encoding="utf-8")
        assert _prompt(world) is None  # a body edit adds no pin

    def test_pins_pruned_then_grown_back_past_the_cap_are_reported(self, world):
        world.claude_md.write_text(_doc(_pins(13)), encoding="utf-8")
        self._baselined(world)
        world.claude_md.write_text(_doc(_pins(11)), encoding="utf-8")
        assert _prompt(world) is None
        world.claude_md.write_text(_doc(_pins(12)), encoding="utf-8")
        assert _prompt(world) is None  # growth within the cap
        world.claude_md.write_text(_doc(_pins(13)), encoding="utf-8")
        assert "now holds 13 pins" in (_prompt(world) or "")

    @pytest.mark.parametrize("record", [None, "{not json", json.dumps(["a"]),
                                        "OTHER-BASE"])
    def test_an_unusable_baseline_is_replaced_silently(self, world, record):
        world.claude_md.write_text(_doc(_pins(13)), encoding="utf-8")
        baseline = world.project_dir / "claude-md-baseline.json"
        if record == "OTHER-BASE":
            record = json.dumps({"base": "/elsewhere", "path": "/elsewhere/CLAUDE.md",
                                 "hash": "x", "count": 1, "state": "found"})
        if record is not None:
            baseline.write_text(record, encoding="utf-8")
        assert _prompt(world) is None
        assert _prompt(world) is None
        assert json.loads(baseline.read_text())["count"] == 13

    def test_growth_a_bash_report_named_still_reaches_the_next_prompt(self, world):
        """The per-Bash leg never moves the baseline."""
        self._baselined(world)
        _bash(world, "true")
        assert _context(_bash(world, _append_pin_script(12))) is not None
        assert "now holds 13 pins" in (_prompt(world) or "")

    def test_an_edit_of_another_claude_md_leaves_the_baseline(self, world):
        self._baselined(world)
        world.claude_md.write_text(_doc(_pins(13)), encoding="utf-8")
        _edit_other(world, world.repo / "pkg" / "CLAUDE.md")
        assert "now holds 13 pins" in (_prompt(world) or "")

    def test_a_gated_edit_that_lands_found_moves_the_baseline(self, world):
        self._baselined(world)
        _edit(world, _doc(_pins(13)))
        assert _prompt(world) is None

    def test_an_edit_on_the_not_found_path_leaves_the_baseline(self, world):
        self._baselined(world)
        unclosed = _pins(13).replace("secret body 5\n", "secret body 5\n```\nnever closed\n")
        _edit(world, _doc(unclosed))
        world.claude_md.write_text(_doc(_pins(13)), encoding="utf-8")
        assert "13 pins" in (_prompt(world) or "")

    def test_a_compaction_session_start_runs_no_drift_check(self, world):
        self._baselined(world)
        world.claude_md.write_text(_doc(_pins(13)), encoding="utf-8")
        assert _prompt(world, hook_event_name="SessionStart", source="compact") is None
        assert _prompt(world, hook_event_name="SessionStart", source="startup") is not None

    def test_a_teammate_session_runs_no_drift_check(self, world):
        self._baselined(world)
        world.claude_md.write_text(_doc(_pins(13)), encoding="utf-8")
        assert _prompt(world, agent_type="pact-backend-coder") is None
        assert _prompt(world) is not None

    def test_markers_removed_warn_once_then_stay_silent(self, world):
        self._baselined(world)
        world.claude_md.write_text(_doc(_pins(17), markers=False), encoding="utf-8")
        advisory = _prompt(world)
        assert advisory is not None and "cannot be counted" in advisory
        assert _prompt(world) is None
        world.claude_md.write_text(_doc(_pins(18), markers=False), encoding="utf-8")
        assert _prompt(world) is None


# ---------------------------------------------------------------------------
# Neither job writes, restores or locks CLAUDE.md; failures stay inside a job
# ---------------------------------------------------------------------------

class TestNoWriteAndIsolation:

    def test_a_read_only_file_under_a_held_lock_is_only_read(self, world):
        lock = world.repo / ".CLAUDE.md.lock"
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import fcntl, sys, time\nf = open(sys.argv[1], 'w')\n"
             "fcntl.flock(f, fcntl.LOCK_EX)\nprint('held', flush=True)\ntime.sleep(60)\n",
             str(lock)], stdout=subprocess.PIPE, text=True)
        try:
            assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
            _prompt(world)
            _bash(world, "true")
            world.claude_md.chmod(0o644)
            world.claude_md.write_text(_doc(_pins(13)), encoding="utf-8")
            world.claude_md.chmod(0o444)
            snapshot = (world.claude_md.read_bytes(), world.claude_md.stat().st_mtime_ns)
            assert "count cap" in (_context(_bash(world, "ls", run=False)) or "")
            assert "13 pins" in (_prompt(world) or "")
            assert (world.claude_md.read_bytes(), world.claude_md.stat().st_mtime_ns) == snapshot
        finally:
            holder.kill()
            holder.wait()
            world.claude_md.chmod(0o644)

    def test_an_occupied_record_path_fails_inside_the_job(self, world):
        """A natural failure: the last-seen record path is a directory."""
        (world.session_dir / "claude-md-last-seen" / "lead.json").mkdir(parents=True)
        out = _edit(world, _doc(_pins(12, body="edited")))
        assert out == {"suppressOutput": True}
        tracked = world.cfg / "pact-memory" / "session-tracking" / f"{SID}.json"
        assert str(world.claude_md) in tracked.read_text()
        assert _context(_bash(world, "true")) is None

    def test_a_raising_job_leaves_the_hosts_other_work(self, world, monkeypatch, capsys):
        import io

        import missed_wake_scan
        import shared.claude_md_drift as drift
        import track_files

        def boom(*_args, **_kwargs):
            raise RuntimeError("drift job failed")

        for name in ("report_after_bash", "record_after_write", "drift_advisory"):
            monkeypatch.setattr(drift, name, boom)
        for key, value in world.env.items():
            monkeypatch.setenv(key, value)
        tracked = []
        monkeypatch.setattr(track_files, "track_file", lambda path, tool: tracked.append(path))
        cleared = []
        monkeypatch.setattr(track_files, "clear_pin_staleness_marker_if_resolved",
                            lambda tool, tool_input: cleared.append(tool))
        frame = {"hook_event_name": "PostToolUse", "tool_name": "Edit", "session_id": SID,
                 "agent_type": LEAD, "tool_input": {"file_path": str(world.claude_md)}}
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(frame)))
        with pytest.raises(SystemExit):
            track_files.main()
        assert tracked == [str(world.claude_md)] and cleared == ["Edit"]

        monkeypatch.setattr(missed_wake_scan, "run_surface", lambda data: "MISSED-WAKE SURFACE")
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
            {"hook_event_name": "UserPromptSubmit", "session_id": SID, "agent_type": LEAD})))
        capsys.readouterr()
        with pytest.raises(SystemExit):
            missed_wake_scan.main()
        assert "MISSED-WAKE SURFACE" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# A teammate's growth reaches it, and the lead hears of it once
# ---------------------------------------------------------------------------

class TestTeammateGrowth:

    @pytest.mark.parametrize("frame", [
        # In-process, the captured shape: the lead's session, agent_type carrying
        # the member's NAME (not its pact- type) and an agent_id with no "@". The
        # gate checks it as a member of the session's team.
        {"agent_type": "probe-coder", "agent_id": "0123456789abcdef"},
        # A separate process: its own session, registered to the team, no
        # agent_id, no context file. The gate checks it as a member too.
        {"agent_type": "pact-backend-coder", "session_id": "teammate-session"},
    ], ids=["in-process", "separate-process"])
    def test_a_teammates_growth_is_reported_to_it_and_once_to_the_lead(self, world, frame):
        """A member gets the gate's member instruction, never the pin command it
        cannot run."""
        from shared.pact_context import is_lead

        assert is_lead(frame) is False
        _prompt(world)
        _bash(world, "true")  # the lead's own last-seen record
        baseline = world.project_dir / "claude-md-baseline.json"
        before = (baseline.read_bytes(), baseline.stat().st_mtime_ns)
        _bash(world, "true", **frame)
        report = _context(_bash(world, _append_pin_script(12), **frame))
        assert report == _count_report(world, _MEMBER_STEP)
        assert report is not None and "prune-memory" not in report
        _edit(world, world.claude_md.read_text(), **frame)  # a teammate's Edit
        assert (baseline.read_bytes(), baseline.stat().st_mtime_ns) == before
        assert "count cap" in (_context(_bash(world, "true")) or "")  # the lead's own record
        assert "13 pins" in (_prompt(world) or "")
        assert _prompt(world) is None


# ---------------------------------------------------------------------------
# A session the pin-cap gate does not check gets no record and no report
# ---------------------------------------------------------------------------

class TestFramesTheGateDoesNotCheck:

    @pytest.mark.parametrize("frame", [
        {"session_id": "plain-session"},  # no --agent: no PACT role, no context file
        # A subagent of that plain session: an Agent-tool type and agent_id.
        {"session_id": "plain-session", "agent_type": "general-purpose", "agent_id": "a1b2c3d4"},
        {"session_id": "agent-session", "agent_type": "my-custom-agent"},  # a non-PACT --agent
    ], ids=["plain-session", "plain-session-subagent", "non-pact-agent"])
    def test_growth_in_a_session_with_no_pact_role_is_left_alone(self, world, frame):
        from shared.pact_context import project_slug

        frame = {"agent_type": None, **frame}  # None: the key is absent
        _bash(world, "true", **frame)
        assert _context(_bash(world, _append_pin_script(12), **frame)) is None
        _edit(world, world.claude_md.read_text(), **frame)
        sessions = world.cfg / "pact-sessions" / project_slug(str(world.repo))
        assert not (sessions / frame["session_id"]).exists()
        assert not (world.session_dir / "claude-md-last-seen").exists()
        assert not (sessions / "claude-md-baseline.json").exists()


# ---------------------------------------------------------------------------
# Wiring, read from the shipped hooks.json
# ---------------------------------------------------------------------------

class TestWiring:

    def _groups(self, event):
        hooks = json.loads((HOOKS / "hooks.json").read_text())["hooks"]
        return hooks.get(event, [])

    def _commands(self, event, script):
        return [(g.get("matcher"), h) for g in self._groups(event) for h in g["hooks"]
                if script in h.get("command", "")]

    def test_the_registrations(self):
        gate = self._commands("PreToolUse", "pin_caps_gate.py")
        assert [m for m, _ in gate] == ["Edit|Write"]
        post = self._commands("PostToolUse", "track_files.py")
        assert len(post) == 1 and {"Bash", "Edit", "Write"} <= set(post[0][0].split("|"))
        failure = self._commands("PostToolUseFailure", "track_files.py")
        assert [m for m, _ in failure] == ["Bash"] and not failure[0][1].get("async")
        assert not post[0][1].get("async")
        for event in ("UserPromptSubmit", "SessionStart"):
            assert self._commands(event, "missed_wake_scan.py")
        bash_pre = [h.get("command", "") for g in self._groups("PreToolUse")
                    if "Bash" in (g.get("matcher") or "Bash").split("|") for h in g["hooks"]]
        assert not any("track_files" in c or "missed_wake_scan" in c for c in bash_pre)

    @pytest.mark.parametrize("tool", ["Bash", "Edit"])
    def test_the_failure_leg_skips_the_launch_record_and_file_tracking(
            self, world, monkeypatch, capsys, tool):
        """A failed call starts no background job and edits no file, so the
        failure leg runs neither job; the success leg runs the one for its
        tool (the control)."""
        import io

        import shared.background_work as background_work
        import track_files

        for key, value in world.env.items():
            monkeypatch.setenv(key, value)
        calls = []
        monkeypatch.setattr(background_work, "record_background_launch",
                            lambda data: calls.append("launch"))
        monkeypatch.setattr(track_files, "track_file", lambda path, name: calls.append("track"))
        tool_input = ({"command": "sleep 5 &", "run_in_background": True} if tool == "Bash"
                      else {"file_path": str(world.repo / "notes.md")})
        for event in ("PostToolUseFailure", "PostToolUse"):
            frame = {"hook_event_name": event, "tool_name": tool, "tool_input": tool_input,
                     "session_id": SID, "agent_type": LEAD}
            monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(frame)))
            with pytest.raises(SystemExit):
                track_files.main()
            capsys.readouterr()
            if event == "PostToolUseFailure":
                assert calls == []
        assert calls == ["launch" if tool == "Bash" else "track"]

    def test_a_failure_report_names_the_failure_event(self, world):
        _bash(world, "true")
        _sh(world, _append_pin_script(12))
        out = _bash(world, "python3 add_pin.py && false", event="PostToolUseFailure", run=False)
        assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUseFailure"

    def test_the_failure_leg_clears_a_resolved_staleness_marker(self, world):
        """An archive step that succeeded inside a failing command leaves the
        staleness signal cleared, so the marker goes on PostToolUseFailure."""
        from shared.constants import PIN_STALENESS_MARKER_NAME

        marker = world.session_dir / PIN_STALENESS_MARKER_NAME
        marker.write_text("armed", encoding="utf-8")
        _bash(world, "python3 archive_pin.py 3 && false", event="PostToolUseFailure", run=False)
        assert not marker.exists()


# ---------------------------------------------------------------------------
# Per-call cost: no git process once the base is cached
# ---------------------------------------------------------------------------

def _worktree_like(w):
    """CLAUDE_PROJECT_DIR names a directory with no CLAUDE.md, as in a worktree
    session, so the resolver reaches its git step. Returns (env, session dir)."""
    from shared.pact_context import project_slug

    sub = w.repo / "sub"
    sub.mkdir()
    session_dir = w.cfg / "pact-sessions" / project_slug(str(sub)) / SID
    session_dir.mkdir(parents=True)
    (session_dir / "pact-session-context.json").write_text(
        json.dumps({"session_id": SID, "project_dir": str(sub)}), encoding="utf-8")
    return dict(w.env, CLAUDE_PROJECT_DIR=str(sub)), session_dir


def _shim_git(env, tmp_path):
    """`env` with a `git` first on PATH that logs each start and fails."""
    shim = tmp_path / "shim"
    shim.mkdir()
    log = tmp_path / "git-calls.log"
    (shim / "git").write_text(f"#!/bin/sh\necho called >> {log}\nexit 1\n")
    (shim / "git").chmod(0o755)
    return dict(env, PATH=f"{shim}:{env['PATH']}"), log


_BASH_TRUE = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": SID,
              "agent_type": LEAD, "tool_input": {"command": "true"}}


def test_no_git_process_starts_once_the_base_is_cached(world, tmp_path):
    env, session_dir = _worktree_like(world)
    _hook(world, TRACK, _BASH_TRUE, env=env)
    assert json.loads((session_dir / "claude-md-base.json").read_text()) == {"base": str(world.repo)}
    shim_env, log = _shim_git(env, tmp_path)
    _sh(world, _append_pin_script(12))
    assert "count cap" in (_context(_hook(world, TRACK, _BASH_TRUE, env=shim_env)) or "")
    _hook(world, MISSED, {"hook_event_name": "UserPromptSubmit", "session_id": SID,
                          "agent_type": LEAD}, env=shim_env)
    assert not log.exists()


def test_a_cached_no_file_starts_no_git_process(world, tmp_path):
    env, session_dir = _worktree_like(world)
    world.claude_md.unlink()
    _hook(world, TRACK, _BASH_TRUE, env=env)
    assert json.loads((session_dir / "claude-md-base.json").read_text()) == {"base": None}
    shim_env, log = _shim_git(env, tmp_path)
    _hook(world, TRACK, _BASH_TRUE, env=shim_env)
    assert not log.exists()


def test_the_report_jobs_add_one_read_and_one_hash_when_nothing_changed(world):
    """Measured, never asserted on time. Neither a first call (no record yet)
    nor a call on an unchanged file loads the pin-growth rule (`difflib`)."""
    probe = (
        "import json, sys\n"
        "import shared.claude_md_drift as d\n"
        "d.report_after_bash(json.loads(sys.argv[1]))\n"
        "print('shared.pin_growth' in sys.modules or 'difflib' in sys.modules)\n"
    )
    frame = json.dumps({"tool_name": "Bash", "session_id": SID, "agent_type": LEAD})
    env = carry_clock_shift(dict(world.env, PYTHONPATH=str(HOOKS)))

    def run():
        return subprocess.run([sys.executable, "-c", probe, frame], capture_output=True,
                              text=True, env=env, cwd=world.repo, timeout=60)

    first, second = run(), run()
    assert first.stdout.strip() == "False" and second.stdout.strip() == "False", first.stderr
    started = time.perf_counter()
    for _ in range(5):
        run()
    print(f"report job, unchanged file: {(time.perf_counter() - started) / 5 * 1000:.1f} ms per process")


@pytest.mark.parametrize("module", ["track_files", "missed_wake_scan"])
def test_the_hosts_load_nothing_heavy_at_module_level(module, tmp_path):
    probe = (f"import sys; import {module}; "
             "print(sorted(m for m in ('shared.claude_md_drift', 'shared.claude_md_markers', "
             "'shared.pin_growth', 'difflib') if m in sys.modules))")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path),
           "CLAUDE_CONFIG_DIR": str(tmp_path), "PYTHONPATH": str(HOOKS)}
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                            env=env, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
