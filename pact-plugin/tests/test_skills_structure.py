"""
Tests for skills/ directory structural validation.

Tests cover:
1. All expected skill directories exist
2. Each skill has a SKILL.md file
3. SKILL.md has valid YAML frontmatter with required fields
4. Skill names and descriptions are present
5. SKILL.md body contains substantive content
"""
from pathlib import Path

import pytest

from helpers import parse_frontmatter

SKILLS_DIR = Path(__file__).parent.parent / "skills"

EXPECTED_SKILLS = {
    "orchestration",
    "pact-agent-teams",
    "pact-architecture-patterns",
    "pact-teachback",
    "pact-coding-standards",
    "pact-handoff-harvest",
    "pact-memory",
    "pact-prepare-research",
    "pact-security-patterns",
    "pact-testing-strategies",
    "request-more-context",
    "worktree-cleanup",
    "worktree-setup",
}

# n8n skills are optional but expected
N8N_SKILLS = {
    "n8n-code-javascript",
    "n8n-code-python",
    "n8n-expression-syntax",
    "n8n-mcp-tools-expert",
    "n8n-node-configuration",
    "n8n-validation-expert",
    "n8n-workflow-patterns",
}


@pytest.fixture
def skill_dirs():
    """Get all skill directories (excluding README)."""
    return [d for d in SKILLS_DIR.iterdir() if d.is_dir()]


class TestSkillDirectoriesExist:
    def test_skills_directory_exists(self):
        assert SKILLS_DIR.is_dir()

    def test_core_skills_present(self, skill_dirs):
        names = {d.name for d in skill_dirs}
        for expected in EXPECTED_SKILLS:
            assert expected in names, f"Missing skill: {expected}"


class TestSkillMdFiles:
    def test_each_skill_has_skill_md(self, skill_dirs):
        for d in skill_dirs:
            skill_md = d / "SKILL.md"
            assert skill_md.is_file(), f"{d.name}/ missing SKILL.md"

    def test_skill_md_has_frontmatter(self, skill_dirs):
        for d in skill_dirs:
            skill_md = d / "SKILL.md"
            if not skill_md.is_file():
                continue
            text = skill_md.read_text(encoding="utf-8")
            assert text.startswith("---"), f"{d.name}/SKILL.md missing frontmatter"

    def test_frontmatter_has_name(self, skill_dirs):
        for d in skill_dirs:
            skill_md = d / "SKILL.md"
            if not skill_md.is_file():
                continue
            text = skill_md.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            if fm is None:
                continue
            assert "name" in fm, f"{d.name}/SKILL.md missing name"

    def test_frontmatter_has_description(self, skill_dirs):
        for d in skill_dirs:
            skill_md = d / "SKILL.md"
            if not skill_md.is_file():
                continue
            text = skill_md.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            if fm is None:
                continue
            assert "description" in fm, f"{d.name}/SKILL.md missing description"

    def test_skill_md_has_substantive_body(self, skill_dirs):
        for d in skill_dirs:
            skill_md = d / "SKILL.md"
            if not skill_md.is_file():
                continue
            text = skill_md.read_text(encoding="utf-8")
            _, _, body = text.partition("---")
            _, _, body = body.partition("---")
            assert len(body.strip()) > 100, f"{d.name}/SKILL.md body too short"


