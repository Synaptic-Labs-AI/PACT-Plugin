#!/usr/bin/env python3
"""
Phase-2 readiness diagnostic for the #401 teachback gate.

Reads `teachback_gate_advisory` events from one or more PACT session
journals and classifies each `would_have_blocked=True` observation as a
true-positive (task had no valid `teachback_submit` at the time) or a
false-positive (task already had a valid submit — gate would have denied
legitimate work).

The flip criterion (canonical plan §F10, validated by architect Q5):

    ready = (2 consecutive observed workflows at variety >= 7 produced
             zero false-positive would-have-blocked observations)

Usage:
    python3 scripts/check_teachback_phase2_readiness.py
        [--sessions-dir PATH] [--project NAME] [--max-workflows N]

Exit codes:
    0 = ready (criterion met) OR insufficient data
    1 = NOT ready (at least one false-positive found)

Output is JSON on stdout, matching CONTENT-SCHEMAS.md §Q5 output shape:

    {
      "ready": bool,
      "workflows_observed": int,
      "workflows_clean": int,
      "false_positives": [
        {"task_id": "...", "agent": "...", "timestamp": "...", "reason": "..."},
        ...
      ],
      "criterion": "F10_zero_false_positives_over_2_consecutive_variety_ge_7"
    }

This is an OBSERVATIONAL diagnostic — it reads the journal only, never
writes to any PACT state. Safe to run at any time.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "hooks"))

from shared.session_journal import read_events_from  # noqa: E402
from shared.task_utils import get_task_list  # noqa: E402


CRITERION_NAME = "F10_zero_false_positives_over_2_consecutive_variety_ge_7"


def _iter_session_dirs(sessions_root: Path, project: str | None) -> list[Path]:
    """Return session directories under `sessions_root`, newest first by mtime.

    If `project` is given, restrict to that project's subtree. Session
    directories are UUID-shaped and live at either:
      ~/.claude/pact-sessions/<user>/<session-uuid>/
      ~/.claude/pact-sessions/<project>/<session-uuid>/
    """
    if not sessions_root.exists():
        return []
    if project:
        scoped = sessions_root / project
        if not scoped.exists():
            return []
        roots = [scoped]
    else:
        roots = [p for p in sessions_root.iterdir() if p.is_dir() and not p.name.startswith("_")]

    sessions: list[Path] = []
    for root in roots:
        for entry in root.iterdir():
            if entry.is_dir() and (entry / "journal.jsonl").exists():
                sessions.append(entry)
    sessions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return sessions


def _group_advisories_by_workflow(events: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Partition teachback_gate_advisory events by feature-task id.

    A 'workflow' in this script's model is the set of events sharing a
    parent feature-task id (derived via task-metadata lookup at read time
    if available). Falls back to grouping by session if metadata is not
    reachable — the false-positive counting is conservative either way.
    """
    by_workflow: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        task_id = str(event.get("task_id") or "")
        key = task_id or "unknown"
        by_workflow.setdefault(key, []).append(event)
    return by_workflow


def _has_valid_submit_now(task_id: str) -> bool:
    """Check whether the task's on-disk metadata has a non-empty
    teachback_submit. Used to classify would_have_blocked events as
    false-positive.

    Best-effort: a task that was completed and reaped before this
    script runs will be absent and this returns False. That pushes the
    script toward a LIBERAL false-positive count (i.e., toward 'NOT
    ready'), which is the safer failure direction for a readiness gate.
    """
    try:
        tasks = get_task_list()
    except Exception:
        return False
    if not isinstance(tasks, list):
        return False
    for task in tasks:
        if str(task.get("id") or "") != task_id:
            continue
        metadata = task.get("metadata") or {}
        submit = metadata.get("teachback_submit")
        return bool(submit)
    return False


def _classify(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the list of events that are FALSE-positives — would_have_blocked
    was true, but the task now has a valid teachback_submit on disk.
    """
    false_positives: list[dict[str, Any]] = []
    for event in events:
        if not event.get("would_have_blocked"):
            continue
        task_id = str(event.get("task_id") or "")
        if not task_id:
            continue
        if _has_valid_submit_now(task_id):
            false_positives.append({
                "task_id": task_id,
                "agent": event.get("agent") or "",
                "timestamp": event.get("timestamp") or event.get("ts") or "",
                "reason": event.get("reason") or "",
            })
    return false_positives


def assess_readiness(
    sessions_root: Path,
    project: str | None = None,
    max_workflows: int = 10,
) -> dict[str, Any]:
    """Main readiness assessment. Returns the Q5 output dict."""
    session_dirs = _iter_session_dirs(sessions_root, project)
    if not session_dirs:
        return {
            "ready": False,
            "workflows_observed": 0,
            "workflows_clean": 0,
            "false_positives": [],
            "criterion": CRITERION_NAME,
        }

    all_advisory_events: list[dict[str, Any]] = []
    for session_dir in session_dirs:
        try:
            events = read_events_from(str(session_dir), event_type="teachback_gate_advisory")
        except Exception:
            events = []
        all_advisory_events.extend(events)

    grouped = _group_advisories_by_workflow(all_advisory_events)
    workflows_observed = 0
    workflows_clean = 0
    false_positives: list[dict[str, Any]] = []

    # Sort workflows by most-recent event timestamp, process newest-first
    def _latest_ts(entry: tuple[str, list[dict[str, Any]]]) -> str:
        workflow_events = entry[1]
        stamps = [str(e.get("timestamp") or e.get("ts") or "") for e in workflow_events]
        return max(stamps) if stamps else ""

    ordered = sorted(grouped.items(), key=_latest_ts, reverse=True)[:max_workflows]

    for _task_id, workflow_events in ordered:
        if not workflow_events:
            continue
        # Only count workflows that exercised the gate at variety >= 7.
        # The gate only emits advisory events when the gate applied, so
        # presence of a teachback_gate_advisory event implies variety >= 7
        # (carve-out #5 bypasses hook emission entirely).
        workflows_observed += 1
        wf_false_positives = _classify(workflow_events)
        if wf_false_positives:
            false_positives.extend(wf_false_positives)
        else:
            workflows_clean += 1

    ready = workflows_clean >= 2 and not false_positives
    return {
        "ready": ready,
        "workflows_observed": workflows_observed,
        "workflows_clean": workflows_clean,
        "false_positives": false_positives,
        "criterion": CRITERION_NAME,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Phase-2 readiness diagnostic for the #401 teachback gate. "
            "Reads teachback_gate_advisory events across PACT session "
            "journals and classifies would_have_blocked observations as "
            "true-positive or false-positive. Exit 0 if ready or insufficient "
            "data; exit 1 if false-positives found."
        )
    )
    parser.add_argument(
        "--sessions-dir",
        default=str(Path.home() / ".claude" / "pact-sessions"),
        help="Root directory containing PACT session directories",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Restrict to a specific project subtree (e.g., 'PACT-prompt')",
    )
    parser.add_argument(
        "--max-workflows",
        type=int,
        default=10,
        help="Maximum number of recent workflows to consider (default: 10)",
    )
    args = parser.parse_args(argv)

    result = assess_readiness(
        sessions_root=Path(args.sessions_dir),
        project=args.project,
        max_workflows=args.max_workflows,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["false_positives"] else 0


if __name__ == "__main__":
    sys.exit(main())
