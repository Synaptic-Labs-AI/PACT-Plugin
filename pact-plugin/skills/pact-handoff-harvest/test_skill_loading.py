"""
pact-plugin/skills/pact-handoff-harvest/test_skill_loading.py

Tests for verifying the pact-handoff-harvest skill file structure and content.
Ensures SKILL.md exists, has valid YAML frontmatter, includes all three workflow
variants, supporting sections, keyword routing, and critical protocol references.
"""

import pytest
from pathlib import Path
import yaml


SKILL_DIR = Path(__file__).parent
SKILL_FILE = SKILL_DIR / "SKILL.md"
AGENTS_DIR = SKILL_DIR.parent.parent / "agents"
SECRETARY_FILE = AGENTS_DIR / "pact-secretary.md"


@pytest.fixture
def skill_content():
    """Load the skill file content."""
    return SKILL_FILE.read_text()


@pytest.fixture
def secretary_content():
    """Load the secretary agent definition."""
    return SECRETARY_FILE.read_text()


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


class TestWorkflowSections:
    """Test that all three workflow variants are present."""

    def test_has_standard_harvest(self, skill_content):
        """Skill must include Standard Harvest Workflow section."""
        assert "## Standard Harvest Workflow" in skill_content

    def test_has_incremental_harvest(self, skill_content):
        """Skill must include Incremental Harvest Workflow section."""
        assert "## Incremental Harvest Workflow" in skill_content

    def test_has_consolidation_harvest(self, skill_content):
        """Skill must include Consolidation Harvest Workflow section."""
        assert "## Consolidation Harvest Workflow" in skill_content

    def test_section_ordering(self, skill_content):
        """Workflows must be ordered: Standard → Incremental → Consolidation."""
        standard_pos = skill_content.index("## Standard Harvest Workflow")
        incremental_pos = skill_content.index("## Incremental Harvest Workflow")
        consolidation_pos = skill_content.index("## Consolidation Harvest Workflow")
        assert standard_pos < incremental_pos < consolidation_pos, (
            "Workflow ordering must be Standard → Incremental → Consolidation"
        )


class TestSupportingSections:
    """Test that supporting sections are present."""

    def test_has_knowledge_extraction_guide(self, skill_content):
        """Skill must include Knowledge Extraction Guide section."""
        assert "## Knowledge Extraction Guide" in skill_content

    def test_has_investigation_protocol(self, skill_content):
        """Skill must include Investigation Protocol section."""
        assert "## Investigation Protocol" in skill_content

    def test_has_ad_hoc_saves(self, skill_content):
        """Skill must include Ad-Hoc Save Requests section."""
        assert "## Ad-Hoc Save Requests" in skill_content

    def test_has_orphaned_breadcrumb_recovery(self, skill_content):
        """Skill must include Orphaned Breadcrumb Recovery section."""
        assert "## Orphaned Breadcrumb Recovery" in skill_content


class TestKeywordRouting:
    """Test that keyword routing covers all three workflow variants."""

    def test_routes_harvest_keyword(self, skill_content):
        """Routing must map 'harvest' to Standard Harvest."""
        assert "harvest" in skill_content.lower()
        assert "Standard Harvest" in skill_content

    def test_routes_incremental_keyword(self, skill_content):
        """Routing must map 'incremental' to Incremental Harvest."""
        assert '"incremental"' in skill_content

    def test_routes_consolidation_keyword(self, skill_content):
        """Routing must map 'consolidation' to Consolidation Harvest."""
        assert '"consolidation"' in skill_content


class TestSecretaryIntegration:
    """Test that the secretary agent definition loads this skill."""

    def test_secretary_frontmatter_includes_skill(self, secretary_content):
        """Secretary frontmatter skills list must include pact-handoff-harvest."""
        assert "pact-handoff-harvest" in secretary_content, (
            "pact-secretary.md must list pact-handoff-harvest in frontmatter skills"
        )


class TestCriticalProtocolReferences:
    """Test that critical protocol references are present in the skill."""

    def test_has_calibration_record_reference(self, skill_content):
        """Skill must reference CalibrationRecord for variety scoring feedback."""
        assert "CalibrationRecord" in skill_content

    def test_has_breadcrumb_cleanup_guidance(self, skill_content):
        """Skill must include Path.unlink breadcrumb cleanup guidance."""
        assert "unlink" in skill_content, (
            "Skill must reference Path.unlink for breadcrumb cleanup"
        )

    def test_has_processed_tasks_tracking(self, skill_content):
        """Skill must reference processed task tracking for dedup."""
        assert "processed_tasks" in skill_content or "session_processed_tasks" in skill_content, (
            "Skill must reference processed task tracking for incremental dedup"
        )
