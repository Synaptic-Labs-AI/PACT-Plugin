"""
Tests for the pact-wait carve-out in shared/turn_end_gate.py: a RUNNING
pact-wait watcher is the arming frame's own scheduled wake, exempt from the
turn-end shell-job block; a lookalike command that merely mentions the
watcher, or a partial invocation missing an arm flag, still counts.

The carve-out applies to exactly the roles whose own completion notice
starts their next turn: the LEAD and a SEPARATE-PROCESS teammate. An
IN-PROCESS teammate is deliberately NOT exempt — its block text is the one
that says the completion notice will NOT wake them, so its watcher stays
accountable and the remedy stays the intentional_wait coverage path.

Driven through the PRODUCTION hook (stop_background_gate.py) with the same
hermetic World layout the stop-gate matrix uses, so the carve-out is proven
at the surface real traffic takes. The PAIR the dispatch names:
  (a) a watcher-shaped job does not block the lead's turn end;
  (b) a lookalike non-watcher command still counts.
The duplicate-watcher re-arm is a named scenario: two watcher jobs (a stale
one and its re-arm) both exempt — in reality the second fast-exits 0 via
deliverable-present, and at this layer neither may block the turn end.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
HOOK = HOOKS_DIR / "stop_background_gate.py"

TEAM = "session-stopgate"
LEAD_SID = "aaaaaaaa-0000-4000-8000-000000000001"
LEAD_TYPE = "PACT:pact-orchestrator"
MATE_SID = "bbbbbbbb-0000-4000-8000-000000000002"
MATE = "backend-coder"
SUPPRESS = {"suppressOutput": True}


def _arm_command(watcher_path="/plugins/cache/pact-plugin/hooks/pact_wait.py"):
    """The command the arm-supply advisories emit, built by the shared
    builder itself so this fixture cannot drift from the emitters."""
    from shared.wait_target import arm_command

    return arm_command(TEAM, "12", "handoff", 1800, watcher=watcher_path)


def job(job_id: str, command: str) -> dict:
    return {
        "id": job_id,
        "type": "shell",
        "status": "running",
        "description": f"background job {job_id}",
        "command": command,
    }


def stop_frame(jobs=()) -> dict:
    return {
        "hook_event_name": "Stop",
        "session_id": LEAD_SID,
        "transcript_path": "<transcript_path>",
        "cwd": "<cwd>",
        "permission_mode": "bypassPermissions",
        "last_assistant_message": "done",
        "background_tasks": list(jobs),
        "session_crons": [],
        "stop_hook_active": False,
        "agent_type": LEAD_TYPE,
    }


class World:
    """The hermetic config root a live PACT team leaves behind (the minimal
    lead-side subset of the stop-gate matrix's World)."""

    def __init__(self, tmp_path: Path):
        self.config = tmp_path / "config"
        self.project = tmp_path / "proj"
        self.project.mkdir()
        team_dir = self.config / "teams" / TEAM
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(json.dumps({
            "leadSessionId": LEAD_SID,
            "members": [
                {"name": "team-lead", "agentType": "pact-orchestrator"},
                {"name": MATE, "agentType": "pact-backend-coder"},
            ],
        }))
        (self.config / "tasks" / TEAM).mkdir(parents=True)
        session_dir = (
            self.config / "pact-sessions" / self.project.resolve().name / LEAD_SID
        )
        session_dir.mkdir(parents=True)
        (session_dir / "pact-session-context.json").write_text(json.dumps({
            "team_name": TEAM,
            "session_id": LEAD_SID,
            "project_dir": str(self.project),
            "plugin_root": "",
            "started_at": "2026-09-13T00:00:00Z",
        }))

    def run(self, frame: dict) -> subprocess.CompletedProcess:
        env = {
            k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"
        }
        env["CLAUDE_CONFIG_DIR"] = str(self.config)
        env["CLAUDE_PROJECT_DIR"] = str(self.project)
        return subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps(frame), capture_output=True, text=True,
            env=env, timeout=60, check=False,
        )

    def register_teammate(self, session_id: str, name: str) -> None:
        path = self.config / "pact-sessions" / ".teammate-registry.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as registry:
            registry.write(
                json.dumps({"session_id": session_id, "value": f"{name}@{TEAM}"})
                + "\n"
            )

    def output(self, proc: subprocess.CompletedProcess):
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout) if proc.stdout.strip() else None


# --- the PAIR ---------------------------------------------------------------

def test_watcher_does_not_block_lead_turn_end(tmp_path):
    """PAIR (a): a running pact-wait watcher is the scheduled wake itself —
    the lead's turn end is allowed, no block printed, nothing marked told."""
    world = World(tmp_path)
    frame = stop_frame(jobs=[job("w1", _arm_command())])
    assert world.output(world.run(frame)) == SUPPRESS


