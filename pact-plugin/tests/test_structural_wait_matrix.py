"""
TEST-phase matrix for the structural wait mechanism — the arms that bind
module SEAMS or PROSE, kept out of the CODE-phase modules so the dated
ablation tables in test_wait_filler_gate.py stay as-executed records
untouched by later additions (this file adds no row to any dated table).

Seams and prose bound here:
- emission <-> carve-out coherence: every arm command an emitter produces
  (the lifecycle gate's advisory and each arming tier-3 text, built by
  wait_target.arm_command) MUST satisfy the turn-end carve-out's watcher
  shape (`is_wait_watcher_command`) for any install path and team name,
  or the arming frame gets blocked over its own scheduled wake — the exact
  loop the mechanism exists to remove — and the same command with a
  composed tail must NOT satisfy it.
- emission <-> registry coherence: every `--predicate` token either surface
  emits must name a live `wait_target.PREDICATES` entry, or a correctly
  shaped arm would exit 4 TERMINAL on delivery of a real wait.
- advisory message pin: the advisory carries its own termination-adjacent
  claims (exit 0/3/4, duplicate fast-exit), so its wording is pinned
  verbatim under the same discipline as the tier-3 outlet reason.
- deadline defaults: 1800 (arm-supply advisory) and 900 (tier-3 template)
  are BOTH pinned as stated-editable defaults; the difference between the
  surfaces is ruled intended and the pair freezes it.
- prose: the falsified deterministic-termination claim stays absent from
  the filler gate's source (population: THAT FILE ONLY — the token is
  common elsewhere in the tree), and the SKILL.md handoff template stays
  string-typed; the list-typed render was the live failure class that
  motivated the template fix.

Counter-test evidence for this module (source-only mutations, each
predicted then observed to redden exactly its target arm, tree restored
byte-identical after each) is recorded in the TEST-phase HANDOFF.
"""
import json
import re
import shlex
from pathlib import Path

import pytest

import pact_wait  # noqa: E402
from test_pact_wait import _step_file_between_polls  # noqa: E402
import task_lifecycle_gate as tlg  # noqa: E402
import wait_filler_gate  # noqa: E402
from shared import wait_target  # noqa: E402
from shared.handoff_schema import HANDOFF_CANONICAL_FIELDS  # noqa: E402
from shared.turn_end_gate import is_wait_watcher_command  # noqa: E402

PLUGIN = Path(__file__).resolve().parents[1]
SKILL_MD = PLUGIN / "skills" / "pact-agent-teams" / "SKILL.md"
FILLER_GATE = PLUGIN / "hooks" / "wait_filler_gate.py"

# Restated literally for the anti-array arm: docs changing alone must trip
# it even if the schema tuple changes with them (the imported arm below
# owns the docs-vs-code divergence direction).
TEMPLATE_FIELDS = (
    "produced",
    "decisions",
    "reasoning_chain",
    "uncertainty",
    "integration",
    "open_questions",
)


def _advisory(predicate="handoff", task_id="12", team="team-x"):
    return tlg._wait_arm_advisory("teachback accepted", team, task_id, predicate)


def _command_lines(text):
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith("python3")]


# --- emission <-> carve-out coherence ---------------------------------------

def test_emitted_advisory_command_satisfies_the_carveout_shape():
    """The advisory's command line IS a watcher the turn-end carve-out
    exempts. If the emission format ever drifts (flag order, a new flag,
    a wrapper), the lead's real watcher starts counting as unowned work
    and the lead-block re-creates the wait loop this mechanism retires."""
    _rule, message = _advisory()
    commands = _command_lines(message)
    assert len(commands) == 1, message
    assert is_wait_watcher_command(commands[0]), commands[0]


# Install paths and team names every emitter must survive: the shipped
# layout, a spaced home, an apostrophe, a shell metacharacter, a quote.
_INSTALL_PATHS = [
    wait_target.WATCHER_PATH,
    "/home/u/.claude/plugins/cache/pact-plugin/PACT/4.8.37/hooks/pact_wait.py",
    "/Users/Jane Doe/.claude/plugins/cache/pact-plugin/hooks/pact_wait.py",
    "/opt/o'brien/hooks/pact_wait.py",
    "/opt/a;b$c/hooks/pact_wait.py",
    '/opt/"q"/hooks/pact_wait.py',
]
_TEAMS = ["session-ecae8e2c", "team x", "o'k"]

# The tier-3 texts that carry an arm command, and the ones that must not.
_ARMING_TIER3 = ["_TIER3_REASON", "_TIER3_TEAMMATE_SEPARATE_PROCESS_REASON"]
_NON_ARMING_TIER3 = [
    "_TIER3_TEAMMATE_REASON",
    "_TIER3_SUBAGENT_REASON",
    "_TIER3_NO_TEAM_REASON",
]


