"""
Tests for wait_filler_gate.py — PreToolUse hook (matcher: Bash) that denies
bare `true`/`sleep <N>` filler commands.

The matrix below IS the executable acceptance spec (per the test
consultation doc): every arm is an acceptance criterion.

Deny arms D1-D15 (exit 2 + hookSpecificOutput deny envelope):
  bare builtin/sleep, leading/trailing whitespace incl. ALL trailing
  newlines, space/tab separators between `sleep` and the duration,
  duration suffixes (s/m/h/d), fractional N, `infinity`, env
  assignments, one `command `/`builtin ` prefix, one trailing comment, and
  the full normalization chain composed.

Allow arms A1-A25 (exit 0, no deny payload):
  composed commands (&&, |, ;, ||), false-positive guards (substring,
  prefix word, quoted substring, -ss flag), the persona §5 watcher template
  verbatim (triad coherence — the hook must never gate the remedy the
  persona teaches), retry loops, interior newline (composed), documented
  under-block compositions (adversarial shapes asserted as ALLOW — they pin
  the honest-mistake scope boundary), degenerate inputs (empty,
  whitespace-only).

Fail-open arms A26-A30 (exit 0):
  invalid JSON on stdin, missing tool_input, null/non-string command,
  wrong tool_name (belt-and-braces behind the hooks.json matcher), and a
  forced internal exception in the matcher (no deny payload, warning to
  stderr).

Payload pins:
  P1  deny exits 2 (asserted by every deny arm).
  P2  deny stdout parses as JSON with the hookSpecificOutput envelope and a
      stable role-neutral reason substring ("Passive waiting is the
      protocol" — distinctive phrase, not full text, so wording edits
      don't churn the pin).
  P3  channel discipline: the deny payload carries ONLY the
      hookSpecificOutput key (no systemMessage/suppressOutput siblings).

Registration pins:
  S2  registered under PreToolUse matcher "Bash" in hooks.json with no
      async:true (the MUST_BE_SYNC entry in test_hooks_json.py is the
      sibling pin — an async flip would otherwise pass silently).
  S3  module-level imports are stdlib only, and the `shared` package loads
      only for a teammate-shaped background launch (subprocess probe, with a
      positive control) — keeps the per-Bash consumer cost minimal.
  S4  membership: "wait_filler_gate" is in SEAM_DEPENDENT_HOOKS, because the
      launch advisory reads team config and the session registry.
  S5  parity: the gate's local copy of the lead `agent_type` spellings
      equals shared.pact_context.LEAD_AGENT_TYPES, which the hook cannot
      import.

S1 (script exists) is auto-covered by
test_hooks_json.py::TestReferencedScriptsExist once registered.

TEST-phase acceptance matrix (issue #1657, appended below the CODE-phase
smoke arms): frozen-literal pins (tier-1 byte-identity vs the 18aea673
blob; tier-3 outlet reason verbatim + the absence pin on the retired
allowance mechanism), tier-3 count/total rendered DISTINCTLY, channel
discipline at tiers 2-3, verdict invariance through deep escalation,
count non-regression through consecutive denials + no budget field in
state, session isolation, normalization-coupled counting (one content
address across decorated spellings), uniform tier-3 outlet verdict
across duration spellings and decorated forms, saturation boundary
armed both sides (totals 6/7, re-fire shape), stale-budget field
ignored not degrading, growth caps, unset-env degrade (O4), allow
fast-path emitted-byte identity (O2), docstring no-raw-tabs (O1),
CROSS-PROCESS subprocess probes (gradient, tier-3 outlet), concurrency
no-lost-update, and a writer->reaper integration close-out. The two
ablation-table entries below close the matrix; O3 (reaper silent skip)
is pinned in test_session_end_filler_state_reaper.py.

Mutation-ablation table (the TEST-phase verification spec; each ablation
predicts the flipped arms before running, and an ablation whose prediction
agrees with the arm proves nothing). AS-EXECUTED, RE-RUN at remediation
cycle 1 against the post-widen grammar (isolated copy, 59 cases incl. the
D6 `.5`/`5.` widened-grammar arms; the pre-widen cycle-0 run observed 14
on the sleep row — the widen moved it by exactly the new arms):
  drop `sleep` alternative from pattern  -> observed 16 flips: every
      sleep-based deny arm (D2, D4, D6x3, D7x4, D8, D9, D11, D13, D14,
      D15x2); unique witnesses D2/D6/D7/D8. (Cycle-2 addendum: the
      separator widen added two D2 separator arms, so a post-cycle-2
      re-run observes 18 with D2x3 — the 16 and its enumeration are
      as-executed at cycle 1.)
  drop whitespace strip                  -> observed 5: D3, D4, D5, D15x2
      (unique witnesses D3/D5).
  drop env-assignment strip              -> observed 3: D9, D10, D14.
  drop command/builtin prefix strip      -> observed 3: D11, D12, D14.
  drop comment strip                     -> observed 2: D13, D14.
  replace \\Z anchor with $               -> observed 0 — MASKED, not a
      missing kill: the strip removes ALL trailing newlines and the
      interior-newline check runs before the pattern, so no input reaches
      the match with a trailing newline and $ == \\Z. The anchor is
      zero-cost defense-in-depth documentation; the newline behavior is
      certified by D15 flipping under the strip and sleep ablations. Do
      not expect a test to couple to the anchor. (Confirmed structural at
      cycle 1: a pyc same-second-mtime collision in the copy produced a
      stale-bytecode restore false-red; the anchor re-run with __pycache__
      cleared still observed 0.)
  invert fail-open to fail-closed        -> observed 2: A26, A30. A27-A29
      route through the validation-allow path, not the exception paths —
      they are validation-allow cases, not fail-open arms; fail-open is
      load-bearing where it exists (both exception paths).
  drop the leading-dot alternative       -> observed 1: the D6 'sleep .5'
      twin (the widened grammar's new member is load-bearing).
  narrow \\.[0-9]* back to \\.[0-9]+      -> observed 1: the D6 'sleep 5.'
      arm (pins the trailing-dot admission — without the arm this
      narrowing ships silently).
A total non-flip across arms is an instrument alarm, not a finding.

TEST-phase gradient/valve ablations (measured 2026-10-24, isolated copy
of the whole pact-plugin tree, gate file at its post-matrix 93-test
size; each prediction recorded before its run and each run OBSERVED to
match its prediction exactly — counts and witnesses):
  loosen _INSTANT_FILLER to _FILLER_PATTERN (the coherent "valve opens
      for every filler" mutation, not a naive flip)  -> observed 8:
      test_gradient_tier_progression_renders_count,
      test_valve_never_opens_for_nonzero_or_infinite_sleep,
      test_valve_instant_grammar_edges,
      test_tier3_renders_count_and_total_distinctly,
      test_verdict_invariance_through_deep_escalation,
      test_normalized_forms_share_one_counter,
      test_saturation_boundary_armed_both_sides,
      test_cross_process_gradient_persists. Survivors are by design:
      the budget and concurrency arms assert STATE, not verdicts, and
      the channel-discipline arm passes because the valve envelope
      keeps the same single-key shape (its teeth are envelope-only).
  drop the saturation boost (tier = min(3, count))   -> observed 2:
      test_saturation_boost_escalates_a_fresh_command,
      test_saturation_boundary_armed_both_sides.

Structural-wait retirement annotations (2026-11-21, recorded without
re-measuring — the dated rows above are as-executed history):
  - The _INSTANT_FILLER loosen row is RETIRED: the instant-form
    predicate was deleted with the valve, so that mutation is no longer
    runnable and the row is a record, not a live spec.
  - test_valve_instant_grammar_edges (cited above) was renamed to
    test_tier3_outlet_uniform_across_duration_spellings; the other
    cited names are unchanged. Eight further valve-era arms were
    renamed in the same retirement (map in the TEST-phase HANDOFF);
    none is cited in these tables.

Counter-test record (measured at authoring time): this module was run
against the repo BEFORE hooks/wait_filler_gate.py existed (TDD red-first):
56 failed, 1 passed — every hook-dependent case red, the single green
being the S4 seam-non-membership pin (it imports only the classifier, not
the hook). Post-implementation: the full module green at authoring
(alongside test_hooks_json.py, whose MUST_BE_SYNC sibling pin covers the
async-flip shape), again at remediation cycle 1 (D6 twin), and again
with the trailing-dot admission arm added at the cycle-1 re-review.
"""

import ast
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = PLUGIN_ROOT / "hooks" / "wait_filler_gate.py"
HOOKS_JSON = PLUGIN_ROOT / "hooks" / "hooks.json"
ORCHESTRATOR = PLUGIN_ROOT / "agents" / "pact-orchestrator.md"

DENY_REASON_ANCHOR = "Passive waiting is the protocol"


def _persona_watcher_template() -> str:
    """The watcher template fenced bash block from persona §5. A11 asserts
    the hook never gates the remedy the persona teaches (triad coherence),
    so the allow arm reads the template's exact loop shape from the persona
    itself — a template edit and this arm cannot drift apart. The anchor is
    the watcher rule's own bold lead ('**Instrument the wait.**'), not the
    §5 section heading: a future earlier bash fence in §5 would otherwise
    re-point the extraction silently while the arm stayed green."""
    text = ORCHESTRATOR.read_text(encoding="utf-8")
    anchor = text.index("**Instrument the wait.**")
    fence_open = text.index("```bash\n", anchor) + len("```bash\n")
    fence_close = text.index("```", fence_open)
    return text[fence_open:fence_close]


def _invoke(stdin_text: str, capsys):
    """Run main() against the given stdin text. Returns (exit_code,
    parsed_stdout_json_or_None, stderr_text)."""
    from wait_filler_gate import main
    with patch("sys.stdin", io.StringIO(stdin_text)):
        with pytest.raises(SystemExit) as exc:
            main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if captured.out.strip() else None
    return exc.value.code, payload, captured.err