def test_lookalike_non_watcher_still_counts(tmp_path):
    """PAIR (b): a command that merely MENTIONS the watcher inside an echo
    is not a watcher — the lead is still blocked over it."""
    world = World(tmp_path)
    lookalike = (
        "echo 'python3 /x/pact_wait.py --team t --task 1 "
        "--predicate handoff --deadline 5'"
    )
    frame = stop_frame(jobs=[job("w2", lookalike)])
    out = world.output(world.run(frame))
    assert out["decision"] == "block"
    assert "`w2`" in out["reason"]


def test_partial_invocation_still_counts(tmp_path):
    """A pact_wait invocation missing a required arm flag is not the armed
    shape: still counted (over-block is the safe direction)."""
    world = World(tmp_path)
    partial = "python3 /x/pact_wait.py --team t --task 1 --predicate handoff"
    frame = stop_frame(jobs=[job("w3", partial)])
    out = world.output(world.run(frame))
    assert out["decision"] == "block"
    assert "`w3`" in out["reason"]


def test_watcher_spaced_quoted_install_does_not_block_turn_end(tmp_path):
    """A live install under a path with spaces emits the arm command with a
    QUOTED path — the classifier must read that command as the scheduled
    wake, or the lead stays blocked over their own watcher and the
    filler-loop this mechanism replaces re-forms. Red on the pre-fix
    charset (which admitted no spaces even inside the quotes), green
    after."""
    world = World(tmp_path)
    frame = stop_frame(jobs=[
        job("wsp1", _arm_command("/opt/my plugins dir/pact_wait.py"))
    ])
    assert world.output(world.run(frame)) == SUPPRESS


def test_watcher_exempt_real_job_still_blocks_on_the_real_job(tmp_path):
    """The carve-out removes only the watcher from the must-account-for
    list: a real unowned job beside it still blocks, listing that job and
    never the watcher id."""
    world = World(tmp_path)
    frame = stop_frame(jobs=[
        job("watcher1", _arm_command()),
        job("real1", "sleep 300"),
    ])
    out = world.output(world.run(frame))
    assert out["decision"] == "block"
    assert "`real1`" in out["reason"]
    assert "`watcher1`" not in out["reason"]


def test_duplicate_watcher_re_arm_both_exempt(tmp_path):
    """Named scenario — duplicate-watcher re-arm: the stale watcher and its
    re-arm are both watcher-shaped, so neither blocks the turn end (in
    reality the duplicate fast-exits 0 via deliverable-present; at this
    layer both are the same scheduled wake)."""
    world = World(tmp_path)
    frame = stop_frame(jobs=[
        job("watcher-stale", _arm_command()),
        job("watcher-rearm", _arm_command()),
    ])
    assert world.output(world.run(frame)) == SUPPRESS


# --- the teammate extension -------------------------------------------------

def teammate_stop_frame(jobs=()) -> dict:
    """A SEPARATE-PROCESS teammate's Stop frame: its own session id, its
    real agent type, no agent_id, a registry entry, no context file."""
    return {
        "hook_event_name": "Stop",
        "session_id": MATE_SID,
        "transcript_path": "<transcript_path>",
        "cwd": "<cwd>",
        "permission_mode": "bypassPermissions",
        "last_assistant_message": "done",
        "background_tasks": list(jobs),
        "session_crons": [],
        "stop_hook_active": False,
        "agent_type": "pact-backend-coder",
    }


def test_watcher_does_not_block_separate_process_teammate_turn_end(tmp_path):
    """A separate-process teammate that armed a watcher gets the same
    carve-out as the lead: its own completion notice starts its next turn,
    so the running watcher IS its scheduled wake and must not block the
    turn end it was armed to end."""
    world = World(tmp_path)
    world.register_teammate(MATE_SID, MATE)
    frame = teammate_stop_frame(jobs=[job("mw1", _arm_command())])
    assert world.output(world.run(frame)) == SUPPRESS


def test_in_process_teammate_watcher_still_counts(tmp_path, monkeypatch):
    """The deliberate asymmetry: an IN-PROCESS teammate's watcher does NOT
    get the carve-out. Its completion notice will not wake them (their
    block text says so), so the watcher stays in the must-account-for list
    and the remedy stays the intentional_wait coverage path. Pinned at the
    _candidates fork, because that is the line the role split lives on."""
    from shared import turn_end_gate

    records = [
        {"agent_name": MATE, "harness_task_id": "iw1", "launched_at": "2026-11-21T00:00:00Z"}
    ]
    running = [{"id": "iw1", "type": "shell", "command": _arm_command()}]
    monkeypatch.setattr(
        turn_end_gate.background_work,
        "load_records_for_discharge",
        lambda team: records,
    )

    subagent_stop = turn_end_gate._candidates(
        {"hook_event_name": "SubagentStop"}, turn_end_gate.ROLE_TEAMMATE,
        MATE, TEAM, running,
    )
    stop = turn_end_gate._candidates(
        {"hook_event_name": "Stop"}, turn_end_gate.ROLE_TEAMMATE,
        MATE, TEAM, running,
    )
    # SubagentStop (in-process): the watcher stays accountable.
    assert [e["id"] for e in subagent_stop] == ["iw1"]
    # Stop (separate-process): the watcher is exempt.
    assert stop == []


