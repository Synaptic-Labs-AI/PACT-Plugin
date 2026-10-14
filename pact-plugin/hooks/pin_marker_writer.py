#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/pin_marker_writer.py

Summary: Inserts the declared `## Pinned Context` marker PAIR into the project
CLAUDE.md when, and only when, one of the two pin commands is invoked. Owns
every side effect for that write -- stdin, path resolution, the file lock, the
atomic write and the journal event. The decision of WHERE the markers go, and
WHETHER they go in at all, belongs to `shared/pin_markers.py`, which is pure.

A PAIR, EMITTED IN ONE COMPOSITION. Both lines go in together or neither does.
A document carrying exactly one of them is HALF-MARKED: not an error, and not
repaired here -- completing the pair would mutate a file this writer only ever
inserts into -- but reported as `unpaired` rather than as a success.

Used by: hooks.json, under TWO events running this same body, mirroring
`bootstrap_marker_writer`'s dual registration:
  - UserPromptSubmit (no matcher): the TYPED route. A human types
    `/PACT:pin-memory`, and the raw text arrives on stdin as `prompt`.
  - PostToolUse matched on `Skill`: the AGENT route. An agent calls
    `Skill("PACT:pin-memory")`, which bypasses the typed path entirely, and the
    skill name arrives as `tool_input["skill"]`.
The two routes are DISJOINT -- one logical invocation reaches exactly one of
them -- so no single command can write twice.

THE AGENT ROUTE, AS OBSERVED ON 2026-08-01. This is a DATED OBSERVATION of a
platform behaviour, not a timeless property, and it has a shelf life. Measured
on that date, on that platform build, in an isolated frame with a borrowed
configuration:

  - a `PostToolUse` hook with matcher `Skill` DOES observe a real `Skill` call;
  - `tool_input` carries the skill name under the key `skill`;
  - a PLUGIN skill arrives PLUGIN-QUALIFIED (`PACT:bootstrap` was observed)
    while a PROJECT-level skill arrives bare (`probeskill`).

Controls: a non-Skill tool was recorded through the same logger, so the
positive is not an artifact of a dead instrument, and the model's own
transcript was read INDEPENDENTLY of the hooks to confirm the call happened at
all. Evidence, including the raw per-arm output, is at the probe path recorded
in this change's task handoff.

The mechanism is the generic tool dispatcher, so it is EXPECTED to generalise
beyond that frame -- but what is stated above is what was measured, not what is
believed. To re-check it, register a `PostToolUse` hook with matcher `Skill`
in an isolated settings file, invoke any skill, and read the frame it receives.

The third bullet is what settles the predicate below: both pin commands are
plugin skills, so they arrive qualified, which is exactly what
`_confirm_pin_command` accepts.

THIS HOOK CANNOT DENY, BY TWO INDEPENDENT MECHANISMS
----------------------------------------------------
It runs on EVERY user prompt, so a crash here would not degrade the pin feature
-- it would block the user from typing anything at all, for a write that does
nothing on most files. That makes non-denial a session-availability
requirement rather than a nicety.

  1. STRUCTURAL. Both registrations carry `"async": true`. The platform
     backgrounds the process and returns a hard-coded success before the child
     exits, so neither arm of the block decision can see this script: the exit
     status the caller reads is 0 whatever the child does, and the stdout it
     parses is empty. MEASURED, three arms with a control: the same script
     registered SYNC and exiting 2 blocks the prompt; registered async it does
     not, and its liveness sentinel proves it ran and exited 2 anyway.
  2. DISCIPLINED. A catch-all around the whole body, exit 0 on every path, a
     wrapper around the imports that also exits 0, and no block decision
     emitted anywhere.

Mechanism 2 stays even though mechanism 1 is measured, and the reason is not
belt and braces: backgrounding is best-effort in the platform, and if a future
version made the fallback path reachable it would go live SILENTLY, with no
test in this repository failing.

A CONSEQUENCE OF ASYNC THAT A LATER EDITOR MUST NOT UNDO: an async hook's
stdout is discarded, so this script CANNOT emit `additionalContext`. Adding an
advisory here would fail to appear with no error anywhere. Observability goes
to the session journal instead.