class TestPreResponseChannelCheckGate:
    """Both pact-agent-teams and orchestration skills carry the
    Pre-Response Channel Check gate (issue family: instruction-surface-leak)."""

    AGENT_TEAMS_PATH = SKILLS_DIR / "pact-agent-teams" / "SKILL.md"
    ORCHESTRATION_PATH = SKILLS_DIR / "orchestration" / "SKILL.md"

    INVARIANT_HEADER = "Pre-Response Channel Check"
    INVARIANT_USER_ADDRESSEE = "Addressee is **user**"
    INVARIANT_AGENT_ADDRESSEE = "Addressee is **team-lead or teammate**"
    INVARIANT_BOTH_ADDRESSEE = "Addressee is **both**"
    INVARIANT_FORMAT_CUE = "Format-cue hijack"
    INVARIANT_CANDOR = "Candor-question"
    INVARIANT_SENDMESSAGE_REQUIRED = "SendMessage is REQUIRED"
    INVARIANT_PLAIN_TEXT_INVISIBLE = "Plain text is invisible to other agents"
    INVARIANT_CHOOSE_BOTH_FALLBACK = "If you are unsure who the addressee is, choose **both**"
    INVARIANT_CHARTER_LINK = "../../protocols/pact-communication-charter.md#pre-send-self-check"
    LEAD_GRAY_AREA_PHRASE = "Lead-side gray-area trap"
    TEAMMATE_GRAY_AREA_PHRASE = "Teammate-side gray-area trap"

    def test_teammate_side_has_gate(self):
        text = self.AGENT_TEAMS_PATH.read_text(encoding="utf-8")
        for phrase in (
            self.INVARIANT_HEADER,
            self.INVARIANT_USER_ADDRESSEE,
            self.INVARIANT_AGENT_ADDRESSEE,
            self.INVARIANT_BOTH_ADDRESSEE,
            self.INVARIANT_FORMAT_CUE,
            self.INVARIANT_CANDOR,
            self.INVARIANT_SENDMESSAGE_REQUIRED,
            self.INVARIANT_PLAIN_TEXT_INVISIBLE,
            self.INVARIANT_CHOOSE_BOTH_FALLBACK,
            self.INVARIANT_CHARTER_LINK,
        ):
            assert phrase in text, f"pact-agent-teams missing gate phrase: {phrase!r}"

    def test_lead_side_has_gate(self):
        text = self.ORCHESTRATION_PATH.read_text(encoding="utf-8")
        for phrase in (
            self.INVARIANT_HEADER,
            self.INVARIANT_USER_ADDRESSEE,
            self.INVARIANT_AGENT_ADDRESSEE,
            self.INVARIANT_BOTH_ADDRESSEE,
            self.INVARIANT_FORMAT_CUE,
            self.INVARIANT_CANDOR,
            self.INVARIANT_SENDMESSAGE_REQUIRED,
            self.INVARIANT_PLAIN_TEXT_INVISIBLE,
            self.INVARIANT_CHOOSE_BOTH_FALLBACK,
            self.INVARIANT_CHARTER_LINK,
        ):
            assert phrase in text, f"orchestration missing gate phrase: {phrase!r}"

    def test_lead_side_has_gray_area_addendum(self):
        text = self.ORCHESTRATION_PATH.read_text(encoding="utf-8")
        assert self.LEAD_GRAY_AREA_PHRASE in text, (
            "orchestration must include the lead-side gray-area trap addendum"
        )

    def test_teammate_side_does_not_have_lead_addendum(self):
        text = self.AGENT_TEAMS_PATH.read_text(encoding="utf-8")
        assert self.LEAD_GRAY_AREA_PHRASE not in text, (
            "lead-side gray-area trap belongs only in orchestration/SKILL.md"
        )

    def test_teammate_side_has_gray_area_addendum(self):
        text = self.AGENT_TEAMS_PATH.read_text(encoding="utf-8")
        assert self.TEAMMATE_GRAY_AREA_PHRASE in text, (
            "pact-agent-teams must include the teammate-side gray-area trap addendum"
        )

    def test_lead_side_does_not_have_teammate_addendum(self):
        text = self.ORCHESTRATION_PATH.read_text(encoding="utf-8")
        assert self.TEAMMATE_GRAY_AREA_PHRASE not in text, (
            "teammate-side gray-area trap belongs only in pact-agent-teams/SKILL.md"
        )

    def test_teammate_gate_appears_before_on_start(self):
        teammate = self.AGENT_TEAMS_PATH.read_text(encoding="utf-8")
        assert teammate.index("## Pre-Response Channel Check") < teammate.index("## On Start"), \
            "pact-agent-teams: gate must appear before On Start"

    def test_lead_gate_appears_before_communication(self):
        lead = self.ORCHESTRATION_PATH.read_text(encoding="utf-8")
        assert lead.index("### Pre-Response Channel Check") < lead.index("### Communication"), \
            "orchestration: gate must appear before Communication"