# --- classifier shape table (in-process, fast) ------------------------------

def test_is_wait_watcher_command_shape_table():
    """The anchored shape: closed prefix set, full arm flags required, every
    value one shell-inert word, AND nothing after the deadline value but an
    optional `2>&1`. The exempt rows are the shapes the builder emits (any
    install path, quoted as shlex.quote renders it) and their harmless
    spellings (cd prefix, env assignment, interpreter by path or version,
    trailing redirection); every counted row is a mention, a partial, a
    reordered invocation, or a composition — spaced or glued, in any slot
    — and a spaced UNQUOTED path still counts (the shell word-splits it,
    so it cannot be the armed command)."""
    from shared.turn_end_gate import is_wait_watcher_command

    exempt = [
        _arm_command(),
        _arm_command("/opt/x/pact_wait.py"),
        _arm_command("/opt/my plugins dir/pact_wait.py"),
        f"cd /repo && {_arm_command()}",
        f"FOO=1 {_arm_command()}",
        _arm_command().replace("python3 ", "python3.12 "),
        _arm_command().replace("python3 ", "/usr/bin/python3 "),
        _arm_command().replace("python3 ", "/repo/.venv/bin/python3 "),
        _arm_command() + " 2>&1",
        _arm_command() + "  ",
        _arm_command("/opt/o'brien/hooks/pact_wait.py"),
        _arm_command('/opt/"q"/hooks/pact_wait.py'),
        # A quoted spaced task id is ONE shell word: the builder renders a
        # pathological id exactly this way, so it is the armed command.
        "pact_wait.py --team t --task '1 2' --predicate h --deadline 5",
        # A special-char install path (+ is in shlex.quote's no-op set): the
        # emitted command renders it UNQUOTED, so the classifier must admit
        # the same set or a +cache-style install stays blocked over its own
        # scheduled wake (cold round 12).
        "python3 /opt/pact+cache/hooks/pact_wait.py --team t --task 1 "
        "--predicate h --deadline 5",
    ]
    counted = [
        "echo 'pact_wait.py --team t --task 1 --predicate h --deadline 5'",
        # An echo MENTION of the spaced quoted command still counts: the
        # prefix guard must hold under the widened quoted charset.
        "echo 'python3 \"/opt/my plugins dir/pact_wait.py\" --team t "
        "--task 1 --predicate h --deadline 5'",
        "pact_wait.py --team t --task 1 --predicate h",
        "pact_wait.py --task 1 --team t --predicate h --deadline 5",
        "pact_wait.py --team t --task 1 --predicate h --deadline 5 & sleep 1",
        "true; pact_wait.py --team t --task 1 --predicate h --deadline 5",
        "cat pact_wait.py --team t --task 1 --predicate h --deadline 5",
        "pact_wait.py --team t --task 1 --predicate h --deadline 1800 extra",
        # GLUED compositions: the shell splits && ; | & BEFORE argv, so
        # each arms a watcher plus a second command — not the bare wake.
        "pact_wait.py --team t --task 1 --predicate h --deadline 5&&rm-rf-x",
        "pact_wait.py --team t --task 1 --predicate h --deadline 5;true",
        "pact_wait.py --team t;make --task 1 --predicate h --deadline 5",
        "pact_wait.py --team t --task 1 --predicate h --deadline 5|tee x",
        "pact_wait.py --team t --task 1 --predicate h --deadline 5 2>&1; make",
        # A compound before the watcher, in the cd slot or an assignment.
        "cd /repo; make && pact_wait.py --team t --task 1 --predicate h "
        "--deadline 5",
        "cd $(make) && pact_wait.py --team t --task 1 --predicate h "
        "--deadline 5",
        "X=$(make) pact_wait.py --team t --task 1 --predicate h --deadline 5",
        # An expansion inside a value slot runs a command too.
        'pact_wait.py --team "$(id)" --task 1 --predicate h --deadline 5',
        "pact_wait.py --team t --task `id` --predicate h --deadline 5",
        # A script that is not named exactly pact_wait.py.
        "python3 /x/not_pact_wait.py --team t --task 1 --predicate h "
        "--deadline 5",
        "python3 /x/pact_wait.py.bak --team t --task 1 --predicate h "
        "--deadline 5",
        # A spaced path left UNQUOTED is not the emitted shape: the shell
        # word-splits it, so it cannot be the armed command — counted
        # (documented over-block lean).
        "python3 /opt/my plugins dir/pact_wait.py --team t --task 1 "
        "--predicate h --deadline 5",
        # A shell-wrapper invocation is a composition, not the bare wake:
        # counted (over-block is the deliberate direction — the wrapper
        # costs the lead one advisory, exemption would acquit a real job).
        "bash -c 'pact_wait.py --team t --task 1 --predicate h --deadline 5'",
        "sleep 300",
        None,
        123,
    ]
    assert all(is_wait_watcher_command(c) for c in exempt)
    assert not any(is_wait_watcher_command(c) for c in counted)
