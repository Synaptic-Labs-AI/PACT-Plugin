# pact-plugin/tests/test_hooks_json.py
"""
Tests for hooks.json structural validation.

Tests cover:
1. Valid JSON structure
2. All hook types are recognized Claude Code hook events
3. Async flags only on non-critical hooks
4. All referenced Python scripts exist on disk
5. TeammateIdle hook entry exists (new in SDK optimization)
6. SessionEnd is async (new in SDK optimization)
7. Matcher patterns use valid pipe syntax
8. SubagentStart matcher covers all PACT agent types
"""
import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent / "hooks"
HOOKS_JSON = HOOKS_DIR / "hooks.json"

# Valid Claude Code hook event types
VALID_HOOK_EVENTS = {
    "SessionStart",
    "SessionEnd",
    "PreCompact",
    "PostCompact",
    "PreToolUse",
    "PostToolUse",
    "UserPromptSubmit",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "TaskCompleted",
    "TaskCreated",  # #401 Commit #5: task_schema_validator.py
    "TeammateIdle",
}

# Hooks that MUST be synchronous (blocking) — they affect tool decisions
MUST_BE_SYNC = {
    "team_guard.py",      # Blocks Task dispatch if no team
    "worktree_guard.py",  # Blocks edits outside worktree
    "bootstrap_gate.py",  # Blocks implementation tools until bootstrap
    "bootstrap_prompt_gate.py",  # Injects bootstrap instruction on prompts
    "validate_handoff.py",  # Validates agent output
    "handoff_gate.py",    # Blocks task completion without metadata
    "peer_inject.py",     # Injects peer context on agent start
    "git_commit_check.py",  # Checks git commit conventions
    "track_files.py",     # Tracks file edits (PostToolUse, non-async)
    "auditor_reminder.py",  # Injects auditor dispatch reminder into context
    "precompact_state_reminder.py",  # Emits state snapshot before compaction
    "postcompact_archive.py",  # Archives compact_summary to disk for session_init + secretary
}

# Hooks that SHOULD be async (non-blocking, fire-and-forget)
SHOULD_BE_ASYNC = {
    "session_end.py",     # Fire-and-forget cleanup
    "file_size_check.py", # Advisory warning only
    "file_tracker.py",    # Advisory tracking only
}


@pytest.fixture
def hooks_config():
    """Load and parse hooks.json."""
    content = HOOKS_JSON.read_text(encoding="utf-8")
    return json.loads(content)


class TestHooksJsonStructure:
    """Validate hooks.json is well-formed."""

    def test_valid_json(self):
        """hooks.json must parse as valid JSON."""
        content = HOOKS_JSON.read_text(encoding="utf-8")
        config = json.loads(content)
        assert "hooks" in config

    def test_all_event_types_valid(self, hooks_config):
        """All top-level keys under 'hooks' must be recognized event types."""
        for event_type in hooks_config["hooks"]:
            assert event_type in VALID_HOOK_EVENTS, (
                f"Unknown hook event type: {event_type}. "
                f"Valid types: {sorted(VALID_HOOK_EVENTS)}"
            )

    def test_all_hook_entries_have_type(self, hooks_config):
        """Every hook entry must have a 'type' field."""
        for event_type, entries in hooks_config["hooks"].items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    assert "type" in hook, (
                        f"Hook under {event_type} missing 'type' field"
                    )

    def test_all_hook_entries_have_command(self, hooks_config):
        """Every command-type hook must have a 'command' field."""
        for event_type, entries in hooks_config["hooks"].items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    if hook.get("type") == "command":
                        assert "command" in hook, (
                            f"Command hook under {event_type} missing 'command' field"
                        )


