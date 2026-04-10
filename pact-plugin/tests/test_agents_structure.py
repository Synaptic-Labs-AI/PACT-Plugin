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
    # carry 1 (pact-teachback). Secretary carries 3 (teachback + memory +
    # handoff-harvest), auditor carries 2 (teachback + architecture-patterns).
    # Bumping this threshold should be a deliberate, reviewed choice.
    MAX_FRONTMATTER_SKILLS = 3

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
                # Handle inline value: "skills: single-skill"
                inline = stripped[len("skills:"):].strip()
                if inline and inline != "|":
                    skills.append(inline)
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

    def test_request_more_context_not_in_frontmatter(self, all_agents):
        """request-more-context must be lazy-loaded, not in frontmatter."""
        for name, (_fm, _text, skill_names) in all_agents.items():
            assert "request-more-context" not in skill_names, (
                f"{name}: request-more-context must be lazy-loaded, not "
                f"declared in frontmatter (see #361). Found skills: "
                f"{skill_names!r}"
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


class TestExtractSkillNamesParser:
    """Edge case tests for _extract_skill_names() parser.

    The parser in TestLazyLoadedAgentTeams._extract_skill_names handles
    YAML-like frontmatter parsing for the skills: block. These tests exercise
    edge cases that could silently break if the parser is changed.

    This is S3 cherry-pick quality scrutiny — the parser was authored by
    Sonnet and needs adversarial verification.
    """

    @staticmethod
    def _extract(text):
        return TestLazyLoadedAgentTeams._extract_skill_names(text)

    def test_empty_skills_block(self):
        """skills: with no value and no continuation lines."""
        text = "---\nname: test-agent\nskills:\ncolor: blue\n---\nBody"
        result = self._extract(text)
        assert result == []

    def test_inline_single_skill(self):
        """skills: single-skill on the same line."""
        text = "---\nname: test-agent\nskills: my-skill\n---\nBody"
        result = self._extract(text)
        assert result == ["my-skill"]

    def test_multiline_list_skills(self):
        """skills: with indented list items."""
        text = (
            "---\n"
            "name: test-agent\n"
            "skills:\n"
            "  - skill-one\n"
            "  - skill-two\n"
            "  - skill-three\n"
            "color: blue\n"
            "---\n"
            "Body"
        )
        result = self._extract(text)
        assert result == ["skill-one", "skill-two", "skill-three"]

    def test_no_skills_key(self):
        """Frontmatter without skills: key at all."""
        text = "---\nname: test-agent\ncolor: blue\n---\nBody"
        result = self._extract(text)
        assert result == []

    def test_no_frontmatter(self):
        """Text without --- frontmatter delimiters."""
        text = "# Just a heading\nNo frontmatter here."
        result = self._extract(text)
        assert result == []

    def test_skills_with_pipe_block_scalar(self):
        """skills: | (block scalar indicator) should not be treated as a skill name."""
        text = "---\nname: test-agent\nskills: |\n  block content\n---\nBody"
        result = self._extract(text)
        # The | is a YAML block scalar indicator, not a skill name
        assert "|" not in result

    def test_skills_terminated_by_non_indented_line(self):
        """Skill list parsing stops at first non-indented line."""
        text = (
            "---\n"
            "name: test-agent\n"
            "skills:\n"
            "  - skill-one\n"
            "color: blue\n"
            "  - not-a-skill\n"
            "---\n"
            "Body"
        )
        result = self._extract(text)
        # Only skill-one should be found; after "color:" breaks the skills block
        assert result == ["skill-one"]

    def test_skills_with_tab_indentation(self):
        """Tabs should also be treated as continuation."""
        text = "---\nname: test-agent\nskills:\n\t- tab-skill\ncolor: blue\n---\nBody"
        result = self._extract(text)
        assert result == ["tab-skill"]

    def test_empty_continuation_lines_skipped(self):
        """Empty continuation items (just '  - ') should be skipped."""
        text = (
            "---\n"
            "name: test-agent\n"
            "skills:\n"
            "  - real-skill\n"
            "  - \n"
            "  - another-skill\n"
            "---\n"
            "Body"
        )
        result = self._extract(text)
        assert "real-skill" in result
        assert "another-skill" in result
        assert "" not in result

    def test_malformed_frontmatter_no_closing(self):
        """Frontmatter without closing --- should return empty."""
        text = "---\nname: test-agent\nskills:\n  - orphan-skill\n"
        result = self._extract(text)
        assert result == []


class TestAgentTeamsProtocolConsistency:
    """Verify AGENT TEAMS PROTOCOL section wording is consistent across
    all 12 agent files.

    The lazy-load instruction block was cherry-picked from #361 and must
    contain identical core wording in all agents. Domain-specific differences
    (like the auditor having a different line count) are acceptable only
    outside the standardized block.
    """

    # The canonical first 7 lines of the AGENT TEAMS PROTOCOL body
    # (after the heading). These must be identical in all agents.
    CANONICAL_LINES = [
        "This agent communicates with the team via `SendMessage`, `TaskList`, `TaskGet`,",
        "`TaskUpdate`, and other team tools. **On first use of any of these tools after",
        "spawn (or after reuse for a new task), invoke the Skill tool:",
        '`Skill("PACT:pact-agent-teams")`** to load the full',
        "communication protocol (teachback, progress signals, message format, lifecycle,",
        "HANDOFF format). This skill was previously eager-loaded via frontmatter; it is",
        "now lazy-loaded to reduce per-spawn context overhead (see issue #361).",
    ]

    def _extract_protocol_block(self, text):
        """Extract the AGENT TEAMS PROTOCOL section body lines."""
        marker = "# AGENT TEAMS PROTOCOL"
        idx = text.find(marker)
        if idx == -1:
            return []
        # Skip the heading line and blank line after it
        after = text[idx + len(marker):]
        lines = after.split("\n")
        # Skip leading blank lines
        body_lines = []
        started = False
        for line in lines:
            stripped = line.strip()
            if not started and not stripped:
                continue
            if not started and stripped:
                started = True
            if started:
                # Stop at next heading or end of section
                if stripped.startswith("#") and not stripped.startswith("##"):
                    break
                body_lines.append(stripped)
        return body_lines

    def test_all_agents_have_canonical_protocol_wording(self, agent_files):
        """Every agent must have the exact canonical AGENT TEAMS PROTOCOL text."""
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            body = self._extract_protocol_block(text)
            for i, canonical_line in enumerate(self.CANONICAL_LINES):
                assert i < len(body), (
                    f"{f.name}: AGENT TEAMS PROTOCOL block has only {len(body)} "
                    f"lines, expected at least {len(self.CANONICAL_LINES)}"
                )
                assert body[i] == canonical_line, (
                    f"{f.name}: AGENT TEAMS PROTOCOL line {i} diverges.\n"
                    f"  Expected: {canonical_line!r}\n"
                    f"  Got:      {body[i]!r}"
                )

    def test_all_agents_have_request_more_context_mention(self, agent_files):
        """Every agent should mention request-more-context as an on-demand skill."""
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            assert "request-more-context" in text, (
                f"{f.name}: missing request-more-context mention in "
                f"AGENT TEAMS PROTOCOL section"
            )


class TestAutonomyCharterExtraction:
    """Tests for the autonomy charter boilerplate extraction (#366 S4).

    All 12 agents should have had their full Autonomy Charter boilerplate
    (>15 lines) replaced with a condensed pointer to the shared
    pact-autonomy-charter skill (~5 lines).
    """

    def test_all_agents_reference_autonomy_charter_skill(self, agent_files):
        """Every agent must reference pact-autonomy-charter skill."""
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            assert "pact-autonomy-charter" in text, (
                f"{f.name}: missing reference to pact-autonomy-charter skill. "
                f"The autonomy charter was extracted to a shared skill in #366."
            )

    def test_no_agent_has_full_autonomy_boilerplate(self, agent_files):
        """No agent should have the full Autonomy Charter boilerplate (>15 lines).

        The full boilerplate includes sections like 'You have authority to:',
        'You must escalate when:', 'Nested PACT:', 'Self-Coordination:',
        'Algedonic Authority:'. If the agent has >15 lines between
        'AUTONOMY CHARTER' and the next heading, the extraction is incomplete.
        """
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            lines = text.split("\n")
            in_charter = False
            charter_lines = 0
            for line in lines:
                stripped = line.strip()
                if "AUTONOMY CHARTER" in stripped:
                    in_charter = True
                    charter_lines = 0
                    continue
                if in_charter:
                    # Stop at next major heading (# or **heading**)
                    if (stripped.startswith("# ") or
                            (stripped.startswith("**") and
                             stripped.endswith("**") and
                             len(stripped) > 4 and
                             stripped != "**AUTONOMY CHARTER**" and
                             "autonomy" not in stripped.lower())):
                        break
                    charter_lines += 1

            if in_charter:
                # Secretary has domain-specific authority extensions beyond
                # the shared charter (context recovery, memory consolidation,
                # direct query response, etc.) — allow a higher cap.
                cap = 25 if f.stem == "pact-secretary" else 15
                assert charter_lines <= cap, (
                    f"{f.name}: AUTONOMY CHARTER section has {charter_lines} lines, "
                    f"exceeding the {cap}-line limit. The full boilerplate should "
                    f"have been extracted to the pact-autonomy-charter skill."
                )

    def test_autonomy_charter_section_mentions_skill_invocation(self, agent_files):
        """The condensed AUTONOMY CHARTER should tell the agent how to load the skill."""
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            if "AUTONOMY CHARTER" not in text:
                continue
            # Find the charter section
            idx = text.find("AUTONOMY CHARTER")
            after = text[idx:]
            # Should contain the Skill invocation instruction
            assert 'Skill("PACT:pact-autonomy-charter")' in after or \
                   "pact-autonomy-charter" in after, (
                f"{f.name}: AUTONOMY CHARTER section doesn't reference "
                f"the pact-autonomy-charter skill for loading."
            )


class TestAutonomyCharterSkillContent:
    """Content integrity tests for the pact-autonomy-charter skill.

    The shared skill must contain all key sections that were extracted from
    the per-agent autonomy charter boilerplate. If a section is missing,
    agents that invoke the skill won't receive the full charter.
    """

    SKILL_PATH = Path(__file__).parent.parent / "skills" / "pact-autonomy-charter" / "SKILL.md"

    REQUIRED_SECTIONS = [
        ("authority", "You have authority to:"),
        ("escalation", "You must escalate when:"),
        ("nested PACT", "Nested PACT"),
        ("self-coordination", "Self-Coordination"),
        ("algedonic", "Algedonic Authority"),
    ]

    def test_skill_file_exists(self):
        assert self.SKILL_PATH.is_file(), (
            "pact-autonomy-charter/SKILL.md missing"
        )

    @pytest.mark.parametrize("label,marker", REQUIRED_SECTIONS)
    def test_contains_required_section(self, label, marker):
        """Each key charter section must be present in the shared skill."""
        content = self.SKILL_PATH.read_text(encoding="utf-8")
        assert marker in content, (
            f"pact-autonomy-charter missing '{label}' section "
            f"(expected marker: {marker!r})"
        )

    def test_contains_algedonic_signal_reference(self):
        """Must reference algedonic.md for the full trigger list."""
        content = self.SKILL_PATH.read_text(encoding="utf-8")
        assert "algedonic.md" in content, (
            "pact-autonomy-charter must reference algedonic.md "
            "for signal format and full trigger list"
        )


class TestRequiredSkillsCondensed:
    """Tests for REQUIRED SKILLS section condensing (#366 S5).

    Each agent's REQUIRED SKILLS section should be condensed to <= 10 lines
    (down from ~15 lines). The condensed version removes the 'How to invoke',
    'Why this matters', and 'Cross-Agent Coordination' sub-sections.

    Note: pact-secretary uses frontmatter skills: (pact-memory, pact-handoff-harvest)
    instead of a REQUIRED SKILLS section, so it's excluded from section-based checks.
    """

    # Secretary uses frontmatter skills, not REQUIRED SKILLS section
    _SECRETARY = "pact-secretary.md"

    # Maximum lines between "# REQUIRED SKILLS" heading and next heading.
    # The architecture targets ~5 lines for most agents, but n8n has 7 skill
    # rows in its table (legitimately larger). 15 lines is the hard ceiling.
    # Before #366 S5 condensing, agents had ~20+ lines including the removed
    # "How to invoke", "Why this matters", and "Cross-Agent Coordination" blocks.
    MAX_SECTION_LINES = 15

    def test_required_skills_condensed(self, agent_files):
        """Every agent's REQUIRED SKILLS section should be <= MAX_SECTION_LINES."""
        for f in agent_files:
            if f.name == self._SECRETARY:
                continue
            text = f.read_text(encoding="utf-8")
            lines = text.split("\n")
            in_section = False
            seen_table = False
            past_table = False
            section_lines = 0
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("# REQUIRED SKILLS"):
                    in_section = True
                    section_lines = 0
                    continue
                if in_section:
                    # Stop at next heading (any level: #, ##, ###, etc.)
                    if (stripped.startswith("#") and
                            not stripped.startswith("# REQUIRED") and
                            len(stripped) > 1 and stripped[1] in (" ", "#")):
                        break
                    # Stop at standalone bold section markers (**TITLE**)
                    if (stripped.startswith("**") and stripped.endswith("**")
                            and len(stripped) > 4):
                        break
                    # Track table state: once we've seen table rows and
                    # then a blank line, the next non-blank non-table line
                    # means we've left the REQUIRED SKILLS section (covers
                    # agents that use prose paragraphs instead of headings)
                    if stripped.startswith("|"):
                        seen_table = True
                        past_table = False
                    elif seen_table and not stripped:
                        past_table = True
                    elif past_table and stripped:
                        break
                    section_lines += 1

            if in_section:
                assert section_lines <= self.MAX_SECTION_LINES, (
                    f"{f.name}: REQUIRED SKILLS section has {section_lines} "
                    f"lines, should be <= {self.MAX_SECTION_LINES} after "
                    f"condensing (was ~15+ before #366 S5)."
                )

    def test_no_how_to_invoke_subsection(self, agent_files):
        """No agent should have the removed 'How to invoke' block."""
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            assert "**How to invoke**" not in text, (
                f"{f.name}: still contains '**How to invoke**' — "
                f"this was removed in #366 REQUIRED SKILLS condensing."
            )

    def test_no_why_this_matters_subsection(self, agent_files):
        """No agent should have the removed 'Why this matters' block."""
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            assert "**Why this matters**" not in text, (
                f"{f.name}: still contains '**Why this matters**' — "
                f"this was removed in #366 REQUIRED SKILLS condensing."
            )

    def test_required_skills_table_preserved(self, agent_files):
        """Non-secretary agents should still have the skill table."""
        for f in agent_files:
            if f.name == self._SECRETARY:
                continue
            text = f.read_text(encoding="utf-8")
            # The table has "Task Involves" or "When Your Task Involves" header
            has_table = ("Task Involves" in text or
                         "| Task" in text or
                         "| Any" in text)
            assert has_table, (
                f"{f.name}: REQUIRED SKILLS section is missing the "
                f"skill lookup table."
            )

    def test_secretary_uses_frontmatter_skills(self, agent_files):
        """Secretary should use frontmatter skills instead of REQUIRED SKILLS section."""
        for f in agent_files:
            if f.name != self._SECRETARY:
                continue
            text = f.read_text(encoding="utf-8")
            assert "skills:" in text, (
                "pact-secretary.md should have skills: in frontmatter"
            )


class TestTeachbackMicroSkillExtraction:
    """Tests for the teachback micro-skill extraction (#385).

    The teachback protocol was extracted from pact-agent-teams into a
    standalone pact-teachback skill. Every agent eager-loads it via
    frontmatter so the teachback format is always available at spawn.

    These tests pin the extraction against silent regression:
      - T1: pact-teachback skill file exists with valid structure
      - T2: skill is under size budget (micro-skill, <1.5K chars)
      - T3: skill contains the actual protocol (not just metadata)
      - T4: every agent has pact-teachback in frontmatter skills
      - T5: pact-agent-teams no longer contains the full teachback protocol
      - T6: pact-agent-teams still references the extracted skill
    """

    SKILLS_DIR = Path(__file__).parent.parent / "skills"
    AGENTS_DIR = Path(__file__).parent.parent / "agents"

    # Micro-skill size budget: teachback protocol should be compact.
    # Measured in characters (not bytes) per lead spec.
    MAX_SKILL_CHARS = 1500

    # Key protocol elements that must be in the extracted skill
    REQUIRED_PROTOCOL_ELEMENTS = [
        "SendMessage",           # Communication tool reference
        "teachback_sent",        # Metadata flag
        "gate",                  # Gate semantics (teachback is a gate)
        "Teachback:",            # Format template marker
    ]

    # Lines that indicate full protocol content (not a stub).
    # If pact-agent-teams contains these, the extraction is incomplete.
    FULL_PROTOCOL_MARKERS = [
        "Send as your **first message**",
        "Keep concise: 3-6 bullet points",
        "Non-blocking: proceed with work after sending",
    ]

    @pytest.fixture
    def teachback_skill(self):
        skill_md = self.SKILLS_DIR / "pact-teachback" / "SKILL.md"
        assert skill_md.is_file(), "pact-teachback/SKILL.md missing"
        return skill_md

    @pytest.fixture
    def agent_teams_skill(self):
        skill_md = self.SKILLS_DIR / "pact-agent-teams" / "SKILL.md"
        assert skill_md.is_file(), "pact-agent-teams/SKILL.md missing"
        return skill_md

    def test_teachback_skill_exists_with_valid_frontmatter(self, teachback_skill):
        """T1: skill file exists with name and description in frontmatter."""
        text = teachback_skill.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        assert fm is not None, "pact-teachback SKILL.md has no valid frontmatter"
        assert fm.get("name") == "pact-teachback", (
            f"Expected name 'pact-teachback', got {fm.get('name')!r}"
        )
        assert "description" in fm, "pact-teachback missing description"

    def test_teachback_skill_under_size_budget(self, teachback_skill):
        """T2: micro-skill must be compact (<1.5K chars)."""
        text = teachback_skill.read_text(encoding="utf-8")
        char_count = len(text)
        assert char_count <= self.MAX_SKILL_CHARS, (
            f"pact-teachback is {char_count} chars, exceeding "
            f"{self.MAX_SKILL_CHARS} char micro-skill budget. If the "
            f"protocol grew legitimately, update MAX_SKILL_CHARS with "
            f"justification."
        )

    def test_teachback_skill_contains_protocol(self, teachback_skill):
        """T3: skill must contain actual protocol, not just metadata."""
        text = teachback_skill.read_text(encoding="utf-8")
        for element in self.REQUIRED_PROTOCOL_ELEMENTS:
            assert element in text, (
                f"pact-teachback missing required protocol element: "
                f"{element!r}. The skill must contain the actual teachback "
                f"protocol, not just a pointer."
            )

    def test_all_agents_have_teachback_in_frontmatter(self, agent_files):
        """T4: every agent must eager-load pact-teachback via frontmatter."""
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            skill_names = TestLazyLoadedAgentTeams._extract_skill_names(text)
            assert "pact-teachback" in skill_names, (
                f"{f.stem}: pact-teachback must be in frontmatter skills "
                f"(eager-loaded at spawn). Found skills: {skill_names!r}"
            )

    def test_agent_teams_no_full_teachback_protocol(self, agent_teams_skill):
        """T5: pact-agent-teams must not contain the full teachback protocol.

        After extraction, pact-agent-teams should have a slim stub/pointer
        to the pact-teachback skill, not the full protocol content.
        """
        text = agent_teams_skill.read_text(encoding="utf-8")
        for marker in self.FULL_PROTOCOL_MARKERS:
            assert marker not in text, (
                f"pact-agent-teams still contains full teachback protocol "
                f"marker: {marker!r}. The protocol should have been "
                f"extracted to pact-teachback skill (#385)."
            )

    def test_agent_teams_references_teachback_skill(self, agent_teams_skill):
        """T6: pact-agent-teams must reference the extracted skill."""
        text = agent_teams_skill.read_text(encoding="utf-8")
        assert "pact-teachback" in text, (
            "pact-agent-teams should reference pact-teachback skill "
            "as a pointer so agents know where the protocol lives."
        )


class TestBootstrapCommand:
    """Tests for the /PACT:bootstrap slash command.

    The bootstrap command replaces the earlier CLAUDE.md sidecar mechanism.
    When the orchestrator invokes it, Claude Code eagerly resolves the
    @${CLAUDE_PLUGIN_ROOT}/protocols/... references inside the command body
    and loads the 8 critical protocols into the lead's context.

    Contract:
      - The command file exists at pact-plugin/commands/bootstrap.md
      - It has YAML frontmatter with a `description` field
      - It contains @${CLAUDE_PLUGIN_ROOT}/protocols/ references for all
        8 critical protocols (algedonic, s5-policy, variety, workflows,
        state-recovery, s4-checkpoints, s4-tension, communication-charter)
      - Every referenced protocol file exists on disk
      - The command is registered in .claude-plugin/plugin.json `commands`
    """

    PLUGIN_ROOT = Path(__file__).parent.parent
    BOOTSTRAP_PATH = PLUGIN_ROOT / "commands" / "bootstrap.md"
    PROTOCOLS_DIR = PLUGIN_ROOT / "protocols"
    PLUGIN_JSON_PATH = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"

    # The 8 critical protocols that must be eagerly loaded by the bootstrap
    # command. Adding/removing a protocol from this set requires a matching
    # change to commands/bootstrap.md and is intentionally visible here.
    CRITICAL_PROTOCOLS = (
        "algedonic",
        "pact-s5-policy",
        "pact-variety",
        "pact-workflows",
        "pact-state-recovery",
        "pact-s4-checkpoints",
        "pact-s4-tension",
        "pact-communication-charter",
    )

    def test_bootstrap_file_exists(self):
        """commands/bootstrap.md must exist in the plugin directory."""
        assert self.BOOTSTRAP_PATH.exists(), (
            f"bootstrap.md not found at {self.BOOTSTRAP_PATH}"
        )
        assert self.BOOTSTRAP_PATH.is_file()

    def test_bootstrap_has_frontmatter_with_description(self):
        """bootstrap.md must have YAML frontmatter with a description field.

        Claude Code uses the frontmatter description to surface the command
        in the slash command palette. A missing description would leave the
        command undiscoverable even if registered in plugin.json.
        """
        text = self.BOOTSTRAP_PATH.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        assert fm is not None, (
            "bootstrap.md is missing YAML frontmatter"
        )
        assert "description" in fm, (
            "bootstrap.md frontmatter must contain a 'description' field"
        )
        assert fm["description"].strip(), (
            "bootstrap.md frontmatter 'description' must be non-empty"
        )

    def test_bootstrap_references_all_critical_protocols(self):
        """bootstrap.md must contain @${CLAUDE_PLUGIN_ROOT}/protocols/<name>.md
        references for all 8 critical protocols.

        Claude Code resolves these @-references at command invocation time,
        loading each referenced file into the orchestrator's context. This
        replaces the sidecar file-write mechanism the tests used to cover.
        """
        text = self.BOOTSTRAP_PATH.read_text(encoding="utf-8")
        missing = []
        for protocol in self.CRITICAL_PROTOCOLS:
            # Match both leading "@" (eager load) and the canonical env-var
            # path fragment. We require the specific form the command body
            # uses so a stray mention in prose doesn't accidentally satisfy
            # the contract.
            expected = f"@${{CLAUDE_PLUGIN_ROOT}}/protocols/{protocol}.md"
            if expected not in text:
                missing.append(expected)
        assert not missing, (
            f"bootstrap.md is missing eager-load references for: {missing}. "
            f"Each of the 8 critical protocols must be referenced via "
            f"@${{CLAUDE_PLUGIN_ROOT}}/protocols/<name>.md so Claude Code "
            f"loads them at command invocation."
        )

    def test_bootstrap_referenced_protocol_files_exist(self):
        """Every protocol referenced in bootstrap.md must exist on disk.

        A broken @-reference would cause a silent load failure at runtime —
        the orchestrator would proceed without the protocol, defeating the
        eager-load guarantee.
        """
        for protocol in self.CRITICAL_PROTOCOLS:
            path = self.PROTOCOLS_DIR / f"{protocol}.md"
            assert path.exists(), (
                f"Protocol file missing: {path}. "
                f"bootstrap.md references it via "
                f"@${{CLAUDE_PLUGIN_ROOT}}/protocols/{protocol}.md but the "
                f"file does not exist in the plugin protocols/ directory."
            )

    def test_bootstrap_registered_in_plugin_json(self):
        """bootstrap.md must be registered in plugin.json's commands list.

        Without this registration, Claude Code will not expose the slash
        command to the user even though the file exists on disk.
        """
        import json

        assert self.PLUGIN_JSON_PATH.exists(), (
            f"plugin.json not found at {self.PLUGIN_JSON_PATH}"
        )
        data = json.loads(self.PLUGIN_JSON_PATH.read_text(encoding="utf-8"))
        commands = data.get("commands", [])
        assert isinstance(commands, list), (
            "plugin.json 'commands' must be a list"
        )
        # Match either "./commands/bootstrap.md" or "commands/bootstrap.md"
        # — both are accepted relative-path forms used in this repo.
        bootstrap_registered = any(
            entry.endswith("commands/bootstrap.md")
            for entry in commands
            if isinstance(entry, str)
        )
        assert bootstrap_registered, (
            f"bootstrap.md is not registered in plugin.json commands list. "
            f"Found commands: {commands}"
        )
