#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/teammate_idle.py
Summary: TeammateIdle hook with two responsibilities: stall detection and idle
         cleanup. Detects agents that go idle without completing or reporting
         blockers, and tracks consecutive idle events for completed agents to
         suggest or force shutdown.
Used by: hooks.json TeammateIdle hook

Stall detection: If a teammate goes idle while their task is still in_progress
and no HANDOFF or BLOCKER was sent, emit a systemMessage alerting the
orchestrator to consider /PACT:imPACT.

Idle cleanup: Track consecutive idle events for completed agents. After 3,
suggest shutdown. After 5, request shutdown via shutdown_request.

Input: JSON from stdin with teammate_name, team_name
Output: JSON with systemMessage (stall alert or shutdown suggestion)
"""

import json
import os
import sys
from pathlib import Path

try:
    import fcntl
    HAS_FLOCK = True
except ImportError:
    HAS_FLOCK = False

# Add hooks directory to path for shared package imports
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

from shared.task_utils import get_task_list


IDLE_PREAMBLE = "[System idle notification — no response needed] "

IDLE_SUGGEST_THRESHOLD = 3
IDLE_FORCE_THRESHOLD = 5


def find_teammate_task(
    tasks: list[dict],
    teammate_name: str,
) -> dict | None:
    """
    Find the most recent task owned by this teammate.

    Looks for tasks with owner matching teammate_name. Returns the
    in_progress task if one exists, otherwise the most recently completed one.

    Args:
        tasks: List of all tasks from get_task_list()
        teammate_name: Name of the idle teammate

    Returns:
        Task dict, or None if no task found for this teammate
    """
    in_progress = None
    completed = None

    for task in tasks:
        owner = task.get("owner", "")
        if owner != teammate_name:
            continue

        status = task.get("status", "")
        if status == "in_progress":
            in_progress = task
        elif status == "completed":
            # Keep the highest-ID completed task (most recent)
            # Task IDs are numeric strings — compare as int to avoid
            # lexicographic errors (e.g., "3" > "20" in string comparison)
            try:
                task_id_num = int(task.get("id", "0"))
                completed_id_num = int(completed.get("id", "0")) if completed else -1
            except (ValueError, TypeError):
                task_id_num = 0
                completed_id_num = -1 if completed is None else 0
            if completed is None or task_id_num > completed_id_num:
                completed = task

    return in_progress or completed


def detect_stall(
    tasks: list[dict],
    teammate_name: str,
) -> str | None:
    """
    Check if a teammate has stalled (idle with in_progress task).

    A stall is detected when:
    - The teammate has a task with status in_progress
    - They went idle (TeammateIdle event fired) without completing

    Args:
        tasks: List of all tasks
        teammate_name: Name of the idle teammate

    Returns:
        Warning message if stall detected, None otherwise
    """
    task = find_teammate_task(tasks, teammate_name)
    if not task:
        return None

    if task.get("status") != "in_progress":
        return None

    # Check if this is a stalled task (not a signal/blocker task)
    metadata = task.get("metadata", {})
    if metadata.get("type") in ("blocker", "algedonic"):
        return None
    if metadata.get("stalled"):
        # Already marked as stalled — don't re-alert
        return None

    task_id = task.get("id", "?")
    subject = task.get("subject", "unknown")

    return (
        f"Teammate '{teammate_name}' went idle without completing task "
        f"#{task_id} ({subject}). Possible stall. "
        f"Consider /PACT:imPACT to triage."
    )


def read_idle_counts(idle_counts_path: str) -> dict:
    """
    Read the idle counts tracking file.

    Args:
        idle_counts_path: Path to the idle_counts.json file

    Returns:
        Dict mapping teammate_name to consecutive idle count
    """
    path = Path(idle_counts_path)
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return {}


def write_idle_counts(idle_counts_path: str, counts: dict) -> None:
    """
    Write the idle counts tracking file with file locking.

    Args:
        idle_counts_path: Path to the idle_counts.json file
        counts: Dict mapping teammate_name to consecutive idle count
    """
    path = Path(idle_counts_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if HAS_FLOCK:
        # Open for append to avoid truncation before lock is acquired,
        # then lock, truncate, and write atomically
        with open(path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                f.truncate()
                f.write(json.dumps(counts))
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    else:
        path.write_text(json.dumps(counts), encoding="utf-8")


def _atomic_update_idle_counts(
    idle_counts_path: str,
    mutator: "Callable[[dict], dict]",
) -> dict:
    """
    Atomically read, mutate, and write the idle counts file under a single lock.

    This prevents TOCTOU races where two concurrent TeammateIdle events both
    read stale state before either writes, causing one update to be lost.

    On platforms without flock (Windows), falls back to non-atomic read+write
    which is acceptable since concurrent hook invocations are unlikely there.

    Args:
        idle_counts_path: Path to the idle_counts.json file
        mutator: Callable that receives the current counts dict and returns
                 the updated counts dict to write back

    Returns:
        The updated counts dict after mutation
    """
    path = Path(idle_counts_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if HAS_FLOCK:
        with open(path, "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                content = f.read()
                try:
                    counts = json.loads(content) if content.strip() else {}
                except json.JSONDecodeError:
                    counts = {}

                counts = mutator(counts)

                f.seek(0)
                f.truncate()
                f.write(json.dumps(counts))
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    else:
        # Fallback: non-atomic read+write (no flock available)
        counts = read_idle_counts(idle_counts_path)
        counts = mutator(counts)
        path.write_text(json.dumps(counts), encoding="utf-8")

    return counts


def check_idle_cleanup(
    tasks: list[dict],
    teammate_name: str,
    idle_counts_path: str,
) -> tuple[str | None, bool]:
    """
    Track idle counts for completed agents and determine cleanup action.

    Only counts idles for teammates whose task is completed (not stalled agents,
    which need triage, not shutdown). Resets count when the teammate's task
    changes (detected via last_seen_task_id).

    The idle counts file stores per-teammate entries as:
        {teammate_name: {"count": N, "task_id": "X"}}

    Args:
        tasks: List of all tasks
        teammate_name: Name of the idle teammate
        idle_counts_path: Path to the idle_counts.json file

    Returns:
        Tuple of (message, should_force_shutdown):
        - message: systemMessage text or None
        - should_force_shutdown: True if shutdown_request should be sent
    """
    task = find_teammate_task(tasks, teammate_name)

    # Only track idles for completed tasks
    if not task or task.get("status") != "completed":
        # Reset count if agent no longer has a completed task (got new work)
        def _remove(counts: dict) -> dict:
            counts.pop(teammate_name, None)
            return counts
        _atomic_update_idle_counts(idle_counts_path, _remove)
        return None, False

    # Don't count stalled agents for idle cleanup — they need triage
    metadata = task.get("metadata", {})
    if metadata.get("stalled") or metadata.get("terminated"):
        return None, False

    current_task_id = task.get("id", "")

    # Atomically read-modify-write the idle count to prevent TOCTOU races
    # between concurrent TeammateIdle events for different agents.
    result = {"count": 0}

    def _increment(counts: dict) -> dict:
        entry = counts.get(teammate_name, {})

        # Migrate legacy format: plain int -> structured dict
        if isinstance(entry, int):
            entry = {"count": entry, "task_id": ""}

        # Reset count if the teammate's task changed (reassigned to new work)
        last_task_id = entry.get("task_id", "")
        if last_task_id and last_task_id != current_task_id:
            entry = {"count": 0, "task_id": current_task_id}

        # Increment idle count
        entry["count"] = entry.get("count", 0) + 1
        entry["task_id"] = current_task_id
        counts[teammate_name] = entry

        # Capture the count for the caller via closure
        result["count"] = entry["count"]
        return counts

    _atomic_update_idle_counts(idle_counts_path, _increment)
    current = result["count"]

    if current >= IDLE_FORCE_THRESHOLD:
        return (
            f"Teammate '{teammate_name}' has been idle for {current} consecutive "
            f"events with no new work. Sending shutdown request."
        ), True

    if current >= IDLE_SUGGEST_THRESHOLD:
        return (
            f"Teammate '{teammate_name}' has been idle for {current} consecutive "
            f"events with no new work. Consider shutting down to free resources."
        ), False

    return None, False


def reset_idle_count(teammate_name: str, idle_counts_path: str) -> None:
    """
    Reset a teammate's idle count (e.g., when they receive new work).

    Args:
        teammate_name: Name of the teammate
        idle_counts_path: Path to the idle_counts.json file
    """
    def _remove(counts: dict) -> dict:
        counts.pop(teammate_name, None)
        return counts
    _atomic_update_idle_counts(idle_counts_path, _remove)


def main():
    try:
        team_name = os.environ.get("CLAUDE_CODE_TEAM_NAME", "").lower()
        if not team_name:
            sys.exit(0)

        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            sys.exit(0)

        teammate_name = input_data.get("teammate_name", "")
        if not teammate_name:
            sys.exit(0)

        tasks = get_task_list()
        if not tasks:
            sys.exit(0)

        idle_counts_path = str(
            Path.home() / ".claude" / "teams" / team_name / "idle_counts.json"
        )

        messages = []
        should_shutdown = False

        # Check for stall (in_progress task + idle)
        stall_msg = detect_stall(tasks, teammate_name)
        if stall_msg:
            messages.append(stall_msg)
        else:
            # Only check idle cleanup if not stalled
            # (stalled agents need triage, not shutdown)
            cleanup_msg, should_shutdown = check_idle_cleanup(
                tasks, teammate_name, idle_counts_path
            )
            if cleanup_msg:
                messages.append(cleanup_msg)

        if messages:
            if should_shutdown:
                # Hooks cannot call SendMessage directly. Instruct the orchestrator
                # to send a shutdown_request via systemMessage.
                messages.append(
                    f"ACTION REQUIRED: Send shutdown_request to '{teammate_name}' "
                    f"via SendMessage(type=\"shutdown_request\", recipient=\"{teammate_name}\")."
                )

            output = {"systemMessage": IDLE_PREAMBLE + " | ".join(messages)}
            print(json.dumps(output))

        sys.exit(0)

    except Exception as e:
        # Don't block on errors — just warn and exit cleanly
        print(f"Hook warning (teammate_idle): {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