class TestReferencedScriptsExist:
    """Verify all Python scripts referenced in hooks.json exist."""

    def test_all_python_scripts_exist(self, hooks_config):
        """Every python3 command should reference an existing .py file."""
        missing = []
        for event_type, entries in hooks_config["hooks"].items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    if "python3" in cmd and ".py" in cmd:
                        # Extract filename from command like:
                        # python3 "${CLAUDE_PLUGIN_ROOT}/hooks/teammate_idle.py"
                        parts = cmd.split("/hooks/")
                        if len(parts) == 2:
                            script_name = parts[1].strip('"').strip("'")
                            script_path = HOOKS_DIR / script_name
                            if not script_path.exists():
                                missing.append(f"{event_type}: {script_name}")

        assert missing == [], f"Referenced scripts not found: {missing}"

    def test_shell_scripts_exist(self, hooks_config):
        """Every shell script referenced should exist."""
        missing = []
        for event_type, entries in hooks_config["hooks"].items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    if ".sh" in cmd and "python3" not in cmd:
                        parts = cmd.split("/hooks/")
                        if len(parts) == 2:
                            script_name = parts[1].strip('"').strip("'")
                            script_path = HOOKS_DIR / script_name
                            if not script_path.exists():
                                missing.append(f"{event_type}: {script_name}")

        assert missing == [], f"Referenced scripts not found: {missing}"


class TestAsyncFlags:
    """Verify async flags are correctly set on hooks."""

    def _get_hook_async_status(self, hooks_config) -> dict:
        """Build map of script_name -> async status."""
        result = {}
        for event_type, entries in hooks_config["hooks"].items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    cmd = hook.get("command", "")
                    if "/hooks/" in cmd:
                        parts = cmd.split("/hooks/")
                        if len(parts) == 2:
                            script_name = parts[1].strip('"').strip("'")
                            is_async = hook.get("async", False)
                            result[script_name] = is_async
        return result

    def test_critical_hooks_are_synchronous(self, hooks_config):
        """Hooks that affect tool decisions MUST be synchronous."""
        status = self._get_hook_async_status(hooks_config)
        for script in MUST_BE_SYNC:
            if script in status:
                assert status[script] is not True, (
                    f"{script} must be synchronous (no async:true) — "
                    "it affects tool decisions"
                )

    def test_noncritical_hooks_are_async(self, hooks_config):
        """Non-blocking hooks SHOULD be async."""
        status = self._get_hook_async_status(hooks_config)
        for script in SHOULD_BE_ASYNC:
            assert script in status, f"{script} not found in hooks.json"
            assert status[script] is True, (
                f"{script} should be async:true — it is fire-and-forget"
            )


class TestNewSDKOptimizationEntries:
    """Verify new hook entries from the SDK optimization feature."""

    def test_teammate_idle_hook_exists(self, hooks_config):
        """TeammateIdle event should have the teammate_idle.py hook."""
        assert "TeammateIdle" in hooks_config["hooks"]
        entries = hooks_config["hooks"]["TeammateIdle"]
        commands = []
        for entry in entries:
            for hook in entry.get("hooks", []):
                commands.append(hook.get("command", ""))

        assert any("teammate_idle.py" in cmd for cmd in commands), (
            "teammate_idle.py not found in TeammateIdle hooks"
        )

    def test_session_end_is_async(self, hooks_config):
        """SessionEnd hook should be async (fire-and-forget)."""
        entries = hooks_config["hooks"].get("SessionEnd", [])
        for entry in entries:
            for hook in entry.get("hooks", []):
                if "session_end.py" in hook.get("command", ""):
                    assert hook.get("async") is True, (
                        "session_end.py should be async:true"
                    )

    def test_file_tracker_is_async(self, hooks_config):
        """file_tracker.py PostToolUse hook should be async."""
        entries = hooks_config["hooks"].get("PostToolUse", [])
        for entry in entries:
            for hook in entry.get("hooks", []):
                if "file_tracker.py" in hook.get("command", ""):
                    assert hook.get("async") is True, (
                        "file_tracker.py should be async:true"
                    )

    def test_file_size_check_is_async(self, hooks_config):
        """file_size_check.py PostToolUse hook should be async."""
        entries = hooks_config["hooks"].get("PostToolUse", [])
        for entry in entries:
            for hook in entry.get("hooks", []):
                if "file_size_check.py" in hook.get("command", ""):
                    assert hook.get("async") is True, (
                        "file_size_check.py should be async:true"
                    )

    def test_track_files_is_sync(self, hooks_config):
        """track_files.py PostToolUse hook should be synchronous (not async)."""
        entries = hooks_config["hooks"].get("PostToolUse", [])
        for entry in entries:
            for hook in entry.get("hooks", []):
                if "track_files.py" in hook.get("command", ""):
                    assert hook.get("async", False) is not True, (
                        "track_files.py should be synchronous"
                    )


