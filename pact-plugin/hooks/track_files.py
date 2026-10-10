#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/track_files.py
Summary: PostToolUse hook. It records the files that an Edit or a Write
         modified, it clears the pin-staleness marker once the condition that
         raised that marker is clear, it records a teammate's background Bash
         launch, and it reports pin growth in the project CLAUDE.md that the
         pin-cap gate does not see (`shared.claude_md_drift`).
Used by: pact-plugin/hooks/hooks.json: PostToolUse, matcher `Edit|Write|Bash`,
         and PostToolUseFailure, matcher `Bash`. The registrations live in
         that file and not in settings.json.

Extracts file paths from Edit and Write tool usage and records them
for the memory system's graph network. The archive command writes the managed
file through a script, so it emits no Edit and no Write event: the `Bash` leg
serves the marker clear for it.

On PostToolUseFailure (a failed Bash call) only two jobs run: the marker clear,
because a failing compound command can still archive, and the pin-growth
report, because it can still write. There is no Edit/Write tracking and no
background-launch record.

Input: JSON from stdin with hook_event_name, tool_name, tool_input, tool_response
Output: a pin-growth report as hookSpecificOutput.additionalContext, else
        nothing (suppressOutput). Never a permission decision.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from shared.error_output import hook_error_json
import shared.pact_context as pact_context
from shared.pact_context import get_session_id
from shared.paths import get_claude_config_dir
from shared import state_file

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

# The events this hook is registered on. A report echoes the firing one.
_FAILURE_EVENT = "PostToolUseFailure"
_EVENTS = ("PostToolUse", _FAILURE_EVENT)

# Directory for tracking data. Accessor (B1) — resolves $CLAUDE_CONFIG_DIR at
# CALL time via the shared resolver, so a non-default config dir is honored and
# the import-time freeze (a #924-class inert trap) is avoided.
def get_tracking_dir() -> Path:
    return get_claude_config_dir() / "pact-memory" / "session-tracking"


def ensure_tracking_dir():
    """Ensure the tracking directory exists."""
    get_tracking_dir().mkdir(parents=True, exist_ok=True)


def get_session_tracking_file() -> Path:
    """Get the tracking file for the current session.

    Requires pact_context.init() to have been called so get_session_id()
    reads from the correct session-scoped context file.
    """
    session_id = get_session_id() or "unknown"
    return get_tracking_dir() / f"{session_id}.json"


def _tracking_root(tracking_file: Path) -> Path:
    """The directory the session tracking file must stay under: its own.

    The file sits directly in the tracking directory, so in production this IS
    `get_tracking_dir()`, and containment for a file directly in its root
    compares that directory with itself.
    """
    return tracking_file.parent


def load_tracked_files() -> dict:
    """Load existing tracked files for this session.

    Reads without a lock: writers swap in a complete file, so a reader sees
    the whole previous file or the whole new one.
    """
    default = {"files": [], "session_id": get_session_id() or "unknown"}
    tracking_file = get_session_tracking_file()
    try:
        content = state_file.read_text(tracking_file, _tracking_root(tracking_file))
    except OSError:
        return default
    try:
        return json.loads(content) if content.strip() else default
    except json.JSONDecodeError:
        return default


def save_tracked_files(data: dict):
    """Save tracked files for this session.

    Replaces the file under its state-file lock: the new content is written to
    a temp file and swapped in, so a failure part-way leaves the previous file
    intact.
    """
    tracking_file = get_session_tracking_file()
    try:
        state_file.write_text(
            tracking_file, json.dumps(data, indent=2), _tracking_root(tracking_file)
        )
    except OSError as e:
        print(f"Warning: Could not save tracking data: {e}", file=sys.stderr)


def extract_file_path(tool_input: dict) -> str:
    """Extract file path from tool input."""
    # Both Edit and Write use file_path parameter
    return tool_input.get("file_path", "")


def _update_data(data: dict, file_path: str, tool_name: str) -> dict:
    """Update tracking data with a file entry (pure function, no I/O).

    Separated from I/O so that the read-modify-write cycle can be done
    under a single lock.
    """
    existing_paths = [f["path"] for f in data["files"]]
    if file_path in existing_paths:
        for f in data["files"]:
            if f["path"] == file_path:
                f["last_modified"] = datetime.now(timezone.utc).isoformat()
                f["tool"] = tool_name
                break
    else:
        data["files"].append({
            "path": file_path,
            "tool": tool_name,
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "last_modified": datetime.now(timezone.utc).isoformat(),
        })
    return data


