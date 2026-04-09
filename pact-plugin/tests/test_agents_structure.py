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
    "pact-auditor",
    "pact-backend-coder",
    "pact-database-engineer",
    "pact-devops-engineer",
    "pact-frontend-coder",
    "pact-secretary",
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
            text = f.read_text(encoding="utf-8")
            # Check frontmatter has skills or body references skills
            assert "skill" in text.lower(), f"{f.name} doesn't reference skills"


class TestLazyLoadedAgentTeams:
    """
    Regression guards for the #361 spawn-overhead reduction.

    The `pact-agent-teams` skill was removed from agent frontmatter to
    eliminate per-spawn eager-load cost. In its place, each agent now carries
    an AGENT TEAMS PROTOCOL block in the body instructing the agent to invoke
    `Skill("PACT:pact-agent-teams")` before its first team-tool call.

    These tests pin the refactor against silent regression:
      - M3: no agent frontmatter may list `pact-agent-teams` under `skills:`
      - M4: every agent body must reference the lazy-load pointer
      - F3: no agent frontmatter may declare more than MAX_FRONTMATTER_SKILLS
    """

    # Upper bound on eager-loaded skills per agent. Currently the secretary
    # carries 2 (pact-memory, pact-handoff-harvest) and all other agents
    # carry 0. Bumping this threshold should be a deliberate, reviewed choice.
    MAX_FRONTMATTER_SKILLS = 2

    @staticmethod
    def _extract_skill_names(text):
        """Extract the raw `skills:` block from frontmatter and parse skill
        names. The shared `parse_frontmatter` helper flattens multiline lists
        into a single continuation string, so list-item names are recovered
        here by splitting the raw frontmatter on `- ` markers within the
        `skills:` block. Returns a list of skill-name strings (may be empty).

        This is scoped to this test class rather than extended in helpers.py
        to avoid destabilizing other tests that rely on the flattened form.
        """
        if not text.startswith("---"):
            return []
        try:
            end = text.index("---", 3)
        except ValueError:
            return []
        fm_text = text[3:end]
        lines = fm_text.split("\n")
        skills = []
        in_skills = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("skills:"):
                in_skills = True
                continue
            if in_skills:
                # Continuation lines are indented list items: "  - name"
                if line.startswith(" ") or line.startswith("\t"):
                    s = stripped.lstrip("-").strip()
                    if s:
                        skills.append(s)
                else:
                    # Non-indented line ends the skills block
                    in_skills = False
        return skills

    @pytest.fixture
    def all_agents(self, agent_files):
        agents = {}
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            if fm:
                skill_names = self._extract_skill_names(text)
                agents[f.stem] = (fm, text, skill_names)
        return agents

    def test_agent_teams_not_in_frontmatter(self, all_agents):
        """M3: `pact-agent-teams` must not appear in any agent's frontmatter."""
        for name, (_fm, _text, skill_names) in all_agents.items():
            assert "pact-agent-teams" not in skill_names, (
                f"{name}: pact-agent-teams must be lazy-loaded, not declared "
                f"in frontmatter (see #361). Found skills: {skill_names!r}"
            )

    def test_agent_teams_protocol_block_present(self, all_agents):
        """M4: the lazy-load pointer block must exist in every agent body."""
        for name, (_fm, text, _skills) in all_agents.items():
            assert "AGENT TEAMS PROTOCOL" in text, (
                f"{name}: missing '# AGENT TEAMS PROTOCOL' section. "
                f"Without it, the agent has no instruction to load "
                f"pact-agent-teams before first team-tool use."
            )
            assert 'Skill("PACT:pact-agent-teams")' in text, (
                f"{name}: AGENT TEAMS PROTOCOL block must reference "
                f'Skill("PACT:pact-agent-teams") so the agent knows '
                f"which skill to invoke."
            )

    def test_frontmatter_skill_count_capped(self, all_agents):
        """F3: no agent may declare more than MAX_FRONTMATTER_SKILLS."""
        for name, (_fm, _text, skill_names) in all_agents.items():
            count = len(skill_names)
            assert count <= self.MAX_FRONTMATTER_SKILLS, (
                f"{name}: {count} frontmatter skills exceeds cap of "
                f"{self.MAX_FRONTMATTER_SKILLS}. Per-spawn eager-load cost "
                f"must stay bounded (see #361). Move additional skills to "
                f"lazy-load via Skill() invocation in the agent body. "
                f"Skills found: {skill_names!r}"
            )

