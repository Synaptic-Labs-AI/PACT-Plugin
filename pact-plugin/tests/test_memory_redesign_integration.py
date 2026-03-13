"""
Cross-cutting integration tests for the pact-memory redesign.

Tests cover:
1. PACT_WORK_AGENTS list consistency between memory_retrieve.py and handoff_gate.py
2. Deleted files confirmed absent: memory_enforce.py, pact-memory-agent.md, pact-memory-lifecycle.md
3. No remaining references to "memory_used" in Python files (field was renamed)
4. No remaining references to "pact-memory-agent" in active markdown files
5. hooks.json registration of memory_retrieve.py as SubagentStart hook
6. hooks.json matcher pattern matches PACT_WORK_AGENTS list
"""
import json
import re
import sys
from pathlib import Path

import pytest

# Project root and plugin root
PLUGIN_ROOT = Path(__file__).parent.parent
PROJECT_ROOT = PLUGIN_ROOT.parent
HOOKS_DIR = PLUGIN_ROOT / "hooks"

sys.path.insert(0, str(HOOKS_DIR))


class TestPactWorkAgentsConsistency:
    """PACT_WORK_AGENTS must be identical in memory_retrieve.py and handoff_gate.py."""

    def test_lists_are_identical(self):
        from memory_retrieve import PACT_WORK_AGENTS as retrieve_agents
        from handoff_gate import PACT_WORK_AGENTS as gate_agents

        assert retrieve_agents == gate_agents, (
            f"PACT_WORK_AGENTS mismatch:\n"
            f"memory_retrieve.py: {retrieve_agents}\n"
            f"handoff_gate.py:    {gate_agents}"
        )

    def test_lists_have_10_agents(self):
        from memory_retrieve import PACT_WORK_AGENTS
        assert len(PACT_WORK_AGENTS) == 10

    def test_no_memory_agent_in_either_list(self):
        from memory_retrieve import PACT_WORK_AGENTS as retrieve_agents
        from handoff_gate import PACT_WORK_AGENTS as gate_agents

        assert "pact-memory-agent" not in retrieve_agents
        assert "pact-memory-agent" not in gate_agents

    def test_is_pact_work_agent_functions_agree(self):
        """Both modules' is_pact_work_agent() should agree on all agent types."""
        from memory_retrieve import is_pact_work_agent as retrieve_check
        from handoff_gate import is_pact_work_agent as gate_check

        test_names = [
            "pact-preparer",
            "pact-architect",
            "pact-backend-coder",
            "pact-frontend-coder",
            "pact-database-engineer",
            "pact-devops-engineer",
            "pact-n8n",
            "pact-test-engineer",
            "pact-security-engineer",
            "pact-qa-engineer",
            "pact-memory-agent",
            "random-agent",
            "",
            "pact-backend-coder-auth-scope",
        ]

        for name in test_names:
            assert retrieve_check(name) == gate_check(name), (
                f"is_pact_work_agent disagrees for '{name}': "
                f"retrieve={retrieve_check(name)}, gate={gate_check(name)}"
            )


class TestDeletedFilesAbsent:
    """Files deleted as part of the redesign should not exist."""

    def test_memory_enforce_deleted(self):
        assert not (HOOKS_DIR / "memory_enforce.py").exists(), (
            "memory_enforce.py should have been deleted"
        )

    def test_pact_memory_agent_md_deleted(self):
        assert not (PLUGIN_ROOT / "agents" / "pact-memory-agent.md").exists(), (
            "pact-memory-agent.md should have been deleted"
        )

    def test_pact_memory_lifecycle_md_deleted(self):
        assert not (PLUGIN_ROOT / "protocols" / "pact-memory-lifecycle.md").exists(), (
            "pact-memory-lifecycle.md should have been deleted"
        )


class TestNoRemainingMemoryUsedReferences:
    """The field 'memory_used' was renamed to 'memory_saved'.
    No Python files should reference the old name."""

    def test_no_memory_used_in_python_files(self):
        python_files = list(PLUGIN_ROOT.rglob("*.py"))
        violations = []
        # Skip this test file itself (it references the old name in its own docstring/comments)
        this_file = Path(__file__).resolve()
        for py_file in python_files:
            py_resolved = py_file.resolve()
            # Skip __pycache__, .pyc, and this test file
            if "__pycache__" in str(py_file) or py_resolved == this_file:
                continue
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            if re.search(r'\bmemory_used\b', content):
                lines = content.split('\n')
                for i, line in enumerate(lines, 1):
                    if re.search(r'\bmemory_used\b', line):
                        # Skip lines that document the rename
                        if "renamed" in line.lower() or "\u2192" in line or "memory_saved" in line:
                            continue
                        violations.append(f"{py_file.relative_to(PLUGIN_ROOT)}:{i}: {line.strip()}")

        assert violations == [], (
            "Found references to old 'memory_used' field:\n" +
            "\n".join(violations)
        )