def track_file(file_path: str, tool_name: str):
    """Add a file to the tracking list.

    Uses a single exclusive lock for the entire read-modify-write cycle
    to prevent TOCTOU race conditions between concurrent hook invocations.
    """
    if not file_path:
        return

    tracking_file = get_session_tracking_file()
    session_id = get_session_id() or "unknown"

    def _apply(content: str):
        try:
            data = json.loads(content) if content.strip() else None
        except json.JSONDecodeError:
            data = None
        if data is None:
            data = {"files": [], "session_id": session_id}
        data = _update_data(data, file_path, tool_name)
        return json.dumps(data, indent=2), True, None

    try:
        state_file.locked_update(tracking_file, _apply, _tracking_root(tracking_file))
    except OSError as e:
        print(f"Warning: Could not track file: {e}", file=sys.stderr)


# The token that identifies the archive command inside a Bash payload. The
# archive runs as `python3 ".../scripts/archive_pin.py" --index N`, so the
# script filename is the cheapest thing that separates it from every other
# shell call.
_ARCHIVE_SCRIPT_TOKEN = "archive_pin.py"


def clear_pin_staleness_marker_if_resolved(
    tool_name: str, tool_input: dict
) -> None:
    """Drop the stale-pins marker once the staleness signal has cleared.

    WHY THIS LIVES HERE AND NOT IN THE GATE. `pin_staleness_gate.py` is a
    PreToolUse REFUSAL and it is READ ONLY on that marker, which the design
    holds SACROSANCT. A refusal that removed its own trigger would make the
    trap vanish by deleting the evidence of it rather than by repair, and the
    suite would go green while that happened. The clear belongs on the
    PostToolUse side, which is here.

    THE TRAP IT CLOSES. BEFORE THIS FUNCTION, the marker was written and removed
    at SessionStart only.
    The deny text tells a user to archive stale pins BEFORE editing, so a user
    who obeys inside a session stayed denied until the next session. That is a
    cardinal over-block on the user's own file, and it is reached by OBEYING
    the message.

    THE TRIGGER SET IS A UNION, AND BINDING ONE MEMBER IS THE FAILURE THAT
    READS AS A REPAIR. The pinned region changes across two routes:
      ROUTE 1, a hand edit, which arrives as `Edit` or `Write`.
      ROUTE 2, THE ARCHIVE ITSELF, which arrives as `Bash`. The archive command
        runs a script that writes the file directly, so it emits NO `Edit` or
        `Write` event. A clear bound to route 1 alone misses the very route the
        deny text recommends, which is the worst of the outcomes available
        here.
    ROUTE 2 IS `Bash` AND NOT `Skill`. An earlier reading bound it to `Skill`
    on the belief that the pin command archives. It does not: the archiving
    command runs the script through a shell, so `Bash` is the event that
    carries the write.

    THE COST OF THE WIDENED MATCHER, STATED RATHER THAN HIDDEN. This hook is
    registered for `Bash` as well now, so it runs one subprocess for EACH shell
    call in each consumer session. That is the same per-event cost a new
    registration would carry, so cost is NOT what chose this shape. OWNERSHIP
    chose it: one hook owning one concern across the two routes, rather than
    two hooks owning one concern between them. THE `Bash` LEG TESTS THE COMMAND
    STRING FIRST, so an ordinary shell call costs one substring test and no
    file read INSIDE THIS FUNCTION.

    THE PROCESS AROUND THE FUNCTION IS THE COST, AND THE SENTENCE ABOVE DOES
    NOT PRICE IT. A shell call pays a whole interpreter start. MEASURED TWICE,
    on two developer machines, 20 sequential runs each, with an ordinary shell
    command that carries no archive token: median 48.9 ms against a bare
    interpreter control of 13.8 ms in one run, and 100.8 ms against 38.9 ms in
    the other. THE ABSOLUTE DOES NOT CROSS MACHINES, and these two runs differ
    by roughly a factor of two, so do not quote one of them as the cost. What
    holds across the two is the SHAPE: this hook costs about three times a bare
    interpreter start.
    READ THAT AGAINST THE LOAD THAT WAS THERE BEFORE, because a cost with no
    baseline reads as a new category. A shell call fired 5 hook processes
    before this change and fires 6 after.
    COUNTING RULE FOR THAT PAIR, AND THE POPULATION IS THE PARAMETER THAT
    MATTERS: one process for each command entry in a `PreToolUse` or a
    `PostToolUse` group of hooks.json whose matcher admits `Bash`, with an
    absent matcher key read as match-all. A SHELL CALL RAISES THOSE TWO EVENTS
    AND NO OTHERS. A count taken over every event type instead reaches 18,
    because it sweeps in SessionStart, UserPromptSubmit and the rest, which do
    not fire on a tool call. That is a different population, not a correction.

    Fail-safe: this never raises and never reports. File tracking is the
    caller's job and a fault here must not disturb it.
    """
    try:
        if tool_name == "Bash":
            command = tool_input.get("command", "")
            if not isinstance(command, str):
                return
            if _ARCHIVE_SCRIPT_TOKEN not in command:
                return
        else:
            # Route 1. Gate on the edited path resolving to the managed
            # CLAUDE.md, so an edit to any other file costs one resolve.
            from shared import match_project_claude_md

            file_path = tool_input.get("file_path", "")
            if not file_path or match_project_claude_md(file_path) is None:
                return

        # THE MARKER NAME COMES FROM `shared.constants`, AND NOT FROM
        # `pin_staleness_gate`. That gate is a fail-CLOSED PreToolUse module:
        # its load wrapper prints a PreToolUse deny and calls `sys.exit(2)`.
        # `SystemExit` derives from `BaseException`, so the `except Exception`
        # below does NOT contain it. This hook then exits 2, emits a deny for
        # an event that nobody can deny, and drops the file tracking that
        # `main()` runs after this call. `shared.constants` has no exit path.
        # DO NOT take this name from the gate again, and do not repair a
        # recurrence by a wider handler: a wider handler catches the symptom
        # and leaves the dependency in place.
        from shared.constants import PIN_STALENESS_MARKER_NAME
        from shared.pact_context import get_session_dir
        from staleness import check_pinned_block_signal, get_project_claude_md_path

        session_dir = get_session_dir()
        if not session_dir:
            return
        marker = Path(session_dir) / PIN_STALENESS_MARKER_NAME
        if not marker.exists():
            return

        # RE-READ THE SIGNAL RATHER THAN ASSUME THE EDIT RESOLVED IT. An edit
        # to the managed file, and an archive run, are both REASONS TO LOOK.
        # Neither is proof that the condition cleared. A clear that skipped
        # this read would drop the marker while stale pins remain.
        #
        # CANNOT-TELL ROUTES TO NOT-CLEARED, AND THAT IS WHAT THE TWO CHECKS
        # BELOW BUY. `check_pinned_block_signal` returns None for a condition
        # that CLEARED and for a document it could not resolve or read, and its
        # own contract calls that fail-open. FAIL-OPEN IS CORRECT AT
        # SessionStart, where None means DO NOT BLOCK and ambiguity is safe.
        # HERE THE SAME None MEANS DROP THE MARKER AND DISARM THE GATE, which
        # is the opposite direction, so one value carries opposite safety for
        # its two callers and the ambiguity must not reach the unlink.
        #
        # AND THE DROP IS DURABLE, WHICH IS WHY THE AMBIGUITY COSTS MORE THAN
        # ONE CALL. This function REMOVES the marker and it never writes one,
        # so a marker dropped here stays dropped. A later call with the file
        # readable does NOT restore it, the gate then ALLOWS an add-shaped
        # edit, and only a new SessionStart re-creates the marker. ONE
        # TRANSIENT READ FAULT THEREFORE DISARMS THE GATE FOR THE REST OF THE
        # SESSION. The fail-open posture is declared and SACROSANCT elsewhere
        # in this family. Its DURABILITY is declared here, because this is
        # where a reader meets the mechanism that makes it permanent.
        #
        # THE TRADE, STATED BECAUSE IT IS A DENY WIDENING. This arms the marker
        # strictly longer. It opens NO over-block under the fault that triggers
        # it: with the document unreadable the gate allows the edit anyway, so
        # the conservative route costs a reminder that stays armed rather than
        # an edit that gets refused.
        #
        # WHAT THESE TWO CHECKS DO NOT COVER, AND THE REMAINING CASES ARE NOT
        # ALIKE. An earlier note here grouped them as one recorded residual.
        # That grouping was incorrect: it put a CORRECT BEHAVIOUR beside a
        # RESIDUAL DEFECT, and a reader takes a grouping as one reasoned
        # decision and stops.
        #
        # CASE A, CORRECT BY DETERMINACY, AT TWO RETURN POINTS RATHER THAN ONE.
        # `check_pinned_block_signal` returns None when no `## Pinned Context`
        # title resolves in the file, and again when a title resolves with an
        # EMPTY body. EACH GIVES ZERO PINS, SO ZERO STALE PINS.
        # CLEARED is a reading of the document at those two points rather than a
        # fallback from ambiguity, so the routing is correct and no repair
        # applies.
        #
        # CASE B, THE PIN PARSE DECLINES, IS A RESIDUAL OF THE CANNOT-TELL
        # CLASS. The section resolved, `section_pins` raised, its own handler
        # returned None, and the stale count is unknown. The marker then drops
        # on an unknown state, with the session-long durability described above.
        #
        # WHY CASE B IS NOT REACHED ON DOCUMENT SHAPE TODAY. `section_pins` reads
        # rows of the whole-file parse, and `parse` never raises on a str.
        # Every pattern it applies is compiled at MODULE scope in `pin_caps`, so
        # a document cannot produce `re.error`. Its one slice that could raise,
        # the comma that starts an override rationale, runs only on a row whose
        # override pattern matched, and that pattern requires the comma. What
        # remains is resource exhaustion, a property of input SIZE, not shape.
        #
        # THE EDITS THAT REOPEN CASE B, named because nothing downstream would
        # flag them: a `re.compile` moved inside a function in `pin_caps` (it
        # turns `re.error` into a per-document event), a pattern built from
        # document text, and a new callee in `section_pins` with no handler of its
        # own. A HANG IS NOT A RAISE: a catastrophic backtrack spins, and
        # neither branch above sees it.
        #
        # AND ONE SHAPE STAYS UNMEASURED, WITH TWO OPEN TERMS RATHER THAN ONE.
        # A document that lacks the title TRANSIENTLY, mid-write from a
        # non-atomic outside editor, reaches case A by a route that is not
        # determinate. TERM ONE: is that shape reachable. TERM TWO: is an
        # outside editor in the writer population this project guards. Neither
        # term is measured, and one answer does not settle the other.
        claude_md_path = get_project_claude_md_path()
        if claude_md_path is None:
            return
        try:
            claude_md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if check_pinned_block_signal(claude_md_path=claude_md_path) is not None:
            return

        try:
            marker.unlink()
        except OSError:
            pass
    except Exception:  # noqa: BLE001 — marker management is best-effort
        return


