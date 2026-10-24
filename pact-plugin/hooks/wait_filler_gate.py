#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/wait_filler_gate.py
Summary: PreToolUse hook (matcher: Bash) that denies bare `true`/`sleep <N>`
    filler commands — the turn-manufacturing no-ops a waiting agent emits
    under the filler-call compulsion — with a count-and-escalate gradient
    and a bounded circuit-breaker valve (issue #1657).
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
separator is `[ \\t]+`: space and tab are the only bash word separators
that can reach the matcher (an interior newline allows at step 1; other
whitespace is not a bash word separator). Any shell metacharacter or
composition fails the anchored pattern and is allowed: this is an
honest-mistake guard, not an adversarial boundary.

Under-block shape consistent with the grammar, by design: quoted env
values containing spaces (`FOO="a b" sleep 5` mangles through the
env-assignment strip). It stays allowed — the persona layer is the
standard; this hook is the floor.

ESCALATION GRADIENT (issue #1657): a byte-identical denial gives the
model nothing to key on, and each denial manufactures the next turn, so
the identical call re-fires dozens of times. The deny branch now counts
filler attempts per (session_id, agent_type) in a small state file and
escalates: tier 1 is FROZEN byte-identical to the historical advisory,
tier 2 appends the rendered count, tier 3 is an imperative FILLER-LOCK.
A session-total saturation boost (`total > 6`) escalates command-walking
(`sleep 30/31/32...` each start fresh but enter at tier 2). Counters key
on the POST-NORMALIZATION command only — `description` is never read (a
description-keyed counter resets when the model varies the description).

VALVE: at computed tier 3, an INSTANT form (bare `true`, or `sleep` with
zero duration) is ALLOWED at most twice per session with a
CIRCUIT-BREAKER additionalContext payload — the sanctioned terminal
no-op that deterministically ends the loop (no hook can end a turn; an
allowed inert call plus an imperative payload can). The instant-form
predicate is derived from the SAME normalized command and is a strict
subset of the deny grammar: `sleep N>0` / `sleep infinity` are NEVER
instant.

STATE: one JSON file per session_id at
<CLAUDE_CONFIG_DIR>/pact-filler-state/<session_id>.json, agent_type
sections inside (in-process teammates share the lead's session_id in
PreToolUse frames, so the section keeps their counters distinct; a
plain consumer frame keys on ""). Written through
shared/state_file.py's locked_update, loaded BY FILE PATH (importing the
`shared` package costs ~134 ms against this hook's ~40 ms budget); all
state I/O sits on the deny branch only — the allow fast-path is
byte-identical to the pre-gradient hook. Unbounded-growth caps: 64 pair
keys per section, 32 sections per file; past a cap the call degrades to
the stateless tier-1 deny without persisting.

Fail direction: OPEN at the matcher level, TIER-1-DENY at the state
level. Any internal error before the filler verdict — malformed stdin, a
matcher exception — allows the command (exit 0 + stderr note): a load
failure that denied on matcher=Bash would block every Bash call in every
consumer session, and the gated commands are inert no-ops. Any state
failure after the verdict — unreadable file, corrupt JSON, non-int
valve_budget, lock timeout — degrades to the frozen tier-1 advisory deny
with the valve CLOSED (UNKNOWN = DENY): bad state must never flip the
verdict to allow and must never manufacture a harder denial. Stale state
files are TTL-reaped (30d) by cleanup_old_filler_state in
hooks/session_end.py.

Input: JSON on stdin, {"tool_name": "Bash", "tool_input": {"command": "<cmd>"}}
Output: deny = {"hookSpecificOutput": {...}} + exit 2 (the reason is also
        mirrored to stderr for harnesses reported to ignore stdout JSON
        at exit 2; the channel probe of 2026-10-24 confirmed stdout JSON
        reaches the model in this harness);
        valve allow = {"hookSpecificOutput": {"additionalContext": ...}} + exit 0;
        plain allow = {"suppressOutput": true} + exit 0. Errors to stderr.
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

# --- Escalation gradient (issue #1657) --------------------------------------
#
# Tier selection: tier = min(3, per_command_count + saturation_boost)
# where the count and total INCLUDE the current attempt, and
# saturation_boost = 1 when the session total exceeds _SATURATION_TOTAL.
# Fresh command, quiet session: count 1 -> tier 1 (the frozen advisory).
# Same command re-fired: 2 -> tier 2, 3+ -> tier 3. Fresh command in a
# saturated session (total past the threshold): 1 + 1 -> tier 2, and it
# reaches tier 3 on its own first re-fire — the command-walk backstop.
#
# The instant-form predicate below is a STRICT SUBSET of _FILLER_PATTERN's
# language (the V1 pin): `0*\.?0*` names exactly the all-zero duration
# spellings the filler grammar admits (`0`, `00`, `0.`, `0.0`, `.0`,
# `.00`, with an optional s/m/h/d suffix). Anything carrying a nonzero
# digit, and `infinity`, fail the anchor and are never valve-eligible.
_MAX_TIER = 3
_SATURATION_TOTAL = 6
_INITIAL_VALVE_BUDGET = 2
_MAX_TRACKED_PAIRS = 64
_MAX_AGENT_SECTIONS = 32
_FILLER_STATE_DIRNAME = "pact-filler-state"
_SAFE_SESSION_ID = re.compile(r"[A-Za-z0-9_-]+")
_INSTANT_FILLER = re.compile(r"\A(?:true|sleep[ \t]+0*\.?0*(?:[smhd])?)\Z")

_TIER2_SUFFIX = " This exact call has now been denied {count} times this session."
_TIER3_REASON = (
    "FILLER-LOCK: this exact call has been denied {count} times this "
    "session ({total} filler calls in total). Your turn ends now — make "
    "NO further tool calls this turn. Passive waiting is the protocol: "
    "teammate messages arrive as their own turns."
)
_VALVE_PAYLOAD = (
    "CIRCUIT-BREAKER: this no-op filler call was allowed as your "
    "sanctioned terminal wait — it produced no new information, and "
    "nothing will come of it. Your turn ends NOW: do not call any tool "
    "again this turn. Teammate messages arrive as their own turns. "
    "(Circuit-breaker budget remaining: {budget}.)"
)


def _pair_key(normalized_command: str) -> str:
    """Content address of a filler attempt: first 16 hex of the SHA-256
    of the post-normalization command. Keyed on the command only; the
    tool-input `description` is never read (varying it must not reset
    the gradient)."""
    # Lazy import keeps the module-level import surface exactly the set
    # the S3 stdlib pin enumerates; the deny branch can afford the ~1 ms.
    import hashlib

    return hashlib.sha256(normalized_command.encode("utf-8")).hexdigest()[:16]


def _load_locked_update():
    """`state_file.locked_update`, loaded by file path, or None.

    Same path-import discipline as `_load_launch_predicate`: importing
    the `shared` package runs its __init__ (~134 ms measured against
    this hook's ~40 ms budget), which a hook that fires before every
    Bash call cannot pay. Any failure returns None and the caller
    degrades to the stateless tier-1 deny.
    """
    try:
        import importlib.util

        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "shared",
            "state_file.py",
        )
        spec = importlib.util.spec_from_file_location("_pact_state_file", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.locked_update
    except Exception:
        return None


def _compute_gradient(section, key: str, instant: bool):
    """Pure tier/valve arithmetic for one agent_type section. No I/O, so
    the gradient is unit-testable without a filesystem.

    `section` is the parsed section dict, or None when absent (a fresh
    session — the first write creates it with the default budget).
    Returns (tier, count, total, valve_open, budget) with count/total
    INCLUDING the current attempt, or None when the section or its
    `valve_budget` is corrupt: the caller treats that as UNKNOWN = DENY
    at tier 1 with the valve closed (V2), never harder on bad state.
    """
    if section is None:
        pairs, total, budget = {}, 0, _INITIAL_VALVE_BUDGET
    elif not isinstance(section, dict):
        return None
    else:
        pairs = section.get("pairs")
        pairs = pairs if isinstance(pairs, dict) else {}
        total = section.get("total")
        total = total if isinstance(total, int) and total >= 0 else 0
        budget = section.get("valve_budget")
        if not isinstance(budget, int) or budget < 0:
            return None
    count = pairs.get(key)
    count = count if isinstance(count, int) and count >= 0 else 0
    new_count = count + 1
    new_total = total + 1
    tier = min(
        _MAX_TIER, new_count + (1 if new_total > _SATURATION_TOTAL else 0)
    )
    valve_open = tier == _MAX_TIER and instant and budget > 0
    return tier, new_count, new_total, valve_open, budget


def _escalate(input_data, normalized):
    """Gradient verdict for a filler-matched command: ("deny", reason) or
    ("valve", additional_context payload).

    FAIL DIRECTION: any state failure — missing/unsafe session_id,
    unresolvable state module, unreadable file, corrupt JSON, non-int
    budget, lock timeout, growth cap reached — degrades to the frozen
    tier-1 advisory deny with the valve closed. A state error must never
    flip the verdict to allow (the matcher-level fail-open one branch up
    owns that) and never deny harder than tier 1.
    """
    session_id = input_data.get("session_id")
    agent_type = input_data.get("agent_type")
    agent_key = agent_type if isinstance(agent_type, str) else ""
    try:
        if not isinstance(normalized, str):
            # Unreachable while _is_filler_command and _normalize_filler
            # share one pure chain; guarded so the pairing cannot rot into
            # a crash if they ever diverge. Degrades, never denies harder.
            return "deny", _DENY_REASON
        if not isinstance(session_id, str) or not _SAFE_SESSION_ID.fullmatch(
            session_id
        ):
            # No session identity, or one that cannot be a filename:
            # never persist, deny at the frozen tier 1 (today's denial).
            return "deny", _DENY_REASON
        locked_update = _load_locked_update()
        if locked_update is None:
            return "deny", _DENY_REASON
        # Config root from the environment ONLY, with no literal
        # fallback: this is a degraded path that must not import the
        # resolver (shared package), and the unset-env case degrades to
        # the stateless tier-1 deny above rather than naming a root the
        # install may not use (test_hook_emitted_config_root contract).
        config_root = os.environ.get("CLAUDE_CONFIG_DIR")
        if not config_root:
            return "deny", _DENY_REASON
        root = os.path.join(config_root, _FILLER_STATE_DIRNAME)
        state_path = os.path.join(root, session_id + ".json")
        key = _pair_key(normalized)
        instant = _INSTANT_FILLER.match(normalized) is not None

        def _apply(text):
            """(new_text, changed, result) for locked_update. Pure given
            `text`; runs at most twice per call (locked_update contract).
            A result of None is the shared DEGRADE marker — corrupt JSON,
            non-dict state, corrupt section/budget, or a growth cap —
            and leaves the file untouched."""
            try:
                data = json.loads(text) if text else {}
            except ValueError:
                return text, False, None
            if not isinstance(data, dict):
                return text, False, None
            if (
                agent_key not in data
                and len(data) >= _MAX_AGENT_SECTIONS
            ):
                return text, False, None
            section = data.get(agent_key)
            gradient = _compute_gradient(section, key, instant)
            if gradient is None:
                return text, False, None
            tier, new_count, new_total, valve_open, budget = gradient
            pairs = dict(section.get("pairs") or {}) if isinstance(
                section, dict
            ) else {}
            if key not in pairs and len(pairs) >= _MAX_TRACKED_PAIRS:
                return text, False, None
            pairs[key] = new_count
            new_budget = budget - 1 if valve_open else budget
            data[agent_key] = {
                "pairs": pairs,
                "total": new_total,
                "valve_budget": new_budget,
            }
            return (
                json.dumps(data),
                True,
                (tier, new_count, new_total, valve_open, new_budget),
            )

        result = locked_update(state_path, _apply, root, timeout=2.0)
        if result is None:
            return "deny", _DENY_REASON
        tier, new_count, new_total, valve_open, budget_left = result
        if valve_open:
            return "valve", _VALVE_PAYLOAD.format(budget=budget_left)
        if tier >= _MAX_TIER:
            return "deny", _TIER3_REASON.format(
                count=new_count, total=new_total
            )
        if tier == 2:
            return "deny", _DENY_REASON + _TIER2_SUFFIX.format(
                count=new_count
            )
        return "deny", _DENY_REASON
    except Exception as error:  # noqa: BLE001 — fail-direction catch-all
        print(
            f"wait_filler_gate: filler-state error — denying at tier 1 "
            f"(fail-direction): {error}",
            file=sys.stderr,
        )
        return "deny", _DENY_REASON


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
# asks `shared.background_work.teammate_launch_name`, which reads team config
# and the session registry. A subagent gets nothing, and so does a frame whose
# team cannot be resolved. A separate-process (tmux) teammate gets nothing
# either: its own completion starts its next turn, and the harness already
# tells it so, so an advisory saying nothing will wake it would contradict it.
#
# IT IS NOT A TERM IN THE DENY VERDICT AND MUST NEVER BECOME ONE. It rides
# the ALLOW branch only. `_is_filler_command` and its inputs are untouched by
# this feature. A denied command never runs, so there is no background work
# to advise about on that branch — which is why the advisory is attached to
# the allow output rather than the deny one, and not because the verdict
# feeds it.
#
# DELIVERY: an allow-path `additionalContext` reaches the model together
# with the tool result, after the call has run. The advisory is read once the
# launch has happened and before the agent decides how to end the turn, which
# is the decision it addresses. It is advice, not enforcement: nothing
# downstream may assume the agent acted on it.
_BACKGROUND_ADVISORY = (
    "This Bash call runs in the background. As an in-process teammate, "
    "NOTHING WILL WAKE YOU when it "
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
    """True iff a teammate that is not a separate-process (tmux) teammate is
    launching background work. False on any error.

    Cheapest first: the stdin `agent_type` test, then the launch predicate. Only
    a teammate-shaped background launch imports `shared` and reads team config
    and the session registry.
    """
    if not is_teammate_frame(input_data) or not is_background_launch(input_data):
        return False
    try:
        from shared.background_work import (
            frame_team_and_name,
            teammate_is_separate_process,
            teammate_launch_name,
        )

        team_name, _name = frame_team_and_name(input_data)
        member = teammate_launch_name(input_data, team_name) if team_name else ""
        return bool(member) and not teammate_is_separate_process(team_name, member)
    except Exception:
        return False


def _normalize_filler(command: str) -> "str | None":
    """The normalization chain from the module docstring, returning the
    reduced command when it matches the anchored filler pattern and None
    otherwise."""
    normalized = command.strip()
    if "\n" in normalized:
        return None  # interior newline = composed command
    while True:
        stripped = _ENV_ASSIGNMENT.sub("", normalized, count=1)
        if stripped == normalized:
            break
        normalized = stripped
    normalized = _WRAPPER_PREFIX.sub("", normalized, count=1)
    normalized = _TRAILING_COMMENT.sub("", normalized, count=1)
    return normalized if _FILLER_PATTERN.match(normalized) else None


def _is_filler_command(command: str) -> bool:
    """True iff the command is nothing but a bare `true`/`sleep <N>`.

    Applies the module-docstring normalization chain in order. The chain
    only ever strips benign decoration; anything it cannot reduce to the
    anchored pattern (metacharacters, composition, quoting, wrappers like
    `sudo`/`time`) is allowed. Kept as its own name: main() reaches the
    verdict through THIS module attribute, and the fail-open test arms
    patch it to raise.
    """
    return _normalize_filler(command) is not None


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
            # branch; it did not participate in reaching it. The gradient's
            # state I/O all sits on the deny branch below — this fast path
            # is byte-identical to the pre-gradient hook.
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

        # DENY. The command normalizes to bare filler; the gradient and
        # valve decide the exact verdict. Every state failure inside
        # _escalate degrades to the frozen tier-1 advisory (fail-direction
        # invariant) — only a failure in the reporting below can reach the
        # outer matcher-level fail-open.
        normalized = _normalize_filler(command)
        verdict, text = _escalate(input_data, normalized)
        if verdict == "valve":
            # Circuit-breaker: a tier-3 instant form is ALLOWED as the
            # session's sanctioned terminal no-op, with a payload whose
            # whole job is to end the turn. Same envelope shape as the
            # background advisory above.
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": text,
                }
            }))
            sys.exit(0)
        # The reason rides stdout JSON (the surface the 2026-10-24 channel
        # probe confirmed this harness parses at exit 2) and is mirrored to
        # stderr for harnesses reported to ignore stdout at exit 2.
        print(text, file=sys.stderr)
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": text,
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