class TestNoRemainingPactMemoryAgentReferences:
    """No active markdown instruction files should reference 'pact-memory-agent'."""

    def test_no_pact_memory_agent_in_agent_files(self):
        agent_dir = PLUGIN_ROOT / "agents"
        if not agent_dir.exists():
            pytest.skip("agents directory not found")

        violations = []
        for md_file in agent_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8", errors="ignore")
            if "pact-memory-agent" in content:
                violations.append(str(md_file.relative_to(PLUGIN_ROOT)))

        assert violations == [], (
            f"Found 'pact-memory-agent' references in agent files:\n" +
            "\n".join(violations)
        )

    def test_no_pact_memory_agent_in_command_files(self):
        commands_dir = PLUGIN_ROOT / "commands"
        if not commands_dir.exists():
            pytest.skip("commands directory not found")

        violations = []
        for md_file in commands_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8", errors="ignore")
            if "pact-memory-agent" in content:
                violations.append(str(md_file.relative_to(PLUGIN_ROOT)))

        assert violations == [], (
            f"Found 'pact-memory-agent' references in command files:\n" +
            "\n".join(violations)
        )

    def test_no_pact_memory_agent_in_claude_md(self):
        claude_md = PLUGIN_ROOT / "CLAUDE.md"
        if not claude_md.exists():
            pytest.skip("CLAUDE.md not found")

        content = claude_md.read_text(encoding="utf-8", errors="ignore")
        assert "pact-memory-agent" not in content, (
            "CLAUDE.md should not reference pact-memory-agent"
        )


class TestHooksJsonRegistration:
    """hooks.json should register memory_retrieve.py as a SubagentStart hook."""

    @pytest.fixture
    def hooks_config(self):
        hooks_file = HOOKS_DIR / "hooks.json"
        assert hooks_file.exists(), "hooks.json not found"
        return json.loads(hooks_file.read_text(encoding="utf-8"))

    def test_memory_retrieve_registered_as_subagent_start(self, hooks_config):
        subagent_start = hooks_config.get("hooks", {}).get("SubagentStart", [])
        assert subagent_start, "No SubagentStart hooks found"

        memory_retrieve_found = False
        for entry in subagent_start:
            hooks = entry.get("hooks", [])
            for hook in hooks:
                cmd = hook.get("command", "")
                if "memory_retrieve.py" in cmd:
                    memory_retrieve_found = True
                    break

        assert memory_retrieve_found, (
            "memory_retrieve.py not registered as SubagentStart hook"
        )

    def test_memory_retrieve_matcher_matches_all_work_agents(self, hooks_config):
        from memory_retrieve import PACT_WORK_AGENTS

        subagent_start = hooks_config.get("hooks", {}).get("SubagentStart", [])

        matcher_str = None
        for entry in subagent_start:
            hooks = entry.get("hooks", [])
            for hook in hooks:
                if "memory_retrieve.py" in hook.get("command", ""):
                    matcher_str = entry.get("matcher", "")
                    break

        assert matcher_str is not None, "Could not find matcher for memory_retrieve.py"

        # Parse matcher — it's a pipe-delimited list of agent types
        matcher_agents = set(matcher_str.split("|"))
        expected_agents = set(PACT_WORK_AGENTS)

        assert matcher_agents == expected_agents, (
            f"hooks.json matcher mismatch:\n"
            f"Matcher agents: {sorted(matcher_agents)}\n"
            f"PACT_WORK_AGENTS: {sorted(expected_agents)}\n"
            f"Missing from matcher: {expected_agents - matcher_agents}\n"
            f"Extra in matcher: {matcher_agents - expected_agents}"
        )

    def test_memory_enforce_not_registered(self, hooks_config):
        """memory_enforce.py should not be registered in any hook."""
        hooks_json_str = json.dumps(hooks_config)
        assert "memory_enforce" not in hooks_json_str, (
            "memory_enforce.py should not be registered in hooks.json"
        )


class TestNoMemoryLifecycleReferences:
    """No active files should reference the deleted memory lifecycle protocol."""

    def test_no_memory_lifecycle_in_agent_files(self):
        agent_dir = PLUGIN_ROOT / "agents"
        if not agent_dir.exists():
            pytest.skip("agents directory not found")

        violations = []
        for md_file in agent_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8", errors="ignore")
            if "pact-memory-lifecycle" in content.lower() or "MEMORY LIFECYCLE" in content:
                violations.append(str(md_file.relative_to(PLUGIN_ROOT)))

        assert violations == [], (
            f"Found memory lifecycle references in agent files:\n" +
            "\n".join(violations)
        )

    def test_no_memory_lifecycle_in_command_files(self):
        commands_dir = PLUGIN_ROOT / "commands"
        if not commands_dir.exists():
            pytest.skip("commands directory not found")

        violations = []
        for md_file in commands_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8", errors="ignore")
            if "pact-memory-lifecycle" in content.lower() or "MEMORY LIFECYCLE" in content:
                violations.append(str(md_file.relative_to(PLUGIN_ROOT)))

        assert violations == [], (
            f"Found memory lifecycle references in command files:\n" +
            "\n".join(violations)
        )
