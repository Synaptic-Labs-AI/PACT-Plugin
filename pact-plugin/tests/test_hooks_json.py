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
    "TeammateIdle",
}

# Hooks that MUST be synchronous (blocking) — they affect tool decisions
MUST_BE_SYNC = {
    "team_guard.py",      # Blocks Agent dispatch if no team (#662)
    "worktree_guard.py",  # Blocks edits outside worktree
    "validate_handoff.py",  # Validates agent output
    "agent_handoff_emitter.py",  # Writes agent_handoff journal event on TaskCompleted
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
        """SubagentStart matcher must include all SPAWNABLE PACT agent types
        from agents/ directory.

        pact-orchestrator.md is excluded: it is delivered via the
        `claude --agent PACT:pact-orchestrator` flag for the team-lead
        session ONLY and never spawns through SubagentStart, so the
        peer_inject hook does not need to fire for it.
        """
        # Read expected agent names from disk (spawnable teammates only)
        expected_agents = set()
        for agent_file in AGENTS_DIR.glob("pact-*.md"):
            if agent_file.stem == "pact-orchestrator":
                continue
            expected_agents.add(agent_file.stem)

        assert len(expected_agents) > 0, "No spawnable agent files found in agents/ directory"

        # Extract the SubagentStart matcher
        subagent_start_entries = hooks_config["hooks"].get("SubagentStart", [])
        matcher_agents = set()
        for entry in subagent_start_entries:
            if "matcher" in entry:
                matcher_agents.update(entry["matcher"].split("|"))

        # Every spawnable agent definition should appear in the matcher
        missing = expected_agents - matcher_agents
        assert missing == set(), (
            f"SubagentStart matcher is missing agent types: {sorted(missing)}. "
            f"Matcher has: {sorted(matcher_agents)}. "
            f"Expected from agents/ (excluding pact-orchestrator): "
            f"{sorted(expected_agents)}"
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

    def test_bootstrap_prompt_gate_registered(self, hooks_config):
        """bootstrap_prompt_gate.py must be registered as a UserPromptSubmit
        hook so the bootstrap-required directive is injected on every prompt
        until the marker exists."""
        user_prompt_entries = hooks_config["hooks"].get("UserPromptSubmit", [])
        commands = []
        for entry in user_prompt_entries:
            for hook in entry.get("hooks", []):
                commands.append(hook.get("command", ""))
        assert any(
            "bootstrap_prompt_gate.py" in cmd for cmd in commands
        ), (
            "bootstrap_prompt_gate.py must be registered under "
            "UserPromptSubmit. Commands found: "
            f"{commands}"
        )

    def test_bootstrap_gate_registered(self, hooks_config):
        """bootstrap_gate.py must be registered as a PreToolUse hook so the
        gate fires before any code-modification tool call."""
        pre_tool_entries = hooks_config["hooks"].get("PreToolUse", [])
        commands = []
        for entry in pre_tool_entries:
            for hook in entry.get("hooks", []):
                commands.append(hook.get("command", ""))
        assert any(
            "bootstrap_gate.py" in cmd for cmd in commands
        ), (
            "bootstrap_gate.py must be registered under PreToolUse. "
            f"Commands found: {commands}"
        )

    def test_bootstrap_marker_writer_registered(self, hooks_config):
        """bootstrap_marker_writer.py must be registered as a
        UserPromptSubmit hook so the marker is written once the ritual's
        pre-conditions are observable on disk."""
        user_prompt_entries = hooks_config["hooks"].get("UserPromptSubmit", [])
        commands = []
        for entry in user_prompt_entries:
            for hook in entry.get("hooks", []):
                commands.append(hook.get("command", ""))
        assert any(
            "bootstrap_marker_writer.py" in cmd for cmd in commands
        ), (
            "bootstrap_marker_writer.py must be registered under "
            "UserPromptSubmit. Commands found: "
            f"{commands}"
        )

    def test_bootstrap_marker_writer_registered_before_prompt_gate(
        self, hooks_config,
    ):
        """Registration order = invocation order. The writer must run
        BEFORE bootstrap_prompt_gate so on prompt 2 of a fresh session
        the marker exists by the time the gate evaluates whether to
        emit its bootstrap-required advisory — avoiding a spurious
        same-turn advisory."""
        user_prompt_entries = hooks_config["hooks"].get("UserPromptSubmit", [])
        commands_in_order = []
        for entry in user_prompt_entries:
            for hook in entry.get("hooks", []):
                commands_in_order.append(hook.get("command", ""))

        writer_idx = next(
            (i for i, c in enumerate(commands_in_order)
             if "bootstrap_marker_writer.py" in c),
            None,
        )
        gate_idx = next(
            (i for i, c in enumerate(commands_in_order)
             if "bootstrap_prompt_gate.py" in c),
            None,
        )
        assert writer_idx is not None, (
            "bootstrap_marker_writer.py not registered under UserPromptSubmit"
        )
        assert gate_idx is not None, (
            "bootstrap_prompt_gate.py not registered under UserPromptSubmit"
        )
        assert writer_idx < gate_idx, (
            f"bootstrap_marker_writer.py (idx {writer_idx}) must precede "
            f"bootstrap_prompt_gate.py (idx {gate_idx}) in the "
            f"UserPromptSubmit array. Order: {commands_in_order}"
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


class TestSpawnToolMatchersPost662:
    """#662: matcher='Agent' on team_guard + auditor_reminder; matcher
    'TaskCreate|TaskUpdate' on wake_lifecycle_emitter PRESERVED. The earlier
    matcher='Task' was wrong — the canonical Claude Code platform tool
    name for sub-agent spawning is `Agent`. Cat-2 task-management tools
    (TaskCreate/TaskUpdate/TaskList/...) are unrelated and MUST stay.
    """

    def _all_matcher_pairs(self, hooks_config):
        pairs = []
        for event_type, entries in hooks_config["hooks"].items():
            for entry in entries:
                if "matcher" not in entry:
                    continue
                commands = [
                    h.get("command", "")
                    for h in entry.get("hooks", [])
                ]
                pairs.append((event_type, entry["matcher"], commands))
        return pairs

    def test_team_guard_matcher_is_agent(self, hooks_config):
        """PreToolUse team_guard matcher MUST be 'Agent' (#662 Cat-1)."""
        for event_type, matcher, commands in self._all_matcher_pairs(
            hooks_config
        ):
            if any("team_guard.py" in c for c in commands):
                assert matcher == "Agent", (
                    f"team_guard.py matcher must be 'Agent' (#662); got "
                    f"{matcher!r} on {event_type}"
                )
                return
        pytest.fail("team_guard.py entry not found in hooks.json")

    def test_auditor_reminder_matcher_is_agent(self, hooks_config):
        """PostToolUse auditor_reminder matcher MUST be 'Agent' (#662 Cat-1)."""
        for event_type, matcher, commands in self._all_matcher_pairs(
            hooks_config
        ):
            if any("auditor_reminder.py" in c for c in commands):
                assert matcher == "Agent", (
                    f"auditor_reminder.py matcher must be 'Agent' (#662); "
                    f"got {matcher!r} on {event_type}"
                )
                return
        pytest.fail("auditor_reminder.py entry not found in hooks.json")

    def test_wake_lifecycle_emitter_matcher_unchanged(self, hooks_config):
        """Cat-2 preservation regression-prevention (#662): the
        wake_lifecycle_emitter matcher 'TaskCreate|TaskUpdate' MUST NOT be
        renamed. These are PACT plugin task-system tools, distinct from
        the spawn-tool `Agent`. Renaming them would break lifecycle hooks.
        """
        for event_type, matcher, commands in self._all_matcher_pairs(
            hooks_config
        ):
            if any("wake_lifecycle_emitter.py" in c for c in commands):
                assert matcher == "TaskCreate|TaskUpdate", (
                    f"wake_lifecycle_emitter.py matcher MUST remain "
                    f"'TaskCreate|TaskUpdate' (Cat-2 preservation, #662); "
                    f"got {matcher!r} on {event_type}"
                )
                return
        pytest.fail(
            "wake_lifecycle_emitter.py entry not found in hooks.json"
        )

    def test_no_matcher_is_bare_task_string(self, hooks_config):
        """Regression-prevention: NO matcher should be the bare 'Task'
        string after #662. The valid uses are matcher='Agent' (spawn tool)
        or matcher='TaskCreate|TaskUpdate' (Cat-2 task-management tools).
        """
        offenders = []
        for event_type, matcher, _ in self._all_matcher_pairs(hooks_config):
            if matcher == "Task":
                offenders.append((event_type, matcher))
        assert not offenders, (
            f"No matcher may be the bare 'Task' literal post-#662 — that "
            f"was the wrong rename direction. Offenders: {offenders}"
        )


class TestCat2PreservationBaseline:
    """#662 PREPARE §2 baseline: Cat-2 task-management names
    (TaskCreate/TaskUpdate/TaskList/TaskGet/TaskStop/TaskOutput) appear
    ≥551 times across pact-plugin/. The Cat-1 rename Task→Agent MUST NOT
    have decreased this count. Counts may grow as new gates land.
    """

    _CAT2_NAMES = (
        "TaskCreate", "TaskUpdate", "TaskList",
        "TaskGet", "TaskStop", "TaskOutput",
    )
    _BASELINE = 551

    def test_cat2_total_at_or_above_baseline(self):
        import re
        plugin_dir = Path(__file__).parent.parent
        pattern = re.compile(
            r"\b(?:TaskCreate|TaskUpdate|TaskList|TaskGet|TaskStop|TaskOutput)\b"
        )
        total = 0
        for path in plugin_dir.rglob("*"):
            if not path.is_file():
                continue
            # Skip binary-ish / vendored paths.
            if any(part.startswith(".") for part in path.relative_to(plugin_dir).parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            total += sum(1 for _ in pattern.finditer(text))
        assert total >= self._BASELINE, (
            f"Cat-2 preservation regression (#662): grep total {total} < "
            f"baseline {self._BASELINE}. The Cat-1 rename Task→Agent MUST "
            f"NOT decrease the Cat-2 count. Investigate which file lost "
            f"a TaskCreate/TaskUpdate/TaskList/TaskGet/TaskStop/TaskOutput "
            f"reference."
        )
