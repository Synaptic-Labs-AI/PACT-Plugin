"""
Location: pact-plugin/hooks/shared/task_scanner.py
Summary: Shared task file scanner for PACT hooks that need cross-team task data.
Used by: precompact_state_reminder.py, postcompact_verify.py

Scans all team directories under ~/.claude/tasks/ and returns parsed task dicts.
Unlike task_utils.get_task_list() which is session-scoped (uses CLAUDE_SESSION_ID),
this scanner reads ALL team directories — needed by compaction hooks that operate
across the full task tree.

Fail-open: returns empty list on any error.
"""

import json
from pathlib import Path
from typing import Any


def scan_all_tasks(
    tasks_base_dir: str | None = None,
) -> list[dict[str, Any]]:
    """
    Scan all team task directories and return parsed task dicts.

    Each returned dict has at least: id, subject, status, metadata.
    The 'id' field falls back to the filename stem if not present in JSON.

    Args:
        tasks_base_dir: Override for ~/.claude/tasks/ (dependency injection
                        for testing). Defaults to ~/.claude/tasks/.

    Returns:
        List of task dicts. Empty list on any error or if no tasks found.
    """
    if tasks_base_dir is None:
        tasks_base_dir = str(Path.home() / ".claude" / "tasks")

    base = Path(tasks_base_dir)
    if not base.exists():
        return []

    tasks = []
    try:
        for team_dir in base.iterdir():
            if not team_dir.is_dir():
                continue
            for task_file in team_dir.iterdir():
                if not task_file.name.endswith(".json"):
                    continue
                try:
                    data = json.loads(task_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue

                # Normalize: ensure 'id' is always present
                if "id" not in data:
                    data["id"] = task_file.stem

                tasks.append(data)
    except OSError:
        pass  # Fail-open: return whatever we collected so far

    return tasks