def _emitted_commands(watcher, team):
    """Every command line an emitter produces for this install path and
    team, placeholders substituted the way the texts instruct."""
    commands = []
    _rule, message = tlg._wait_arm_advisory("teachback accepted", team, "12", "handoff")
    advisory = _command_lines(message)
    assert len(advisory) == 1, message
    commands.append(
        advisory[0].replace(
            shlex.quote(wait_target.WATCHER_PATH), shlex.quote(watcher), 1
        )
    )
    command = wait_target.arm_command(
        team, wait_target.TASK_ID_PLACEHOLDER, "handoff",
        wait_filler_gate._TIER3_DEADLINE, watcher=watcher,
    )
    for name in _ARMING_TIER3:
        text = getattr(wait_filler_gate, name).format(
            command=command, count=3, total=3
        )
        lines = _command_lines(text)
        assert len(lines) == 1, (name, text)
        commands.append(lines[0].replace(wait_target.TASK_ID_PLACEHOLDER, "12"))
    return commands


@pytest.mark.parametrize("watcher", _INSTALL_PATHS)
@pytest.mark.parametrize("team", _TEAMS)
def test_every_emitted_command_satisfies_the_carveout(watcher, team):
    """Every emitter (the lifecycle advisory and each arming tier-3 text),
    for any install path and team name, produces a command the turn-end
    carve-out classifies as the watcher once the task id is substituted —
    or the arming frame is blocked over its own scheduled wake."""
    for command in _emitted_commands(watcher, team):
        assert is_wait_watcher_command(command), command


@pytest.mark.parametrize("watcher", _INSTALL_PATHS)
@pytest.mark.parametrize(
    "tail", ["; make", ";make", " && make", "&&make", " | tee x", " & sleep 1"]
)
def test_every_emitted_command_with_a_composed_tail_counts(watcher, tail):
    """The same emitted commands with any command composed onto them are
    not the bare wake, so the carve-out must not exempt them."""
    for command in _emitted_commands(watcher, "session-ecae8e2c"):
        assert not is_wait_watcher_command(command + tail), command + tail


def test_emitters_use_the_one_builder():
    """The arming tier-3 texts embed the builder's output (a {command}
    slot, no hand-written command), and the texts for roles a watcher
    cannot wake carry no command at all."""
    for name in _ARMING_TIER3:
        text = getattr(wait_filler_gate, name)
        assert "{command}" in text, name
        assert "pact_wait.py" not in text, name
    for name in _NON_ARMING_TIER3:
        text = getattr(wait_filler_gate, name)
        assert "{command}" not in text, name
        assert "pact_wait" not in text, name
    assert not hasattr(tlg, "_WAIT_WATCHER_PATH")
    assert not hasattr(wait_filler_gate, "_WATCHER_PATH")


def test_every_emitted_predicate_names_a_live_registry_entry():
    """Both emission surfaces may only name predicates the registry ships.
    A renamed or typo'd predicate would still produce a perfectly shaped
    command whose watch TERMINALs (exit 4) instead of watching. The
    lifecycle shapes are driven through evaluate_lifecycle END-TO-END so
    the literals at the SHIPPED CALL SITES are what gets checked — a
    direct _wait_arm_advisory call would stay green on a typo'd call
    site (observed: the first draft of this arm did exactly that)."""
    _rule, message = _advisory()
    emitted = set(re.findall(r"--predicate\s+(\S+)", message))
    emitted |= set(
        re.findall(r"--predicate\s+(\S+)", wait_filler_gate._tier3_arm_command("t"))
    )
    emitted |= set(re.findall(r"--predicate\s+(\w+)", wait_filler_gate._TIER3_REASON))
    assert emitted, "no predicate tokens found on either emission surface"
    assert emitted <= set(wait_target.PREDICATES), emitted - set(
        wait_target.PREDICATES
    )


def _drive_lifecycle_arm(tmp_path, monkeypatch, pact_context, payload, seed=None):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name="test-team", session_id="test-session")
    if seed is not None:
        _seed_task(
            tmp_path, "test-team", seed[0],
            subject=seed[1], status=seed[2], owner=seed[3],
        )
    monkeypatch.setattr(tlg, "is_pact_specialist_owner", lambda *_a, **_k: True)
    advisories = tlg.evaluate_lifecycle(payload)
    arms = [msg for rule, msg in advisories if rule == "wait_arm_supply"]
    return arms


