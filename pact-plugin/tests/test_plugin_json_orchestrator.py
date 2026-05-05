"""
plugin.json structural invariants for the PACT plugin.

Pins the pinned plugin version, the 13-entry alphabetized `agents` array
(12 teammates + orchestrator), and the absence of the removed bootstrap
commands (`bootstrap.md` and `teammate-bootstrap.md`) which are no longer
registered now that the orchestrator persona is delivered via the
`--agent` flag.
"""
import json
from pathlib import Path

import pytest


PLUGIN_JSON_PATH = (
    Path(__file__).parent.parent / ".claude-plugin" / "plugin.json"
)

EXPECTED_VERSION = json.loads(PLUGIN_JSON_PATH.read_text(encoding="utf-8"))["version"]

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
    "./commands/bootstrap.md",
    "./commands/teammate-bootstrap.md",
}


@pytest.fixture
def plugin_json():
    return json.loads(PLUGIN_JSON_PATH.read_text())


def test_plugin_json_version_is_pinned_to_current_release(plugin_json):
    """Structural existence-check that plugin.json carries a version string.

    EXPECTED_VERSION is sourced dynamically from plugin.json at module
    load, so this assertion is structurally tautological and serves as a
    schema-level guard ("the `version` key exists and equals itself").
    Cross-file version drift across plugin.json/marketplace.json/READMEs
    is caught by sibling test_plugin_version_bump.py.
    """
    assert plugin_json["version"] == EXPECTED_VERSION


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


def test_plugin_json_drops_bootstrap_commands(plugin_json):
    """Bootstrap commands are not registered; orchestrator persona is delivered via --agent."""
    commands = set(plugin_json.get("commands", []))
    leaked = REMOVED_COMMANDS & commands
    assert not leaked, (
        f"plugin.json must not register removed bootstrap commands: {leaked}"
    )
