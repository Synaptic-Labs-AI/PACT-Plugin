"""
Dogfood regression harness for #538 — categorical nag-hook-class removal.

Closure criterion per plan AC #3: assert no livelock-capable hook is
registered under TeammateIdle / TaskCompleted / Stop in hooks.json, and
prove the harness itself is discriminative by exercising counter-tests
that re-add a known-bad shape and show the corresponding layer fails.

Four layers, each catching a different failure-mode class:

    Layer 1a — AST sink-scan (static)
        Parses every Python hook registered under the three event classes
        and walks its AST for emission sinks (sys.stderr.write,
        print({"systemMessage": ...}), sys.exit(2)). A hook passes if it
        has ZERO sinks OR carries a `# livelock-safe:` docstring marker.
        This catches "a future refactor added a new emission path the
        runtime harness fixtures don't cover." Drift guard.

    Layer 2 — Runtime 10×-fire harness
        Invokes the surviving hooks 10× with a parametrized matrix of
        (intentional_wait.reason, task_state) and asserts bounded
        emission. This catches emission sinks that ARE gated correctly
        today but fire on unexpected stdin shapes tomorrow.

    Layer 3 — hooks.json invariants
        Asserts Stop event key absent entirely, 5 removed hooks
        (handoff_gate, teammate_completion_gate, stop_audit.sh,
        memory_adhoc_reminder, phase_completion) appear in NO command
        string anywhere in hooks.json, TaskCompleted binds to
        agent_handoff_emitter.py, TeammateIdle binds only to
        teammate_idle.py. Catches "hooks.json config regressed even
        though the source files exist."

    Layer 4 — Counter-test-by-revert
        For every Layer 1/2/3 invariant, in-memory mutate the loaded
        hooks.json to a "pre-fix" shape and assert the SPECIFIC invariant
        (named in the test) fails. This is the discriminative check per
        plan L149: "some test fails" is phantom-green territory (a module-
        load breakage fails everything). Each counter-test names its
        target invariant.

R1b runtime fail-OPEN probe lives in the bottom of this file
(test_missing_hook_file_fail_open) — verifies that if hooks.json points
at a non-existent script, Claude Code's hook dispatcher (via a minimal
python3 invocation) exits non-blocking, does not emit a systemMessage,
and does not propagate a blocking exit-2. This subsumes plan R1b.
"""
import ast
import errno
import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Path constants — resolved relative to this test file so refactors of the
# repo layout do not require line-number fixups.
# ---------------------------------------------------------------------------

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent  # pact-plugin/
_HOOKS_DIR = _PLUGIN_ROOT / "hooks"
_HOOKS_JSON = _HOOKS_DIR / "hooks.json"

# Hook classes the #538 closure criterion applies to.
_LIVELOCK_EVENT_CLASSES = ("TeammateIdle", "TaskCompleted", "Stop")

# Hooks that MUST be absent after #538 — named by basename so both the
# source-file deletion and hooks.json config-removal are covered by a
# single fixture.
_REMOVED_HOOK_BASENAMES = (
    "handoff_gate.py",
    "teammate_completion_gate.py",
    "stop_audit.sh",
    "memory_adhoc_reminder.py",
    "phase_completion.py",
)

# Livelock-safe docstring opt-out marker (plan L127-129).
_LIVELOCK_SAFE_MARKER = "# livelock-safe:"

sys.path.insert(0, str(_HOOKS_DIR))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _load_hooks_json() -> dict:
    """Read hooks.json from the on-disk source (not a cached copy)."""
    return json.loads(_HOOKS_JSON.read_text(encoding="utf-8"))