def test_acceptance_shape_emits_a_live_predicate(tmp_path, monkeypatch, pact_context):
    """End-to-end at the teachback-acceptance shape: the emitted arm names
    a registry-shipped predicate (the call-site literal is the thing a
    rename can silently orphan). The blocked entry qualifies via its
    on-disk record — specialist owner, still pending."""
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "A", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "A",
                "subject": "preparer: TEACHBACK for foo",
                "owner": "pact-preparer",
                "blocks": ["B"],
                "metadata": {"teachback_submit": {"understanding": "u"}},
            }
        },
    }
    arms = _drive_lifecycle_arm(
        tmp_path, monkeypatch, pact_context, payload,
        seed=("B", "implement hook core", "pending", "backend-coder"),
    )
    assert len(arms) == 1, f"expected one arm advisory, got: {arms}"
    for token in re.findall(r"--predicate\s+(\S+)", arms[0]):
        assert token in wait_target.PREDICATES, token


def test_wiring_shape_emits_a_live_predicate(tmp_path, monkeypatch, pact_context):
    """End-to-end at the work-task wiring shape: same call-site coupling
    for the `handoff` literal the wiring branch passes."""
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "B",
            "owner": "backend-coder",
            "addBlockedBy": ["A"],
        },
        "tool_response": {},
    }
    arms = _drive_lifecycle_arm(
        tmp_path, monkeypatch, pact_context, payload,
        seed=("B", "implement hook core", "pending", "backend-coder"),
    )
    assert len(arms) == 1, f"expected one arm advisory, got: {arms}"
    for token in re.findall(r"--predicate\s+(\S+)", arms[0]):
        assert token in wait_target.PREDICATES, token


def test_dispatch_shape_emits_a_live_predicate(tmp_path, monkeypatch, pact_context):
    """End-to-end at the teachback-gate dispatch shape: the pending-only
    branch's `teachback_submit` literal is registry-coupled too."""
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "A", "owner": "pact-preparer"},
        "tool_response": {},
    }
    arms = _drive_lifecycle_arm(
        tmp_path, monkeypatch, pact_context, payload,
        seed=("A", "preparer: TEACHBACK for foo", "pending", "pact-preparer"),
    )
    assert len(arms) == 1, f"expected one arm advisory, got: {arms}"
    for token in re.findall(r"--predicate\s+(\S+)", arms[0]):
        assert token in wait_target.PREDICATES, token


def test_emitted_flag_set_is_accepted_by_the_watcher_cli(tmp_path):
    """The advisory's flag VALUES are exactly what the watcher consumes:
    feeding the emitted command's arguments to the real CLI, hermetic via
    the testing seam, watches the named task and FIRES on a delivered
    handoff. This closes the emission->consumption contract end to end."""
    _rule, message = _advisory(predicate="handoff", task_id="12", team="team-x")
    command = _command_lines(message)[0]
    argv = shlex.split(command)[2:]  # drop the interpreter and script tokens
    (Path(tmp_path) / "team-x").mkdir()
    (Path(tmp_path) / "team-x" / "12.json").write_text(
        '{"metadata": {"handoff": {"produced": "delivered"}}}', encoding="utf-8"
    )
    rc = pact_wait.main(argv + ["--tasks-base-dir", str(tmp_path)])
    assert rc == pact_wait.EXIT_FIRED


def test_advisory_message_pinned_verbatim():
    """The advisory message carries termination-adjacent claims (the
    0/3/4 exit contract, the duplicate fast-exit reassurance), so its
    wording is pinned the way the outlet reason is: an edit must be a
    conscious pin update, never a silent drift. Built around the live
    watcher path so only WORDING drifts redden it. The path renders
    UNQUOTED here: the emission shlex-quotes it, which is a no-op on this
    special-char-free install path (a spaced install path renders safely
    quoted instead)."""
    expected = (
        "PACT task_lifecycle_gate: teachback accepted — you are about to "
        "wait on a teammate deliverable. Arm the structural wait watcher "
        "BEFORE you end this turn, with ONE `Bash` call (`run_in_background: "
        "true`):\n"
        f"python3 {wait_target.WATCHER_PATH} --team team-x "
        "--task 12 --predicate handoff --deadline 1800\n"
        "Its completion notification re-invokes you: exit 0 deliverable "
        "landed, 3 deadline expired with it absent, 4 could not watch at "
        "all. A duplicate arm whose deliverable is already present exits "
        "0 immediately — re-arming is safe."
    )
    _rule, message = _advisory()
    assert message == expected


# --- deadline defaults (the pair is ruled intended; freeze both) ------------

