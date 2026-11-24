#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/wait_filler_gate.py
Summary: PreToolUse hook (matcher: Bash) that denies bare `true`/`sleep <N>`
    filler commands — the turn-manufacturing no-ops a waiting agent emits
    under the filler-call compulsion — with a count-and-escalate gradient
    (issue #1657) whose computed tier 3 carries a PACT-WAIT-OUTLET: a
    ready-to-run arm command for the structural wait watcher
    (hooks/pact_wait.py).
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

OUTLET: at computed tier 3 the denial's reason ends the wait structurally,
ROLE-BRANCHED. A lead or a separate-process teammate — whose background
completion starts their next turn — gets a ready-to-run arm command for
the structural wait watcher (hooks/pact_wait.py, built by
shared/wait_target.arm_command with the resolved team substituted) and
launches it with ONE background `Bash` frame (`run_in_background: true`)
before ending the turn. An in-process teammate, whose background
completion wakes no one, gets no arm command: it SETs
metadata.intentional_wait naming the lead and ends the turn. An Agent-tool
subagent gets no arm command: it ends the turn and reports. A frame with
no PACT team gets the no-command text. The tier-3 verdict is ALWAYS a
denial: no hook can end a turn, so an allowed inert no-op only ever
manufactured the next one.

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
failure after the verdict — unreadable file, corrupt JSON, lock
timeout — degrades to the frozen tier-1 advisory deny: bad state must
never flip the verdict to allow and must never manufacture a harder
denial. Stale state
files are TTL-reaped (30d) by cleanup_old_filler_state in
hooks/session_end.py.

Input: JSON on stdin, {"tool_name": "Bash", "tool_input": {"command": "<cmd>"}}
Output: deny = {"hookSpecificOutput": {...}} + exit 2 (the reason is also
        mirrored to stderr for harnesses reported to ignore stdout JSON
        at exit 2; the channel probe of 2026-10-24 confirmed stdout JSON
        reaches the model in this harness; the tier-3 reason carries the
        PACT-WAIT-OUTLET arm command);
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
_MAX_TIER = 3
_SATURATION_TOTAL = 6
_MAX_TRACKED_PAIRS = 64
_MAX_AGENT_SECTIONS = 32
_FILLER_STATE_DIRNAME = "pact-filler-state"
_SAFE_SESSION_ID = re.compile(r"[A-Za-z0-9_-]+")

# The tier-3 arm command's deadline default (seconds); the agent edits it
# when the expected wait differs.
_TIER3_DEADLINE = 900

# A state-section key this version only writes (as 0) for the previous
# release's reader; see _escalate.
_LEGACY_BUDGET_FIELD = "valve_budget"

_TIER2_SUFFIX = (
    " This exact call has now been denied {count} time{plural} this "
    "session ({total} filler calls in total)."
)
# --- tier-3 outlet texts: rendered from one closed table --------------------
#
# Each role's text is a preamble plus branches. A branch pairs one wait kind
# (what the waiting agent is waiting on) with one action; only an "arm"
# branch carries a THEN text (the watcher's exits and the wake it gives).
# The texts are rendered from this table, never hand-written, so the rules
# below are properties of the table a test can check:
#   R-ARM  an arming role arms exactly when the wait is another agent's
#          handoff or teachback, and no other branch names that wait;
#   R-THEN a THEN text appears only on an arm branch;
#   R-ROLE a role whose wake a watcher cannot give has no arm branch.
# "other" is the catch-all every role must carry: any wait not named above
# (a user reply, a message) still has an action.
WAIT_KINDS = ("teammate_deliverable", "own_write", "lead_action", "other")
WAIT_CONDITIONS = {
    "teammate_deliverable": "another agent's handoff or teachback submission",
    "own_write": "something only YOU can write (your own notes or a summary)",
    "lead_action": "the lead's review or action",
    "other": "anything else (a user reply, a message)",
}
# The watcher's exit vocabulary, owned here and rendered only inside an arm
# branch.
ARM_THEN = (
    "the watcher exits 0 when the deliverable lands, 3 when the deadline "
    "passes without it, 4 when it cannot watch at all"
)
_TIER3_HEADER = (
    "FILLER-LOCK: this exact call has been denied {count} times this "
    "session ({total} filler calls in total). PACT-WAIT-OUTLET: stop "
    "re-filling — end this wait structurally instead."
)
_ACTIONS = {
    "arm": (
        "arm the structural watcher — this is the wait it exists for, even "
        "though you cannot write the deliverable — with ONE `Bash` call "
        "(`run_in_background: true`), substituting the id of the task "
        "you are waiting on for <task-id>:\n{command}\n(use --predicate "
        "teachback_submit when the awaited deliverable is a teachback "
        "submission), then end your turn with no further tool calls"
    ),
    "intentional_wait": (
        "do NOT arm: SET metadata.intentional_wait on every task the wait "
        "covers — `reason`, `expected_resolver` and `since` are the "
        "keys validate_wait requires — with `expected_resolver` set to "
        "{resolver}, and end your turn with no further tool calls; a "
        "message or that resolver's own wake resolves it"
    ),
    "write_now": (
        "do NOT arm and do not wait: write it now, in this turn — nothing "
        "wakes you later to write it"
    ),
    "end_turn": (
        "end your turn with no further tool calls — a reply or message "
        "arrives as its own turn"
    ),
    "report": (
        "do NOT arm the structural watcher: end the turn with no further "
        "tool calls and put what the lead needs in your final report"
    ),
}
OUTLET = {
    "lead": {
        "preamble": "",
        "resolver": "`lead`",
        "branches": [
            ("teammate_deliverable", "arm", (
                "its completion notification re-invokes you; when the user "
                "may be away, also schedule a recurring `CronCreate` check "
                "keyed on the deliverable, not on whether a message arrived, "
                "and delete it when the wait resolves"
            )),
            ("own_write", "write_now", None),
            ("other", "end_turn", None),
        ],
    },
    "separate_process": {
        "preamble": (
            "As a separate-process teammate, a watcher's completion notice "
            "WILL start your next turn."
        ),
        "resolver": "`lead`",
        "branches": [
            ("teammate_deliverable", "arm", "its completion notice starts your next turn"),
            ("own_write", "write_now", None),
            ("lead_action", "intentional_wait", None),
            ("other", "end_turn", None),
        ],
    },
    "in_process": {
        "preamble": (
            "As an in-process teammate, a background watcher's completion "
            "will NOT wake you — the lead's message will."
        ),
        "resolver": "`lead`",
        "branches": [
            ("own_write", "write_now", None),
            ("teammate_deliverable", "intentional_wait", None),
            ("lead_action", "intentional_wait", None),
            ("other", "intentional_wait", None),
        ],
    },
    "subagent": {
        "preamble": (
            "As an Agent-tool subagent, nothing wakes you when your turn "
            "ends: your final report is your completion, and the lead reads "
            "it when it returns."
        ),
        "resolver": "`lead`",
        "branches": [(kind, "report", None) for kind in WAIT_KINDS],
    },
    "no_team": {
        "preamble": (
            "No task store resolves for this frame (there is no PACT team, "
            "or its task store is ambiguous), so there is nothing to watch "
            "and no arm command to run."
        ),
        "resolver": "`lead`",
        "branches": [(kind, "end_turn", None) for kind in WAIT_KINDS],
    },
}


def _join_conditions(kinds) -> str:
    conditions = [WAIT_CONDITIONS[kind] for kind in kinds]
    if len(conditions) == 1:
        return conditions[0]
    return ", ".join(conditions[:-1]) + " or " + conditions[-1]


def render_outlet(entry: dict) -> str:
    """One role's tier-3 text from its table entry: the header, the
    preamble, then each run of consecutive branches sharing an action and
    THEN text as one "If the event you await is ..., <action>." sentence.
    The result keeps {count}, {total} and (on an arm branch) {command} as
    format slots for the caller."""
    parts = [_TIER3_HEADER]
    if entry["preamble"]:
        parts.append(entry["preamble"])
    groups: list = []
    for kind, action, then in entry["branches"]:
        if groups and groups[-1][1:] == (action, then):
            groups[-1][0].append(kind)
        else:
            groups.append(([kind], action, then))
    for kinds, action, then in groups:
        sentence = (
            f"If the event you await is {_join_conditions(kinds)}, "
            + _ACTIONS[action].replace("{resolver}", entry["resolver"])
        )
        if then is not None:
            sentence += f" — {ARM_THEN} — and {then}"
        parts.append(sentence + ".")
    return " ".join(parts)


_TIER3_REASON = render_outlet(OUTLET["lead"])
_TIER3_TEAMMATE_SEPARATE_PROCESS_REASON = render_outlet(OUTLET["separate_process"])
_TIER3_TEAMMATE_REASON = render_outlet(OUTLET["in_process"])
_TIER3_SUBAGENT_REASON = render_outlet(OUTLET["subagent"])
_TIER3_NO_TEAM_REASON = render_outlet(OUTLET["no_team"])


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


def _compute_gradient(section, key: str):
    """Pure tier arithmetic for one agent_type section. No I/O, so
    the gradient is unit-testable without a filesystem.

    `section` is the parsed section dict, or None when absent (a fresh
    session). Returns (tier, count, total) with count/total INCLUDING
    the current attempt, or None when the section is not a dict: the
    caller treats that as UNKNOWN = DENY at tier 1, never harder on bad
    state.

    Soft fields — `pairs` (non-dict) and `total` (non-int/negative) —
    are reset to {}/0 and the reset is PERSISTED (repair-on-write): a
    reset only softens the gradient, so rewriting the record clean is
    safe. Unknown section keys (e.g. fields written by other hook
    versions) are ignored here and preserved by the writer.
    """
    if section is None:
        pairs, total = {}, 0
    elif not isinstance(section, dict):
        return None
    else:
        pairs = section.get("pairs")
        pairs = pairs if isinstance(pairs, dict) else {}
        total = section.get("total")
        total = total if isinstance(total, int) and total >= 0 else 0
    count = pairs.get(key)
    count = count if isinstance(count, int) and count >= 0 else 0
    new_count = count + 1
    new_total = total + 1
    tier = min(
        _MAX_TIER, new_count + (1 if new_total > _SATURATION_TOTAL else 0)
    )
    return tier, new_count, new_total


def _escalate(input_data, normalized):
    """Gradient verdict for a filler-matched command: the deny reason.
    The tier-3 reason carries the PACT-WAIT-OUTLET arm command.

    FAIL DIRECTION: any state failure — missing/unsafe session_id,
    unresolvable state module, unreadable file, corrupt JSON, lock
    timeout, growth cap reached — degrades to the frozen tier-1 advisory
    deny. A state error must never flip the verdict to allow (the
    matcher-level fail-open one branch up owns that) and never deny
    harder than tier 1.
    """
    session_id = input_data.get("session_id")
    agent_type = input_data.get("agent_type")
    agent_key = agent_type if isinstance(agent_type, str) else ""
    try:
        if not isinstance(normalized, str):
            # Unreachable while _is_filler_command and _normalize_filler
            # share one pure chain; guarded so the pairing cannot rot into
            # a crash if they ever diverge. Degrades, never denies harder.
            return _DENY_REASON
        if not isinstance(session_id, str) or not _SAFE_SESSION_ID.fullmatch(
            session_id
        ):
            # No session identity, or one that cannot be a filename:
            # never persist, deny at the frozen tier 1 (today's denial).
            return _DENY_REASON
        locked_update = _load_locked_update()
        if locked_update is None:
            return _DENY_REASON
        # Config root from the environment ONLY, with no literal
        # fallback: this is a degraded path that must not import the
        # resolver (shared package), and the unset-env case degrades to
        # the stateless tier-1 deny above rather than naming a root the
        # install may not use (test_hook_emitted_config_root contract).
        config_root = os.environ.get("CLAUDE_CONFIG_DIR")
        if not config_root:
            return _DENY_REASON
        root = os.path.join(config_root, _FILLER_STATE_DIRNAME)
        state_path = os.path.join(root, session_id + ".json")
        key = _pair_key(normalized)

        def _apply(text):
            """(new_text, changed, result) for locked_update. Pure given
            `text`; runs at most twice per call (locked_update contract).
            A result of None is the shared DEGRADE marker — corrupt JSON,
            non-dict state, corrupt section, or a growth cap — and
            leaves the file untouched."""
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
            gradient = _compute_gradient(section, key)
            if gradient is None:
                return text, False, None
            tier, new_count, new_total = gradient
            # Repair-and-persist for the soft `pairs` field: a non-dict
            # pairs value — truthy or falsy —
            # coerces to {} instead of raising, matching the guard
            # _compute_gradient already applies and the documented
            # repair-on-write contract. dict(x or {}) alone raised on
            # truthy non-dicts (dict("xy"), dict([1])) and degraded the
            # whole call without repairing the file.
            raw_pairs = (
                section.get("pairs") if isinstance(section, dict) else None
            )
            pairs = dict(raw_pairs) if isinstance(raw_pairs, dict) else {}
            if key not in pairs and len(pairs) >= _MAX_TRACKED_PAIRS:
                return text, False, None
            pairs[key] = new_count
            # Preserve unknown keys: copy the section and overwrite the
            # two known fields, so a rewrite by an OLDER hook never
            # strips fields a NEWER version wrote.
            new_section = dict(section) if isinstance(section, dict) else {}
            new_section["pairs"] = pairs
            new_section["total"] = new_total
            # Inert here, and always 0. The previous release reads a section
            # without a non-negative int under this key as corrupt and pins
            # the session at tier 1 after a downgrade; any value above 0
            # would re-open that release's retired allowance.
            new_section[_LEGACY_BUDGET_FIELD] = 0
            data[agent_key] = new_section
            return (json.dumps(data), True, (tier, new_count, new_total))

        result = locked_update(state_path, _apply, root, timeout=2.0)
        if result is None:
            return _DENY_REASON
        tier, new_count, new_total = result
        if tier >= _MAX_TIER:
            # Role-branched text, resolved through ONE identity pass (the
            # sibling launch advisory's resolution): a frame with no PACT
            # team gets the no-command text (nothing to watch); a resolved
            # NON-member non-lead frame — an Agent-tool subagent — gets the
            # dedicated subagent text (its completion IS its report;
            # teammate_launch_name refuses its subagent-shaped id); a
            # separate-process teammate gets the lead-accurate wake
            # statement; an in-process teammate gets the teammate
            # guidance; a lead keeps the lead text. Only the lead and
            # separate-process texts carry an arm command, built by the
            # shared builder with the resolved team substituted.
            team, member, separate = _tier3_frame_identity(input_data)
            command = _tier3_arm_command(team) if team else ""
            if not team or not command:
                template = _TIER3_NO_TEAM_REASON
            elif not is_teammate_frame(input_data):
                template = _TIER3_REASON
            elif not member:
                # agent_type present and non-lead, but member resolution
                # refused: an Agent-tool subagent (its subagent-shaped id
                # is refused by teammate_launch_name). Its completion IS
                # its report — no wake promise of any kind.
                template = _TIER3_SUBAGENT_REASON
            elif separate:
                template = _TIER3_TEAMMATE_SEPARATE_PROCESS_REASON
            else:
                template = _TIER3_TEAMMATE_REASON
            return template.format(
                command=command, count=new_count, total=new_total
            )
        if tier == 2:
            return _DENY_REASON + _TIER2_SUFFIX.format(
                count=new_count,
                plural="" if new_count == 1 else "s",
                total=new_total,
            )
        return _DENY_REASON
    except Exception as error:  # noqa: BLE001 — fail-direction catch-all
        print(
            f"wait_filler_gate: filler-state error — denying at tier 1 "
            f"(fail-direction): {error}",
            file=sys.stderr,
        )
        return _DENY_REASON


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


def _tier3_arm_command(team_name):
    """The tier-3 arm command for `team_name`, with the task id left as
    the placeholder the text tells the agent to substitute; "" when the
    shared builder cannot be loaded (the caller then falls back to the
    no-command text). Lazy import, like _tier3_frame_identity: `shared`
    is imported only on the tier-3 deny path."""
    try:
        from shared.wait_target import TASK_ID_PLACEHOLDER, arm_command

        return arm_command(
            team_name, TASK_ID_PLACEHOLDER, "handoff", _TIER3_DEADLINE
        )
    except Exception:
        return ""


def _tier3_frame_identity(input_data):
    """(team, member, separate_process) for the tier-3 text choice, from
    shared/wait_target.wait_identity — the one identity source the
    lifecycle gate's advisory also uses, so both producers name the same
    store:

    - ``team``: the store team an arm command names; "" when no store
      resolves (the no-command text's trigger).
    - ``member``: this frame's teammate name, "" for a lead, a plain frame,
      or an Agent-tool subagent (whose subagent-shaped ``agent_id`` is
      refused, the SAME rule that keeps the launch advisory off subagent
      frames).
    - ``separate_process``: ``teammate_is_separate_process`` under the
      membership team, FALSE on every doubt.

    The same lazy-import posture as launch_advisory_applies: `shared` is
    imported only here, on the tier-3 deny path — never on the allow fast
    path every non-filler Bash call takes."""
    try:
        from shared.background_work import teammate_is_separate_process
        from shared.wait_target import wait_identity

        identity = wait_identity(input_data)
        if not identity.store_team:
            return "", "", False
        if not identity.member:
            return identity.store_team, "", False
        return (
            identity.store_team,
            identity.member,
            teammate_is_separate_process(identity.member_team, identity.member),
        )
    except Exception:
        return "", "", False


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

        # DENY. The command normalizes to bare filler; the gradient picks
        # the exact tier text — at tier 3 the reason carries the
        # PACT-WAIT-OUTLET arm command. Every state failure inside
        # _escalate degrades to the frozen tier-1 advisory (fail-direction
        # invariant) — only a failure in the reporting below can reach the
        # outer matcher-level fail-open.
        normalized = _normalize_filler(command)
        reason = _escalate(input_data, normalized)
        # The reason rides stdout JSON (the surface the 2026-10-24 channel
        # probe confirmed this harness parses at exit 2) and is mirrored to
        # stderr for harnesses reported to ignore stdout at exit 2.
        print(reason, file=sys.stderr)
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
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
