#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/precompact_state_reminder.py
Summary: PreCompact hook that gathers mechanical state from disk and instructs
         the orchestrator to persist ephemeral context before compaction.
Used by: hooks.json PreCompact hook

Reads task files and team config to build a concrete state snapshot:
- Task counts by status (completed, in_progress, pending)
- Active teammate names from team config
- Feature task subject (highest-level non-phase task)

Then emits a systemMessage with that snapshot plus instructions for the
orchestrator to (a) TaskCreate a brain dump task and (b) SendMessage to
the secretary with what only exists in the context window.

This is a non-blocking reminder (always exits 0), not a gate.

Input: JSON from stdin (PreCompact event data)
Output: JSON systemMessage on stdout
"""

import json
import sys
from pathlib import Path

from shared.error_output import hook_error_json


# ---------------------------------------------------------------------------
# Disk state gathering — all functions fail open (return defaults on error)
# ---------------------------------------------------------------------------


def _gather_task_counts(
    tasks_base_dir: str | None = None,
) -> dict:
    """
    Scan all team task directories for task counts by status.

    Returns dict with keys: completed, in_progress, pending, total,
    feature_subject (str or None).
    """
    counts = {
        "completed": 0,
        "in_progress": 0,
        "pending": 0,
        "total": 0,
        "feature_subject": None,
    }

    if tasks_base_dir is None:
        tasks_base_dir = str(Path.home() / ".claude" / "tasks")

    base = Path(tasks_base_dir)
    if not base.exists():
        return counts

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

                status = data.get("status", "pending")
                if status in counts:
                    counts[status] += 1
                counts["total"] += 1

                # Heuristic: feature task = in_progress task without
                # "Phase:" or "BLOCKER:" or "ALERT:" or "HALT:" prefix
                if (
                    status == "in_progress"
                    and counts["feature_subject"] is None
                ):
                    subject = data.get("subject", "")
                    if subject and not any(
                        subject.startswith(prefix)
                        for prefix in (
                            "Phase:",
                            "BLOCKER:",
                            "ALERT:",
                            "HALT:",
                        )
                    ):
                        counts["feature_subject"] = subject
    except OSError:
        pass  # Can't read base dir — return whatever we have

    return counts


def _gather_active_teammates(
    teams_base_dir: str | None = None,
) -> list[str]:
    """
    Read team config files for active teammate names.

    Returns list of teammate name strings.
    """
    if teams_base_dir is None:
        teams_base_dir = str(Path.home() / ".claude" / "teams")

    base = Path(teams_base_dir)
    if not base.exists():
        return []

    names: list[str] = []
    try:
        for team_dir in base.iterdir():
            if not team_dir.is_dir():
                continue
            config_path = team_dir / "config.json"
            if not config_path.exists():
                continue
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            # config.json has "members" list with "name" fields
            members = data.get("members", [])
            for member in members:
                name = member.get("name", "") if isinstance(member, dict) else ""
                if name:
                    names.append(name)
    except OSError:
        pass  # Can't read teams dir — return empty

    return names


def _build_state_summary(
    task_counts: dict,
    active_teammates: list[str],
) -> str:
    """Build a human-readable state summary from gathered data."""
    lines = []

    # Task counts
    total = task_counts["total"]
    if total > 0:
        lines.append(
            f"Tasks: {task_counts['completed']} completed, "
            f"{task_counts['in_progress']} in_progress, "
            f"{task_counts['pending']} pending "
            f"(total: {total})"
        )
    else:
        lines.append("Tasks: none found on disk")

    # Feature subject
    feature = task_counts.get("feature_subject")
    if feature:
        lines.append(f"Feature: {feature}")

    # Active teammates
    if active_teammates:
        lines.append(f"Active teammates: {', '.join(active_teammates)}")
    else:
        lines.append("Active teammates: none found")

    return "\n".join(lines)


BRAIN_DUMP_INSTRUCTIONS = (
    "IMPORTANT — Compaction will erase your context window. "
    "Before it happens, do BOTH of the following:\n"
    "\n"
    "1. TaskCreate a 'Pre-compaction state dump' task and immediately "
    "complete it with metadata containing: current phase, active blockers, "
    "key decisions made this session, and anything NOT captured in the "
    "mechanical state above.\n"
    "\n"
    "2. SendMessage to the secretary: '[lead→secretary] Pre-compaction "
    "brain dump: {your ephemeral context — decisions in flight, "
    "reasoning not yet in task metadata, user preferences discovered "
    "this session}'"
)


def build_message(
    tasks_base_dir: str | None = None,
    teams_base_dir: str | None = None,
) -> str:
    """
    Build the full precompact systemMessage.

    Separated from main() for testability. Accepts optional dir overrides.
    """
    task_counts = _gather_task_counts(tasks_base_dir)
    active_teammates = _gather_active_teammates(teams_base_dir)
    state_summary = _build_state_summary(task_counts, active_teammates)

    return (
        f"Compaction imminent — mechanical state snapshot:\n"
        f"\n"
        f"{state_summary}\n"
        f"\n"
        f"{BRAIN_DUMP_INSTRUCTIONS}"
    )


def main():
    try:
        # Consume stdin (PreCompact may provide transcript_path, etc.)
        try:
            json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            pass

        message = build_message()
        print(json.dumps({"systemMessage": message}))
        sys.exit(0)

    except Exception as e:
        # Fail open — never block compaction
        print(f"Hook warning (precompact_state_reminder): {e}", file=sys.stderr)
        print(hook_error_json("precompact_state_reminder", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
