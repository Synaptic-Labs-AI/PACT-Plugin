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

from shared import pact_context
from shared.error_output import hook_error_json
from shared.session_state import summarize_session_state
from shared.teachback_schema import resolve_variety_total
from shared.variety_scorer import MAX_SCORE, MIN_SCORE


# ---------------------------------------------------------------------------
# Output builders
# ---------------------------------------------------------------------------


def _extract_variety_total(variety: Any) -> int | None:
    """
    Normalize the `variety_score` field (opaque passthrough from the
    journal) into a scalar suitable for f-string rendering.

    Render-scoped wrapper over the shared resolve_variety_total resolver.
    Both input shapes are range-gated to [MIN_SCORE, MAX_SCORE] (4..16) — the
    resolver's no-clamp/no-fabricate policy: an out-of-range candidate is
    dropped (line omitted), never clamped or rendered verbatim.
      - A bare int is accepted only when it is a non-bool int IN range; an
        out-of-range bare int (e.g. 99 or 0) drops the line rather than
        rendering an impossible score. Bool is rejected because it
        subclasses int — a True/False would surface as 1/0.
      - The dict path delegates to the shared resolver, so a non-canonical
        stamp (score / dimension-sum) now renders a total instead of
        silently dropping the line, while an out-of-range dict `total`
        (e.g. {"total": 99}) drops the line for the same reason. precompact
        calls the resolver with only `variety` (no metadata) — the variety
        object IS the journal's variety_score render context; there is no
        separate sibling key.

    Returns `None` if no usable in-range total is present (caller should omit
    the variety line from its rendered output).
    """
    if isinstance(variety, int) and not isinstance(variety, bool):
        # Bare-int render affordance, range-gated to match the resolver's
        # [MIN_SCORE, MAX_SCORE] policy (an out-of-range bare int drops the line).
        return variety if MIN_SCORE <= variety <= MAX_SCORE else None
    return resolve_variety_total(variety)  # dict path → canonical + fallbacks


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
        # Parse stdin (PreCompact provides session_id, transcript_path, etc.)
        try:
            input_data = json.load(sys.stdin)
        except (json.JSONDecodeError, ValueError):
            input_data = {}

        # Coerce non-dict JSON (null / [] / scalar) to {} so it degrades
        # identically to the empty-dict path. A non-dict would otherwise make
        # pact_context.init() raise AttributeError on .get() and route to the
        # fail-open except (an error systemMessage); coercing here yields the
        # normal empty-state reminder instead, and keeps non-dict handling
        # consistent with the sibling compaction hook postcompact_archive.py
        # (which guards isinstance(dict)). Production PreCompact stdin is
        # always a JSON object, so this is defense-in-depth.
        if not isinstance(input_data, dict):
            input_data = {}

        # Initialize session context BEFORE building output. Without this,
        # build_hook_output() -> summarize_session_state() (called with no
        # session_dir/team_name overrides) resolves scope from an
        # uninitialized pact_context (empty team_name/session_dir), so the
        # compaction reminder ships blank ("phase: unknown / agents: none
        # found") on every compaction. init() stays inside the outer
        # try/except as defense-in-depth for genuinely-unexpected errors;
        # malformed/non-dict input_data is already coerced to {} above, so it
        # no longer reaches the fail-open except path.
        pact_context.init(input_data)

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