Input: JSON on stdin. Output: a suppressOutput envelope carrying
hookSpecificOutput.hookEventName echoing the ACTUAL firing event, resolved from
the frame because the two registrations fire under different event names.
"""

from __future__ import annotations

# Module-load wrapper. Only stdlib is imported at module level -- see the
# ordering rule below -- but the wrapper is still here, because a hook on this
# channel must exit 0 even when its own import line is what failed. SystemExit
# is raised directly rather than through sys.exit, since `sys` is one of the
# names that may not have bound.
try:
    import json
    import sys
except BaseException:  # noqa: BLE001 -- a load failure must still exit 0
    raise SystemExit(0)


# A VALID event name, used only when the frame is genuinely unavailable
# (unparseable stdin). The platform rejects a missing or unknown event name
# silently, so no emit path may leave it unset.
_DEFAULT_HOOK_EVENT = "UserPromptSubmit"

# The two commands this write is authorised for, WITHOUT the leading slash.
# The write is authorised on these and on nothing else: no clock, no session
# start, no migration pass, no sweep of files nobody asked about.
_PIN_COMMANDS = ("PACT:pin-memory", "PACT:prune-memory")


def _resolve_event_name(input_data, default: str = _DEFAULT_HOOK_EVENT) -> str:
    """Return the firing event's name from the parsed frame, else `default`.

    NEVER raises, and uses stdlib only, so it stays callable from any emit
    path including one reached because everything else failed.
    """
    try:
        event = input_data.get("hook_event_name")
    except AttributeError:
        return default
    if isinstance(event, str) and event:
        return event
    return default


def _suppress_output(event_name: str) -> str:
    """Return the JSON envelope that says nothing and decides nothing.

    Carries the ACTUAL firing event, never a hard-coded one: a static name
    under a PostToolUse fire is a silent schema rejection at the platform
    layer. On the async path this output is discarded, so it is inert -- it
    exists so the script stays correct if the registration is ever made
    synchronous.
    """
    return json.dumps({
        "suppressOutput": True,
        "hookSpecificOutput": {"hookEventName": event_name},
    })


def _confirm_pin_command(frame) -> tuple[str, str] | None:
    """Return `(route, command)` when this invocation IS one of the two pin
    commands, else None. stdlib only. NEVER raises.

    THIS IS THE CRASH GATE AS WELL AS THE AUTHORISATION GATE, which is why it
    runs before everything else and touches nothing but the parsed frame. The
    hook is invoked broadly -- on every prompt, and on every skill call -- and
    that is permitted, because what makes the write command-scoped is that NO
    side effect of any kind occurs before this function has confirmed one of
    the two commands. Breadth of invocation is irrelevant; reachability of the
    write is the whole criterion. So on the overwhelming majority of
    invocations the script has done one `json.load`, one string test, and
    stopped, and there is no code on that path that can fail.

    Typed route: the command name must be followed by end-of-string or
    whitespace, so `/PACT:pin-memory-something` is NOT a prefix match.

    Agent route: the skill name must EQUAL one of the two, with an optional
    leading slash tolerated. A bare unqualified name such as `pin-memory` is
    deliberately NOT accepted: it could name a different plugin's skill, and
    the write must be reachable only through a confirmed invocation of THESE
    commands. Observed 2026-08-01 on this platform build: a plugin skill
    arrives PLUGIN-QUALIFIED (`PACT:bootstrap`) while a project-level skill
    arrives bare, so both pin commands arrive qualified and this predicate
    matches them. The shape is pinned by test, so it cannot re-open silently.
    """
    try:
        prompt = frame.get("prompt")
        if isinstance(prompt, str):
            stripped = prompt.strip()
            for name in _PIN_COMMANDS:
                token = "/" + name
                if stripped.startswith(token):
                    rest = stripped[len(token):]
                    if not rest or rest[0].isspace():
                        return ("typed", name)

        tool_input = frame.get("tool_input")
        if isinstance(tool_input, dict):
            skill = tool_input.get("skill")
            if isinstance(skill, str):
                candidate = skill.strip()
                if candidate.startswith("/"):
                    candidate = candidate[1:]
                if candidate in _PIN_COMMANDS:
                    return ("skill", candidate)
    except BaseException:  # noqa: BLE001 -- the gate itself must never fail
        return None
    return None


def _plan_and_write() -> str:
    """Perform the write and return an outcome string. NEVER raises.

    Every failure path returns a status string instead of propagating, copying
    the established template for writers of this file. The messages are
    deliberately uninformative about paths: an OS error embeds the absolute
    CLAUDE.md path, so it is truncated, and a containment refusal stays opaque
    and never names the victim path.

    The plugin imports sit HERE, inside the function, and NOT at module level.
    A later reader will want to tidy them upward; the reason they must not is
    that a module-level plugin import moves failure BEFORE the command test,
    which is the one ordering this hook cannot lose.

    The hardening is opt-in and this write inherits none of it, so it takes
    the lock explicitly, re-reads inside the lock and abandons the pass if the
    file changed, and writes through the atomic helper. The containment anchor
    is the base the resolver ACTUALLY used, not a re-derived one -- a
    re-derived anchor makes the containment check vacuous. The UNRESOLVED
    target is passed through unchanged, because resolving it here and using
    the resolved path downstream would turn every benign in-project redirect
    into a real escape.
    """
    # Bind the containment class BEFORE the main try. Naming it in an `except`
    # clause while importing it INSIDE the same try is a live defect: when the
    # import is what failed, the name is unbound, and evaluating the handler
    # raises NameError out of a function whose whole contract is that it never
    # raises. The sentinel keeps the clause bound and cannot be raised by
    # anything, so a failed import falls through to the catch-all instead.
    class _NeverRaised(OSError):
        pass

    try:
        from shared.claude_md_manager import ContainmentError
    except BaseException:  # noqa: BLE001 -- see the comment above
        ContainmentError = _NeverRaised  # type: ignore[assignment,misc]

    try:
        from shared.claude_md_manager import (
            _atomic_write_text,
            file_lock,
        )
        from shared.pin_markers import (
            Insertion,
            apply_insertion,
            certify_expel_nothing,
            plan_insertion,
        )
        from staleness import _resolve_project_claude_md_with_base

        # Returns (None, None) when no project CLAUDE.md exists, so this
        # resolver never brings one into being. Creating the file is not this
        # hook's business under any circumstance.
        path, base = _resolve_project_claude_md_with_base()
        if path is None or base is None:
            return "noop_no_file"

        # Decoded with replacement, so a file that is not UTF-8 still gets the
        # ladder's own no-op. The text is NEVER WRITTEN as read: the write
        # below needs a strict re-read under the lock to equal it.
        content = path.read_text(encoding="utf-8", errors="replace")
        planned = plan_insertion(content)
        if not isinstance(planned, Insertion):
            # A decline (SkipReason) or a refusal that names the line the
            # parser could not read with certainty (Refusal). Either way the
            # file is not touched.
            return planned.value

        new_content = apply_insertion(content, planned)
        if not certify_expel_nothing(content, new_content, planned):
            # The planner has already refused every document that carries a
            # marker, so a refusal here means the composition could not be
            # proven to be the original plus the two marker lines: an
            # assembly defect. Refusing is the safe direction, with no repair
            # attempt.
            return "certificate_failed"

        with file_lock(path):
            # Re-read under the lock. Another writer of this file may have
            # landed between the outer read and the lock, and the markers are
            # idempotent, so abandoning the pass costs only a delay -- they
            # go in on the next pin command.
            current = path.read_text(encoding="utf-8")
            if current != content:
                return "skipped_concurrent_change"
            _atomic_write_text(path, new_content, base)
        return "written"
    except ContainmentError:
        return "skipped_containment"
    except TimeoutError:
        return "skipped_lock"
    except UnicodeDecodeError:
        # The strict re-read under the lock: a marker was due and the file is
        # not UTF-8, so it is left untouched rather than written back with
        # replacement characters.
        return "skipped_not_utf8"
    except OSError as error:
        return f"error_os: {str(error)[:50]}"
    except BaseException as error:  # noqa: BLE001 -- fail open, always
        return f"error_other: {type(error).__name__}"


def _journal(frame, route: str, command: str, outcome: str) -> None:
    """Record one event for this invocation. NEVER raises, NEVER fails the hook.

    Wrapped independently of the write for a specific reason: an audit write
    that could abort a legitimate operation would be a data-loss bug arriving
    from the observability layer. A prompt that is not a pin command journals
    nothing, so this stays quiet rather than filling the journal with noise.
    """
    try:
        import shared.pact_context as pact_context
        from shared.session_journal import append_event, make_event

        pact_context.init(frame)
        append_event(make_event(
            "pin_marker_write",
            route=route,
            command=command,
            outcome=outcome,
        ))

    except BaseException:  # noqa: BLE001 -- observability must not deny
        return


def main() -> None:
    try:
        frame = json.load(sys.stdin)
    except BaseException:  # noqa: BLE001 -- malformed stdin is not our domain
        # No parsed frame, so the firing event is unrecoverable here. This is
        # the only emit path where the safe default is used.
        print(_suppress_output(_resolve_event_name(None)))
        sys.exit(0)

    event = _resolve_event_name(frame)

    confirmed = _confirm_pin_command(frame)
    if confirmed is None:
        # The hot path. Almost every invocation ends here, having imported
        # nothing from the plugin and touched no file.
        print(_suppress_output(event))
        sys.exit(0)

    route, command = confirmed
    outcome = _plan_and_write()
    _journal(frame, route, command, outcome)

    print(_suppress_output(event))
    sys.exit(0)


if __name__ == "__main__":
    # Outermost catch-all. `_plan_and_write` and `_journal` are already total,
    # so nothing should reach this -- which is exactly why it is here. The one
    # exception deliberately allowed through is SystemExit, so the explicit
    # exit 0 above is not swallowed and re-raised as something else.
    try:
        main()
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001 -- this hook exits 0 or not at all
        raise SystemExit(0)
