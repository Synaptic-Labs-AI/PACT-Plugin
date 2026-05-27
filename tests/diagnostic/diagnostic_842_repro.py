"""
Phase A diagnostic harness for #842 — empirical reproduction against current main.

Reads task state from a temp ~/.claude/teams + ~/.claude/tasks fixture mirroring
the canonical orchestrate.md teachback-gated dispatch sequence. Probes
count_active_tasks at each transition under canonical and non-canonical
variants to distinguish H1/H2/H3/H4.

Run as a script (NOT pytest) so the empirical traces print to stdout directly.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "pact-plugin" / "hooks"
EMITTER_PATH = HOOKS_DIR / "teardown_request_emitter.py"

sys.path.insert(0, str(HOOKS_DIR))
from shared.wake_lifecycle import (
    _ACTIVE_STATUSES,
    _lifecycle_relevant,
    count_active_tasks,
)


def make_team_config(teams_dir: Path, team_name: str, lead_agent_id: str, members: list) -> None:
    team_dir = teams_dir / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(json.dumps({
        "teamId": team_name,
        "leadSessionId": "test-lead-session-id",
        "leadAgentId": lead_agent_id,
        "members": members,
    }, indent=2))


def write_task(tasks_dir: Path, team_name: str, task: dict) -> Path:
    team_tasks = tasks_dir / team_name
    team_tasks.mkdir(parents=True, exist_ok=True)
    path = team_tasks / f"{task['id']}.json"
    path.write_text(json.dumps(task, indent=2))
    return path


def trace_count(team_name: str, label: str, tasks_dir: Path) -> int:
    """Read each task file and apply _lifecycle_relevant directly, printing per-task verdicts."""
    team_tasks = tasks_dir / team_name
    files = sorted(team_tasks.glob("*.json")) if team_tasks.exists() else []
    print(f"\n--- {label}: enumerate ~/.claude/tasks/{team_name}/ ---")
    total = 0
    for f in files:
        try:
            t = json.loads(f.read_text())
        except Exception as e:
            print(f"  [{f.name}] PARSE_FAIL {e}")
            continue
        lc = _lifecycle_relevant(t, team_name)
        print(f"  [{f.name}] status={t.get('status')!r} owner={t.get('owner')!r} blocks={t.get('blocks')!r} -> lifecycle_relevant={lc}")
        if lc:
            total += 1
    # Compare against count_active_tasks
    ca = count_active_tasks(team_name)
    print(f"  manual_count={total}  count_active_tasks()={ca}  match={total==ca}")
    return ca


def fire_teardown_hook(team_name: str, task_id: str, hook_event_name: str, tasks_dir: Path) -> dict:
    """Invoke teardown_request_emitter.py via subprocess with stdin payload.
    Returns {'stdout': ..., 'stderr': ..., 'exit': ...}."""
    payload = {
        "hook_event_name": hook_event_name,
        "task_id": task_id,
        "team_name": team_name,
        # is_lead_context discriminator: no agent_id, no teammate_name -> lead
    }
    env = os.environ.copy()
    # Override HOME so the hook's Path.home() resolves to our fixture.
    # tasks_dir is tmp_root/.claude/tasks; HOME must be tmp_root so
    # Path.home()/.claude/tasks/{team} resolves correctly.
    env["HOME"] = str(tasks_dir.parent.parent)
    # PYTHONPATH so 'shared.x' imports resolve
    env["PYTHONPATH"] = str(HOOKS_DIR)
    proc = subprocess.run(
        [sys.executable, str(EMITTER_PATH)],
        input=json.dumps(payload),
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return {"stdout": proc.stdout, "stderr": proc.stderr, "exit": proc.returncode}


def variant_canonical_teachback_handoff(tmp_root: Path):
    """Canonical orchestrate.md sequence:
       1. TaskCreate(A unowned)
       2. TaskCreate(B unowned)
       3. TaskUpdate(A, owner=teammate, addBlocks=[B])
       4. TaskUpdate(B, owner=teammate, addBlockedBy=[A])
       5. TaskUpdate(A, status=completed) <- TaskCompleted hook fires here
    """
    print("\n" + "=" * 70)
    print("VARIANT V1: Canonical orchestrate.md teachback A->B handoff")
    print("=" * 70)
    teams_dir = tmp_root / ".claude" / "teams"
    tasks_dir = tmp_root / ".claude" / "tasks"
    team = "pact-diag-v1"

    make_team_config(teams_dir, team, "lead-agent-id-1", [
        {"agentId": "lead-agent-id-1", "agentType": "team-lead", "name": "team-lead"},
        {"agentId": "preparer-agent-id-2", "agentType": "preparer", "name": "preparer"},
    ])

    # Step 5 state: A=completed, B=pending+owner=preparer+blockedBy=[A]
    write_task(tasks_dir, team, {
        "id": "A", "subject": "preparer: TEACHBACK for diag-feature",
        "status": "completed", "owner": "preparer",
        "blocks": ["B"], "blockedBy": [],
    })
    write_task(tasks_dir, team, {
        "id": "B", "subject": "preparer: do the feature",
        "status": "pending", "owner": "preparer",
        "blocks": [], "blockedBy": ["A"],
    })

    # Patch hooks env to use our tmp HOME
    os.environ["HOME"] = str(tmp_root)
    n = trace_count(team, "Post TaskUpdate(A, status=completed)", tasks_dir)
    print(f"\nExpected (per backend-coder Task #8 trace): count >= 1 (B pending+owner counts)")
    print(f"Actual: count = {n}")

    out = fire_teardown_hook(team, "A", "TaskCompleted", tasks_dir)
    print(f"\nteardown_request_emitter subprocess:")
    print(f"  exit={out['exit']}")
    print(f"  stdout={out['stdout']!r}")
    print(f"  stderr={out['stderr'][:200]!r}")
    teardown_fired = is_teardown_emitted(out['stdout'])
    print(f"  TEARDOWN EMITTED? {teardown_fired}")


def is_teardown_emitted(stdout: str) -> bool:
    """A teardown is emitted iff hookSpecificOutput contains additionalContext
    referencing stop-pending-scan. _SUPPRESS_OUTPUT is {'suppressOutput': True}."""
    try:
        # Strip trailing newline; subprocess may emit only the JSON
        line = stdout.strip().splitlines()[-1] if stdout.strip() else ""
        if not line:
            return False
        obj = json.loads(line)
        if isinstance(obj, dict) and obj.get("suppressOutput"):
            return False
        hso = obj.get("hookSpecificOutput") if isinstance(obj, dict) else None
        if isinstance(hso, dict) and "additionalContext" in hso:
            return True
        return False
    except Exception:
        return False


def variant_h4_unowned_B(tmp_root: Path):
    """H4 variant: Task B exists but owner is NOT set yet (write-race scenario).
       A completes; B is still owner-null pending.
    """
    print("\n" + "=" * 70)
    print("VARIANT V2 (H4 test): B exists, owner=null at TaskCompleted(A)")
    print("=" * 70)
    teams_dir = tmp_root / ".claude" / "teams"
    tasks_dir = tmp_root / ".claude" / "tasks"
    team = "pact-diag-v2"

    make_team_config(teams_dir, team, "lead-agent-id-1", [
        {"agentId": "lead-agent-id-1", "agentType": "team-lead", "name": "team-lead"},
        {"agentId": "preparer-agent-id-2", "agentType": "preparer", "name": "preparer"},
    ])

    write_task(tasks_dir, team, {
        "id": "A", "subject": "preparer: TEACHBACK for diag-feature",
        "status": "completed", "owner": "preparer",
        "blocks": ["B"], "blockedBy": [],
    })
    write_task(tasks_dir, team, {
        "id": "B", "subject": "preparer: do the feature",
        "status": "pending", "owner": None,       # <-- unowned
        "blocks": [], "blockedBy": ["A"],
    })

    os.environ["HOME"] = str(tmp_root)
    n = trace_count(team, "Post TaskUpdate(A, status=completed) with B.owner=null", tasks_dir)
    print(f"\nExpected if H4 holds: count = 0 (B excluded by teammate-owner-check)")
    print(f"Actual: count = {n}")

    out = fire_teardown_hook(team, "A", "TaskCompleted", tasks_dir)
    print(f"\nteardown_request_emitter subprocess:")
    print(f"  exit={out['exit']}")
    print(f"  stdout={out['stdout']!r}")
    teardown_fired = is_teardown_emitted(out['stdout'])
    print(f"  TEARDOWN EMITTED? {teardown_fired}")


def variant_terminal_only_no_B(tmp_root: Path):
    """Baseline: A completes, no other tasks. Expect teardown to fire."""
    print("\n" + "=" * 70)
    print("VARIANT V3 (baseline): A=completed, no other tasks — expect TEARDOWN")
    print("=" * 70)
    teams_dir = tmp_root / ".claude" / "teams"
    tasks_dir = tmp_root / ".claude" / "tasks"
    team = "pact-diag-v3"

    make_team_config(teams_dir, team, "lead-agent-id-1", [
        {"agentId": "lead-agent-id-1", "agentType": "team-lead", "name": "team-lead"},
        {"agentId": "preparer-agent-id-2", "agentType": "preparer", "name": "preparer"},
    ])

    write_task(tasks_dir, team, {
        "id": "A", "subject": "preparer: TEACHBACK for diag-feature",
        "status": "completed", "owner": "preparer",
        "blocks": [], "blockedBy": [],
    })

    os.environ["HOME"] = str(tmp_root)
    n = trace_count(team, "Only A=completed, nothing else", tasks_dir)
    print(f"\nExpected: count = 0; teardown FIRES (legitimate)")
    print(f"Actual: count = {n}")

    out = fire_teardown_hook(team, "A", "TaskCompleted", tasks_dir)
    print(f"\nteardown_request_emitter subprocess:")
    print(f"  exit={out['exit']}")
    print(f"  stdout={out['stdout']!r}")
    teardown_fired = is_teardown_emitted(out['stdout'])
    print(f"  TEARDOWN EMITTED? {teardown_fired}  (expected True)")


def variant_cross_teammate_concurrent(tmp_root: Path):
    """Reproduces the actual pact-450f3d63 pattern:
       Teammate X completes their work; Teammate Y has a different in-progress task.
       Does the teardown fire when X completes while Y is still active?
       This tests whether the bug is actually about cross-teammate scenarios.
    """
    print("\n" + "=" * 70)
    print("VARIANT V4 (pact-450f3d63 pattern): X completes while Y is active")
    print("=" * 70)
    teams_dir = tmp_root / ".claude" / "teams"
    tasks_dir = tmp_root / ".claude" / "tasks"
    team = "pact-diag-v4"

    make_team_config(teams_dir, team, "lead-agent-id-1", [
        {"agentId": "lead-agent-id-1", "agentType": "team-lead", "name": "team-lead"},
        {"agentId": "agent-id-X", "agentType": "preparer", "name": "preparer"},
        {"agentId": "agent-id-Y", "agentType": "architect", "name": "architect"},
    ])

    # X's just-completed task
    write_task(tasks_dir, team, {
        "id": "X1", "subject": "preparer: do thing",
        "status": "completed", "owner": "preparer",
        "blocks": [], "blockedBy": [],
    })
    # Y has an in_progress task
    write_task(tasks_dir, team, {
        "id": "Y1", "subject": "architect: design thing",
        "status": "in_progress", "owner": "architect",
        "blocks": [], "blockedBy": [],
    })

    os.environ["HOME"] = str(tmp_root)
    n = trace_count(team, "X=completed, Y=in_progress (cross-teammate)", tasks_dir)
    print(f"\nExpected: count = 1 (Y counts); teardown SUPPRESSED")
    print(f"Actual: count = {n}")

    out = fire_teardown_hook(team, "X1", "TaskCompleted", tasks_dir)
    teardown_fired = is_teardown_emitted(out['stdout'])
    print(f"  TEARDOWN EMITTED? {teardown_fired}  (expected False)")


def variant_a_completes_b_unclaimed_no_owner_yet(tmp_root: Path):
    """The strict #842 reading: A is completed; B has NO owner because the lead
       hasn't wired step 4 yet. Tests whether the teachback-gated dispatch
       can produce a 0-count if the work-task is created BEFORE owner is wired.
       Per orchestrate.md, the canonical sequence sets owner on B in step 4
       which can land AFTER A's status=completed in a rare ordering.
    """
    print("\n" + "=" * 70)
    print("VARIANT V5: A completed BEFORE B's owner-TaskUpdate landed")
    print("=" * 70)
    teams_dir = tmp_root / ".claude" / "teams"
    tasks_dir = tmp_root / ".claude" / "tasks"
    team = "pact-diag-v5"

    make_team_config(teams_dir, team, "lead-agent-id-1", [
        {"agentId": "lead-agent-id-1", "agentType": "team-lead", "name": "team-lead"},
        {"agentId": "agent-id-X", "agentType": "preparer", "name": "preparer"},
    ])

    write_task(tasks_dir, team, {
        "id": "A", "subject": "preparer: TEACHBACK",
        "status": "completed", "owner": "preparer",
        "blocks": ["B"], "blockedBy": [],
    })
    write_task(tasks_dir, team, {
        "id": "B", "subject": "preparer: do thing",
        "status": "pending", "owner": "",       # owner empty string
        "blocks": [], "blockedBy": ["A"],
    })

    os.environ["HOME"] = str(tmp_root)
    n = trace_count(team, "A=completed, B=pending+owner=''(empty)", tasks_dir)
    out = fire_teardown_hook(team, "A", "TaskCompleted", tasks_dir)
    teardown_fired = is_teardown_emitted(out['stdout'])
    print(f"  TEARDOWN EMITTED? {teardown_fired}")


def variant_h1_lead_owned_umbrella(tmp_root: Path):
    """H1 variant: an umbrella task exists with owner=team-lead, plus a
       completed teammate task. team-lead is filtered out by classification.is_lead,
       so umbrella tasks should NOT count. Verifies that the team-lead owner
       check is not a discrimination source for the bug.
    """
    print("\n" + "=" * 70)
    print("VARIANT V6: team-lead-owned umbrella + completed teammate task")
    print("=" * 70)
    teams_dir = tmp_root / ".claude" / "teams"
    tasks_dir = tmp_root / ".claude" / "tasks"
    team = "pact-diag-v6"
    make_team_config(teams_dir, team, "lead-agent-id-1", [
        {"agentId": "lead-agent-id-1", "agentType": "team-lead", "name": "team-lead"},
        {"agentId": "agent-id-X", "agentType": "preparer", "name": "preparer"},
    ])

    write_task(tasks_dir, team, {
        "id": "U1", "subject": "Feature: do thing", "status": "in_progress",
        "owner": "team-lead", "blocks": [], "blockedBy": [],
    })
    write_task(tasks_dir, team, {
        "id": "T1", "subject": "preparer: do thing", "status": "completed",
        "owner": "preparer", "blocks": [], "blockedBy": [],
    })
    os.environ["HOME"] = str(tmp_root)
    n = trace_count(team, "U1=lead-owned in_progress + T1=completed teammate", tasks_dir)
    out = fire_teardown_hook(team, "T1", "TaskCompleted", tasks_dir)
    fired = is_teardown_emitted(out['stdout'])
    print(f"  TEARDOWN EMITTED? {fired}  (expected True — lead-owned umbrella does NOT count)")


def variant_session_journal_pre_existing(tmp_root: Path):
    """Replay the pact-450f3d63 session-journal teardown events to
       count how many of them are H4-pattern (count=0 fires)."""
    # Hard-code real HOME; the harness has overridden HOME for hook subprocesses
    real_home = Path("/Users/mj")
    journal_path = real_home / ".claude" / "pact-sessions" / "PACT-prompt" / "450f3d63-178b-4296-8e68-3fc36961bcaa" / "session-journal.jsonl"
    if not journal_path.exists():
        print(f"\n[V7 SKIP] pact-450f3d63 journal not found at {journal_path}")
        return
    print("\n" + "=" * 70)
    print(f"VARIANT V7: replay pact-450f3d63 session journal teardown events")
    print("=" * 70)
    tier_counts = {"1": 0, "2": 0}
    tier1_reasons = {}
    with open(journal_path) as f:
        for line in f:
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("type") != "teardown_request":
                continue
            tier = str(e.get("tier", ""))
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            if tier == "1":
                r = e.get("reason", "")
                tier1_reasons[r] = tier1_reasons.get(r, 0) + 1
    print(f"  Tier-1 teardown events: {tier_counts.get('1', 0)} (primary teardown_request_emitter)")
    print(f"  Tier-2 teardown events: {tier_counts.get('2', 0)} (wake_inbox_drain)")
    print(f"  Tier-1 reasons: {tier1_reasons}")


def main():
    with tempfile.TemporaryDirectory(prefix="pact-diag-842-") as td:
        tmp_root = Path(td)
        print(f"# Diagnostic harness for #842 — tmp_root={tmp_root}")
        print(f"# _ACTIVE_STATUSES = {_ACTIVE_STATUSES}")
        variant_canonical_teachback_handoff(tmp_root)
        variant_h4_unowned_B(tmp_root)
        variant_a_completes_b_unclaimed_no_owner_yet(tmp_root)
        variant_terminal_only_no_B(tmp_root)
        variant_cross_teammate_concurrent(tmp_root)
        variant_h1_lead_owned_umbrella(tmp_root)
        variant_session_journal_pre_existing(tmp_root)
        print("\n" + "=" * 70)
        print("END of diagnostic harness")
        print("=" * 70)


if __name__ == "__main__":
    main()
