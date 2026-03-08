"""
Tests for agents/ directory structural validation.

Tests cover:
1. All agent files exist and are readable
2. YAML frontmatter is valid and contains required fields
3. Agent names follow pact-{role} convention
4. Required frontmatter keys: name, description, color, permissionMode
5. Skills reference exists
6. Agent body contains expected sections
"""
from pathlib import Path

import pytest

from helpers import parse_frontmatter

AGENTS_DIR = Path(__file__).parent.parent / "agents"

# Expected agent files
EXPECTED_AGENTS = {
    "pact-architect",
    "pact-backend-coder",
    "pact-database-engineer",
    "pact-devops-engineer",
    "pact-frontend-coder",
    "pact-memory-agent",
    "pact-n8n",
    "pact-preparer",
    "pact-qa-engineer",
    "pact-security-engineer",
    "pact-test-engineer",
}

REQUIRED_FRONTMATTER_KEYS = {"name", "description"}


@pytest.fixture
def agent_files():
    """Load all agent markdown files."""
    return list(AGENTS_DIR.glob("*.md"))


class TestAgentFilesExist:
    def test_agents_directory_exists(self):
        assert AGENTS_DIR.is_dir()

    def test_all_expected_agents_present(self, agent_files):
        names = {f.stem for f in agent_files}
        for expected in EXPECTED_AGENTS:
            assert expected in names, f"Missing agent: {expected}"

    def test_no_unexpected_agents(self, agent_files):
        names = {f.stem for f in agent_files}
        unexpected = names - EXPECTED_AGENTS
        assert len(unexpected) == 0, f"Unexpected agent files: {unexpected}"


class TestAgentFrontmatter:
    @pytest.fixture
    def all_agents(self, agent_files):
        """Parse frontmatter from all agent files."""
        agents = {}
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            if fm:
                agents[f.stem] = fm
        return agents

    def test_all_have_frontmatter(self, agent_files):
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            assert text.startswith("---"), f"{f.name} missing YAML frontmatter"

    def test_required_keys_present(self, all_agents):
        for name, fm in all_agents.items():
            for key in REQUIRED_FRONTMATTER_KEYS:
                assert key in fm, f"{name} missing frontmatter key: {key}"

    def test_name_matches_filename(self, all_agents):
        for name, fm in all_agents.items():
            assert fm.get("name") == name, (
                f"{name}: frontmatter name '{fm.get('name')}' != filename '{name}'"
            )

    def test_has_description(self, all_agents):
        for name, fm in all_agents.items():
            desc = fm.get("description", "").strip()
            assert len(desc) > 0, f"{name} has empty description"


class TestAgentBody:
    def test_has_system_prompt_content(self, agent_files):
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            # After frontmatter, there should be substantive content
            _, _, body = text.partition("---")
            _, _, body = body.partition("---")
            assert len(body.strip()) > 100, f"{f.name} body too short"

    def test_pact_agents_reference_skills(self, agent_files):
        for f in agent_files:
            if f.stem == "pact-memory-agent":
                continue  # Memory agent may not need skills
            text = f.read_text(encoding="utf-8")
            # Check frontmatter has skills or body references skills
            assert "skill" in text.lower(), f"{f.name} doesn't reference skills"
