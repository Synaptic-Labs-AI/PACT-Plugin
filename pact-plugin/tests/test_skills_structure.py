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
    """Both pact-agent-teams (teammate-side) and pact-orchestrator agent body
    (lead-side) carry the Pre-Response Channel Check gate (issue family:
    instruction-surface-leak).

    Under v4.0.0 the lead-side gate is in agents/pact-orchestrator.md
    (the orchestrator persona delivered via --agent flag); the teammate-side
    gate is in pact-agent-teams/SKILL.md.
    """

    AGENT_TEAMS_PATH = SKILLS_DIR / "pact-agent-teams" / "SKILL.md"
    ORCHESTRATION_PATH = (
        SKILLS_DIR.parent / "agents" / "pact-orchestrator.md"
    )

    INVARIANT_HEADER = "Pre-Response Channel Check"
    INVARIANT_USER_ADDRESSEE = "Addressee is **user**"
    INVARIANT_AGENT_ADDRESSEE = "Addressee is **team-lead or teammate**"
    INVARIANT_BOTH_ADDRESSEE = "Addressee is **both**"
    INVARIANT_FORMAT_CUE = "Format-cue hijack"
    INVARIANT_CANDOR = "Candor-question"
    INVARIANT_SENDMESSAGE_REQUIRED = "SendMessage is REQUIRED"
    INVARIANT_PLAIN_TEXT_INVISIBLE = "Plain text is invisible to other agents"
    INVARIANT_CHOOSE_BOTH_FALLBACK = "If you are unsure who the addressee is, choose **both**"
    # Teammate-side (pact-agent-teams/SKILL.md) is two levels deep; lead-side
    # (agents/pact-orchestrator.md) is one level deep — pin separate link
    # paths per surface.
    TEAMMATE_CHARTER_LINK = "../../protocols/pact-communication-charter.md#pre-send-self-check"
    LEAD_CHARTER_LINK = "../protocols/pact-communication-charter.md"
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
            self.TEAMMATE_CHARTER_LINK,
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
            self.LEAD_CHARTER_LINK,
        ):
            assert phrase in text, f"orchestrator agent body missing gate phrase: {phrase!r}"

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
        # Architect's §1-§13 numbered hierarchy: Pre-Response Channel Check
        # is §1; Communication is §7 (post #628 §2 Session-Start Ritual
        # insertion + §2-§12 → §3-§13 renumber). The pre-response gate
        # must precede the Communication section so the agent reads the
        # channel-check rules before reading the SendMessage discipline.
        assert lead.index("## 1. Pre-Response Channel Check") < lead.index("## 7. Communication"), \
            "pact-orchestrator agent body: Pre-Response Channel Check (§1) must appear before Communication (§7)"


_ALL_SKILL_FILES = sorted(SKILLS_DIR.glob("*/SKILL.md"))


class TestNoFirstActionFossilInSkillBodies:
    """Negative-invariant fossilization guard: skill bodies must not contain
    the v3.x FIRST-ACTION + Skill("PACT:teammate-bootstrap") + peer_inject
    delivery-mechanism prose. The mechanism and the bootstrap command were
    deleted; any surviving prose tells spawned teammates to look for content
    delivered by absent machinery.

    Symmetric to TestNoFirstActionFossilInConsumerCommands in
    test_commands_structure.py — that guard scans `commands/`; this one
    scans `skills/**/SKILL.md`. Skill bodies auto-load into every teammate
    spawn via `skills:` frontmatter preload, so a stale-mechanism reference
    here misroutes every teammate.
    """

    FORBIDDEN_FOSSIL_PATTERNS = (
        "YOUR FIRST ACTION (YOU MUST DO THIS IMMEDIATELY)",
        'Skill("PACT:teammate-bootstrap")',
        "Skill('PACT:teammate-bootstrap')",
        "/PACT:teammate-bootstrap",
        "/PACT:bootstrap",
        "peer_inject",
    )

    @pytest.mark.parametrize(
        "skill_path",
        _ALL_SKILL_FILES,
        ids=[p.parent.name for p in _ALL_SKILL_FILES],
    )
    def test_skill_body_has_no_v3x_delivery_fossil(self, skill_path):
        text = skill_path.read_text(encoding="utf-8")
        offenders = [p for p in self.FORBIDDEN_FOSSIL_PATTERNS if p in text]
        assert not offenders, (
            f"{skill_path.relative_to(SKILLS_DIR.parent)} contains v3.x "
            f"delivery-mechanism fossil(s): {offenders}. The bootstrap "
            f"command, peer_inject hook, and FIRST-ACTION prelude were all "
            f"removed; surviving prose describes machinery that no longer "
            f"exists and contradicts the current `skills:` frontmatter "
            f"preload model. Replace with current-state instructions."
        )