def _bash_payload(command: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


# ---------------------------------------------------------------------------
# Deny matrix — D1-D15. Each asserts P1 (exit 2) + the deny envelope.
# ---------------------------------------------------------------------------

DENY_ARMS = [
    ("D1", "true"),
    ("D2", "sleep 30"),
    ("D2", "sleep  30"),
    ("D2", "sleep\t30"),
    ("D3", "  true"),
    ("D4", "\tsleep 5"),
    ("D5", "true   "),
    ("D6", "sleep 0.5"),
    ("D6", "sleep .5"),
    ("D6", "sleep 5."),
    ("D7", "sleep 1m"),
    ("D7", "sleep 2h"),
    ("D7", "sleep 10s"),
    ("D7", "sleep 3d"),
    ("D8", "sleep infinity"),
    ("D9", "FOO=1 sleep 5"),
    ("D10", "A=1 B=2 true"),
    ("D11", "command sleep 5"),
    ("D12", "builtin true"),
    ("D13", "sleep 5 # waiting"),
    ("D14", "FOO=1 command sleep 5 # wait"),
    ("D15", "sleep 30\n"),
    ("D15", "sleep 30\n\n"),
]


@pytest.mark.parametrize(
    "arm, command", DENY_ARMS, ids=[f"{a}:{c!r}" for a, c in DENY_ARMS]
)
def test_deny_bare_filler(arm, command, capsys):
    """A command that normalizes to bare `true`/`sleep <N>` is denied:
    exit 2 (P1) with the hookSpecificOutput deny envelope."""
    code, payload, _ = _invoke(_bash_payload(command), capsys)
    assert code == 2, f"{arm} {command!r}: expected deny (exit 2), got {code}"
    envelope = payload["hookSpecificOutput"]
    assert envelope["hookEventName"] == "PreToolUse"
    assert envelope["permissionDecision"] == "deny"


# ---------------------------------------------------------------------------
# Allow matrix — A1-A25. Each asserts exit 0 and no deny payload.
# ---------------------------------------------------------------------------

ALLOW_ARMS = [
    ("A1", "sleep 2 && git status"),
    ("A2", "sleep 5 | tee log"),
    ("A3", "true; git status"),
    ("A4", "sleep 5 || echo done"),
    ("A5", "echo true"),
    ("A6", "ffmpeg -ss 30 -i in.mp4 out.mp4"),
    ("A7", 'git commit -m "sleep 30 while waiting"'),
    ("A8", "pytest -k test_true_marker"),
    ("A9", "truecrypt mount volume"),
    ("A10", "sleepy 5"),
    ("A11", _persona_watcher_template()),
    ("A12", "for i in 1 2 3; do curl -s api; sleep 60; done"),
    ("A13", "echo x\nsleep 30"),
    # A14-A23: documented under-block shapes — asserted as ALLOW to pin the
    # honest-mistake scope boundary (deliberate evasion is out of scope).
    ("A14", "true && true"),
    ("A15", "(sleep 5)"),
    ("A16", '"true"'),
    ("A16", "'sleep' 5"),
    ("A17", "sudo sleep 5"),
    ("A18", "time sleep 5"),
    ("A19", "\\sleep 5"),
    ("A20", "sleep"),
    ("A21", "sleep 5x"),
    ("A22", "sleep -5"),
    ("A23", "TRUE"),
    ("A23", "Sleep 5"),
    ("A24", ""),
    ("A25", "   "),
]


@pytest.mark.parametrize(
    "arm, command", ALLOW_ARMS, ids=[f"{a}:{c[:30]!r}" for a, c in ALLOW_ARMS]
)
def test_allow_non_filler(arm, command, capsys):
    """Anything that is not a bare filler command passes: exit 0 and no
    hookSpecificOutput deny payload."""
    code, payload, _ = _invoke(_bash_payload(command), capsys)
    assert code == 0, f"{arm} {command!r}: expected allow (exit 0), got {code}"
    if payload is not None:
        assert "hookSpecificOutput" not in payload, (
            f"{arm} {command!r}: unexpected deny payload {payload!r}"
        )


# ---------------------------------------------------------------------------
# Fail-open arms — A26-A30.
# ---------------------------------------------------------------------------


def test_a26_invalid_json_fails_open(capsys):
    code, payload, err = _invoke("this is not json{", capsys)
    assert code == 0
    assert payload is None or "hookSpecificOutput" not in payload
    assert err.strip(), "fail-open arms emit a stderr note"


def test_a27_missing_tool_input_fails_open(capsys):
    code, payload, _ = _invoke(json.dumps({"tool_name": "Bash"}), capsys)
    assert code == 0
    if payload is not None:
        assert "hookSpecificOutput" not in payload


def test_a28_null_command_fails_open(capsys):
    code, _, _ = _invoke(
        json.dumps({"tool_name": "Bash", "tool_input": {"command": None}}), capsys
    )
    assert code == 0


def test_a28_nonstring_command_fails_open(capsys):
    code, _, _ = _invoke(
        json.dumps({"tool_name": "Bash", "tool_input": {"command": 42}}), capsys
    )
    assert code == 0


def test_a29_wrong_tool_name_allowed(capsys):
    """The hooks.json matcher is the first filter; the hook-side tool_name
    check is belt-and-braces — a deny-shaped command under another tool is
    allowed."""
    stdin_text = json.dumps(
        {"tool_name": "Edit", "tool_input": {"command": "sleep 30"}}
    )
    code, payload, _ = _invoke(stdin_text, capsys)
    assert code == 0
    if payload is not None:
        assert "hookSpecificOutput" not in payload


def test_a30_internal_error_fails_open(monkeypatch, capsys):
    """A forced exception inside the matcher fails OPEN: exit 0, no deny
    payload, warning to stderr. A broken discipline gate must never block
    real work on its own breakage."""
    import wait_filler_gate

    def _boom(_command):
        raise RuntimeError("forced matcher failure")

    monkeypatch.setattr(wait_filler_gate, "_is_filler_command", _boom)
    code, payload, err = _invoke(_bash_payload("sleep 30"), capsys)
    assert code == 0
    assert payload is None or "hookSpecificOutput" not in payload
    assert err.strip(), "fail-open on internal error must note it on stderr"


# ---------------------------------------------------------------------------
# Payload pins — P2 reason language, P3 channel discipline.
# ---------------------------------------------------------------------------


def test_p2_deny_reason_carries_stable_anchor(capsys):
    """The deny reason carries the role-neutral mechanism language — a
    distinctive substring, not the full text, so wording edits don't churn
    the pin."""
    _, payload, _ = _invoke(_bash_payload("true"), capsys)
    reason = payload["hookSpecificOutput"]["permissionDecisionReason"]
    assert DENY_REASON_ANCHOR in reason
    assert "(§5)" not in reason, (
        "the hook fires for all agents; teammates do not load the "
        "orchestrator persona, so a literal section pointer is a dead "
        "reference on this surface"
    )


def test_p3_deny_payload_channel_discipline(capsys):
    """The deny path emits the hookSpecificOutput envelope ONLY — no
    systemMessage or suppressOutput siblings."""
    _, payload, _ = _invoke(_bash_payload("true"), capsys)
    assert set(payload.keys()) == {"hookSpecificOutput"}


# ---------------------------------------------------------------------------
# Registration pins — S2 hooks.json, S3 stdlib-only, S4 seam non-membership.
# ---------------------------------------------------------------------------


def test_s2_registered_under_pretooluse_bash_no_async():
    """The hook is registered inside the existing PreToolUse matcher "Bash"
    entry, and neither the entry nor the hook carries async:true (a deny
    hook must be synchronous — test_hooks_json.py's MUST_BE_SYNC entry is
    the sibling pin)."""
    config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    entries = [
        e for e in config["hooks"]["PreToolUse"] if e.get("matcher") == "Bash"
    ]
    matching = [
        h
        for e in entries
        for h in e.get("hooks", [])
        if "wait_filler_gate.py" in h.get("command", "")
    ]
    assert matching, (
        "wait_filler_gate.py not registered under a PreToolUse matcher "
        '"Bash" entry in hooks.json'
    )
    for hook in matching:
        assert hook.get("async") is not True, (
            "wait_filler_gate.py must be synchronous (no async:true) — "
            "it is a deny-capable hook"
        )


_STDLIB_ALLOWLIST = frozenset({"__future__", "importlib", "json", "os", "re", "sys"})


def test_s3_module_level_imports_are_stdlib_only():
    """Module-level imports are stdlib only and never shared.*. The membership
    check is an explicit allowlist rather than sys.stdlib_module_names: the
    allowlist names the intent (a fixed stdlib surface), stays identical on
    every interpreter, and is stricter than the attribute. It originated as a
    3.9 workaround (the attribute is 3.10+) and is kept deliberately.

    `os` and `importlib` are allowed because the hook loads the shared launch
    predicate BY FILE PATH (`importlib.util` with an `os.path` join) instead of
    importing it. The one `shared` import sits inside `launch_advisory_applies`;
    the next arm pins when it runs."""
    tree = ast.parse(HOOK_PATH.read_text(encoding="utf-8"))
    imported = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "shared" not in imported, f"module-level shared.* import found: {imported}"
    assert imported <= _STDLIB_ALLOWLIST, (
        f"imports outside the stdlib allowlist: "
        f"{sorted(imported - _STDLIB_ALLOWLIST)}"
    )


_SHARED_LOADED_PROBE = """
import io, json, runpy, sys
sys.stdin = io.StringIO(sys.argv[2])
try:
    runpy.run_path(sys.argv[1], run_name="__main__")
except SystemExit:
    pass
sys.stderr.write("SHARED_LOADED=%s" % any(m == "shared" or m.startswith("shared.") for m in sys.modules))
"""


def _shared_loaded_for(frame: dict, tmp_path) -> bool:
    """Run the gate as a script in a fresh interpreter; report whether `shared` loaded."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR")}
    env.update(HOME=str(tmp_path), CLAUDE_CONFIG_DIR=str(tmp_path / ".claude"))
    proc = subprocess.run(
        [sys.executable, "-c", _SHARED_LOADED_PROBE, str(HOOK_PATH), json.dumps(frame)],
        capture_output=True, text=True, timeout=30, env=env, cwd=str(HOOK_PATH.parent),
    )
    assert "SHARED_LOADED=" in proc.stderr, proc.stderr
    return proc.stderr.rsplit("SHARED_LOADED=", 1)[1].startswith("True")


def test_s3_shared_loads_only_for_a_teammate_background_launch(tmp_path):
    """The gate runs before every Bash call in every consumer session, so the
    `shared` package, and the team config and registry reads behind it, load
    only for a teammate-shaped frame launching background work. The positive
    control proves the probe can see the import at all."""
    launch = {"command": "echo hi", "run_in_background": True}
    plain = {"command": "echo hi"}
    teammate = {"agent_type": "probe-coder", "session_id": "sid", "tool_name": "Bash"}
    lead = {"agent_type": "PACT:pact-orchestrator", "session_id": "sid", "tool_name": "Bash"}

    assert _shared_loaded_for({**teammate, "tool_input": launch}, tmp_path) is True, (
        "positive control: a teammate background launch did not load `shared`, "
        "so the probe cannot see the import it is meant to rule out"
    )
    assert _shared_loaded_for({**teammate, "tool_input": plain}, tmp_path) is False
    assert _shared_loaded_for({**lead, "tool_input": launch}, tmp_path) is False
    assert _shared_loaded_for({"tool_name": "Bash", "tool_input": launch}, tmp_path) is False


def test_s4_seam_dependent():
    """The launch advisory reads team config and the session registry to tell
    a teammate from an Agent-tool subagent, so the hook is seam-dependent and
    needs a non-mocked L2 test (decision pin)."""
    from shared.hook_infra_classifier import SEAM_DEPENDENT_HOOKS

    assert "wait_filler_gate" in SEAM_DEPENDENT_HOOKS


def test_s5_lead_spellings_match_the_source_set():
    """The gate keeps its own copy of the lead `agent_type` spellings because it
    imports only the standard library. That copy must equal
    `shared.pact_context.LEAD_AGENT_TYPES`, the source of truth for who is the
    lead. Both are frozensets, so equality compares members."""
    import wait_filler_gate
    from shared.pact_context import LEAD_AGENT_TYPES

    assert wait_filler_gate._LEAD_AGENT_TYPES == LEAD_AGENT_TYPES, (
        "wait_filler_gate._LEAD_AGENT_TYPES has drifted from "
        f"shared.pact_context.LEAD_AGENT_TYPES (gate {sorted(wait_filler_gate._LEAD_AGENT_TYPES)}, "
        f"source {sorted(LEAD_AGENT_TYPES)}). A lead spelling missing from the gate "
        "makes a lead frame read as a teammate, so the lead gets the "
        "background-launch advisory; a spelling only in the gate silences every "
        "teammate whose agent_type matches it. Update the gate's copy."
    )


# ---------------------------------------------------------------------------
# Gradient + valve smoke arms (issue #1657). CODE-phase proof that the
# implementation works end to end against the real on-disk state channel
# (hermetic CLAUDE_CONFIG_DIR per arm); the full acceptance matrix —
# cross-process persistence probes, shape-scope ablations, concurrency —
# is TEST-phase work per the plan.
# ---------------------------------------------------------------------------


def _gradient_frame(command, session_id="s-grad", agent_type=None, description=None):
    frame = {"tool_name": "Bash", "tool_input": {"command": command}}
    if session_id is not None:
        frame["session_id"] = session_id
    if agent_type is not None:
        frame["agent_type"] = agent_type
    if description is not None:
        frame["tool_input"]["description"] = description
    return json.dumps(frame)


def _drive(commands, tmp_path, monkeypatch, capsys, session_id="s-grad",
           agent_type=None):
    """Run main() once per command under a hermetic CLAUDE_CONFIG_DIR.
    Returns the list of (exit_code, parsed_payload_or_None, stderr)."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    return [
        _invoke(
            _gradient_frame(c, session_id=session_id, agent_type=agent_type),
            capsys,
        )
        for c in commands
    ]