def test_deadline_defaults_pinned_on_both_surfaces():
    """1800 (arm-supply advisory) and 900 (tier-3 template) are both
    stated-editable defaults on different surfaces; the difference is
    ruled intended. The pair pin freezes BOTH numbers so neither surface
    silently adopts the other's value."""
    _rule, message = _advisory()
    tier3 = wait_filler_gate._tier3_arm_command("t")
    assert wait_filler_gate._TIER3_DEADLINE == 900
    assert "--deadline 1800" in message
    assert tier3.endswith("--deadline 900")
    assert "--deadline 900" not in message


# --- prose: the falsified claim stays absent --------------------------------

def test_falsified_deterministic_termination_claim_stays_absent():
    """The filler gate's docstring once claimed the filler valve
    deterministically ended waits — measured false (the allowance produced
    exactly the loop it claimed to end). The claim's token stays absent
    from THAT FILE's source. Population is this one file: the token is
    common and legitimate elsewhere in the tree."""
    source = FILLER_GATE.read_text(encoding="utf-8")
    assert "deterministic" not in source.lower()


# --- prose: the SKILL.md template stays string-typed ------------------------

def _store_handoff_block():
    """The fenced TaskUpdate example under 'Store HANDOFF in task
    metadata' — the template two live teammates followed into list-typed
    handoffs."""
    text = SKILL_MD.read_text(encoding="utf-8")
    marker = "1. **Store HANDOFF in task metadata**:"
    head, rest = text.split(marker, 1)
    fence_start = rest.index("```")
    fence_end = rest.index("```", fence_start + 3)
    return rest[fence_start:fence_end]


def test_skillmd_handoff_template_values_are_string_typed():
    """Docs-vs-code, imported arm: every canonical handoff field the schema
    ships is rendered in the SKILL.md example as a JSON STRING (the value
    opens with a quote). A template that taught a non-string `produced`
    would be refused by validate_handoff_schema's produced type gate and
    read as ABSENT by wait_target.handoff_present — the list-typed failures
    this mechanism answers."""
    block = _store_handoff_block()
    for field in HANDOFF_CANONICAL_FIELDS:
        assert f'"{field}": "' in block, (
            f"{field} is not rendered as a string-typed example value"
        )


def test_skillmd_handoff_template_has_no_array_or_object_renders():
    """Literal anti-revert arm, restated field list: no canonical field is
    rendered as a JSON array or object in the example. The list-typed
    render was the measured root cause of two live list-typed handoffs;
    reverting the example to arrays must redden here even if nothing else
    changed."""
    block = _store_handoff_block()
    for field in TEMPLATE_FIELDS:
        assert f'"{field}": [' not in block, field
        assert f'"{field}": {{' not in block, field


def test_handoff_teaching_surfaces_render_no_array_canonical_fields():
    """Coherence arm: the array-template failure class must
    be dead on EVERY LLM-loaded surface that teaches handoff metadata —
    the protocols SSOT, its byte-compared task-hierarchy extract, and the
    secretary's memory-save example — not only the SKILL.md template. A
    canonical field rendered with a bracket-opened value is the live
    false-EXPIRED class: the watcher reads a list-typed `produced` as
    ABSENT and burns to a loud EXPIRED (degraded-loud, per
    wait_target.handoff_present)."""
    surfaces = [
        PLUGIN / "protocols" / "pact-protocols.md",
        PLUGIN / "protocols" / "pact-task-hierarchy.md",
        PLUGIN / "agents" / "pact-secretary.md",
    ]
    for surface in surfaces:
        text = surface.read_text(encoding="utf-8")
        for field in TEMPLATE_FIELDS:
            assert f'"{field}": [' not in text, (
                f"{surface.name}: canonical field {field} is rendered as "
                "a JSON array — the watcher reads it as ABSENT and burns "
                "its deadline (the false-EXPIRED class)"
            )


# --- silent skip: no team context, no advisory, no crash --------------------

def _seed_task(tmp_path: Path, team: str, task_id: str, **fields) -> None:
    tasks_dir = tmp_path / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps({"id": task_id, **fields}), encoding="utf-8"
    )


