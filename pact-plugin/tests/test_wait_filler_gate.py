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
    check is an explicit allowlist rather than sys.stdlib_module_names (3.10+):
    the CI matrix runs this suite on Python 3.9, where that attribute does not
    exist.

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
    assert "denied 2 times this session" in tier2
    assert "FILLER-LOCK" in tier3


def test_gradient_stderr_mirrors_the_deny_reason(tmp_path, monkeypatch, capsys):
    """The denial text rides stdout JSON (the surface this harness parses
    at exit 2) and is mirrored to stderr for harnesses reported to drop
    stdout JSON at exit 2 (channel probe, issue #1657 plan)."""
    code, payload, err = _drive(["true"], tmp_path, monkeypatch, capsys)[0]
    assert code == 2
    assert err.strip() == _deny_reason(payload)


def test_valve_allows_instant_form_at_tier3_twice_then_denies(
    tmp_path, monkeypatch, capsys
):
    """The circuit-breaker fires exactly at computed tier 3 for an instant
    form, twice per session (budget 2), then closes back to the tier-3
    deny. Attempt counts keep growing through valve allows so the gradient
    never regresses."""
    results = _drive(["true"] * 5, tmp_path, monkeypatch, capsys)
    codes = [code for code, _p, _e in results]
    assert codes == [2, 2, 0, 0, 2]
    valve1 = results[2][1]["hookSpecificOutput"]["additionalContext"]
    valve2 = results[3][1]["hookSpecificOutput"]["additionalContext"]
    assert "CIRCUIT-BREAKER" in valve1 and "CIRCUIT-BREAKER" in valve2
    assert "remaining: 1" in valve1 and "remaining: 0" in valve2
    assert "FILLER-LOCK" in _deny_reason(results[4][1])


def test_valve_never_opens_for_nonzero_or_infinite_sleep(
    tmp_path, monkeypatch, capsys
):
    """`sleep 1` and `sleep infinity` are never instant forms: three calls
    deny all three times, third at tier 3, and the exit code is never 0."""
    for command in ("sleep 1", "sleep infinity"):
        results = _drive([command] * 3, tmp_path, monkeypatch, capsys)
        assert [c for c, _p, _e in results] == [2, 2, 2], command
        assert "FILLER-LOCK" in _deny_reason(results[2][1]), command


def test_valve_instant_grammar_edges(tmp_path, monkeypatch, capsys):
    """Every all-zero duration spelling (`0`, `0.0`, `.0`, `0s`) is
    valve-eligible at tier 3; `0.1` is not. Fresh session per form so the
    shared budget does not couple the arms (explicit safe-shape sids: a
    derived id containing `.` would itself fail the gate's safe-sid
    fullmatch and pass this arm for the wrong reason)."""
    for session_id, command, expect_valve in (
        ("s-zero", "sleep 0", True),
        ("s-zero-dot-zero", "sleep 0.0", True),
        ("s-dot-zero", "sleep .0", True),
        ("s-zero-s", "sleep 0s", True),
        ("s-zero-dot-one", "sleep 0.1", False),
    ):
        results = _drive([command] * 3, tmp_path, monkeypatch, capsys,
                         session_id=session_id)
        third_code = results[2][0]
        if expect_valve:
            assert third_code == 0, command
        else:
            assert third_code == 2, command


def test_state_failure_degrades_to_tier1_and_closes_valve(
    tmp_path, monkeypatch, capsys
):
    """Fail-direction invariant: with the state channel unavailable the
    hook still denies every filler at the frozen tier-1 advisory — never
    allows, never harder, valve closed (V2 UNKNOWN = DENY)."""
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
    assert "denied 2 times this session" in _deny_reason(second[1])


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
    assert "denied 1 times this session" in _deny_reason(payload)


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
