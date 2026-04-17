#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/precompact_state_reminder.py
Summary: PreCompact hook that gathers mechanical state from disk and emits
         custom_instructions for the compaction model. Per #444 Tertiary,
         the previously-emitted systemMessage ("Compaction imminent...") was
         removed — it fired as part of the compaction event, too late to be
         actioned before the context cut. custom_instructions still reaches
         the summary-generating model, which is the correct channel for
         state-preservation guidance.
Used by: hooks.json PreCompact hook

Reads task files and team config to build a concrete state snapshot:
- Task counts by status (completed, in_progress, pending)
- Active teammate names from team config
- Feature task subject and ID (highest-level non-phase task)
- Current phase (from Phase:-prefixed in_progress tasks)
- Variety score (from feature task metadata)
- Team name(s) from team directories

Emits: custom_instructions (injected into compaction model to guide preservation)

This is a non-blocking reminder (always exits 0), not a gate.

Input: JSON from stdin (PreCompact event data)
Output: JSON with custom_instructions on stdout
"""

import json
import sys
from typing import Any

from shared.error_output import hook_error_json
from shared.session_state import summarize_session_state


# ---------------------------------------------------------------------------
# Output builders
# ---------------------------------------------------------------------------


def _extract_variety_total(variety: Any) -> int | None:
    """
    Normalize the `variety_score` field (opaque passthrough from the
    journal) into a scalar suitable for f-string rendering.

    The journal writer stores variety as a dict
    `{"novelty": N, "scope": N, "uncertainty": N, "risk": N,
    "total": N}`. The module-boundary contract is "opaque dict";
    consumers that want a clean scalar render call this helper. Bool
    is rejected because it subclasses int — a `True`/`False` in the
    dict would otherwise surface as a variety score of 1/0.

    Returns `None` if no usable total is present (caller should omit
    the variety line from its rendered output).
    """
    if isinstance(variety, dict):
        total = variety.get("total")
        if isinstance(total, int) and not isinstance(total, bool):
            return total
        return None
    if isinstance(variety, int) and not isinstance(variety, bool):
        # Defensive: a legacy or test-fixture payload may pass a bare
        # int. Render it as-is rather than dropping it.
        return variety
    return None


def build_custom_instructions(state: dict) -> str:
    """
    Build custom_instructions for the compaction model from the 10-key
    state dict returned by `summarize_session_state`.

    These tell the compaction model what critical context to preserve.
    """
    lines = ["CRITICAL CONTEXT TO PRESERVE:"]

    feature = state.get("feature_subject")
    feature_id = state.get("feature_id")
    if feature:
        id_str = f" (task #{feature_id})" if feature_id else ""
        lines.append(f"- Feature: {feature}{id_str}")

    phase = state.get("current_phase")
    if phase:
        lines.append(f"- Current phase: {phase}")
    else:
        lines.append("- Current phase: unknown")

    teammates = state.get("teammates", [])
    if teammates:
        lines.append(f"- Active agents: {', '.join(teammates)}")
    else:
        lines.append("- Active agents: none found")

    variety_total = _extract_variety_total(state.get("variety_score"))
    if variety_total is not None:
        lines.append(f"- Variety score: {variety_total}")

    team_names = state.get("team_names", [])
    if team_names:
        lines.append(f"- Team name: {', '.join(team_names)}")

    lines.append("Preserve task IDs and agent names exactly.")

    return "\n".join(lines)


def build_hook_output(
    tasks_base_dir: str | None = None,
    teams_base_dir: str | None = None,
) -> dict:
    """
    Build the complete hook output with custom_instructions only.

    Per #444 Tertiary: no systemMessage emission. The previously-emitted
    "Compaction imminent" message fired as part of the compaction event,
    too late to be actioned before the context cut. custom_instructions
    still reaches the summary-generating model and is the right channel
    for state-preservation guidance.

    Returns dict ready for json.dumps().
    """
    state = summarize_session_state(
        tasks_base_dir=tasks_base_dir,
        teams_base_dir=teams_base_dir,
    )

    custom_instructions = build_custom_instructions(state)

    return {
        "custom_instructions": custom_instructions,
    }


def main():
    try:
        # Consume stdin (PreCompact may provide transcript_path, etc.)
        try:
            json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            pass

        output = build_hook_output()
        print(json.dumps(output))
        sys.exit(0)

    except Exception as e:
        # Fail open — never block compaction
        print(f"Hook warning (precompact_state_reminder): {e}", file=sys.stderr)
        print(hook_error_json("precompact_state_reminder", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