def test_no_team_context_skips_the_arm_advisory_silently(tmp_path, monkeypatch):
    """Without a resolvable team the wiring branch is skipped outright: no
    arm advisory, no crash, no half-formed command. The advisory is
    SUPPLY at known-target shapes; an unresolvable target is silence, not
    a broken arm."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _seed_task(tmp_path, "test-team", "B", subject="implement hook core", status="pending")
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "B",
            "owner": "backend-coder",
            "addBlockedBy": ["A"],
        },
        "tool_response": {},
    }
    monkeypatch.setattr(tlg, "is_pact_specialist_owner", lambda *_a, **_k: True)
    advisories = tlg.evaluate_lifecycle(payload)
    assert not any(rule == "wait_arm_supply" for rule, _ in advisories)


# --- post-rejection wait: reject -> arm -> not fired -> revise -> fired -----

@pytest.mark.parametrize(
    "predicate,key,rejection_key,deliverable",
    [
        ("handoff", "handoff", "handoff_rejection", {"produced": "p", "decisions": "d"}),
        ("teachback_submit", "teachback_submit", "teachback_rejection", {"understanding": "u"}),
    ],
)
def test_watch_armed_after_a_rejection_fires_only_on_the_revision(
    tmp_path, monkeypatch, predicate, key, rejection_key, deliverable
):
    """A watch armed after the lead rejects a deliverable must not fire on
    the rejected original still on disk, and must fire once the teammate
    re-submits with metadata.revision_number raised past the rejection's
    (the rejection protocol). Driven through the real watcher loop."""
    team_dir = tmp_path / "team-x"
    team_dir.mkdir()
    task_file = team_dir / "12.json"
    rejected = {"metadata": {key: deliverable, rejection_key: {"revision_number": 1}}}
    task_file.write_text(json.dumps(rejected), encoding="utf-8")

    # Not fired: the stale original alone runs the watch to EXPIRED.
    assert pact_wait.wait_for_deliverable(
        "team-x", "12", predicate, deadline=0.2,
        tasks_base_dir=str(tmp_path), poll_interval=0.05,
    ) == pact_wait.EXIT_EXPIRED

    # Fired: the revision lands between the first and second poll.
    revised = {"metadata": {**rejected["metadata"], "revision_number": 2}}
    sleeps = _step_file_between_polls(monkeypatch, task_file, [json.dumps(revised)])
    rc = pact_wait.wait_for_deliverable(
        "team-x", "12", predicate, deadline=30,
        tasks_base_dir=str(tmp_path), poll_interval=0.05,
    )
    assert rc == pact_wait.EXIT_FIRED
    assert len(sleeps) == 1


# --- prose: each role's teammate-deliverable wait in the agent-teams skill ---

SELF_STARTED_HEADING = "### Wait Discipline for Self-Started Work"

# One whole clause per role, each carrying its polarity word ("arms" or
# "does not arm"), so inverting any role's rule reddens its own row.
ROLE_WAIT_CLAUSES = [
    "A separate-process teammate (tmux teammateMode) arms the structural "
    "watcher with one Bash call (run_in_background: true) and ends the turn",
    "An in-process teammate does not arm a watcher: it SETs intentional_wait "
    "with expected_resolver naming the lead and ends the turn.",
    "An Agent-tool subagent does not arm a watcher; it puts what the lead "
    "needs in its final report.",
]


def _normalized(text):
    return " ".join(text.replace("`", "").split())


def _self_started_section():
    """The body under the self-started-work heading, up to the next
    heading. The heading line itself is excluded, so a heading can never
    satisfy a clause pin."""
    lines = SKILL_MD.read_text(encoding="utf-8").splitlines()
    start = lines.index(SELF_STARTED_HEADING)
    body = []
    for line in lines[start + 1:]:
        if line.startswith("#"):
            break
        body.append(line)
    return _normalized("\n".join(body))


@pytest.mark.parametrize("clause", ROLE_WAIT_CLAUSES, ids=["separate", "in-process", "subagent"])
def test_skill_states_each_roles_deliverable_wait(clause):
    """Each role's wait on another teammate's deliverable matches what the
    tier-3 outlet tells that role: the separate-process teammate arms, the
    in-process teammate and the subagent do not."""
    assert _normalized(clause) in _self_started_section(), clause


# --- one identity source: wait_identity cells and cross-producer --team ------
#
# Each cell builds the hermetic config root a live session leaves behind and
# runs the REAL entry points as child processes (fresh interpreter caches):
# wait_identity itself, the filler gate's tier-3 denial, and the lifecycle
# gate's owner-wiring advisory. Both producers must name the store
# wait_identity names, and a fail-closed cell must make neither emit.

_PLUGIN = Path(__file__).resolve().parents[1]
_HOOKS = _PLUGIN / "hooks"
_LEAD_SID = "aaaa1111-0000-4000-8000-000000000001"
_SP_SID = "bbbb2222-0000-4000-8000-000000000002"
_IN_PROCESS = [
    {"name": "team-lead", "agentType": "pact-orchestrator"},
    {"name": "backend-coder", "agentType": "pact-backend-coder"},
]
_SEPARATE = [
    {"name": "team-lead", "agentType": "pact-orchestrator"},
    {"name": "backend-coder", "agentType": "pact-backend-coder", "backendType": "tmux"},
]
_LEAD_CONFIG = {"members": _IN_PROCESS, "leadSessionId": _LEAD_SID}
_LEAD_FRAME = {"session_id": _LEAD_SID, "agent_type": "PACT:pact-orchestrator"}

# (cell, world, frame, store_team, member, tier3 teams, lifecycle teams)
_IDENTITY_CELLS = [
    ("lead", dict(ctx_team="s-team", tasks=["s-team"], configs={"s-team": _LEAD_CONFIG}),
     _LEAD_FRAME, "s-team", "", ["s-team"], ["s-team"]),
    # No team config: the context names session-<id8>, the store is the
    # session-UUID directory. No config means no specialist owner, so the
    # lifecycle gate has nothing to arm; the outlet names the store.
    ("lead, config-less", dict(ctx_team="session-aaaa1111", tasks=[_LEAD_SID]),
     _LEAD_FRAME, _LEAD_SID, "", [_LEAD_SID], []),
    # The context and the team config are keyed session-<id8>, the store is
    # the session-UUID directory: tasks come from the store, roles from the
    # config, so the lifecycle gate arms too and both name the store.
    ("lead, config under session-<id8>",
     dict(ctx_team="session-aaaa1111", tasks=[_LEAD_SID],
          configs={"session-aaaa1111": _LEAD_CONFIG}),
     _LEAD_FRAME, _LEAD_SID, "", [_LEAD_SID], [_LEAD_SID]),
    ("lead, both names exist (fail closed)",
     dict(ctx_team="persisted-team", tasks=["persisted-team", "aligned-team"],
          configs={"aligned-team": _LEAD_CONFIG,
                   "persisted-team": {"members": _IN_PROCESS}}),
     _LEAD_FRAME, "", "", [], []),
    ("in-process teammate", dict(ctx_team="s-team", tasks=["s-team"], configs={"s-team": _LEAD_CONFIG}),
     {"session_id": _LEAD_SID, "agent_type": "backend-coder",
      "agent_id": "abackend-coder-0123456789abcdef"},
     "s-team", "backend-coder", [], []),
    ("separate-process teammate", dict(tasks=["s-team"], configs={"s-team": {"members": _SEPARATE}},
                                       registry=(_SP_SID, "backend-coder@s-team")),
     {"session_id": _SP_SID, "agent_type": "pact-backend-coder"},
     "s-team", "backend-coder", ["s-team"], []),
    ("separate-process teammate, no store (fail closed)",
     dict(configs={"s-team": {"members": _SEPARATE}}, registry=(_SP_SID, "backend-coder@s-team")),
     {"session_id": _SP_SID, "agent_type": "pact-backend-coder"},
     "", "backend-coder", [], []),
    ("Agent-tool subagent", dict(ctx_team="s-team", tasks=["s-team"], configs={"s-team": _LEAD_CONFIG}),
     {"session_id": _LEAD_SID, "agent_type": "general-purpose", "agent_id": "ad2b1261fdd77c958"},
     "s-team", "", [], []),
    ("nothing resolves", dict(), {"session_id": "cccc3333-0000-4000-8000-000000000003"},
     "", "", [], []),
]


def _identity_world(root, ctx_team=None, tasks=(), configs=None, registry=None):
    import os

    project = root / "proj"
    project.mkdir()
    config = root / "config"
    if ctx_team is not None:
        session_dir = config / "pact-sessions" / project.resolve().name / _LEAD_SID
        session_dir.mkdir(parents=True)
        (session_dir / "pact-session-context.json").write_text(json.dumps({
            "team_name": ctx_team, "session_id": _LEAD_SID,
            "project_dir": str(project), "plugin_root": str(_PLUGIN),
            "started_at": "2026-11-24T00:00:00Z",
        }))
    for team in tasks:
        (config / "tasks" / team).mkdir(parents=True, exist_ok=True)
    for team, team_config in (configs or {}).items():
        (config / "teams" / team).mkdir(parents=True, exist_ok=True)
        (config / "teams" / team / "config.json").write_text(json.dumps(team_config))
    if registry:
        (config / "pact-sessions").mkdir(parents=True, exist_ok=True)
        (config / "pact-sessions" / ".teammate-registry.jsonl").write_text(
            json.dumps({"session_id": registry[0], "value": registry[1]}) + "\n"
        )
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
    env.update(CLAUDE_CONFIG_DIR=str(config), CLAUDE_PROJECT_DIR=str(project),
               CLAUDE_PLUGIN_ROOT=str(_PLUGIN))
    return env, config


def _run(args, env, stdin=None):
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, *args], input=stdin, capture_output=True, text=True,
        env=env, cwd="/", timeout=60, check=False,
    )


def _teams_in(text):
    return [ln.split("--team ")[1].split(" ")[0]
            for ln in text.splitlines() if ln.startswith("python3")]


@pytest.mark.parametrize(
    "cell,world,frame,store_team,member,tier3_teams,lifecycle_teams",
    _IDENTITY_CELLS, ids=[c[0] for c in _IDENTITY_CELLS],
)
def test_wait_identity_cells_and_cross_producer_team(
    tmp_path, cell, world, frame, store_team, member, tier3_teams, lifecycle_teams
):
    env, config = _identity_world(tmp_path, **world)
    probe = (
        "import sys, json; "
        "from shared.wait_target import wait_identity; "
        "print(json.dumps(list(wait_identity(json.loads(sys.argv[1])))))"
    )
    # The child imports shared.* from the hooks dir through PYTHONPATH.
    probe_env = dict(env, PYTHONPATH=str(_HOOKS))
    identity = _run(["-c", probe, json.dumps(frame)], probe_env)
    assert identity.returncode == 0, identity.stderr
    got_store, _member_team, got_member = json.loads(identity.stdout)
    assert (got_store, got_member) == (store_team, member), cell

    bash = dict(frame, tool_name="Bash", tool_input={"command": "true"},
                hook_event_name="PreToolUse")
    for _ in range(3):
        denied = _run([str(_HOOKS / "wait_filler_gate.py")], env, json.dumps(bash))
    reason = json.loads(denied.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert _teams_in(reason) == tier3_teams, (cell, reason)

    if store_team:
        (config / "tasks" / store_team / "B.json").write_text(json.dumps({
            "id": "B", "subject": "implement x", "status": "pending",
            "owner": "backend-coder",
        }))
    wiring = dict(frame, hook_event_name="PostToolUse", tool_name="TaskUpdate",
                  tool_input={"taskId": "B", "owner": "backend-coder"},
                  tool_response={"success": True, "taskId": "B",
                                 "updatedFields": ["owner"]})
    wired = _run([str(_HOOKS / "task_lifecycle_gate.py")], env, json.dumps(wiring))
    assert wired.returncode == 0, wired.stderr
    advisory = (json.loads(wired.stdout).get("hookSpecificOutput", {})
                .get("additionalContext", "") if wired.stdout.strip() else "")
    assert _teams_in(advisory) == lifecycle_teams, (cell, advisory)
    # Cross-producer: every emitted --team is the one identity's store.
    assert set(_teams_in(reason)) | set(_teams_in(advisory)) <= {store_team}


# The wait-mechanism functions in both gates: every place a store or
# membership team is consumed on an arm or advisory path.
_WAIT_PATH_FUNCTIONS = {
    tlg: ("arm_candidates", "_handoff_ready", "_teachback_ready", "_open_blockers",
          "revision_advisories", "_wait_arm_advisory"),
    wait_filler_gate: ("_tier3_frame_identity", "_tier3_arm_command", "render_outlet"),
}
_TEAM_RESOLVERS = {"resolve_task_dir", "frame_team_and_name", "get_team_name",
                   "resolve_team_dir", "task_dir_for"}


def _called_names(func):
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(func).lstrip())
    return {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }


def test_identity_consumer_census():
    """Census of every identity consumer on the wait paths: no wait-path
    function in either gate resolves a team itself, the filler gate's
    identity comes from wait_identity, and evaluate_lifecycle feeds BOTH
    wait paths (arming and revision advisories) from one wait_identity
    call: arming reads tasks under the store team and roles under the
    membership team. Lead cells alone cannot tell a bypass from a call, so
    this holds the route by enumeration."""
    import ast
    import inspect

    for module, names in _WAIT_PATH_FUNCTIONS.items():
        for name in names:
            leaked = _called_names(getattr(module, name)) & _TEAM_RESOLVERS
            assert not leaked, (module.__name__, name, leaked)
    assert "wait_identity" in _called_names(wait_filler_gate._tier3_frame_identity)

    tree = ast.parse(inspect.getsource(tlg.evaluate_lifecycle).lstrip())
    calls = {
        getattr(node.func, "id", getattr(node.func, "attr", "")): node
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    assert "wait_identity" in calls
    rendered = {name: ast.unparse(calls[name]) for name in
                ("revision_advisories", "arm_candidates")}
    assert "identity.store_team" in rendered["revision_advisories"], rendered
    assert "identity.member_team" in rendered["arm_candidates"], rendered
    source = inspect.getsource(tlg.evaluate_lifecycle)
    assert "store_team = identity.store_team" in source


# --- tier-3 outlet table: the five mechanical checks ------------------------

_ARMING_ROLES = ("lead", "separate_process")


def _check_r_arm(table):
    """R-ARM: each arming role arms exactly on another agent's deliverable:
    that wait has one branch and it arms, and no branch for any other wait
    arms."""
    for role in _ARMING_ROLES:
        deliverable = [b for b in table[role]["branches"]
                       if b[0] == "teammate_deliverable"]
        assert len(deliverable) == 1 and deliverable[0][1] == "arm", role
        for kind, action, _then in table[role]["branches"]:
            assert (action == "arm") == (kind == "teammate_deliverable"), (role, kind)


def _check_r_then(table):
    """R-THEN: a THEN text exists exactly on arm branches."""
    for role, entry in table.items():
        for kind, action, then in entry["branches"]:
            assert (then is not None) == (action == "arm"), (role, kind)


def _check_r_role(table):
    """R-ROLE: a role a watcher cannot wake has no arm branch, and its
    rendered text carries no command slot and no exit clause."""
    for role, entry in table.items():
        if role in _ARMING_ROLES:
            continue
        assert all(action != "arm" for _k, action, _t in entry["branches"]), role
        text = wait_filler_gate.render_outlet(entry)
        assert "{command}" not in text, role
        assert wait_filler_gate.ARM_THEN not in text, role


def _check_catch_all(table):
    """Every role has a branch for the catch-all wait, so a wait the named
    kinds do not cover (a user reply, CI, a message) still has an action."""
    for role, entry in table.items():
        kinds = [kind for kind, _action, _then in entry["branches"]]
        assert kinds.count("other") == 1, role


def test_outlet_table_satisfies_r_arm_r_then_r_role():
    table = wait_filler_gate.OUTLET
    _check_catch_all(table)
    assert set(table) == {"lead", "separate_process", "in_process", "subagent", "no_team"}
    assert {b[0] for e in table.values() for b in e["branches"]} <= set(
        wait_filler_gate.WAIT_KINDS
    )
    _check_r_arm(table)
    _check_r_then(table)
    _check_r_role(table)


def test_rendered_texts_are_the_table_render():
    """Check 4: each module text IS the render of its table entry, so a
    hand edit to a rendered string that bypasses the table goes red. The
    arming texts carry the {command} slot exactly once."""
    by_role = {
        "lead": wait_filler_gate._TIER3_REASON,
        "separate_process": wait_filler_gate._TIER3_TEAMMATE_SEPARATE_PROCESS_REASON,
        "in_process": wait_filler_gate._TIER3_TEAMMATE_REASON,
        "subagent": wait_filler_gate._TIER3_SUBAGENT_REASON,
        "no_team": wait_filler_gate._TIER3_NO_TEAM_REASON,
    }
    for role, text in by_role.items():
        assert text == wait_filler_gate.render_outlet(wait_filler_gate.OUTLET[role]), role
    for role in _ARMING_ROLES:
        assert by_role[role].count("{command}") == 1, role
        assert by_role[role].count(wait_filler_gate.ARM_THEN) == 1, role


def test_outlet_checks_bite_on_bad_tables():
    """Check 5, negative controls: a table that maps another agent's
    deliverable to intentional_wait in an arming role fails R-ARM, and one
    with a THEN text on a non-arm branch fails R-THEN, so both checks are
    live."""
    import copy

    bad_arm = copy.deepcopy(wait_filler_gate.OUTLET)
    bad_arm["lead"]["branches"][0] = ("teammate_deliverable", "intentional_wait", None)
    with pytest.raises(AssertionError):
        _check_r_arm(bad_arm)
    # An arming role that also arms on a wait other than another agent's
    # deliverable fails R-ARM, even with the same THEN text (which renders
    # as one sentence and one command, so the render checks cannot see it).
    extra_arm = copy.deepcopy(wait_filler_gate.OUTLET)
    then = extra_arm["lead"]["branches"][0][2]
    extra_arm["lead"]["branches"][1] = ("own_write", "arm", then)
    with pytest.raises(AssertionError):
        _check_r_arm(extra_arm)
    bad_then = copy.deepcopy(wait_filler_gate.OUTLET)
    bad_then["in_process"]["branches"][0] = ("teammate_deliverable", "intentional_wait", "exits")
    with pytest.raises(AssertionError):
        _check_r_then(bad_then)
    bad_role = copy.deepcopy(wait_filler_gate.OUTLET)
    bad_role["subagent"]["branches"][0] = ("teammate_deliverable", "arm", "wakes")
    with pytest.raises(AssertionError):
        _check_r_role(bad_role)
    # A role with no catch-all branch fails the catch-all check.
    no_catch_all = copy.deepcopy(wait_filler_gate.OUTLET)
    no_catch_all["lead"]["branches"] = [
        b for b in no_catch_all["lead"]["branches"] if b[0] != "other"
    ]
    with pytest.raises(AssertionError):
        _check_catch_all(no_catch_all)