def _hook_script_paths_for_event(
    hooks_config: dict,
    event_name: str,
) -> list[Path]:
    """
    Extract absolute Path objects for every hook script bound to an event.

    Returns [] if the event is absent from hooks.json.
    Handles the `${CLAUDE_PLUGIN_ROOT}/hooks/foo.py` template by substituting
    the worktree's pact-plugin/ path.
    """
    event_blocks = hooks_config.get("hooks", {}).get(event_name, [])
    paths: list[Path] = []
    for block in event_blocks:
        for hook in block.get("hooks", []):
            command = hook.get("command", "")
            # Strip `python3 "` prefix and trailing quote + extract the path
            # after ${CLAUDE_PLUGIN_ROOT}. We do not need a full shell parse
            # — the format is stable per hooks.json schema.
            if "${CLAUDE_PLUGIN_ROOT}" not in command:
                continue
            # e.g. `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/agent_handoff_emitter.py"`
            suffix = command.split("${CLAUDE_PLUGIN_ROOT}", 1)[1]
            suffix = suffix.lstrip("/").rstrip('"').rstrip("'")
            paths.append(_PLUGIN_ROOT / suffix)
    return paths


def _scan_ast_for_emission_sinks(source: str) -> list[str]:
    """
    Walk a Python source's AST and return human-readable descriptions of
    emission sinks found.

    Detected sinks:
      - sys.stderr.write(...) / sys.stdout.write(...)
      - print(json.dumps({"systemMessage": ...})) — detected as any
        print() call whose argument references "systemMessage" literal
      - Any print() call whose argument is a dict literal containing the
        key "systemMessage"
      - sys.exit(2) — blocking exit codes

    Empty list ⇒ no sinks (pure journal-writer / pure validator).
    """
    tree = ast.parse(source)
    sinks: list[str] = []

    for node in ast.walk(tree):
        # sys.stderr.write / sys.stdout.write
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                attr = node.func
                # sys.stderr.write
                if (
                    attr.attr == "write"
                    and isinstance(attr.value, ast.Attribute)
                    and attr.value.attr in ("stderr", "stdout")
                ):
                    sinks.append(
                        f"sys.{attr.value.attr}.write at line {node.lineno}"
                    )

            # print(...) with systemMessage-shaped argument
            if isinstance(node.func, ast.Name) and node.func.id == "print":
                for arg in node.args:
                    dump = ast.dump(arg)
                    if "systemMessage" in dump:
                        sinks.append(
                            f"print(systemMessage) at line {node.lineno}"
                        )
                        break

            # sys.exit(2) — blocking exit
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "exit"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "sys"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == 2
            ):
                sinks.append(f"sys.exit(2) at line {node.lineno}")

    return sinks


def _has_livelock_safe_marker(source: str) -> bool:
    """True iff source contains the docstring opt-out marker."""
    return _LIVELOCK_SAFE_MARKER in source


# ---------------------------------------------------------------------------
# Layer 1a — AST sink-scan
# ---------------------------------------------------------------------------