AGENTS_DIR = Path(__file__).parent.parent / "agents"


class TestMatcherPatterns:
    """Validate matcher patterns use correct pipe-separated syntax."""

    def _get_all_matchers(self, hooks_config) -> list[tuple[str, str]]:
        """Extract all (event_type, matcher) pairs from hooks.json."""
        matchers = []
        for event_type, entries in hooks_config["hooks"].items():
            for entry in entries:
                if "matcher" in entry:
                    matchers.append((event_type, entry["matcher"]))
        return matchers

    def test_no_empty_segments_in_matchers(self, hooks_config):
        """Pipe-separated matchers must not have empty segments (e.g., '|foo' or 'foo||bar')."""
        errors = []
        for event_type, matcher in self._get_all_matchers(hooks_config):
            segments = matcher.split("|")
            for i, seg in enumerate(segments):
                if seg.strip() == "":
                    errors.append(
                        f"{event_type}: matcher '{matcher}' has empty segment at position {i}"
                    )
        assert errors == [], f"Invalid matcher patterns:\n" + "\n".join(errors)

    def test_no_leading_or_trailing_pipes(self, hooks_config):
        """Matchers must not start or end with '|'."""
        errors = []
        for event_type, matcher in self._get_all_matchers(hooks_config):
            if matcher.startswith("|"):
                errors.append(f"{event_type}: matcher starts with '|': '{matcher}'")
            if matcher.endswith("|"):
                errors.append(f"{event_type}: matcher ends with '|': '{matcher}'")
        assert errors == [], f"Invalid matcher patterns:\n" + "\n".join(errors)

    def test_subagent_start_covers_all_agent_types(self, hooks_config):
        """SubagentStart matcher must include all PACT agent types from agents/ directory."""
        # Read expected agent names from disk
        expected_agents = set()
        for agent_file in AGENTS_DIR.glob("*.md"):
            # Agent files are named pact-{type}.md — the stem is the agent name
            expected_agents.add(agent_file.stem)

        assert len(expected_agents) > 0, "No agent files found in agents/ directory"

        # Extract the SubagentStart matcher
        subagent_start_entries = hooks_config["hooks"].get("SubagentStart", [])
        matcher_agents = set()
        for entry in subagent_start_entries:
            if "matcher" in entry:
                matcher_agents.update(entry["matcher"].split("|"))

        # Every agent definition should appear in the matcher
        missing = expected_agents - matcher_agents
        assert missing == set(), (
            f"SubagentStart matcher is missing agent types: {sorted(missing)}. "
            f"Matcher has: {sorted(matcher_agents)}. "
            f"Expected from agents/: {sorted(expected_agents)}"
        )


class TestBootstrapGateInvariants:
    """Structural invariants for bootstrap gate hooks."""

    def test_bootstrap_gate_has_no_matcher(self, hooks_config):
        """bootstrap_gate.py PreToolUse entry must have NO matcher (fires for all tools)."""
        pre_tool_entries = hooks_config["hooks"].get("PreToolUse", [])
        for entry in pre_tool_entries:
            for hook in entry.get("hooks", []):
                if "bootstrap_gate.py" in hook.get("command", ""):
                    assert "matcher" not in entry, (
                        "bootstrap_gate.py must NOT have a matcher — "
                        "it must fire for ALL hookable tools to enforce the gate"
                    )