def _state_path(tmp_path, session_id="s-grad"):
    return tmp_path / "pact-filler-state" / f"{session_id}.json"


def _deny_reason(payload):
    return payload["hookSpecificOutput"]["permissionDecisionReason"]


def test_gradient_tier_progression_renders_count(tmp_path, monkeypatch, capsys):
    """Three identical filler calls escalate 1 -> 2 -> 3, tier 1 is frozen
    byte-identical to the historical advisory, tier 2 renders the count,
    tier 3 is the imperative FILLER-LOCK."""
    import wait_filler_gate

    results = _drive(["sleep 30"] * 3, tmp_path, monkeypatch, capsys)
    codes = [code for code, _payload, _err in results]
    assert codes == [2, 2, 2]
    tier1, tier2, tier3 = (_deny_reason(p) for _c, p, _e in results)
    assert tier1 == wait_filler_gate._DENY_REASON
    assert tier2.startswith(wait_filler_gate._DENY_REASON)
    assert "denied 2 times this session (2 filler calls in total)" in tier2
    assert "FILLER-LOCK" in tier3


def test_gradient_stderr_mirrors_the_deny_reason(tmp_path, monkeypatch, capsys):
    """The denial text rides stdout JSON (the surface this harness parses
    at exit 2) and is mirrored to stderr for harnesses reported to drop
    stdout JSON at exit 2 (channel probe, issue #1657 plan)."""
    code, payload, err = _drive(["true"], tmp_path, monkeypatch, capsys)[0]
    assert code == 2
    assert err.strip() == _deny_reason(payload)


def _seed_team_registry(tmp_path, session_id, value):
    """Seed the session-registry route (a teammate's `name@team` entry) so
    frame_team_and_name resolves this session's team in the hermetic
    config root — the measured separate-process shape."""
    registry_dir = tmp_path / "pact-sessions"
    registry_dir.mkdir(parents=True, exist_ok=True)
    with (registry_dir / ".teammate-registry.jsonl").open("a") as registry:
        registry.write(json.dumps({"session_id": session_id, "value": value}) + "\n")


def _seed_team_config(tmp_path, team="tm-team", tmux_member=None):
    """Seed a team config with one member; `tmux_member` names a member
    that runs backendType tmux (the separate-process shape). The team's
    task store is created too, as a live team has one: a frame without a
    session context gets an arm command only when its store exists."""
    config_dir = tmp_path / "teams" / team
    config_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "tasks" / team).mkdir(parents=True, exist_ok=True)
    members = [{"name": "backend-coder", "agentType": "pact-backend-coder"}]
    if tmux_member:
        members.append({"name": tmux_member, "agentType": "pact-backend-coder",
                        "backendType": "tmux"})
    (config_dir / "config.json").write_text(
        json.dumps({"members": members}), encoding="utf-8"
    )


def test_tier3_outlet_denies_instant_form_every_time(
    tmp_path, monkeypatch, capsys
):
    """The retired instant-form allowance is gone: at computed tier 3 an
    instant form denies EVERY time, and from the third call the reason
    carries the PACT-WAIT-OUTLET arm command (watcher path, the resolved
    team substituted, the --task placeholder, the background-launch
    instruction). Attempt counts keep
    climbing through the denials so the gradient never regresses."""
    _seed_team_registry(tmp_path, "s-grad", "backend-coder@tm-team")
    _seed_team_config(tmp_path)
    results = _drive(["true"] * 5, tmp_path, monkeypatch, capsys)
    codes = [code for code, _p, _e in results]
    assert codes == [2, 2, 2, 2, 2]
    for _c, payload, _e in results[2:]:
        reason = _deny_reason(payload)
        assert "PACT-WAIT-OUTLET" in reason
        assert "pact_wait.py" in reason
        assert "run_in_background" in reason
        assert "--team tm-team" in reason and "--task <task-id>" in reason
        assert "<team-name>" not in reason
    assert (
        "denied 5 times this session (5 filler calls in total)"
        in _deny_reason(results[4][1])
    )


def test_valve_never_opens_for_nonzero_or_infinite_sleep(
    tmp_path, monkeypatch, capsys
):
    """`sleep 1` and `sleep infinity` are never instant forms: three calls
    deny all three times, third at tier 3, and the exit code is never 0.

    Name kept although the valve mechanism it guarded against is retired:
    this arm is a cited witness in the dated gradient/valve ablation table
    above, and renaming would orphan that as-executed record."""
    for command in ("sleep 1", "sleep infinity"):
        results = _drive([command] * 3, tmp_path, monkeypatch, capsys)
        assert [c for c, _p, _e in results] == [2, 2, 2], command
        assert "FILLER-LOCK" in _deny_reason(results[2][1]), command


def test_tier3_outlet_uniform_across_duration_spellings(
    tmp_path, monkeypatch, capsys
):
    """The retired instant/non-instant verdict split is gone: every
    filler spelling reaches the SAME tier-3 outlet deny. All-zero
    duration spellings (`0`, `0.0`, `.0`, `0s`), a nonzero duration,
    and `infinity` all deny with the outlet text at the third attempt.
    Fresh session per form so counters do not couple the arms (explicit
    safe-shape sids: a derived id containing `.` would itself fail the
    gate's safe-sid fullmatch and pass this arm for the wrong reason)."""
    for session_id, command in (
        ("s-zero", "sleep 0"),
        ("s-zero-dot-zero", "sleep 0.0"),
        ("s-dot-zero", "sleep .0"),
        ("s-zero-s", "sleep 0s"),
        ("s-zero-dot-one", "sleep 0.1"),
        ("s-infinity", "sleep infinity"),
    ):
        results = _drive([command] * 3, tmp_path, monkeypatch, capsys,
                         session_id=session_id)
        assert results[2][0] == 2, command
        assert "PACT-WAIT-OUTLET" in _deny_reason(results[2][1]), command


def test_state_failure_degrades_to_frozen_tier1_deny(
    tmp_path, monkeypatch, capsys
):
    """Fail-direction invariant: with the state channel unavailable the
    hook still denies every filler at the frozen tier-1 advisory — never
    allows, never harder (V2 UNKNOWN = DENY). Renamed at the valve
    retirement: the closed mechanism it used to name no longer exists,
    and no dated record cites this arm."""
    import wait_filler_gate

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(wait_filler_gate, "_load_locked_update", lambda: None)
    for expected_tier1 in range(5):
        code, payload, _err = _invoke(
            _gradient_frame("true"), capsys
        )
        assert code == 2, expected_tier1
        assert _deny_reason(payload) == wait_filler_gate._DENY_REASON
    assert not _state_path(tmp_path).exists()


