"""
/Users/mj/Sites/collab/PACT-prompt/pact-plugin/skills/pact-agent-teams/test_skill_loading.py

Tests for verifying the pact-agent-teams skill file structure and content.
Ensures SKILL.md exists, has valid YAML frontmatter, and includes key sections
for the Agent Teams execution model.
"""

import pytest
from pathlib import Path
import yaml


SKILL_DIR = Path(__file__).parent
SKILL_FILE = SKILL_DIR / "SKILL.md"


@pytest.fixture
def skill_content():
    """Load the skill file content."""
    return SKILL_FILE.read_text()


class TestSkillFileExists:
    """Test that the skill file exists."""

    def test_skill_md_exists(self):
        """SKILL.md file must exist in the skill directory."""
        assert SKILL_FILE.exists(), f"SKILL.md not found at {SKILL_FILE}"

    def test_skill_md_is_file(self):
        """SKILL.md must be a regular file, not a directory."""
        assert SKILL_FILE.is_file(), f"{SKILL_FILE} exists but is not a file"


class TestYamlFrontmatter:
    """Test that YAML frontmatter is valid and has required fields."""

    @pytest.fixture
    def frontmatter(self, skill_content):
        """Extract and parse YAML frontmatter from skill file."""
        if not skill_content.startswith("---"):
            pytest.fail("SKILL.md must start with YAML frontmatter (---)")

        # Find the closing ---
        end_marker = skill_content.find("---", 3)
        if end_marker == -1:
            pytest.fail("SKILL.md has unclosed YAML frontmatter")

        yaml_content = skill_content[3:end_marker].strip()
        return yaml.safe_load(yaml_content)

    def test_has_name_field(self, frontmatter):
        """Frontmatter must include 'name' field."""
        assert "name" in frontmatter, "YAML frontmatter must include 'name' field"
        assert frontmatter["name"], "'name' field must not be empty"

    def test_has_description_field(self, frontmatter):
        """Frontmatter must include 'description' field."""
        assert "description" in frontmatter, "YAML frontmatter must include 'description' field"
        assert frontmatter["description"], "'description' field must not be empty"

    def test_name_matches_directory(self, frontmatter):
        """Skill name should match the directory name."""
        expected_name = SKILL_DIR.name
        assert frontmatter["name"] == expected_name, (
            f"Skill name '{frontmatter['name']}' should match directory name '{expected_name}'"
        )


class TestKeyContentSections:
    """Test that the skill content includes required sections for Agent Teams."""

    def test_has_teammate_identity_section(self, skill_content):
        """Skill must include 'You Are a Teammate' section."""
        assert "## You Are a Teammate" in skill_content, (
            "SKILL.md must include '## You Are a Teammate' section"
        )

    def test_has_on_start_section(self, skill_content):
        """Skill must include 'On Start' section."""
        assert "## On Start" in skill_content, "SKILL.md must include '## On Start' section"

    def test_has_on_blocker_section(self, skill_content):
        """Skill must include 'On Blocker' section."""
        assert "## On Blocker" in skill_content, "SKILL.md must include '## On Blocker' section"

    def test_has_on_completion_section(self, skill_content):
        """Skill must include an 'On Completion' section."""
        assert "## On Completion" in skill_content, (
            "SKILL.md must include '## On Completion' section"
        )

    def test_has_peer_communication_section(self, skill_content):
        """Skill must include 'Peer Communication' section."""
        assert "## Peer Communication" in skill_content, (
            "SKILL.md must include '## Peer Communication' section"
        )

    def test_has_consultant_mode_section(self, skill_content):
        """Skill must include 'Consultant Mode' section."""
        assert "## Consultant Mode" in skill_content, (
            "SKILL.md must include '## Consultant Mode' section"
        )

    def test_has_shutdown_section(self, skill_content):
        """Skill must include 'Shutdown' section."""
        assert "## Shutdown" in skill_content, "SKILL.md must include '## Shutdown' section"

    def test_has_variety_signals_section(self, skill_content):
        """Skill must include 'Variety Signals' section."""
        assert "## Variety Signals" in skill_content, (
            "SKILL.md must include '## Variety Signals' section"
        )

    def test_has_before_completing_section(self, skill_content):
        """Skill must include 'Before Completing' section."""
        assert "## Before Completing" in skill_content, (
            "SKILL.md must include '## Before Completing' section"
        )


class TestAgentTeamsProtocolKeywords:
    """Test that Agent Teams-specific protocol keywords are present."""

    def test_references_send_message(self, skill_content):
        """Skill must reference SendMessage for teammate communication."""
        assert "SendMessage" in skill_content, (
            "SKILL.md must reference 'SendMessage' for Agent Teams communication"
        )

    def test_references_task_list(self, skill_content):
        """Skill must reference TaskList for task discovery."""
        assert "TaskList" in skill_content, (
            "SKILL.md must reference 'TaskList' for task discovery"
        )

    def test_references_task_update(self, skill_content):
        """Skill must reference TaskUpdate for status reporting."""
        assert "TaskUpdate" in skill_content, (
            "SKILL.md must reference 'TaskUpdate' for status reporting"
        )

    def test_references_handoff(self, skill_content):
        """Skill must reference HANDOFF format for completion reporting."""
        assert "HANDOFF" in skill_content, (
            "SKILL.md must reference 'HANDOFF' for completion reporting"
        )

    def test_references_blocker(self, skill_content):
        """Skill must reference BLOCKER for blocker reporting."""
        assert "BLOCKER" in skill_content, (
            "SKILL.md must reference 'BLOCKER' for blocker reporting"
        )

    def test_references_algedonic(self, skill_content):
        """Skill must reference ALGEDONIC for viability threat signaling."""
        assert "ALGEDONIC" in skill_content, (
            "SKILL.md must reference 'ALGEDONIC' for viability threat signaling"
        )

    def test_references_shutdown_request(self, skill_content):
        """Skill must reference shutdown_request for graceful termination."""
        assert "shutdown_request" in skill_content, (
            "SKILL.md must reference 'shutdown_request' for graceful termination"
        )
