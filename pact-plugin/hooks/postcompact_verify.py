#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/postcompact_verify.py
Summary: PostCompact hook that verifies compaction preserved critical context
         and writes the compact summary to disk for the secretary.
Used by: hooks.json PostCompact hook

After compaction completes:
1. Reads compact_summary from stdin (PostCompact input field)
2. Reads current task state from disk (same pattern as PreCompact)
3. Checks if the compact_summary mentions key items: feature task ID,
   current phase, active agents
4. Writes the compact_summary to ~/.claude/pact-sessions/compact-summary.txt
   (the secretary reads this during post-compaction briefing)
5. Emits systemMessage flagging any gaps

This is a non-blocking verifier (always exits 0), not a gate.

Input: JSON from stdin with compact_summary field
Output: JSON systemMessage on stdout
"""

import json
import os
import sys
from pathlib import Path

from shared.error_output import hook_error_json


# ---------------------------------------------------------------------------
# Compact summary persistence
# ---------------------------------------------------------------------------

COMPACT_SUMMARY_FILENAME = "compact-summary.txt"


def _get_summary_path(
    sessions_base_dir: str | None = None,
) -> Path:
    """Get the path for the compact summary file."""
    if sessions_base_dir is None:
        sessions_base_dir = str(Path.home() / ".claude" / "pact-sessions")
    return Path(sessions_base_dir) / COMPACT_SUMMARY_FILENAME


def write_compact_summary(
    summary: str,
    sessions_base_dir: str | None = None,
) -> bool:
    """
    Write the compact summary to disk for the secretary.

    Creates the directory if needed. Uses secure file permissions (0o600).
    Returns True on success, False on any error.
    """
    try:
        path = _get_summary_path(sessions_base_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Secure write: 0o600 permissions
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, summary.encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------


def _gather_expected_items(
    tasks_base_dir: str | None = None,
    teams_base_dir: str | None = None,
) -> dict:
    """
    Gather key items that should appear in the compact summary.

    Returns dict with keys: feature_id, feature_subject, current_phase,
    agent_names, team_names.
    """
    expected = {
        "feature_id": None,
        "feature_subject": None,
        "current_phase": None,
        "agent_names": [],
        "team_names": [],
    }

    # Gather from tasks
    if tasks_base_dir is None:
        tasks_base_dir = str(Path.home() / ".claude" / "tasks")

    base = Path(tasks_base_dir)
    if base.exists():
        try:
            for team_dir in base.iterdir():
                if not team_dir.is_dir():
                    continue
                for task_file in team_dir.iterdir():
                    if not task_file.name.endswith(".json"):
                        continue
                    try:
                        data = json.loads(
                            task_file.read_text(encoding="utf-8")
                        )
                    except (json.JSONDecodeError, OSError):
                        continue

                    status = data.get("status", "pending")
                    subject = data.get("subject", "")
                    task_id = data.get("id", task_file.stem)

                    if status == "in_progress" and subject.startswith("Phase:"):
                        if expected["current_phase"] is None:
                            expected["current_phase"] = subject

                    if (
                        status == "in_progress"
                        and expected["feature_subject"] is None
                        and subject
                        and not any(
                            subject.startswith(p)
                            for p in ("Phase:", "BLOCKER:", "ALERT:", "HALT:")
                        )
                    ):
                        expected["feature_subject"] = subject
                        expected["feature_id"] = str(task_id)
        except OSError:
            pass

    # Gather from teams
    if teams_base_dir is None:
        teams_base_dir = str(Path.home() / ".claude" / "teams")

    teams_path = Path(teams_base_dir)
    if teams_path.exists():
        try:
            for team_dir in teams_path.iterdir():
                if not team_dir.is_dir():
                    continue
                config_path = team_dir / "config.json"
                if not config_path.exists():
                    continue
                try:
                    data = json.loads(
                        config_path.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, OSError):
                    continue

                team_name = data.get("name", team_dir.name)
                if team_name:
                    expected["team_names"].append(team_name)

                members = data.get("members", [])
                for member in members:
                    name = (
                        member.get("name", "")
                        if isinstance(member, dict)
                        else ""
                    )
                    if name:
                        expected["agent_names"].append(name)
        except OSError:
            pass

    return expected


def check_summary_gaps(
    summary: str,
    expected: dict,
) -> list[str]:
    """
    Check if the compact summary mentions expected key items.

    Returns list of gap descriptions (empty if all items found).
    """
    gaps = []
    summary_lower = summary.lower()

    # Check feature task ID
    feature_id = expected.get("feature_id")
    if feature_id and feature_id not in summary:
        gaps.append(f"feature task ID (#{feature_id})")

    # Check current phase
    phase = expected.get("current_phase")
    if phase and phase.lower() not in summary_lower:
        # Also check for just the phase name after "Phase:"
        phase_name = phase.replace("Phase:", "").strip().lower()
        if phase_name and phase_name not in summary_lower:
            gaps.append(f"current phase ({phase})")

    # Check agent names — flag if none of the active agents are mentioned
    agent_names = expected.get("agent_names", [])
    if agent_names:
        mentioned = any(name.lower() in summary_lower for name in agent_names)
        if not mentioned:
            gaps.append("active agent names")

    return gaps


def build_verification_message(
    summary: str,
    tasks_base_dir: str | None = None,
    teams_base_dir: str | None = None,
) -> str:
    """
    Build the post-compaction verification systemMessage.

    Checks for gaps and returns an appropriate message.
    """
    expected = _gather_expected_items(tasks_base_dir, teams_base_dir)
    gaps = check_summary_gaps(summary, expected)

    if gaps:
        gap_list = ", ".join(gaps)
        return (
            f"Post-compaction: summary may be missing {gap_list}. "
            f"Verify via TaskList."
        )
    return "Post-compaction: critical context preserved in summary."


def main():
    try:
        # Read PostCompact input
        stdin_data = {}
        try:
            stdin_data = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            pass

        compact_summary = ""
        if isinstance(stdin_data, dict):
            compact_summary = stdin_data.get("compact_summary", "")

        # Write summary to disk for secretary
        if compact_summary:
            write_compact_summary(compact_summary)

        # Build verification message
        message = build_verification_message(compact_summary)
        print(json.dumps({"systemMessage": message}))
        sys.exit(0)

    except Exception as e:
        # Fail open — never block post-compaction
        print(
            f"Hook warning (postcompact_verify): {e}", file=sys.stderr
        )
        print(hook_error_json("postcompact_verify", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