def test_corrupt_state_file_denies_at_tier1(tmp_path, monkeypatch, capsys):
    """A state file that does not parse degrades to the tier-1 deny and
    is left untouched (no rewrite on the degrade path)."""
    import wait_filler_gate

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    state = _state_path(tmp_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("not json {{{", encoding="utf-8")
    code, payload, _err = _invoke(_gradient_frame("true"), capsys)
    assert code == 2
    assert _deny_reason(payload) == wait_filler_gate._DENY_REASON
    assert state.read_text(encoding="utf-8") == "not json {{{"


def test_no_or_unsafe_session_id_never_persists(tmp_path, monkeypatch, capsys):
    """Frames without a session_id, or carrying one that cannot be a
    filename, deny stateless at tier 1 and never create state."""
    import wait_filler_gate

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    for frame in (
        _gradient_frame("true", session_id=None),
        _gradient_frame("true", session_id="../evil"),
        _gradient_frame("true", session_id="a/b"),
    ):
        code, payload, _err = _invoke(frame, capsys)
        assert code == 2
        assert _deny_reason(payload) == wait_filler_gate._DENY_REASON
    assert not (tmp_path / "pact-filler-state").exists()
    assert not (tmp_path / "evil.json").exists()


def test_description_variance_does_not_reset_the_gradient(
    tmp_path, monkeypatch, capsys
):
    """Counters key on the post-normalization command only (G5): varying
    the description between identical commands continues the count."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    first = _invoke(
        _gradient_frame("true", description="waiting for build"), capsys
    )
    second = _invoke(
        _gradient_frame("true", description="still waiting, different words"),
        capsys,
    )
    assert first[0] == 2 and second[0] == 2
    assert "denied 2 times this session (2 filler calls in total)" in _deny_reason(second[1])


def test_saturation_boost_escalates_a_fresh_command(
    tmp_path, monkeypatch, capsys
):
    """Past total 6, a FIRST-seen filler command enters at tier 2 — the
    command-walk backstop (`sleep 30/31/32...` cannot reset the gradient
    by varying the number)."""
    import wait_filler_gate

    commands = [f"sleep {n}" for n in range(40, 46)]  # totals 1..6
    results = _drive(commands, tmp_path, monkeypatch, capsys)
    # totals 1..6: no boost, every first-seen command is tier 1
    for (_c, payload, _e) in results:
        assert _deny_reason(payload) == wait_filler_gate._DENY_REASON
    _code, payload, _err = _invoke(
        _gradient_frame("sleep 46"), capsys
    )  # total 7 > 6 -> boost -> tier 2
    assert "denied 1 time this session (7 filler calls in total)" in _deny_reason(payload)


def test_agent_type_sections_are_distinct(tmp_path, monkeypatch, capsys):
    """In-process teammates share the lead's session_id (G3 identity
    collapse): the agent_type section keeps their counters distinct, and
    a frame with no agent_type keys on the empty string."""
    import wait_filler_gate

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    code_a, payload_a, _e = _invoke(
        _gradient_frame("true", agent_type="pact-devops-engineer"), capsys
    )
    code_b, payload_b, _e = _invoke(
        _gradient_frame("true", agent_type=None), capsys
    )
    assert code_a == 2 and code_b == 2
    # both are FIRST denials in their own section
    assert _deny_reason(payload_a) == wait_filler_gate._DENY_REASON
    assert _deny_reason(payload_b) == wait_filler_gate._DENY_REASON
    state = json.loads(_state_path(tmp_path).read_text(encoding="utf-8"))
    assert state["pact-devops-engineer"]["total"] == 1
    assert state[""]["total"] == 1


# ---------------------------------------------------------------------------
# TEST-phase acceptance matrix (issue #1657) — plan Test Phase, P0s first.
# Appended by the test-engineer dispatch; the CODE-phase smoke arms above
# stay untouched (extend, don't break). The cross-process subprocess probes
# are the plan's own named P0: the hook runs as a fresh interpreter per
# Bash call, so in-process main() calls cannot prove persistence.
#
# Frozen-literal pins below: the tier-1 blob pin was embedded
# MECHANICALLY at its authoring time (ast.literal_eval of the 18aea673
# blob — the strings carry an em-dash, where char-count and byte-count
# diverge); the tier-3 outlet pin is the full module constant hand-copied
# at the outlet's introduction and must be updated consciously.
# ---------------------------------------------------------------------------

# The historical advisory, byte-identical (245 chars, verified against the
# 18aea673 blob). Tier 1 is FROZEN by the plan; drift here must redden.
_FROZEN_TIER1 = (
    "Passive waiting is the protocol — end the turn with no tool call; "
    "teammate messages arrive as their own turns. A bare true/sleep filler "
    "call manufactures the next turn without producing new information; "
    "compose it with real work or end the turn."
)

# The tier-3 outlet reason, verbatim — this text IS the loop-breaking
# mechanism now (it carries the ready-to-run watcher arm command), so a
# wording edit must be a conscious pin update, never a silent drift.
_TIER3_REASON_PIN = (
    "FILLER-LOCK: this exact call has been denied {count} times this "
    "session ({total} filler calls in total). PACT-WAIT-OUTLET: stop "
    "re-filling — end this wait structurally instead. If the event you "
    "await is another agent's handoff or teachback submission, arm the "
    "structural watcher — this is the wait it exists for, even though "
    "you cannot write the deliverable — with ONE `Bash` call "
    "(`run_in_background: true`), substituting the id of the task you "
    "are waiting on for <task-id>:\n"
    "{command}\n"
    "(use --predicate "
    "teachback_submit when the awaited deliverable is a teachback "
    "submission), then end your turn with no further tool calls — the "
    "watcher exits 0 when the deliverable lands, 3 when the deadline "
    "passes without it, 4 when it cannot watch at all — and its "
    "completion notification re-invokes you. If the event you await is "
    "something only YOU can write (your own notes or a summary), do "
    "NOT arm and do not wait: write it now, in this turn — nothing "
    "wakes you later to write it. If the event you await is anything else (a user "
    "reply, a message), end your turn with no further tool calls — a "
    "reply or message arrives as its own turn."
)


def test_tier1_is_frozen_byte_identical_to_history():
    """P0 tier-1 freeze: _DENY_REASON equals the 18aea673 blob's constant
    byte-for-byte. The existing P2 anchor checks a substring; this pin is
    the full-string contract — the half of the behavior that must NOT
    change while everything around it escalates."""
    import wait_filler_gate

    assert wait_filler_gate._DENY_REASON == _FROZEN_TIER1, (
        "tier-1 denial text drifted from the frozen historical advisory"
    )


def test_tier3_renders_count_and_total_distinctly(tmp_path, monkeypatch, capsys):
    """P0 rendered count: tier 3 renders BOTH numbers, and they are the
    per-command count and the session total respectively — count and total
    are made DISTINCT (a shared counter would render the same number
    twice and this arm would pass on the wrong arithmetic)."""
    # command A twice (total 2, A count 2 -> tier 2)
    # command B twice (total 4, B count 2 -> tier 2)
    # command B again (total 5, B count 3 -> tier 3)
    _drive(["sleep 30", "sleep 30", "sleep 31", "sleep 31"], tmp_path,
           monkeypatch, capsys)
    _code, payload, _err = _invoke(
        _gradient_frame("sleep 31"), capsys
    )
    reason = _deny_reason(payload)
    assert "denied 3 times this session" in reason
    assert "(5 filler calls in total)" in reason


def test_tier2_tier3_keep_channel_discipline(tmp_path, monkeypatch, capsys):
    """P3 extended to the escalating tiers: the deny payload at tier 2 and
    tier 3 still carries ONLY the hookSpecificOutput key — escalation adds
    text inside the envelope, never sibling channels."""
    results = _drive(["sleep 30"] * 3, tmp_path, monkeypatch, capsys)
    for tier, (_c, payload, _e) in zip((1, 2, 3), results):
        assert set(payload.keys()) == {"hookSpecificOutput"}, tier


def test_verdict_invariance_through_deep_escalation(
    tmp_path, monkeypatch, capsys
):
    """P0 verdict invariance: a filler command is denied on every one of
    8 consecutive attempts — escalation changes text, never the verdict.
    Nothing on the filler branch exits 0: the tier-3 outlet is itself a
    denial (its productivity is the arm command in the reason, not an
    allowance)."""
    results = _drive(["sleep 30"] * 8, tmp_path, monkeypatch, capsys)
    assert [c for c, _p, _e in results] == [2] * 8
    for payload in (p for _c, p, _e in results):
        envelope = payload["hookSpecificOutput"]
        assert envelope["permissionDecision"] == "deny"


def test_valve_mechanism_absent_from_module():
    """Absence pin for the retired allowance mechanism: the payload
    constant, the budget constant and the instant-form predicate are
    gone from the module, and neither the payload head, the budget
    render string, nor the mechanism's name appears anywhere in the
    source — the mechanism left no live trace. The one exception is the
    legacy state key, written as an inert 0 so the previous release still
    reads the section as valid after a downgrade."""
    import wait_filler_gate

    assert not hasattr(wait_filler_gate, "_VALVE_PAYLOAD")
    assert not hasattr(wait_filler_gate, "_INITIAL_VALVE_BUDGET")
    assert not hasattr(wait_filler_gate, "_INSTANT_FILLER")
    source = HOOK_PATH.read_text(encoding="utf-8")
    assert "CIRCUIT-BREAKER" not in source
    assert "remaining:" not in source
    assert "valve" not in source.lower().replace('"valve_budget"', "")
    assert source.count('"valve_budget"') == 1


def test_outlet_reason_pinned_verbatim():
    """P0 outlet pin: the tier-3 reason, full-string — this text now
    carries the loop-breaking mechanism (the ready-to-run arm command),
    so a wording edit must be a conscious pin update, never a silent
    drift."""
    import wait_filler_gate

    assert wait_filler_gate._TIER3_REASON == _TIER3_REASON_PIN


# The IN-PROCESS TEAMMATE tier-3 outlet, verbatim — the role branch of the
# same mechanism. An in-process teammate's background completion does NOT
# start their next turn, so a watcher would wake no one: the branch carries
# NO arm command, only SET metadata.intentional_wait naming the lead;
# messages from the lead wake them.
_TIER3_TEAMMATE_REASON_PIN = (
    "FILLER-LOCK: this exact call has been denied {count} times this "
    "session ({total} filler calls in total). PACT-WAIT-OUTLET: stop "
    "re-filling — end this wait structurally instead. As an in-process "
    "teammate, a background watcher's completion will NOT wake you — "
    "the lead's message will. If the event you await is another "
    "agent's handoff or teachback submission, something only YOU can "
    "write (your own notes or a summary), the lead's review or action "
    "or anything else (a user reply, a message), do NOT arm: SET "
    "metadata.intentional_wait on every task the wait covers — "
    "`reason`, `expected_resolver` and `since` are the keys "
    "validate_wait requires — with `expected_resolver` set to `lead`, "
    "and end your turn with no further tool calls; a message or that "
    "resolver's own wake resolves it."
)


def test_tier3_teammate_text_names_the_wait_keys():
    """The in-process teammate text names the three keys validate_wait
    requires (shared/intentional_wait.py is the SSOT): a literal follower
    who copies the text verbatim must not write a malformed wait and
    re-enter the filler loop. A background watcher would wake no one for
    an in-process teammate, so the text forbids arming and carries no arm
    command."""
    import wait_filler_gate

    for key in ("reason", "expected_resolver", "since"):
        assert key in wait_filler_gate._TIER3_TEAMMATE_REASON, key
    # The key-value SPLIT (cold round 9): `expected_resolver` names the
    # LEAD — a watcher name would leave the resolver vocabulary and drop
    # the teammate from the resolver-scoped stall scans — while the
    # watcher's name belongs in `reason`.
    assert "`expected_resolver` set to `lead`" in (
        wait_filler_gate._TIER3_TEAMMATE_REASON
    )
    assert "do NOT arm" in wait_filler_gate._TIER3_TEAMMATE_REASON
    assert "{command}" not in wait_filler_gate._TIER3_TEAMMATE_REASON
    assert "pact_wait" not in wait_filler_gate._TIER3_TEAMMATE_REASON
    assert "do NOT arm" in (
        wait_filler_gate._TIER3_TEAMMATE_SEPARATE_PROCESS_REASON
    )


# The separate-process teammate's text, verbatim — its watcher-exit/wake
# tail is GATED on having armed (the do-NOT-arm route resolves without any
# watcher, and describing watcher exits to a non-arming follower is the
# contradiction class the gated tail closes).
_TIER3_TEAMMATE_SEPARATE_PROCESS_REASON_PIN = (
    "FILLER-LOCK: this exact call has been denied {count} times this "
    "session ({total} filler calls in total). PACT-WAIT-OUTLET: stop "
    "re-filling — end this wait structurally instead. As a "
    "separate-process teammate, a watcher's completion notice WILL "
    "start your next turn. If the event you await is another agent's "
    "handoff or teachback submission, arm the structural watcher — "
    "this is the wait it exists for, even though you cannot write the "
    "deliverable — with ONE `Bash` call (`run_in_background: true`), "
    "substituting the id of the task you are waiting on for "
    "<task-id>:\n"
    "{command}\n"
    "(use --predicate teachback_submit when the "
    "awaited deliverable is a teachback submission), then end your "
    "turn with no further tool calls — the watcher exits 0 when the "
    "deliverable lands, 3 when the deadline passes without it, 4 when "
    "it cannot watch at all — and its completion notice starts your "
    "next turn. If the event you await is something only YOU can write "
    "(your own notes or a summary) or the lead's review or action, do "
    "NOT arm: SET metadata.intentional_wait on every task the wait "
    "covers — `reason`, `expected_resolver` and `since` are the keys "
    "validate_wait requires — with `expected_resolver` set to `lead`, "
    "and end your turn with no further tool calls; a message or that "
    "resolver's own wake resolves it. If the event you await is "
    "anything else (a user reply, a message), end your turn with no "
    "further tool calls — a reply or message arrives as its own turn."
)


def test_tier3_sp_text_pinned_verbatim():
    import wait_filler_gate

    assert (
        wait_filler_gate._TIER3_TEAMMATE_SEPARATE_PROCESS_REASON
        == _TIER3_TEAMMATE_SEPARATE_PROCESS_REASON_PIN
    )


def test_tier3_sp_text_gates_its_watcher_tail():
    """The SP text's watcher-exit and wake clauses sit inside its arm
    sentence only: the exit vocabulary appears once, after the arm
    instruction and before the do-NOT-arm sentence, so a reader who does
    not arm is never told about a watcher's exits."""
    import wait_filler_gate

    sp = wait_filler_gate._TIER3_TEAMMATE_SEPARATE_PROCESS_REASON
    then = wait_filler_gate.ARM_THEN
    assert sp.count(then) == 1
    assert sp.index("arm the structural watcher") < sp.index(then)
    assert sp.index(then) < sp.index("do NOT arm")
    assert "THEN end your turn" not in sp