class TestBootstrapBeforeTeachbackGate:
    """#401 Commit #14a — hooks.json PreToolUse ordering invariant.

    bootstrap_gate.py and teachback_gate.py are BOTH matcherless
    PreToolUse hooks. Claude Code fires PreToolUse hooks in registration
    order. The invariant is: bootstrap_gate MUST fire BEFORE
    teachback_gate. Rationale:

    - bootstrap_gate is the gate-of-gates: if bootstrap hasn't run,
      NO teammate work should proceed regardless of teachback state.
    - If teachback_gate fires first and denies (because teachback_submit
      is missing), the deny reason is misleading — the real blocker is
      that bootstrap never ran.
    - Cleaner error surface for teammates: one "you need to bootstrap"
      message, not a confusing "you need to teachback" followed by
      "oh wait, you also need to bootstrap".

    This test SKIPS until teachback_gate.py is registered in hooks.json
    (#401 Commit #7 adds the registration). Once both hooks are
    present, the invariant is load-bearing and must hold.
    """

    def test_bootstrap_gate_precedes_teachback_gate(self, hooks_config):
        pre_tool_entries = hooks_config["hooks"].get("PreToolUse", [])
        bootstrap_index = None
        teachback_index = None
        for i, entry in enumerate(pre_tool_entries):
            for hook in entry.get("hooks", []):
                command = hook.get("command", "")
                if "bootstrap_gate.py" in command and bootstrap_index is None:
                    bootstrap_index = i
                if "teachback_gate.py" in command and teachback_index is None:
                    teachback_index = i

        if teachback_index is None:
            pytest.skip(
                "teachback_gate.py not yet registered in hooks.json — "
                "this ordering invariant activates once #401 Commit #7 lands"
            )

        assert bootstrap_index is not None, (
            "bootstrap_gate.py must be registered in PreToolUse; "
            "teachback_gate.py ordering cannot be checked without it"
        )
        assert bootstrap_index < teachback_index, (
            f"bootstrap_gate.py (PreToolUse entry #{bootstrap_index}) must "
            f"precede teachback_gate.py (PreToolUse entry #{teachback_index}). "
            f"Registration order determines hook-fire order in Claude Code. "
            f"If teachback fires first and denies, the deny reason misleads "
            f"the teammate — bootstrap is the real blocker."
        )

    def test_both_gates_are_matcherless(self, hooks_config):
        """Both gates must be matcherless — they fire for ALL hookable tools.
        A matcher on either would create a gate-bypass on non-matched tools.
        Skipped if teachback_gate.py isn't registered yet.
        """
        pre_tool_entries = hooks_config["hooks"].get("PreToolUse", [])
        teachback_found = False
        for entry in pre_tool_entries:
            for hook in entry.get("hooks", []):
                command = hook.get("command", "")
                if "teachback_gate.py" in command:
                    teachback_found = True
                    assert "matcher" not in entry, (
                        "teachback_gate.py must NOT have a matcher — it must "
                        "fire for ALL hookable tools to enforce the gate"
                    )
        if not teachback_found:
            pytest.skip(
                "teachback_gate.py not yet registered — matcherless invariant "
                "activates once #401 Commit #7 lands"
            )


