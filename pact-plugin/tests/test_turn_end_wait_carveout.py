"""
Tests for the pact-wait carve-out in shared/turn_end_gate.py: a RUNNING
pact-wait watcher is the lead's own scheduled wake, exempt from the lead
shell-job block; a lookalike command that merely mentions the watcher, or a
partial invocation missing an arm flag, still counts.

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
SUPPRESS = {"suppressOutput": True}


def _arm_command(watcher_path="/plugins/cache/pact-plugin/hooks/pact_wait.py"):
    """The exact shape the arm-supply advisories emit (batch 2)."""
    return (
        f'python3 "{watcher_path}" --team {TEAM} --task 12 '
        "--predicate handoff --deadline 1800"
    )


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
            "members": [{"name": "team-lead", "agentType": "pact-orchestrator"}],
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


# --- classifier shape table (in-process, fast) ------------------------------

def test_is_wait_watcher_command_shape_table():
    """The anchored shape: closed prefix set, full arm flags required, AND
    nothing after the deadline value — a composed tail (`& sleep 1`) means
    the job is not purely the wake, so it counts. The exempt rows are
    exactly the shapes the arm-supply advisories emit (with their benign
    prefix variants); every counted row is a mention, a partial, a
    composition, or a reordered invocation."""
    from shared.turn_end_gate import is_wait_watcher_command

    exempt = [
        _arm_command(),
        _arm_command("/opt/x/pact_wait.py"),
        f"cd /repo && {_arm_command()}",
        f"FOO=1 {_arm_command()}",
        _arm_command().replace("python3 ", "python3.12 "),
        _arm_command() + "  ",
    ]
    counted = [
        "echo 'pact_wait.py --team t --task 1 --predicate h --deadline 5'",
        "pact_wait.py --team t --task 1 --predicate h",
        "pact_wait.py --task 1 --team t --predicate h --deadline 5",
        "pact_wait.py --team t --task 1 --predicate h --deadline 5 & sleep 1",
        "true; pact_wait.py --team t --task 1 --predicate h --deadline 5",
        "cat pact_wait.py --team t --task 1 --predicate h --deadline 5",
        "pact_wait.py --team t --task 1 --predicate h --deadline 1800 extra",
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
