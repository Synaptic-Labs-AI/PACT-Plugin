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
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "TaskCompleted",
    "TeammateIdle",
}

# Hooks that MUST be synchronous (blocking) — they affect tool decisions
MUST_BE_SYNC = {
    "team_guard.py",      # Blocks Task dispatch if no team
    "worktree_guard.py",  # Blocks edits outside worktree
    "validate_handoff.py",  # Validates agent output
    "handoff_gate.py",    # Blocks task completion without metadata
    "peer_inject.py",     # Injects peer context on agent start
    "git_commit_check.py",  # Checks git commit conventions
    "track_files.py",     # Tracks file edits (PostToolUse, non-async)
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