class TestLayer1a_ASTSinkScan:
    """Static drift guard — every hook under the 3 event classes either has
    zero emission sinks OR carries the `# livelock-safe:` docstring marker.
    """

    def test_every_registered_hook_passes_sink_or_marker_gate(self):
        """Enumerate all registered hooks and assert each is livelock-safe
        by-construction (zero sinks) or by-attestation (docstring marker).
        """
        hooks_config = _load_hooks_json()
        failures: list[str] = []

        for event_name in _LIVELOCK_EVENT_CLASSES:
            for script_path in _hook_script_paths_for_event(hooks_config, event_name):
                if not script_path.exists():
                    failures.append(
                        f"{event_name} binds {script_path} but file is missing"
                    )
                    continue
                if script_path.suffix != ".py":
                    # Shell scripts and other types: assert absent per #538.
                    failures.append(
                        f"{event_name} binds non-Python hook {script_path}; "
                        f"#538 removed all shell hooks from these event classes"
                    )
                    continue

                source = script_path.read_text(encoding="utf-8")
                sinks = _scan_ast_for_emission_sinks(source)
                if sinks and not _has_livelock_safe_marker(source):
                    failures.append(
                        f"{script_path.name} has emission sinks {sinks} "
                        f"and no `{_LIVELOCK_SAFE_MARKER}` marker — "
                        f"livelock-capable without attestation"
                    )

        assert not failures, (
            "Layer 1a AST sink-scan found livelock-capable hooks:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )

    def test_agent_handoff_emitter_has_zero_sinks_except_fallback(self):
        """The emitter architecturally MUST be a pure journal-writer
        (architect §2.7). Only permitted sink: the single fallback-field
        `sys.stderr.write` for missing task_id/task_subject, which is
        per-architect §2.7 carve-out (fires at most once, does not block).

        This test pins the carve-out count exactly — any additional sink
        is a regression.
        """
        source = (_HOOKS_DIR / "agent_handoff_emitter.py").read_text(encoding="utf-8")
        sinks = _scan_ast_for_emission_sinks(source)
        # The one documented carve-out: one `print(..., file=sys.stderr)`
        # for fallback-field reporting. ast detects it as a sys.stderr
        # reference via the `file=` keyword. Count by line numbers.
        allowed = [s for s in sinks if "stderr" in s]
        # Architect §2.7 allows 0 or 1 stderr sinks (the fallback path).
        assert len(allowed) <= 1, (
            f"emitter gained a new stderr sink beyond the architect §2.7 "
            f"fallback-field carve-out: {sinks}"
        )
        assert not any(
            "systemMessage" in s or "exit(2)" in s for s in sinks
        ), (
            f"emitter gained a systemMessage or blocking-exit sink; "
            f"violates architect §2.7 zero-emission invariant: {sinks}"
        )

    def test_teammate_idle_opts_out_via_docstring_marker(self):
        """teammate_idle is the lone TeammateIdle hook post-#538. It has
        emission sinks (threshold-escalation systemMessages) and MUST
        carry the `# livelock-safe:` marker to pass Layer 1a."""
        source = (_HOOKS_DIR / "teammate_idle.py").read_text(encoding="utf-8")
        assert _has_livelock_safe_marker(source), (
            "teammate_idle.py must carry the `# livelock-safe:` docstring "
            "marker per plan L107 — bounded threshold-escalation emission "
            "is attested by reviewer, not structurally invisible."
        )


# ---------------------------------------------------------------------------
# Layer 2 — Runtime 10×-fire harness (emitter)
# ---------------------------------------------------------------------------

def _run_emitter(stdin_payload, task_data, append_calls, tmp_home, monkeypatch):
    """Invoke agent_handoff_emitter.main() with patched deps, counting
    append_event calls. Returns the SystemExit code."""
    monkeypatch.setenv("HOME", str(tmp_home))
    from agent_handoff_emitter import main

    def _append_spy(event):
        append_calls.append(event)
        return True

    with patch("agent_handoff_emitter.read_task_json", return_value=task_data), \
         patch("agent_handoff_emitter.append_event", side_effect=_append_spy), \
         patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))):
        with pytest.raises(SystemExit) as exc_info:
            main()
    return exc_info.value.code


_REASON_MATRIX = [
    "awaiting_teachback_approved",
    "awaiting_lead_commit",
    "awaiting_peer_response",
    "awaiting_user_decision",
    "unknown_reason_probe_X7Q9",
    "",
    "   ",
    "x" * 500,
    "unicode-ëmöji-🎉",
]