def test_tier3_lead_text_names_the_no_arm_alternative():
    """The LEAD text carries a no-arm alternative: when the awaited event
    is the lead's own write, arming burns the deadline and an
    intentional_wait resolved by the lead itself is never woken, so the
    lead is told to write it now."""
    import wait_filler_gate

    assert "do NOT arm" in wait_filler_gate._TIER3_REASON
    assert "write it now, in this turn" in wait_filler_gate._TIER3_REASON
    assert "intentional_wait" not in wait_filler_gate._TIER3_REASON
    # THE LEAD-SIDE DISAMBIGUATION (Bugbot Medium, cold round 12): the
    # lead's PRIMARY wait is a teammate's handoff or teachback — a
    # deliverable the lead cannot write — so a "not a deliverable you can
    # write" clause read literally makes the lead skip arming for the
    # exact case the watcher exists for. The text must name the
    # teammate-written deliverable as the ARM-ON case and scope the
    # do-NOT-arm route to what only the lead can write.
    assert "something only YOU can write" in wait_filler_gate._TIER3_REASON
    assert "even though you cannot write the deliverable" in (
        wait_filler_gate._TIER3_REASON
    )


def test_tier3_subagent_text_pinned_verbatim():
    """The Agent-tool subagent branch's own text, verbatim: NO wake
    promise — nothing wakes a subagent whose turn ends — and the
    completion phrasing the lead's ruling requires."""
    import wait_filler_gate

    assert wait_filler_gate._TIER3_SUBAGENT_REASON == _TIER3_SUBAGENT_REASON_PIN
    # A watcher would wake no one for a subagent: no arm command at all.
    assert "do NOT arm the structural watcher" in (
        wait_filler_gate._TIER3_SUBAGENT_REASON
    )
    assert "{command}" not in wait_filler_gate._TIER3_SUBAGENT_REASON


# The Agent-tool subagent's own text, verbatim — the role branch's fourth
# arm. A subagent's turn end returns its report to the caller: nothing wakes
# it, so the text carries NO arm command and NO wake promise — only the
# completion phrasing.
_TIER3_SUBAGENT_REASON_PIN = (
    "FILLER-LOCK: this exact call has been denied {count} times this "
    "session ({total} filler calls in total). PACT-WAIT-OUTLET: stop "
    "re-filling — end this wait structurally instead. As an Agent-tool "
    "subagent, nothing wakes you when your turn ends: your final "
    "report is your completion, and the lead reads it when it returns. "
    "If the event you await is another agent's handoff or teachback "
    "submission, something only YOU can write (your own notes or a "
    "summary), the lead's review or action or anything else (a user "
    "reply, a message), do NOT arm the structural watcher: end the "
    "turn with no further tool calls and put what the lead needs in "
    "your final report."
)


def test_tier3_teammate_outlet_pinned_verbatim():
    import wait_filler_gate

    assert (
        wait_filler_gate._TIER3_TEAMMATE_REASON == _TIER3_TEAMMATE_REASON_PIN
    )


def test_tier3_no_team_frame_gets_no_command(tmp_path, monkeypatch, capsys):
    """A frame with NO resolvable PACT team gets the loop-breaking
    instruction WITHOUT the arm command: there is no deliverable store for
    a <team-name> placeholder to name, so handing it a ready-to-run
    command would point at a store that does not exist for it."""
    results = _drive(
        ["true"] * 3, tmp_path, monkeypatch, capsys,
        session_id="s-noteam-outlet", agent_type="pact-backend-coder",
    )
    assert [c for c, _p, _e in results] == [2, 2, 2]
    reason = _deny_reason(results[2][1])
    assert "FILLER-LOCK" in reason
    assert "PACT-WAIT-OUTLET" in reason
    assert "pact_wait.py" not in reason
    assert "--team <team-name>" not in reason
    assert "No task store resolves for this frame" in reason


def test_tier3_agent_tool_subagent_gets_completion_report_text(
    tmp_path, monkeypatch, capsys
):
    """An Agent-tool subagent is neither teammate: its subagent-shaped
    agent_id refuses member resolution — the sibling launch advisory's own
    rule, via the same teammate_launch_name — so it gets its OWN text: no
    arm command, the completion phrasing, and NO wake promise (nothing
    wakes a subagent whose turn ends; its final report is its completion)."""
    _seed_team_registry(tmp_path, "s-sub-outlet", "backend-coder@tm-team")
    _seed_team_config(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    frame = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": "true"},
        "session_id": "s-sub-outlet",
        "agent_type": "general-purpose",
        "agent_id": "ad2b1261fdd77c958",
    })
    results = [_invoke(frame, capsys) for _ in range(3)]
    assert [c for c, _p, _e in results] == [2, 2, 2]
    reason = _deny_reason(results[2][1])
    assert "PACT-WAIT-OUTLET" in reason
    assert "pact_wait.py" not in reason
    assert "your final report is your completion" in reason
    assert "re-invokes" not in reason
    assert "will NOT wake you" not in reason
    assert "metadata.intentional_wait" not in reason
    assert "starts your next turn" not in reason


