"""
plugin.json structural invariants for the PACT plugin.

Pins the 13-entry alphabetized `agents` array (12 teammates + orchestrator)
and the absence of the removed `teammate-bootstrap.md` command (replaced by
teammate frontmatter delivery). The session-start ritual command
`bootstrap.md` IS registered: it is the per-session ritual surface invoked
by the orchestrator persona's §2 Session-Start Ritual via
`Skill("PACT:bootstrap")`. Cross-file version-consistency is owned by
sibling test_plugin_version_bump.py.
"""
import json
from pathlib import Path

import pytest


PLUGIN_JSON_PATH = (
    Path(__file__).parent.parent / ".claude-plugin" / "plugin.json"
)

EXPECTED_AGENTS = {
    "./agents/pact-architect.md",
    "./agents/pact-auditor.md",
    "./agents/pact-backend-coder.md",
    "./agents/pact-database-engineer.md",
    "./agents/pact-devops-engineer.md",
    "./agents/pact-frontend-coder.md",
    "./agents/pact-n8n.md",
    "./agents/pact-orchestrator.md",
    "./agents/pact-preparer.md",
    "./agents/pact-qa-engineer.md",
    "./agents/pact-secretary.md",
    "./agents/pact-security-engineer.md",
    "./agents/pact-test-engineer.md",
}

REMOVED_COMMANDS = {
    "./commands/teammate-bootstrap.md",
}


@pytest.fixture
def plugin_json():
    return json.loads(PLUGIN_JSON_PATH.read_text())


def test_plugin_json_has_13_agents(plugin_json):
    agents = plugin_json.get("agents", [])
    assert len(agents) == 13, (
        f"plugin.json `agents` must have 13 entries (12 teammates + orchestrator), "
        f"got {len(agents)}"
    )


def test_plugin_json_registers_pact_orchestrator(plugin_json):
    agents = set(plugin_json.get("agents", []))
    assert "./agents/pact-orchestrator.md" in agents, (
        "plugin.json must register ./agents/pact-orchestrator.md in `agents` array"
    )


def test_plugin_json_agents_match_expected_set(plugin_json):
    agents = set(plugin_json.get("agents", []))
    missing = EXPECTED_AGENTS - agents
    extra = agents - EXPECTED_AGENTS
    assert not missing, f"plugin.json missing agents: {missing}"
    assert not extra, f"plugin.json has unexpected agents: {extra}"


def test_plugin_json_agents_alphabetized(plugin_json):
    """13-entry alphabetized list: stable order eases diff review under future bumps."""
    agents = plugin_json.get("agents", [])
    assert agents == sorted(agents), (
        "plugin.json `agents` array must be alphabetized; "
        f"got order: {agents}"
    )


def test_plugin_json_drops_removed_commands(plugin_json):
    """Removed commands stay deregistered (teammate-bootstrap.md absorbed into
    teammate frontmatter); the session-start ritual command bootstrap.md IS
    registered (separate concern — covered by sibling test below)."""
    commands = set(plugin_json.get("commands", []))
    leaked = REMOVED_COMMANDS & commands
    assert not leaked, (
        f"plugin.json must not register removed commands: {leaked}"
    )


def test_plugin_json_registers_bootstrap_command(plugin_json):
    """The session-start ritual command must be registered so the orchestrator
    persona's `Skill("PACT:bootstrap")` invocation resolves."""
    commands = set(plugin_json.get("commands", []))
    assert "./commands/bootstrap.md" in commands, (
        "plugin.json must register ./commands/bootstrap.md in `commands` array"
    )
