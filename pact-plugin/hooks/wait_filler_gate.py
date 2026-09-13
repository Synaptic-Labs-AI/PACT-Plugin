#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/wait_filler_gate.py
Summary: PreToolUse hook (matcher: Bash) that denies bare `true`/`sleep <N>`
    filler commands — the turn-manufacturing no-ops a waiting agent emits
    under the filler-call compulsion.
Used by: pact-plugin/hooks/hooks.json PreToolUse "Bash" matcher entry.

A command is denied iff, after normalization, it IS nothing but a filler
no-op. Normalization, in order:
  1. Strip leading AND trailing whitespace (including ALL trailing
     newlines). A newline remaining after that strip is interior — the
     command is composed — allow.
  2. Strip leading env assignments (`FOO=1 BAR=2 sleep 5` is still filler).
  3. Strip one optional `command `/`builtin ` prefix.
  4. Strip one optional trailing comment (` # ...`).
Then deny on \\A(true|sleep[ \\t]+(([0-9]+(\\.[0-9]*)?|\\.[0-9]+)[smhd]?|infinity))\\Z —
anchored \\A...\\Z, never $ (which matches before a single trailing
newline and would re-make the newline forms order-dependent). The
separator is `[ \t]+`: space and tab are the only bash word separators
that can reach the matcher (an interior newline allows at step 1; other
whitespace is not a bash word separator). Any shell metacharacter or
composition fails the anchored pattern and is allowed: this is an
honest-mistake guard, not an adversarial boundary.

Under-block shape consistent with the grammar, by design: quoted env
values containing spaces (`FOO="a b" sleep 5` mangles through the
env-assignment strip). It stays allowed — the persona layer is the
standard; this hook is the floor.

Fail direction: OPEN. Any internal error — malformed stdin, a matcher
exception — allows the command (exit 0 + stderr note). A load/match
failure that denied on matcher=Bash would block every Bash call in every
consumer session, and the gated commands are inert no-ops whose occasional
escape costs nothing.

Input: JSON on stdin, {"tool_name": "Bash", "tool_input": {"command": "<cmd>"}}
Output: deny = {"hookSpecificOutput": {...}} + exit 2;
        allow = {"suppressOutput": true} + exit 0. Errors to stderr.
