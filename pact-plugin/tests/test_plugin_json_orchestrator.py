"""
plugin.json registers pact-orchestrator agent (13-entry list under v4.0.0).

C1 adds `./agents/pact-orchestrator.md` to the `agents` array; C9 drops
`./commands/bootstrap.md` and `./commands/teammate-bootstrap.md`; C11 bumps
the version to 4.0.0. These tests assert all three are landed.

Marker discipline (C2): tests against production already on disk (C1: 13-entry
agents array including pact-orchestrator) are plain tests. Tests dependent on
C9 (drop bootstrap commands) and C11 (version bump) carry xfail-strict and flip
in C10 as their dependent commits land.
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
    "./commands/bootstrap.md",
    "./commands/teammate-bootstrap.md",
}


@pytest.fixture
def plugin_json():
    return json.loads(PLUGIN_JSON_PATH.read_text())


@pytest.mark.xfail(strict=True, reason="v4.0.0 — flips in C10")
def test_plugin_json_version_is_4_0_0(plugin_json):
    assert plugin_json["version"] == "4.0.0", (
        f"plugin.json version should be 4.0.0 (BREAKING), got {plugin_json['version']}"
    )


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


@pytest.mark.xfail(strict=True, reason="v4.0.0 — flips in C10")
def test_plugin_json_drops_bootstrap_commands(plugin_json):
    """C9 removes bootstrap commands — replaced by --agent flag."""
    commands = set(plugin_json.get("commands", []))
    leaked = REMOVED_COMMANDS & commands
    assert not leaked, (
        f"plugin.json must not register removed bootstrap commands: {leaked}"
    )