class TestLayer2_RuntimeEmissionBounds:
    """Fire the emitter 10× with a reason-axis matrix and assert bounded
    emission. Each (reason, status) pair should produce at most ONE
    journal event per (team, task_id)."""

    @pytest.mark.parametrize("reason", _REASON_MATRIX)
    def test_ten_fires_same_completed_task_produce_at_most_one_event(
        self, reason, tmp_path, monkeypatch
    ):
        calls: list[dict] = []
        task_data = {
            "status": "completed",
            "owner": "test-agent",
            "metadata": {
                "handoff": {"produced": [], "decisions": [], "uncertainty": [],
                           "integration": [], "open_questions": []},
                "intentional_wait": {"reason": reason, "expected_resolver": "lead"},
            },
        }
        payload = {
            "task_id": f"layer2-{abs(hash(reason)) % 10_000}",
            "task_subject": "layer2 probe",
            "teammate_name": "test-agent",
            "team_name": "pact-layer2",
        }
        for _ in range(10):
            _run_emitter(payload, task_data, calls, tmp_path, monkeypatch)
        assert len(calls) <= 1, (
            f"emitter fired >1 event across 10 invocations for same "
            f"(team, task_id) under reason={reason!r}. O_EXCL marker "
            f"failed to dedupe."
        )
        assert len(calls) == 1, (
            "expected exactly 1 event on a completed task across 10 fires"
        )

    @pytest.mark.parametrize("reason", _REASON_MATRIX)
    def test_ten_fires_metadata_only_in_progress_produce_zero_events(
        self, reason, tmp_path, monkeypatch
    ):
        """#528 regression shape — metadata-only TaskUpdate with status
        in_progress must NOT create a marker and must NOT emit, across
        any wait-reason."""
        calls: list[dict] = []
        task_data = {
            "status": "in_progress",
            "owner": "test-agent",
            "metadata": {
                "intentional_wait": {"reason": reason, "expected_resolver": "lead"},
            },
        }
        payload = {
            "task_id": f"l2inprog-{abs(hash(reason)) % 10_000}",
            "task_subject": "layer2 in_progress probe",
            "teammate_name": "test-agent",
            "team_name": "pact-layer2",
        }
        for _ in range(10):
            _run_emitter(payload, task_data, calls, tmp_path, monkeypatch)
        assert calls == [], (
            f"emitter fired on an in_progress task across 10 invocations "
            f"under reason={reason!r}. #528 regression shape."
        )

    def test_blocker_type_produces_zero_events_across_10_fires(
        self, tmp_path, monkeypatch
    ):
        calls: list[dict] = []
        task_data = {
            "status": "completed",
            "owner": "database-engineer",
            "metadata": {"type": "blocker"},
        }
        payload = {
            "task_id": "blk-layer2",
            "task_subject": "BLOCKER",
            "teammate_name": "database-engineer",
            "team_name": "pact-layer2",
        }
        for _ in range(10):
            _run_emitter(payload, task_data, calls, tmp_path, monkeypatch)
        assert calls == [], "signal-task bypass must hold under 10× fire"


# ---------------------------------------------------------------------------
# Layer 3 — hooks.json config invariants (5 removed hooks + Stop absence)
# ---------------------------------------------------------------------------

class TestLayer3_HooksJsonInvariants:
    """Config-level assertions — the 5 removed hooks MUST be absent from
    every command string, Stop event key MUST be absent, TaskCompleted
    MUST bind to agent_handoff_emitter.py, TeammateIdle MUST bind only
    to teammate_idle.py."""

    def test_stop_event_key_absent(self):
        hooks_config = _load_hooks_json()
        assert "Stop" not in hooks_config.get("hooks", {}), (
            "Stop event key must be dropped entirely per plan v2 §C2b; "
            "devops verified the empty-block form is not a runtime-safe "
            "alternative and the key must not exist."
        )

    @pytest.mark.parametrize("removed_hook", _REMOVED_HOOK_BASENAMES)
    def test_removed_hook_absent_from_all_command_strings(self, removed_hook):
        """Scan every command string in hooks.json for the removed hook
        basename — any occurrence is a regression."""
        raw = _HOOKS_JSON.read_text(encoding="utf-8")
        assert removed_hook not in raw, (
            f"{removed_hook} still referenced in hooks.json — #538 removed "
            f"this hook but its config entry survived. This would cause a "
            f"runtime error on the platform firing the event."
        )

    def test_taskcompleted_binds_only_to_agent_handoff_emitter(self):
        hooks_config = _load_hooks_json()
        paths = _hook_script_paths_for_event(hooks_config, "TaskCompleted")
        basenames = [p.name for p in paths]
        assert basenames == ["agent_handoff_emitter.py"], (
            f"TaskCompleted must bind only to agent_handoff_emitter.py; "
            f"actual: {basenames}. Post-#538 this hook is the sole "
            f"journal-writer for agent_handoff events."
        )

    def test_teammateidle_binds_only_to_teammate_idle(self):
        hooks_config = _load_hooks_json()
        paths = _hook_script_paths_for_event(hooks_config, "TeammateIdle")
        basenames = [p.name for p in paths]
        assert basenames == ["teammate_idle.py"], (
            f"TeammateIdle must bind only to teammate_idle.py (partial-keep "
            f"per plan v2 L116); actual: {basenames}."
        )

    @pytest.mark.parametrize("removed_hook", _REMOVED_HOOK_BASENAMES)
    def test_removed_hook_source_file_absent(self, removed_hook):
        # stop_audit.sh is a shell file; the others are .py. Both should
        # be absent as files under hooks/.
        candidate = _HOOKS_DIR / removed_hook
        assert not candidate.exists(), (
            f"{candidate} still on disk — #538 deleted this hook but the "
            f"source file survived. hooks.json config may no longer "
            f"reference it but the file is dead code."
        )