class TestSessionStartCardinality:
    """Post-#444 SessionStart registration invariant.

    Before #444, SessionStart had two entries: session_init.py and
    compaction_refresh.py. The Secondary-layer consolidation folded the
    post-compaction checkpoint logic into session_init.py's source=compact
    branch and deleted compaction_refresh.py. SessionStart now has exactly
    one entry. A second entry (accidental restoration OR new hook addition)
    could interact with session_init's state-reset logic in subtle ways:
    - Ordering: Claude Code runs SessionStart hooks sequentially; a second
      hook's stdin consumption could starve session_init or vice versa.
    - Marker races: bootstrap_marker cleanup in session_init assumes it is
      the sole writer to additionalContext on source=compact.
    - Context budget: each hook's additionalContext counts against the
      same budget; duplicate PACT ROLE markers or overlapping directives
      would violate the single-source-of-truth invariant.
    Pin the cardinality so a new hook addition is a conscious decision,
    not a silent merge.
    """

    def test_session_start_has_exactly_one_hook(self, hooks_config):
        """SessionStart must have exactly one entry post-#444
        (compaction_refresh.py was consolidated into session_init.py).
        """
        session_start = hooks_config["hooks"].get("SessionStart", [])
        assert len(session_start) == 1, (
            "SessionStart must have exactly one entry post-#444 "
            "(compaction_refresh.py was consolidated into session_init.py). "
            "A second entry indicates either accidental restoration or new "
            "hook addition that may interact with session_init's state-reset "
            "logic."
        )
        assert len(session_start[0]["hooks"]) == 1, (
            "SessionStart's sole entry must contain exactly one hook command."
        )
        assert "session_init.py" in session_start[0]["hooks"][0]["command"], (
            "SessionStart's sole hook must be session_init.py."
        )


class TestTeachbackModeDrift:
    """F4 + M-R4-2 drift guard (cycle-5, round-4 architect sketch): the
    two `_TEACHBACK_MODE` module constants MUST stay locked to the same
    value.

    Context: `teachback_gate.py` (PreToolUse gate) and
    `teachback_check.py` (PostToolUse legacy advisory) each declare
    their own `_TEACHBACK_MODE` constant. Cycle 4 C12 established
    symmetry — both must sit in advisory during Phase 1 and flip to
    blocking in lockstep at Phase 2 — but there was no mechanical
    enforcement. A future refactor that flipped one without the other
    would produce a split-brain at the Phase-2 cutover: the gate would
    block (exit 2) while the legacy advisory warning continued to emit
    teachback_gate_advisory events, poisoning the
    check_teachback_phase2_readiness.py diagnostic's single-mode
    invariant.

    Precedent: mirrors `TestStripPatternDrift` at
    test_teachback_validate.py:1812 — same pattern of locking two
    parallel constants to grep-level equivalence so divergence surfaces
    at pytest time rather than after a partial flip ships.
    """

    def test_teachback_mode_constants_locked_to_same_value(self):
        import teachback_check as tb_check
        import teachback_gate as tb_gate

        assert tb_gate._TEACHBACK_MODE == tb_check._TEACHBACK_MODE, (
            "Mode drift: teachback_gate._TEACHBACK_MODE and "
            "teachback_check._TEACHBACK_MODE MUST ship with the same "
            "value. Flipping one to 'blocking' without the other "
            "creates a split-brain at the Phase-2 cutover — the gate "
            "denies tool calls (exit 2) while the legacy "
            "teachback_check hook keeps emitting "
            "teachback_gate_advisory events alongside the real "
            "teachback_gate_blocked stream. The Phase-2 readiness "
            "diagnostic (scripts/check_teachback_phase2_readiness.py) "
            "assumes a single-mode advisory stream and would mis-count "
            "false positives. Update BOTH constants in the same commit."
        )

    def test_teachback_mode_is_known_vocabulary_value(self):
        from shared import (
            TEACHBACK_MODE_ADVISORY,
            TEACHBACK_MODE_BLOCKING,
        )

        import teachback_check as tb_check
        import teachback_gate as tb_gate

        known = {TEACHBACK_MODE_ADVISORY, TEACHBACK_MODE_BLOCKING}
        assert tb_gate._TEACHBACK_MODE in known, (
            f"teachback_gate._TEACHBACK_MODE='{tb_gate._TEACHBACK_MODE}' "
            f"is not one of the known mode constants {known}. "
            "Use TEACHBACK_MODE_ADVISORY or TEACHBACK_MODE_BLOCKING "
            "from shared — ad-hoc string values break the gate's "
            "mode-check branches."
        )
        assert tb_check._TEACHBACK_MODE in known, (
            f"teachback_check._TEACHBACK_MODE='{tb_check._TEACHBACK_MODE}' "
            f"is not one of the known mode constants {known}."
        )
