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
import re
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
    # (#979: team_guard.py removed from the Agent PreToolUse bind — the
    # create-before-dispatch model is obsolete; dispatch_gate ⑧ is the
    # residual fail-closed backstop.)
    "worktree_guard.py",  # Blocks edits outside worktree
    "validate_handoff.py",  # Validates agent output
    "stop_background_gate.py",  # Refuses a turn end over unacknowledged background work
    "agent_handoff_emitter.py",  # Writes agent_handoff journal event on TaskCompleted
    "git_commit_check.py",  # Checks git commit conventions
    "wait_filler_gate.py",  # Denies bare true/sleep filler commands
    "track_files.py",     # Tracks file edits (PostToolUse, non-async)
    "precompact_state_reminder.py",  # Emits state snapshot before compaction
    "postcompact_archive.py",  # Archives compact_summary to disk for session_init + secretary
}

# Hooks that SHOULD be async (non-blocking, fire-and-forget)
SHOULD_BE_ASYNC = {
    "session_end.py",     # Fire-and-forget cleanup
    "file_tracker.py",    # Advisory tracking only
    # async is LOAD-BEARING here, not a performance choice. The platform
    # backgrounds an async hook and reports success before the child exits, so
    # the marker writer cannot block a user prompt whatever it does.
    #
    # THIS ENTRY IS PARTIAL COVER, AND THE EARLIER CLAIM THAT IT TURNS ANY
    # SILENT REMOVAL OF THE FLAG INTO A RED TEST WAS FALSE. `_get_hook_async_status`
    # keys its map by SCRIPT NAME and OVERWRITES on each registration, and
    # PostToolUse is the last event key in hooks.json -- so for a script
    # registered under two events only the LAST registration's flag is
    # examined. Measured: removing `async` from the PostToolUse registration
    # fails as promised, while removing it from the UserPromptSubmit
    # registration ALONE still PASSES. The UserPromptSubmit side is exactly
    # where the session-availability argument lives, and it is unguarded.
    #
    # Widening the shared helper is tracked separately and deliberately NOT
    # done here.
    "pin_marker_writer.py",
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

    def test_track_files_is_sync(self, hooks_config):
        """track_files.py PostToolUse hook should be synchronous (not async)."""
        entries = hooks_config["hooks"].get("PostToolUse", [])
        for entry in entries:
            for hook in entry.get("hooks", []):
                if "track_files.py" in hook.get("command", ""):
                    assert hook.get("async", False) is not True, (
                        "track_files.py should be synchronous"
                    )


class TestTrackFilesPostToolUseMatcher:
    """THE REGISTRATION IS PART OF THE MECHANISM, AND IT IS A SEPARATE FAILURE.

    `track_files.py` carries the pin-staleness marker clear, and that clear
    serves TWO routes. A hand edit of the managed file arrives as `Edit` or
    `Write`. THE ARCHIVE ITSELF arrives as `Bash`, because the archiving
    command runs a script through a shell and emits no edit event.

    So a matcher of `Edit|Write` is not a smaller version of the mechanism. It
    silently drops the route the shipped deny text RECOMMENDS to the user,
    which is the worst of the outcomes available here. The behaviour tests for
    the clear all call the function directly, so every one of them stays green
    while the hook is registered for the wrong events and never fires.
    """

    def _track_files_entries(self, hooks_config):
        """Every PostToolUse group whose commands mention track_files.py."""
        return [
            entry
            for entry in hooks_config["hooks"].get("PostToolUse", [])
            if any(
                "track_files.py" in hook.get("command", "")
                for hook in entry.get("hooks", [])
            )
        ]

    def test_track_files_is_registered_exactly_once_for_post_tool_use(
        self, hooks_config
    ):
        """NON-VACUITY FOR THE ARM BELOW, WHICH READS ONE ENTRY.

        A matcher assertion written as a loop over matched entries passes
        PERFECTLY when the loop finds nothing, and a rename of the command
        string is enough to empty it. This arm makes the population explicit,
        so an empty result is a failure here rather than a silent green there.
        """
        entries = self._track_files_entries(hooks_config)
        assert len(entries) == 1, (
            f"expected exactly 1 PostToolUse group invoking track_files.py "
            f"and found {len(entries)}. A count of 0 means the command string "
            f"moved and the matcher arm below now reads nothing. A count "
            f"above 1 means the registration was split, and the two halves "
            f"can carry different matchers"
        )

    def test_the_track_files_matcher_carries_the_bash_archive_route(
        self, hooks_config
    ):
        """The matcher is pinned as a SET, so a drop and a widening both fire.

        `Bash` is the route the archive takes. `Edit` and `Write` are the hand
        edit. The set is compared rather than the string, because the segment
        order carries no meaning and a reorder is not a defect.
        """
        entries = self._track_files_entries(hooks_config)
        assert entries, "no PostToolUse group invokes track_files.py"

        matcher = entries[0].get("matcher", "")
        segments = set(matcher.split("|"))

        assert segments == {"Edit", "Write", "Bash"}, (
            f"the track_files PostToolUse matcher is {matcher!r}, and it must "
            f"cover Edit, Write and Bash.\n"
            f"  missing: {sorted({'Edit', 'Write', 'Bash'} - segments)}\n"
            f"  unexpected: {sorted(segments - {'Edit', 'Write', 'Bash'})}\n"
            f"WITHOUT `Bash` the pin-staleness marker clear never sees the "
            f"archive run, because the archive writes the file through a "
            f"script and emits no edit event. A user who obeys the refusal "
            f"then stays denied for the rest of the session. Every behaviour "
            f"test for the clear calls the function directly and stays green "
            f"while this happens"
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
        """SubagentStart must SELECT every spawnable PACT agent type in agents/.

        pact-orchestrator.md is excluded: it is delivered through the
        `claude --agent PACT:pact-orchestrator` flag for the team-lead
        session ONLY and does not spawn through SubagentStart, so the
        peer_inject hook does not have to fire for it.

        THIS ASSERTS SELECTION, NOT ENUMERATION, and the difference is the
        point. The former shape of this test read the matcher string and
        demanded each agent type appear in it. An enumeration cannot reach an
        in-process teammate: the platform puts the TEAMMATE NAME in the field
        the matcher reads, and a name is user-chosen. A registration with NO
        matcher selects every frame, which covers the same agent types and the
        teammate frames too, so it must satisfy this test rather than fail it.
        The teammate-frame half is armed separately in
        test_subagent_start_selects_teammate_frames.py.
        """
        # Read expected agent names from disk (spawnable teammates only)
        expected_agents = set()
        for agent_file in AGENTS_DIR.glob("pact-*.md"):
            if agent_file.stem == "pact-orchestrator":
                continue
            expected_agents.add(agent_file.stem)

        assert len(expected_agents) > 0, "No spawnable agent files found in agents/ directory"

        subagent_start_entries = hooks_config["hooks"].get("SubagentStart", [])
        assert subagent_start_entries, "SubagentStart has no registration at all"

        def _selected(agent_type):
            """An entry with no matcher selects every frame; an entry with one
            selects a frame when the pattern matches the whole agent type."""
            for entry in subagent_start_entries:
                pattern = entry.get("matcher")
                if pattern is None or re.fullmatch(pattern, agent_type):
                    return True
            return False

        missing = {a for a in expected_agents if not _selected(a)}
        assert missing == set(), (
            f"SubagentStart does not select agent types: {sorted(missing)}. "
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
    """SessionStart registration invariant.

    Before #444, SessionStart had two entries: session_init.py and
    compaction_refresh.py. The Secondary-layer consolidation folded the
    post-compaction checkpoint logic into session_init.py's source=compact
    branch and deleted compaction_refresh.py — leaving session_init.py the
    sole entry.

    The #903 deferred missed-wake alarm then ADDED missed_wake_scan.py as a
    SessionStart recovery backstop (it scans for cross-session stale
    awaiting_lead_completion waits at session start). SessionStart now has
    exactly TWO entries, in order: session_init.py then missed_wake_scan.py.
    The original cardinality concerns are satisfied because missed_wake_scan.py
    is journal-only:
    - Ordering / stdin: Claude Code runs SessionStart hooks sequentially, but
      each is a SEPARATE process with its own stdin copy — no starvation
      between session_init and missed_wake_scan. session_init runs first.
    - Marker / additionalContext races: missed_wake_scan emits NO
      additionalContext (it returns suppressOutput and writes only a journal
      event), so it does not touch session_init's bootstrap_marker /
      additionalContext single-source-of-truth on source=compact.
    - Context budget: missed_wake_scan contributes no additionalContext, so the
      budget invariant is preserved.
    Pin the exact set + order so any FURTHER hook addition is a conscious
    decision, not a silent merge.
    """

    def test_session_start_registration(self, hooks_config):
        """SessionStart must have exactly two entries, in order:
        session_init.py (state-reset) then missed_wake_scan.py
        (the #903 missed-wake recovery backstop).
        """
        session_start = hooks_config["hooks"].get("SessionStart", [])
        assert len(session_start) == 2, (
            "SessionStart must have exactly two entries: session_init.py and "
            "missed_wake_scan.py (the #903 missed-wake recovery backstop). A "
            "different count indicates accidental restoration or a new hook "
            "addition that may interact with session_init's state-reset logic."
        )
        for entry in session_start:
            assert len(entry["hooks"]) == 1, (
                "Each SessionStart entry must contain exactly one hook command."
            )
        assert "session_init.py" in session_start[0]["hooks"][0]["command"], (
            "SessionStart's first hook must be session_init.py "
            "(runs before the #903 backstop)."
        )
        assert "missed_wake_scan.py" in session_start[1]["hooks"][0]["command"], (
            "SessionStart's second hook must be missed_wake_scan.py "
            "(the #903 missed-wake recovery backstop)."
        )


class TestMissedWakeSurfacerRegistration:
    """#903 B1 remediation: the missed-wake SURFACER (missed_wake_scan.py) binds
    UserPromptSubmit (turn-start additionalContext) + SessionStart (cross-session
    recovery) — the record-only Stop carrier was DROPPED (Stop fired at turn-END
    and could only suppressOutput, so it never surfaced). Pins the post-B1
    registration so a regression to the record-only Stop shape is caught: Stop
    now binds only the background-work turn-end gate, never the surfacer."""

    def test_missed_wake_scan_on_user_prompt_submit(self, hooks_config):
        commands = [
            c["command"]
            for entry in hooks_config["hooks"].get("UserPromptSubmit", [])
            for c in entry["hooks"]
        ]
        assert any("missed_wake_scan.py" in c for c in commands), (
            "missed_wake_scan.py must be registered under UserPromptSubmit "
            "(the #903 B1 turn-start surfacer)."
        )

    def test_missed_wake_scan_on_session_start(self, hooks_config):
        commands = [
            c["command"]
            for entry in hooks_config["hooks"].get("SessionStart", [])
            for c in entry["hooks"]
        ]
        assert any("missed_wake_scan.py" in c for c in commands), (
            "missed_wake_scan.py must be registered under SessionStart "
            "(the #903 cross-session recovery backstop)."
        )

    def test_stop_binds_only_stop_background_gate(self, hooks_config):
        commands = [
            c["command"]
            for entry in hooks_config["hooks"].get("Stop", [])
            for c in entry["hooks"]
        ]
        assert len(commands) == 1 and commands[0].endswith(
            '/hooks/stop_background_gate.py"'
        ), (
            "Stop must bind only stop_background_gate.py — the #903 record-only "
            "Stop carrier stays dropped (UserPromptSubmit + SessionStart carry the "
            f"surfacer); actual: {commands}"
        )


class TestSpawnToolMatchersPost662:
    """#662: matcher='Agent' on the spawn-gate hooks; the
    'TaskCreate|TaskUpdate' Cat-2 matcher (now on task_lifecycle_gate) is
    PRESERVED. The earlier matcher='Task' was wrong — the canonical Claude
    Code platform tool name for sub-agent spawning is `Agent`. Cat-2
    task-management tools (TaskCreate/TaskUpdate/TaskList/...) are unrelated
    and MUST stay. (#979: team_guard was removed from the Agent bind; the
    surviving Agent-matcher spawn gate is dispatch_gate.)
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

    def test_team_guard_unbound(self, hooks_config):
        """#979: team_guard.py MUST NOT be bound to any hooks.json event — the
        create-before-dispatch model is obsolete (its DENY could block the
        secretary in the bootstrap window and its remediation called the
        removed TeamCreate). dispatch_gate ⑧ is the residual fail-closed
        backstop on the Agent PreToolUse bind.
        """
        for event_type, matcher, commands in self._all_matcher_pairs(
            hooks_config
        ):
            assert not any("team_guard.py" in c for c in commands), (
                f"team_guard.py must be UNBOUND (#979); found on "
                f"{event_type} matcher={matcher!r}"
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