def _report_pin_growth(input_data: dict) -> str | None:
    """The pin-growth report for a Bash call, in a try of its own: a failure
    in the report job changes nothing else this hook does."""
    try:
        from shared.claude_md_drift import report_after_bash

        return report_after_bash(input_data)
    except Exception:  # noqa: BLE001 - the report is best-effort
        return None


def _record_pin_baseline(input_data: dict) -> None:
    """After an Edit or Write, update the pin-growth records, in a try of its
    own."""
    try:
        from shared.claude_md_drift import record_after_write

        record_after_write(input_data)
    except Exception:  # noqa: BLE001
        pass


def _finish(event: str, report: str | None) -> None:
    """Print the report for the firing event, or suppress output, and exit 0."""
    if report:
        print(json.dumps({
            "hookSpecificOutput": {"hookEventName": event, "additionalContext": report}
        }))
    else:
        print(_SUPPRESS_OUTPUT)
    sys.exit(0)


def main():
    """Main entry point for the PostToolUse and PostToolUseFailure hook."""
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        pact_context.init(input_data)
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})
        event = input_data.get("hook_event_name")
        if event not in _EVENTS:
            event = "PostToolUse"

        # THE CLEAR RUNS BEFORE THE TRACKING GATE, because it serves `Bash`
        # too and the gate below drops every tool that is not Edit or Write.
        # It runs on a failed Bash call as well: a compound command whose
        # archive step succeeded and whose last step failed has still cleared
        # the staleness signal, and keeping the marker would keep the
        # staleness gate refusing the user's edits.
        clear_pin_staleness_marker_if_resolved(tool_name, tool_input)

        if event == _FAILURE_EVENT:
            _finish(event, _report_pin_growth(input_data) if tool_name == "Bash" else None)

        # ANOTHER JOB: record a teammate's background Bash launch — a frame
        # carrying the harness `run_in_background` flag, or a command ending
        # in a bare `&`. All of the logic lives in shared.background_work;
        # this host holds only the gate and the call. Its own try/except, so a
        # fault there cannot take down the other jobs in this hook — file
        # tracking is this hook's contract and must survive anything that
        # happens in the registry write.
        #
        # The `Bash` test gates the IMPORT as well as the call, so Edit and
        # Write calls never load the module. Every Bash call does load it,
        # background or not; the launch test runs inside the call.
        if tool_name == "Bash":
            try:
                from shared.background_work import record_background_launch

                record_background_launch(input_data)
            except Exception:
                pass
            _finish(event, _report_pin_growth(input_data))

        # Only track Edit and Write tools
        if tool_name not in ("Edit", "Write"):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # Extract and track the file path
        file_path = extract_file_path(tool_input)
        if file_path:
            track_file(file_path, tool_name)
        _record_pin_baseline(input_data)

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        # Don't block on errors
        print(f"Hook warning (track_files): {e}", file=sys.stderr)
        print(hook_error_json("track_files", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
