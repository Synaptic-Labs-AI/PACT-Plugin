#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/stop_audit.py
Summary: Stop hook that audits session state including Tasks and uncommitted changes.
Used by: Claude Code settings.json Stop hook

Audits for:
1. Orphaned in_progress Tasks (workflow may be incomplete)
2. Uncommitted file changes (git working tree)

This replaces the older stop_audit.sh shell script with Task system integration.

Input: JSON from stdin with session context
Output: JSON with `systemMessage` for audit warnings if needed
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# Add hooks directory to path for shared package imports
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

# Import shared Task utilities (DRY - used by multiple hooks)
from shared.task_utils import get_task_list


def audit_tasks(tasks: list[dict[str, Any]]) -> list[str]:
    """
    Audit Tasks for incomplete workflows.

    Checks for:
    - Orphaned in_progress Tasks (agents that didn't complete)
    - Unresolved blocker/algedonic Tasks

    Args:
        tasks: List of all tasks

    Returns:
        List of warning messages
    """
    warnings = []

    in_progress = [t for t in tasks if t.get("status") == "in_progress"]
    pending_blockers = []
    orphaned_agents = []

    for task in in_progress:
        subject = task.get("subject", "")
        metadata = task.get("metadata", {})
        task_type = metadata.get("type", "")

        if task_type in ("blocker", "algedonic"):
            pending_blockers.append(task)
        elif any(subject.lower().startswith(p) for p in (
            "pact-preparer:",
            "pact-architect:",
            "pact-backend-coder:",
            "pact-frontend-coder:",
            "pact-database-engineer:",
            "pact-devops-engineer:",
            "pact-n8n:",
            "pact-test-engineer:",
            "pact-security-engineer:",
            "pact-qa-engineer:",
            "pact-memory-agent:",
        )):
            orphaned_agents.append(task)

    if pending_blockers:
        blocker_subjects = [t.get("subject", "unknown")[:40] for t in pending_blockers]
        warnings.append(
            f"Unresolved blockers ({len(pending_blockers)}): "
            f"{', '.join(blocker_subjects[:3])}"
            + (f" (+{len(blocker_subjects)-3} more)" if len(blocker_subjects) > 3 else "")
        )

    if orphaned_agents:
        agent_subjects = [t.get("subject", "").split(":")[0] for t in orphaned_agents]
        warnings.append(
            f"Agents still in_progress ({len(orphaned_agents)}): "
            f"{', '.join(agent_subjects[:3])}"
            + (f" (+{len(agent_subjects)-3} more)" if len(agent_subjects) > 3 else "")
        )

    # Summary stats
    completed = len([t for t in tasks if t.get("status") == "completed"])
    pending = len([t for t in tasks if t.get("status") == "pending"])

    if in_progress or pending:
        warnings.append(
            f"Task summary: {completed} completed, {len(in_progress)} in_progress, "
            f"{pending} pending"
        )

    return warnings


def audit_git_changes() -> list[str]:
    """
    Audit for uncommitted git changes.

    Returns:
        List of warning messages about uncommitted files
    """
    warnings = []

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode != 0:
            return []  # Not in a git repo or git error

        changes = result.stdout.strip()
        if not changes:
            return []

        # Count changes by type
        lines = changes.split("\n")
        modified = [l for l in lines if l.startswith(" M") or l.startswith("M ")]
        added = [l for l in lines if l.startswith("A ") or l.startswith("??")]
        deleted = [l for l in lines if l.startswith(" D") or l.startswith("D ")]

        parts = []
        if modified:
            parts.append(f"{len(modified)} modified")
        if added:
            parts.append(f"{len(added)} added/untracked")
        if deleted:
            parts.append(f"{len(deleted)} deleted")

        if parts:
            warnings.append(f"Uncommitted changes: {', '.join(parts)}")

            # List first few files
            files = [l[3:].strip() for l in lines[:5]]
            if files:
                file_list = ", ".join(files)
                if len(lines) > 5:
                    file_list += f" (+{len(lines)-5} more)"
                warnings.append(f"Files: {file_list}")

    except subprocess.TimeoutExpired:
        pass
    except FileNotFoundError:
        pass  # git not installed
    except Exception:
        pass

    return warnings


def main():
    """
    Main entry point for the Stop audit hook.

    Audits session state and warns about incomplete workflows or uncommitted changes.
    """
    try:
        # Read input from stdin (may contain transcript or other context)
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            input_data = {}

        warnings = []

        # Audit Tasks for incomplete workflows
        tasks = get_task_list()
        if tasks:
            task_warnings = audit_tasks(tasks)
            warnings.extend(task_warnings)

        # Audit git for uncommitted changes
        git_warnings = audit_git_changes()
        warnings.extend(git_warnings)

        # Output warnings if any
        if warnings:
            output = {
                "systemMessage": "=== PACT Session Audit ===\n" + "\n".join(warnings)
            }
            print(json.dumps(output))

        sys.exit(0)

    except Exception as e:
        # Don't block on errors - just warn
        print(f"Hook warning (stop_audit): {e}", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