def test_tier3_outlet_separate_process_teammate_branch(tmp_path, monkeypatch, capsys):
    """The separate-process branch of the role-branched outlet, resolved
    through the REAL member record and session registry: a tmux teammate
    gets the arm command naming its team with the ACCURATE wake statement
    (its completion notice starts its next turn), never the in-process
    text's "will NOT wake you". The lead and in-process branches are
    driven by test_tier3_outlet_branches_on_role."""
    config = tmp_path / "teams" / "tm-team"
    config.mkdir(parents=True)
    (config / "config.json").write_text(json.dumps({
        "members": [{
            "name": "backend-coder",
            "agentType": "pact-backend-coder",
            "backendType": "tmux",
        }],
    }), encoding="utf-8")
    (tmp_path / "tasks" / "tm-team").mkdir(parents=True)  # the live store
    # The measured separate-process frame resolves its member through the
    # session REGISTRY (no team_name/teammate_name fields, no agent_id) —
    # seed that route, not a fabricated field shape.
    registry_dir = tmp_path / "pact-sessions"
    registry_dir.mkdir(parents=True)
    (registry_dir / ".teammate-registry.jsonl").write_text(
        json.dumps({"session_id": "s-sp-outlet",
                    "value": "backend-coder@tm-team"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    def sp_frame(session_id):
        return json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "true"},
            "session_id": session_id,
            "agent_type": "pact-backend-coder",
        })

    results = [
        _invoke(sp_frame("s-sp-outlet"), capsys) for _n in range(3)
    ]
    assert [c for c, _p, _e in results] == [2, 2, 2]
    sp_reason = _deny_reason(results[2][1])
    assert "PACT-WAIT-OUTLET" in sp_reason
    assert "pact_wait.py" in sp_reason
    assert "--team tm-team" in sp_reason
    assert "<team-name>" not in sp_reason
    assert "starts your next turn" in sp_reason
    assert "will NOT wake you" not in sp_reason


def test_tier3_outlet_branches_on_role(tmp_path, monkeypatch, capsys):
    """The tier-3 text is role-branched: a TEAMMATE frame (agent_type
    naming a non-lead actor) gets the in-process guidance — intentional_wait
    naming the lead, the lead's messages as the wake — with NO arm command
    and NEVER the lead-only completion-wakes claim; a lead frame gets the
    lead text with the arm command and the resolved team. Same gradient,
    same deny verdict, both roles. Both arms resolve a team through the
    seeded registry (the no-team case is pinned separately)."""
    _seed_team_registry(tmp_path, "s-mate-outlet", "backend-coder@tm-team")
    _seed_team_registry(tmp_path, "s-lead-outlet", "backend-coder@tm-team")
    _seed_team_config(tmp_path)
    teammate = _drive(
        ["true"] * 3, tmp_path, monkeypatch, capsys,
        session_id="s-mate-outlet", agent_type="pact-backend-coder",
    )
    assert [c for c, _p, _e in teammate] == [2, 2, 2]
    mate_reason = _deny_reason(teammate[2][1])
    assert "PACT-WAIT-OUTLET" in mate_reason
    assert "pact_wait.py" not in mate_reason
    assert "metadata.intentional_wait" in mate_reason
    assert "will NOT wake you" in mate_reason
    assert "re-invokes you" not in mate_reason

    lead = _drive(
        ["true"] * 3, tmp_path, monkeypatch, capsys,
        session_id="s-lead-outlet", agent_type="pact-orchestrator",
    )
    lead_reason = _deny_reason(lead[2][1])
    assert "PACT-WAIT-OUTLET" in lead_reason
    assert "pact_wait.py" in lead_reason
    assert "--team tm-team" in lead_reason
    assert "re-invokes you" in lead_reason
    # The lead text DOES name metadata.intentional_wait (the do-NOT-arm
    # alternative, cold round 8) — the mate discriminator is the
    # wake-model phrasing, not the metadata key.
    assert "will NOT wake you" not in lead_reason


def test_tier3_command_names_the_live_task_store_without_team_config(
    tmp_path, monkeypatch, capsys, pact_context
):
    """Config-less substrate: the context names session-<id8>, but the live
    task store is the session-UUID directory and no team config exists.
    The tier-3 command must name the store the lifecycle advisory names
    (resolve_task_dir), or the watcher is aimed at a team with no task
    files and exits 4."""
    import shutil

    from shared.paths import get_claude_config_dir

    session_uuid = "aaaa1111-0000-4000-8000-000000000001"
    pact_context(team_name="session-aaaa1111", session_id=session_uuid)
    root = get_claude_config_dir()
    shutil.rmtree(root / "tasks" / "session-aaaa1111")
    shutil.rmtree(root / "teams" / "session-aaaa1111")
    (root / "tasks" / session_uuid).mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    frame = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": "true"},
        "session_id": session_uuid,
    })
    results = [_invoke(frame, capsys) for _ in range(3)]
    reason = _deny_reason(results[2][1])
    commands = [ln for ln in reason.splitlines() if ln.startswith("python3")]
    assert len(commands) == 1, reason
    assert f"--team {session_uuid} " in commands[0]
    assert "session-aaaa1111" not in commands[0]


def test_counts_climb_through_denials_and_budget_field_zero(
    tmp_path, monkeypatch, capsys
):
    """The budget bookkeeping is retired: counts climb through DENIALS
    (no call ever exits 0), the persisted section's legacy budget key is
    an inert 0 (written only for the previous release's reader), and the
    re-fire lands at tier 3 with the count rendered — the gradient never
    regresses, and nothing is allowed."""
    results = _drive(["true"] * 5, tmp_path, monkeypatch, capsys)
    assert [c for c, _p, _e in results] == [2, 2, 2, 2, 2]
    state = json.loads(_state_path(tmp_path).read_text(encoding="utf-8"))
    assert state[""]["valve_budget"] == 0
    _code, payload, _err = _invoke(_gradient_frame("true"), capsys)
    reason = _deny_reason(payload)
    assert "FILLER-LOCK" in reason
    assert "denied 6 times this session (6 filler calls in total)" in reason


def test_sessions_are_isolated_by_session_id(tmp_path, monkeypatch, capsys):
    """One file per session_id: counters in s-iso-1 and s-iso-2 never
    share a count."""
    import wait_filler_gate

    _drive(["true", "true"], tmp_path, monkeypatch, capsys,
           session_id="s-iso-1")
    code, payload, _err = _invoke(
        _gradient_frame("true", session_id="s-iso-2"), capsys
    )
    assert code == 2
    assert _deny_reason(payload) == wait_filler_gate._DENY_REASON  # tier 1
    for sid in ("s-iso-1", "s-iso-2"):
        assert _state_path(tmp_path, sid).exists()


def test_normalized_forms_share_one_counter(tmp_path, monkeypatch, capsys):
    """Command-keying is on the POST-NORMALIZATION command: whitespace
    decoration, a wrapper prefix and an env-assignment prefix of the same
    filler increment ONE count (they are one content address). Uses a
    non-instant filler so tier 3 is a FILLER-LOCK deny, not a valve."""
    import wait_filler_gate

    results = _drive(
        ["sleep 30", "   sleep 30   ", "command sleep 30"],
        tmp_path, monkeypatch, capsys,
    )
    reasons = [_deny_reason(p) for _c, p, _e in results]
    assert reasons[0] == wait_filler_gate._DENY_REASON  # count 1
    assert "denied 2 times this session (2 filler calls in total)" in reasons[1]
    assert "FILLER-LOCK" in reasons[2]  # count 3 -> tier 3


def test_decorated_instant_forms_take_the_same_outlet_deny(
    tmp_path, monkeypatch, capsys
):
    """The retired derivation pin's coverage, re-homed: a decorated
    instant filler (env-assignment or wrapper prefix) normalizes to the
    same command and takes the SAME tier-3 outlet deny the bare spelling
    takes — decoration changes nothing about the verdict."""
    for decorated in ("FOO=1 sleep 0", "builtin true"):
        results = _drive([decorated] * 3, tmp_path, monkeypatch, capsys)
        assert [c for c, _p, _e in results] == [2, 2, 2], decorated
        assert "PACT-WAIT-OUTLET" in _deny_reason(results[2][1]), decorated


def test_saturation_boundary_armed_both_sides(tmp_path, monkeypatch, capsys):
    """O5 both sides: totals 1..6 carry no boost (a re-fire at total 6 is
    still plain tier 2), and the attempt that takes the total PAST 6 gets
    the boost — pinned on a RE-FIRE (count 2 + boost -> tier 3), which the
    fresh-command arm above does not cover."""
    # 5 distinct fresh commands: totals 1..5, all tier 1
    _drive([f"sleep {n}" for n in range(50, 55)], tmp_path, monkeypatch,
           capsys)
    # 6th attempt: re-fire of sleep 50 -> count 2, total 6, no boost
    _code, payload, _err = _invoke(_gradient_frame("sleep 50"), capsys)
    assert "denied 2 times this session (6 filler calls in total)" in _deny_reason(payload)
    assert "FILLER-LOCK" not in _deny_reason(payload)
    # 7th attempt: fresh sleep 55 -> count 1 + boost -> tier 2
    _code, payload, _err = _invoke(_gradient_frame("sleep 55"), capsys)
    assert "denied 1 time this session (7 filler calls in total)" in _deny_reason(payload)
    # 8th attempt: re-fire of sleep 50 -> count 3 + boost -> tier 3
    _code, payload, _err = _invoke(_gradient_frame("sleep 50"), capsys)
    assert "FILLER-LOCK" in _deny_reason(payload)