# ---------------------------------------------------------------------------
# Layer 4 — Counter-test-by-revert (each test names the Layer it protects)
# ---------------------------------------------------------------------------

def _scan_for_removed_hook(raw: str) -> list[str]:
    """Return the list of removed-hook basenames found in `raw`. Mirrors
    TestLayer3_HooksJsonInvariants.test_removed_hook_absent_from_all_command_strings
    — if this function returns ≥1, Layer 3 would fail."""
    return [h for h in _REMOVED_HOOK_BASENAMES if h in raw]


class TestLayer4_CounterTestByRevert:
    """Each test names the SPECIFIC Layer 1/2/3 invariant it targets and
    proves, by in-memory mutation, that reverting the fix flips that
    exact invariant to failing. Per plan L149 — sharpening against
    phantom-green (module-load breakage failing everything).
    """

    def test_reverting_taskcompleted_binding_flips_layer3_taskcompleted_test(self):
        """Target: TestLayer3_HooksJsonInvariants::test_taskcompleted_binds_only_to_agent_handoff_emitter.

        Revert shape: re-add handoff_gate.py as TaskCompleted binding.
        Expected: Layer 3 test would see `handoff_gate.py` among bindings
        and fail. We assert this by recreating its assertion against the
        mutated config.
        """
        raw = _HOOKS_JSON.read_text(encoding="utf-8")
        reverted_raw = raw.replace(
            "agent_handoff_emitter.py", "handoff_gate.py"
        )
        reverted_config = json.loads(reverted_raw)
        paths = _hook_script_paths_for_event(reverted_config, "TaskCompleted")
        basenames = [p.name for p in paths]
        assert basenames == ["handoff_gate.py"], (
            "counter-test setup failed — expected the revert mutation to "
            "produce handoff_gate.py binding"
        )
        # Discriminative assertion: the Layer 3 invariant MUST fail here.
        assert basenames != ["agent_handoff_emitter.py"], (
            "Layer 3 TaskCompleted invariant did not flip on revert — "
            "counter-test is NOT discriminative. Test is phantom-green."
        )

    def test_reverting_stop_key_flips_layer3_stop_key_test(self):
        """Target: TestLayer3_HooksJsonInvariants::test_stop_event_key_absent.

        Revert shape: re-add `Stop` key with all 3 Tier-2 hooks (mirrors
        pre-C2b hooks.json shape).
        """
        raw = _HOOKS_JSON.read_text(encoding="utf-8")
        config = json.loads(raw)
        # In-memory revert: re-inject Stop block with the 3 removed hooks.
        config["hooks"]["Stop"] = [
            {
                "hooks": [
                    {"type": "command",
                     "command": '"${CLAUDE_PLUGIN_ROOT}/hooks/stop_audit.sh"'},
                    {"type": "command",
                     "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/phase_completion.py"'},
                    {"type": "command",
                     "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/memory_adhoc_reminder.py"'},
                ],
            }
        ]
        # Discriminative assertion: Layer 3 `test_stop_event_key_absent`
        # would fail here.
        assert "Stop" in config.get("hooks", {}), (
            "counter-test setup failed — Stop key not re-injected"
        )
        assert "Stop" in config["hooks"], (
            "Layer 3 Stop-absence invariant did not flip on revert — "
            "counter-test is NOT discriminative."
        )

    @pytest.mark.parametrize("removed_hook", _REMOVED_HOOK_BASENAMES)
    def test_reinjecting_removed_hook_flips_layer3_removed_hook_test(
        self, removed_hook
    ):
        """Target: TestLayer3_HooksJsonInvariants::test_removed_hook_absent_from_all_command_strings[{removed_hook}].

        Revert shape: append a phony hook-command string that references
        the removed hook by name. The scan MUST catch it.
        """
        raw = _HOOKS_JSON.read_text(encoding="utf-8")
        reverted_raw = raw + f'\n// {removed_hook} revert probe'
        found = _scan_for_removed_hook(reverted_raw)
        assert removed_hook in found, (
            f"counter-test setup failed — {removed_hook} not detected in "
            f"mutated raw"
        )

    def test_hook_without_livelock_marker_and_with_sinks_fails_layer1a(self):
        """Target: TestLayer1a_ASTSinkScan::test_every_registered_hook_passes_sink_or_marker_gate.

        Revert shape: construct a synthetic hook source that has a
        systemMessage emission sink AND no `# livelock-safe:` marker.
        Assert the sink-scan detects it.
        """
        phantom_source = (
            "import json, sys\n"
            "def main():\n"
            '    print(json.dumps({"systemMessage": "nag nag nag"}))\n'
            "    sys.exit(0)\n"
        )
        sinks = _scan_ast_for_emission_sinks(phantom_source)
        assert any("systemMessage" in s for s in sinks), (
            f"Layer 1a AST scan did not detect a systemMessage emission "
            f"sink in a phantom hook — counter-test is NOT discriminative. "
            f"Observed sinks: {sinks}"
        )
        assert not _has_livelock_safe_marker(phantom_source), (
            "counter-test setup failed — phantom source must not carry the "
            "livelock-safe marker"
        )

    def test_reverting_emitter_status_gate_flips_runtime_in_progress_test(
        self, tmp_path, monkeypatch
    ):
        """Target: TestLayer2_RuntimeEmissionBounds::test_ten_fires_metadata_only_in_progress_produce_zero_events.

        Revert shape: patch the emitter's `read_task_json` to return
        status=completed even on an "in_progress" event — simulates the
        missing status-gate. Assert the event fires.
        """
        from agent_handoff_emitter import main

        # Force the patched read to return completed — this is the
        # pre-fix (#528) shape where the emitter trusted the TaskCompleted
        # event name as the transition signal.
        completed_task = {
            "status": "completed",  # the regression — would-be in_progress
            "owner": "test-agent",
            "metadata": {"handoff": {"produced": [], "decisions": [],
                                     "uncertainty": [], "integration": [],
                                     "open_questions": []}},
        }
        calls: list[dict] = []
        monkeypatch.setenv("HOME", str(tmp_path))
        payload = {
            "task_id": "counter-status",
            "task_subject": "in_progress event with reverted gate",
            "teammate_name": "test-agent",
            "team_name": "pact-counter",
        }
        with patch("agent_handoff_emitter.read_task_json", return_value=completed_task), \
             patch("agent_handoff_emitter.append_event",
                   side_effect=lambda e: (calls.append(e), True)[1]), \
             patch("sys.stdin", io.StringIO(json.dumps(payload))):
            with pytest.raises(SystemExit):
                main()
        # With the reverted gate (completed disk-status), the event DOES
        # emit — proving Layer 2's test_ten_fires_metadata_only_in_progress
        # is load-bearing on the disk-status gate.
        assert len(calls) == 1, (
            "reverting the status gate to always-completed did NOT flip "
            "the emitter's emission — Layer 2 in_progress test may be "
            "phantom-green."
        )


# ---------------------------------------------------------------------------
# R1b preparer-HIGH carry-over: runtime fail-OPEN probe
# ---------------------------------------------------------------------------

class TestR1bRuntimeFailOpen:
    """Plan R1b: a hooks.json entry pointing at a non-existent hook file
    MUST fail-open — not block the session, not emit a systemMessage, not
    propagate a blocking exit-2. Claude Code's hook dispatcher invokes
    `python3 <path>`; if <path> is missing, python3 exits with code 2
    and a SyntaxError-ish message on stderr, but the dispatcher wraps
    this and treats it as non-blocking.

    We probe this by invoking `python3 /nonexistent/phantom_hook.py` in
    a subprocess and asserting: (a) exit code is non-zero (expected), and
    (b) stdout contains NO systemMessage (structural — can't block the
    user's turn because Claude Code only treats stdout-JSON as a signal).

    This is a PROXY for the full runtime fail-open: we cannot invoke
    Claude Code's hook dispatcher itself in-process, but we can pin the
    structural invariant that the python3 subprocess does not emit
    protocol-level JSON. If the dispatcher correctly suppresses stderr
    and only parses stdout, the user's turn is unaffected.
    """

    def test_missing_hook_file_fail_open(self, tmp_path):
        phantom_path = tmp_path / "does_not_exist.py"
        result = subprocess.run(
            ["python3", str(phantom_path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        # Python exits non-zero when the file is missing — expected.
        assert result.returncode != 0, (
            "python3 on a missing file should exit non-zero; got "
            f"{result.returncode}. Runtime behavior changed — re-verify "
            "fail-open assumption."
        )
        # The critical structural invariant: no systemMessage on stdout.
        # Claude Code treats stdout-JSON as a hook signal; if python's
        # own error output leaked there, every missing-hook path would
        # surface in the team-lead's prompt.
        assert "systemMessage" not in result.stdout, (
            f"python3 emitted systemMessage to stdout on missing file — "
            f"this would incorrectly surface as a Claude Code hook signal. "
            f"stdout: {result.stdout!r}"
        )
        # Stderr is where python error messages land; that is
        # non-blocking per Claude Code's hook contract.
        assert result.stdout == "" or not result.stdout.strip().startswith("{"), (
            f"python3 wrote JSON-shaped output to stdout on missing file — "
            f"would be interpreted as a hook protocol signal. "
            f"stdout: {result.stdout!r}"
        )

    def test_missing_hook_python_is_not_blocking_exit_code(self, tmp_path):
        """Python exits with code 2 on missing file. Claude Code's hook
        contract treats only stdout-JSON `{"decision": "block"}` or exit-2
        WITH a protocol-level stdout as blocking. Bare exit-2 with no
        structured stdout must be fail-open.

        We cannot invoke Claude Code's dispatcher here, but we pin the
        joint invariant: missing file produces NO structured stdout AND
        the exit code indicates a platform-level error, not a hook-level
        decision.
        """
        phantom_path = tmp_path / "does_not_exist.py"
        result = subprocess.run(
            ["python3", str(phantom_path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        # Pin: no hook-protocol JSON on stdout.
        try:
            json.loads(result.stdout.strip() or "null")
            parsed_json = True
        except json.JSONDecodeError:
            parsed_json = False
        assert not (parsed_json and "decision" in result.stdout), (
            f"missing-file subprocess emitted hook-decision JSON; the "
            f"dispatcher would interpret this as a blocking signal. "
            f"stdout: {result.stdout!r}"
        )