"""

from __future__ import annotations

import json
import os
import re
import sys

_ALLOW_OUTPUT = json.dumps({"suppressOutput": True})

_DENY_REASON = (
    "Passive waiting is the protocol — end the turn with no tool call; "
    "teammate messages arrive as their own turns. A bare true/sleep filler "
    "call manufactures the next turn without producing new information; "
    "compose it with real work or end the turn."
)

_FILLER_PATTERN = re.compile(
    r"\A(true|sleep[ \t]+(([0-9]+(\.[0-9]*)?|\.[0-9]+)[smhd]?|infinity))\Z"
)
_ENV_ASSIGNMENT = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*=\S*\s+")
_WRAPPER_PREFIX = re.compile(r"\A(?:command|builtin)\s+")
_TRAILING_COMMENT = re.compile(r"\s+#.*\Z")

# --- Background-launch advisory (a SECOND, INDEPENDENT concern) -------------
# Fired at the moment a background launch is committed, which is where the
# association actually fails: an agent frames the moment as "the tool will
# wake me" and ends the turn without flagging. A later idle-time reminder
# reaches that agent only after they have already stalled.
#
# IT FIRES ON TEAMMATE FRAMES ONLY. The cheap stdin test runs first:
# `agent_type` present, non-empty and not a lead spelling. A lead frame gets
# nothing, because a lead IS re-invoked when its background job finishes and
# holds no task wait to flag. A plain non-PACT frame carries no `agent_type`
# and gets nothing. An Agent-tool subagent also carries a non-lead
# `agent_type`, so for a background launch the gate then resolves the team and
# asks `shared.background_work.is_teammate_launch_frame`, which reads team
# config and the session registry. A subagent gets nothing, and so does a frame
# whose team cannot be resolved.
#
# IT IS NOT A TERM IN THE DENY VERDICT AND MUST NEVER BECOME ONE. It rides
# the ALLOW branch only. `_is_filler_command` and its inputs are untouched by
# this feature. A denied command never runs, so there is no background work
# to advise about on that branch — which is why the advisory is attached to
# the allow output rather than the deny one, and not because the verdict
# feeds it.
#
# DELIVERY: an allow-path `additionalContext` reaches the model together with
# the tool result, after the call has run. The advisory is read once the
# launch has happened and before the agent decides how to end the turn, which
# is the decision it addresses. It is advice, not enforcement: nothing
# downstream may assume the agent acted on it.
_BACKGROUND_ADVISORY = (
    "This Bash call runs in the background. NOTHING WILL WAKE YOU when it "
    "finishes — the result waits for you to collect it. Before you end this "
    "turn, either collect the result or SET metadata.intentional_wait on "
    "every task the wait covers, naming what you are waiting for. "
    "validate_wait accepts a free-form reason, so a reason describing the "
    "background job is valid even though KNOWN_REASONS does not enumerate one."
)


def _load_launch_predicate():
    """`background_launch.is_background_launch`, loaded by file path, or None.

    Loaded by PATH, not imported, so a Bash call that is not a teammate's
    background launch never runs the `shared` package's `__init__`, which
    costs tens of milliseconds on a call that happens before every Bash. The
    module is not registered in `sys.modules`.
    Any failure returns None, and the caller then emits no advisory: the
    advisory is optional, and the deny verdict never reaches this call.
    """
    try:
        import importlib.util

        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "shared",
            "background_launch.py",
        )
        spec = importlib.util.spec_from_file_location("_pact_background_launch", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.is_background_launch
    except Exception:
        return None


def is_background_launch(input_data) -> bool:
    """True iff this frame launches background work (the flag, or a command
    ending in a bare `&`). False when the shared predicate cannot be loaded."""
    launched = _load_launch_predicate()
    if launched is None:
        return False
    try:
        return launched(input_data) is True
    except Exception:
        return False


# The lead's `agent_type` spellings. Mirrors `shared.pact_context.LEAD_AGENT_TYPES`,
# which is the source of truth; held locally because this hook runs before every
# Bash call and imports only the standard library until a teammate-shaped frame
# launches background work.
_LEAD_AGENT_TYPES = frozenset({"PACT:pact-orchestrator", "pact-orchestrator"})


def is_teammate_frame(input_data) -> bool:
    """True iff stdin carries a non-empty `agent_type` that is not a lead spelling."""
    if not isinstance(input_data, dict):
        return False
    agent_type = input_data.get("agent_type")
    return (
        isinstance(agent_type, str)
        and bool(agent_type)
        and agent_type not in _LEAD_AGENT_TYPES
    )


def launch_advisory_applies(input_data) -> bool:
    """True iff a teammate is launching background work. False on any error.

    Cheapest first: the stdin `agent_type` test, then the launch predicate. Only
    a teammate-shaped background launch imports `shared` and reads team config
    and the session registry.
    """
    if not is_teammate_frame(input_data) or not is_background_launch(input_data):
        return False
    try:
        from shared.background_work import frame_team_and_name, is_teammate_launch_frame

        team_name, _name = frame_team_and_name(input_data)
        return bool(team_name) and is_teammate_launch_frame(input_data, team_name)
    except Exception:
        return False


def _is_filler_command(command: str) -> bool:
    """True iff the command is nothing but a bare `true`/`sleep <N>`.

    Applies the module-docstring normalization chain in order. The chain
    only ever strips benign decoration; anything it cannot reduce to the
    anchored pattern (metacharacters, composition, quoting, wrappers like
    `sudo`/`time`) is allowed.
    """
    normalized = command.strip()
    if "\n" in normalized:
        return False  # interior newline = composed command
    while True:
        stripped = _ENV_ASSIGNMENT.sub("", normalized, count=1)
        if stripped == normalized:
            break
        normalized = stripped
    normalized = _WRAPPER_PREFIX.sub("", normalized, count=1)
    normalized = _TRAILING_COMMENT.sub("", normalized, count=1)
    return _FILLER_PATTERN.match(normalized) is not None


def main() -> None:
    try:
        try:
            input_data = json.load(sys.stdin)
        except ValueError as error:
            print(
                f"wait_filler_gate: malformed stdin JSON — allowing "
                f"(fail-open): {error}",
                file=sys.stderr,
            )
            sys.exit(0)

        if not isinstance(input_data, dict) or input_data.get("tool_name") != "Bash":
            print(_ALLOW_OUTPUT)
            sys.exit(0)

        tool_input = input_data.get("tool_input")
        command = tool_input.get("command") if isinstance(tool_input, dict) else None
        if not isinstance(command, str) or not _is_filler_command(command):
            # ALLOW. The background advisory rides this branch and only this
            # branch; it did not participate in reaching it.
            if launch_advisory_applies(input_data):
                print(json.dumps({
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "additionalContext": _BACKGROUND_ADVISORY,
                    }
                }))
                sys.exit(0)
            print(_ALLOW_OUTPUT)
            sys.exit(0)

        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": _DENY_REASON,
            }
        }))
        sys.exit(2)

    except Exception as error:  # noqa: BLE001 — fail-open catch-all
        print(
            f"wait_filler_gate: internal error — allowing (fail-open): {error}",
            file=sys.stderr,
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