def test_stale_budget_value_is_ignored_not_degrading(
    tmp_path, monkeypatch, capsys
):
    """A stale non-int budget field (written by an older hook version)
    is not a degrade trigger: the gradient escalates normally off
    pairs/total. The field is repaired to the inert 0 on write, the one
    value the previous release reads as valid, so a downgrade after this
    write does not pin the session at tier 1."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    state = _state_path(tmp_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps({"": {"pairs": {}, "total": 9, "valve_budget": "two"}}),
        encoding="utf-8",
    )
    # total 9 > 6 -> boost: a first-seen command enters at tier 2, its
    # re-fire at tier 3 — the escalation the old V2 degrade suppressed.
    _code, payload, _err = _invoke(_gradient_frame("sleep 30"), capsys)
    assert (
        "denied 1 time this session (10 filler calls in total)"
        in _deny_reason(payload)
    )
    _code, payload, _err = _invoke(_gradient_frame("sleep 30"), capsys)
    assert "FILLER-LOCK" in _deny_reason(payload)
    assert (
        json.loads(state.read_text(encoding="utf-8"))[""]["valve_budget"]
        == 0
    )


def test_nondict_agent_section_degrades_tier1_untouched(
    tmp_path, monkeypatch, capsys
):
    """test-M3(a), review remediation: a well-formed state file whose
    agent section is NOT a dict takes _compute_gradient's isinstance
    degrade (a DIFFERENT branch from the corrupt-budget one) — frozen
    tier-1 deny, valve closed, file byte-untouched."""
    import wait_filler_gate

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    state = _state_path(tmp_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    seeded = json.dumps({"": "corrupt-section"})
    state.write_text(seeded, encoding="utf-8")
    for _ in range(2):
        code, payload, _err = _invoke(_gradient_frame("true"), capsys)
        assert code == 2
        assert _deny_reason(payload) == wait_filler_gate._DENY_REASON
    assert state.read_text(encoding="utf-8") == seeded


def test_corrupt_total_silently_resets_and_persists(
    tmp_path, monkeypatch, capsys
):
    """test-M3(b), review remediation: a non-int `total` is NOT a degrade
    — the as-built lenient branch silently treats it as 0 (repair-on-
    write): the denial still escalates normally off a fresh total, and
    the repaired integer total IS persisted. Pins the asymmetry bc-M3
    documented: corrupt pairs/total reset, corrupt budget degrades."""
    import wait_filler_gate

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    state = _state_path(tmp_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps({
            "": {"pairs": {}, "total": "nine", "valve_budget": 2}
        }),
        encoding="utf-8",
    )
    # count 1, total resets 0 -> 1: tier 1 (frozen text)
    code, payload, _err = _invoke(_gradient_frame("sleep 30"), capsys)
    assert code == 2
    assert _deny_reason(payload) == wait_filler_gate._DENY_REASON
    # re-fire: count 2, total 2 -> tier 2 — the reset total is what the
    # boost reads, and the repair persisted an int
    _code, payload, _err = _invoke(_gradient_frame("sleep 30"), capsys)
    assert "denied 2 times this session (2 filler calls in total)" in _deny_reason(payload)
    state_data = json.loads(state.read_text(encoding="utf-8"))
    assert state_data[""]["total"] == 2
    assert isinstance(state_data[""]["total"], int)


def test_version_skew_unknown_section_key_survives_write(
    tmp_path, monkeypatch, capsys
):
    """bc-F1 arm, review remediation: version-skew preserve — a section
    written by a FUTURE hook version carries unknown keys; a rewrite by
    THIS version (deny increment and valve decrement alike) must
    merge-preserve them, not strip them. (Pre-remediation _apply replaced
    the whole section dict and stripped them; devops lands the preserve —
    expected transient red until that stage lands.)"""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    state = _state_path(tmp_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps({
            "": {
                "pairs": {},
                "total": 0,
                "valve_budget": 2,
                "future_field": "keep-me",
            }
        }),
        encoding="utf-8",
    )
    _code, _payload, _err = _invoke(_gradient_frame("sleep 30"), capsys)
    assert json.loads(state.read_text(encoding="utf-8"))[""][
        "future_field"
    ] == "keep-me"


def test_tier3_outlet_write_preserves_unknown_keys(
    tmp_path, monkeypatch, capsys
):
    """Bugbot-2 arm, re-homed: version-skew preserve through the TIER-3
    outlet write (every filler write is a deny write now — the retired
    budget decrement no longer has its own rewrite shape). Seeded at
    count 2 so this instant form's third attempt is a tier-3 outlet
    deny; assert the deny fired with the outlet text, the unknown keys
    survived, the legacy budget field was written as its inert 0, and
    pairs/total landed their new values in the same write."""
    import wait_filler_gate

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    state = _state_path(tmp_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    true_key = wait_filler_gate._pair_key("true")
    state.write_text(
        json.dumps({
            "": {
                "pairs": {true_key: 2},
                "total": 2,
                "valve_budget": 2,
                "future_field": "keep-me",
            }
        }),
        encoding="utf-8",
    )
    code, payload, _err = _invoke(_gradient_frame("true"), capsys)
    assert code == 2
    assert "PACT-WAIT-OUTLET" in _deny_reason(payload)
    section = json.loads(state.read_text(encoding="utf-8"))[""]
    assert section["future_field"] == "keep-me"
    assert section["valve_budget"] == 0
    assert section["pairs"] == {true_key: 3}
    assert section["total"] == 3


def test_truthy_nondict_pairs_repair_and_persist(
    tmp_path, monkeypatch, capsys
):
    """Bugbot-1 arm: a TRUTHY non-dict `pairs` (a string passes the
    falsy-checks a `if not pairs` guard would use) takes the soft-field
    repair branch — reset to {} in-memory, denial escalates normally off
    the fresh count, and the repaired single-key pairs dict is PERSISTED
    on the very first write. The persisted file after call 1 is the
    discriminator: a degrade path would leave the seed untouched, and a
    non-persisting reset would loop at tier 1 forever."""
    import wait_filler_gate

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    state = _state_path(tmp_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(
        json.dumps({
            "": {"pairs": "corrupt", "total": 5, "valve_budget": 2}
        }),
        encoding="utf-8",
    )
    # call 1: count 0 -> 1, total 5 -> 6, no boost -> tier 1, and the
    # repair is persisted in the same write
    code, payload, _err = _invoke(_gradient_frame("sleep 30"), capsys)
    assert code == 2
    assert _deny_reason(payload) == wait_filler_gate._DENY_REASON
    section = json.loads(state.read_text(encoding="utf-8"))[""]
    assert section["pairs"] == {wait_filler_gate._pair_key("sleep 30"): 1}
    assert section["total"] == 6
    assert isinstance(section["pairs"], dict)
    # call 2: count 2, total 7 > 6 -> boost -> tier 3 FILLER-LOCK — the
    # gradient resumed, not a stuck tier-1 loop
    _code, payload, _err = _invoke(_gradient_frame("sleep 30"), capsys)
    assert "FILLER-LOCK" in _deny_reason(payload)


def test_state_raise_degrades_tier1_with_stderr_note(
    tmp_path, monkeypatch, capsys
):
    """The _escalate catch-all itself: a locked_update that RAISES
    (sidecar-lock timeout, containment refusal) — not one that returns
    None — degrades to the frozen tier-1 deny with a stderr note, valve
    closed. Distinct from the loader-None and corrupt-file arms, which
    take explicit returns."""
    import wait_filler_gate

    def _raising_update(_path, _apply, _root, timeout=None):
        raise TimeoutError("sidecar lock held past timeout")

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(
        wait_filler_gate, "_load_locked_update", lambda: _raising_update
    )
    for _ in range(3):  # instant form that would be tier 3 valve-eligible
        code, payload, err = _invoke(_gradient_frame("true"), capsys)
        assert code == 2
        assert _deny_reason(payload) == wait_filler_gate._DENY_REASON
        assert "filler-state error" in err
    assert not _state_path(tmp_path).exists()


def test_growth_caps_degrade_without_persisting(tmp_path, monkeypatch, capsys):
    """Unbounded-growth guards: a section at the 64-pair cap (fresh key)
    and a file at the 32-section cap (fresh agent) degrade to the
    stateless tier-1 deny and leave the file byte-untouched."""
    import wait_filler_gate

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    state = _state_path(tmp_path)
    state.parent.mkdir(parents=True, exist_ok=True)

    capped_pairs = {"": {
        "pairs": {f"k{n:02x}": 1 for n in range(64)},
        "total": 64, "valve_budget": 2,
    }}
    state.write_text(json.dumps(capped_pairs), encoding="utf-8")
    frozen = state.read_text(encoding="utf-8")
    code, payload, _err = _invoke(_gradient_frame("sleep 30"), capsys)
    assert code == 2
    assert _deny_reason(payload) == wait_filler_gate._DENY_REASON
    assert state.read_text(encoding="utf-8") == frozen

    capped_sections = {f"agent{n:02x}": {
        "pairs": {}, "total": 1, "valve_budget": 2,
    } for n in range(32)}
    state.write_text(json.dumps(capped_sections), encoding="utf-8")
    frozen = state.read_text(encoding="utf-8")
    code, payload, _err = _invoke(
        _gradient_frame("sleep 30", agent_type="pact-test-engineer"), capsys
    )
    assert code == 2
    assert _deny_reason(payload) == wait_filler_gate._DENY_REASON
    assert state.read_text(encoding="utf-8") == frozen


def test_unset_config_env_degrades_stateless_tier1(
    tmp_path, monkeypatch, capsys
):
    """O4: with CLAUDE_CONFIG_DIR unset (the conftest autouse scrub leaves
    it unset; this arm deliberately does not set it), a session_id-bearing
    filler denial is today's tier-1 deny — permanently, no gradient — and
    NOTHING is written under the (redirected) home root. Documented,
    bounded degradation; dogfood-probe material, not a bug."""
    import wait_filler_gate

    for _ in range(3):
        code, payload, _err = _invoke(
            _gradient_frame("sleep 30", session_id="s-o4"), capsys
        )
        assert code == 2
        assert _deny_reason(payload) == wait_filler_gate._DENY_REASON
    assert not (Path.home() / "pact-filler-state").exists()
    assert not (tmp_path / "pact-filler-state").exists()


def test_allow_fast_path_emits_identical_bytes(capsys):
    """O2: the allow fast-path's EMITTED output is byte-identical to the
    pre-gradient hook (not merely source-level 'unchanged' — comments
    moved in the branch, the bytes on stdout must not). Plain non-filler
    command, no agent_type: exactly the historical allow line on stdout,
    nothing on stderr, exit 0."""
    from wait_filler_gate import main

    with patch("sys.stdin", io.StringIO(_bash_payload("git status"))):
        with pytest.raises(SystemExit) as exc:
            main()
    captured = capsys.readouterr()
    assert exc.value.code == 0
    # print()'s trailing newline is part of the emitted bytes — the
    # pre-gradient hook printed the same constant the same way.
    assert captured.out == '{"suppressOutput": true}\n'
    assert captured.err == ""


def test_docstring_carries_no_raw_tabs():
    """O1: the pattern documentation keeps `\\t` as the two-character
    escape (visible in any viewer), not a raw tab byte — the cosmetic
    normalization the audit observed, pinned so it cannot drift back."""
    import wait_filler_gate

    assert "\t" not in (wait_filler_gate.__doc__ or "")


# ---------------------------------------------------------------------------
# Cross-process subprocess probes — the plan's named P0. The hook runs as
# a FRESH interpreter for every Bash call; only these arms prove the
# gradient, valve and budget live on disk, not in process memory.
# ---------------------------------------------------------------------------


def _run_hook_script(frame: dict) -> "tuple[int, str, str]":
    """Run the hook as a script in a fresh interpreter. Returns
    (exit_code, stdout, stderr). CLAUDE_CONFIG_DIR is inherited from the
    test process (monkeypatch-scoped, so hermetic per arm)."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(frame),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _probe_reason(stdout: str) -> str:
    payload = json.loads(stdout)
    return payload["hookSpecificOutput"]["permissionDecisionReason"]


def test_cross_process_gradient_persists(tmp_path, monkeypatch):
    """P0 subprocess probe: three separate interpreters, same session —
    tier 1, tier 2, tier 3 in order. In-process state would leave every
    invocation at tier 1; only on-disk state produces this sequence."""
    import wait_filler_gate

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    reasons = []
    for _ in range(3):
        code, out, _err = _run_hook_script(
            json.loads(_gradient_frame("sleep 30"))
        )
        assert code == 2
        reasons.append(_probe_reason(out))
    assert reasons[0] == wait_filler_gate._DENY_REASON
    assert "denied 2 times this session (2 filler calls in total)" in reasons[1]
    assert "FILLER-LOCK" in reasons[2]


def test_cross_process_tier3_outlet_persists(tmp_path, monkeypatch):
    """P0 subprocess probe: six separate interpreters deny every time —
    no interpreter ever exits 0 — and the count persists across
    processes: from the third interpreter the reason carries the outlet
    arm command, and the 6th renders the through-denial count."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    reason = ""
    for i in range(1, 7):
        code, out, _err = _run_hook_script(json.loads(_gradient_frame("true")))
        assert code == 2, i
        reason = _probe_reason(out)
        if i >= 3:
            assert "PACT-WAIT-OUTLET" in reason, i
    assert (
        "denied 6 times this session (6 filler calls in total)" in reason
    )


def test_concurrent_updates_lose_nothing(tmp_path, monkeypatch):
    """P1 concurrency: two hook processes racing on one state file, and
    every increment lands — distinct commands both count (total 2), and
    the same command raced adds both increments onto the phase-1 count
    (1 -> 3), not a last-writer's partial. CLAIM SCOPE: this pins the
    NO-LOST-UPDATE property of the final state under concurrent
    spawning; it does NOT prove the sidecar lock serialized a genuinely
    overlapping hold (interpreter startup may schedule the processes
    benignly). Lock correctness is state_file.locked_update's own
    contract and suite; contention past the 2.0s timeout is pinned by
    test_held_lock_times_out_to_tier1."""
    import subprocess
    import sys

    import wait_filler_gate

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    def _spawn(command):
        return subprocess.Popen(
            [sys.executable, str(HOOK_PATH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _frame(command):
        return json.dumps(json.loads(_gradient_frame(command)))

    # distinct commands, concurrent
    procs = [_spawn("sleep 30"), _spawn("sleep 31")]
    for proc, command in zip(procs, ("sleep 30", "sleep 31")):
        proc.communicate(input=_frame(command), timeout=30)
    state = json.loads(_state_path(tmp_path).read_text(encoding="utf-8"))
    assert state[""]["total"] == 2
    assert sorted(state[""]["pairs"].values()) == [1, 1]

    # same command, concurrent: the sidecar lock serializes both
    # increments onto the phase-1 count (1 -> 3, not a last-writer 1 or 2)
    procs = [_spawn("sleep 30"), _spawn("sleep 30")]
    for proc in procs:
        proc.communicate(input=_frame("sleep 30"), timeout=30)
    state = json.loads(_state_path(tmp_path).read_text(encoding="utf-8"))
    assert state[""]["total"] == 4
    assert state[""]["pairs"][
        wait_filler_gate._pair_key("sleep 30")
    ] == 3
    assert state[""]["pairs"][
        wait_filler_gate._pair_key("sleep 31")
    ] == 1


def test_held_lock_times_out_to_tier1(tmp_path, monkeypatch, capsys):
    """CONTENTION, deterministically — the arm the benign-scheduling
    concurrency probe above cannot be. Another writer (this test) holds
    the sidecar flock past the hook's 2.0s locked_update timeout, so the
    hook's acquire RAISES TimeoutError inside _escalate, and the
    catch-all degrades to the frozen tier-1 deny with the valve closed
    and a stderr note — nothing written, and the wait really happened
    (elapsed >= the timeout; a hook that skipped the lock would finish
    in milliseconds and fail this). flock conflicts across distinct fds
    in one process, so the in-process hold genuinely blocks the hook."""
    import fcntl
    import os
    import time

    import wait_filler_gate

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    state = _state_path(tmp_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    seeded = json.dumps({"": {"pairs": {}, "total": 0, "valve_budget": 2}})
    state.write_text(seeded, encoding="utf-8")
    lock_fd = os.open(
        str(state.with_name(state.name + ".lock")), os.O_RDWR | os.O_CREAT, 0o600
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        start = time.monotonic()
        code, payload, err = _invoke(_gradient_frame("true"), capsys)
        elapsed = time.monotonic() - start
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    assert code == 2
    assert _deny_reason(payload) == wait_filler_gate._DENY_REASON
    assert "filler-state error" in err
    assert elapsed >= 2.0
    assert state.read_text(encoding="utf-8") == seeded


def test_cross_process_reaper_sees_probe_state(tmp_path, monkeypatch):
    """Integration close-out: state the probe wrote is exactly what
    cleanup_old_filler_state reaps — the writer and the reaper share the
    env-resolved root and the per-sid file naming."""
    import os
    import time

    import session_end

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    _run_hook_script(json.loads(_gradient_frame("sleep 30")))
    state = _state_path(tmp_path)
    assert state.exists()
    stale = time.time() - 31 * 86400
    os.utime(state, (stale, stale))
    reaped, _skipped = session_end.cleanup_old_filler_state()
    assert reaped == 1
    assert not state.exists()


# The previous release's (4.8.36) section reader, copied verbatim from its
# wait_filler_gate._compute_gradient (docstring dropped). A downgrade runs
# THIS code against state the current release wrote, so the section must
# read as valid to it, not as corrupt (which pins the session at tier 1).
_PREVIOUS_RELEASE_READER = '''
_MAX_TIER = 3
_SATURATION_TOTAL = 6
_INITIAL_VALVE_BUDGET = 2


def _compute_gradient(section, key: str, instant: bool):
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
'''


def test_previous_release_reads_this_release_state_as_valid(
    tmp_path, monkeypatch, capsys
):
    """Downgrade compatibility: a state section this release wrote reads as
    VALID to the previous release's reader (a tier tuple, not the None that
    means corrupt), keeps that reader's counts, and leaves its retired
    allowance closed at tier 3."""
    _drive(["true"] * 3, tmp_path, monkeypatch, capsys)
    state = json.loads(_state_path(tmp_path).read_text(encoding="utf-8"))
    section = state[""]
    namespace: dict = {}
    exec(_PREVIOUS_RELEASE_READER, namespace)  # frozen literal above, not input
    key = next(iter(section["pairs"]))
    result = namespace["_compute_gradient"](section, key, True)
    assert result is not None, section
    tier, count, total, allowance_open, _budget = result
    assert (tier, count, total) == (3, 4, 4)
    assert allowance_open is False


@pytest.mark.parametrize("seeded", [2, 1, 0, "two", -1, True, None])
def test_legacy_budget_is_always_written_as_zero(tmp_path, monkeypatch, capsys, seeded):
    """Whatever an older writer left under the legacy budget key, a section
    this release writes carries 0: a positive value would keep the previous
    release's retired allowance live after a downgrade."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    state = _state_path(tmp_path)
    state.parent.mkdir(parents=True, exist_ok=True)
    section = {"pairs": {}, "total": 0}
    if seeded is not None:
        section["valve_budget"] = seeded
    state.write_text(json.dumps({"": section}), encoding="utf-8")
    _invoke(_gradient_frame("true"), capsys)
    written = json.loads(state.read_text(encoding="utf-8"))[""]
    assert written["valve_budget"] == 0
    assert type(written["valve_budget"]) is int


def test_every_intentional_wait_branch_names_a_concrete_resolver():
    """Each non-arm intentional_wait instruction gives the value to write:
    expected_resolver set to the lead, never an abstract 'the resolver'."""
    import wait_filler_gate

    for text in (wait_filler_gate._TIER3_TEAMMATE_SEPARATE_PROCESS_REASON,
                 wait_filler_gate._TIER3_TEAMMATE_REASON):
        assert "metadata.intentional_wait" in text
        assert "`expected_resolver` set to `lead`" in text
        assert "naming the resolver" not in text


def test_catch_all_names_no_ci_wait():
    """A finished CI run arrives as no turn, so the catch-all branch (end
    the turn and let the reply arrive) must not name CI as one of its
    waits; no rendered outlet text names it."""
    import re

    import wait_filler_gate

    assert not re.search(r"\bCI\b", wait_filler_gate.WAIT_CONDITIONS["other"])
    for role in wait_filler_gate.OUTLET:
        text = wait_filler_gate.render_outlet(wait_filler_gate.OUTLET[role])
        assert not re.search(r"\bCI\b", text), role
