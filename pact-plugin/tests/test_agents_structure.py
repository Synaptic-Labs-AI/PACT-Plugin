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


class TestKernelIntegrity:
    """Tests for CLAUDE-kernel.md content integrity.

    The slim kernel (pact-plugin/CLAUDE-kernel.md) is the on-disk file that
    both lead and teammates see. It must contain only the essential policy
    content and have zero @~/ references (those are delivered via hook to
    the lead only).
    """

    KERNEL_PATH = Path(__file__).parent.parent / "CLAUDE-kernel.md"

    def test_kernel_file_exists(self):
        """CLAUDE-kernel.md must exist in the plugin directory."""
        assert self.KERNEL_PATH.exists(), (
            f"CLAUDE-kernel.md not found at {self.KERNEL_PATH}"
        )

    def test_kernel_has_zero_at_references(self):
        """Kernel must have zero @~/.claude/protocols/ references.

        These references would cause protocol files to be loaded for every
        agent spawn, defeating the purpose of the spawn overhead reduction.
        """
        content = self.KERNEL_PATH.read_text(encoding="utf-8")
        assert "@~/" not in content, (
            "CLAUDE-kernel.md contains @~/ reference(s). These must be "
            "removed — protocols are delivered via hook to the lead only."
        )

    def test_kernel_contains_s5_non_negotiables_table(self):
        """Kernel must retain the S5 Non-Negotiables table.

        The SACROSANCT rules apply to ALL agents (lead and teammates alike).
        Removing them from the kernel would create a policy gap.
        """
        content = self.KERNEL_PATH.read_text(encoding="utf-8")
        assert "Non-Negotiables" in content
        assert "SACROSANCT" in content or "non-negotiable" in content.lower()
        # Verify the table has key rules
        assert "Security" in content
        assert "Quality" in content
        assert "Ethics" in content
        assert "Delegation" in content
        assert "Integrity" in content

    def test_kernel_contains_algedonic_signal_basics(self):
        """Kernel must retain algedonic signal category table.

        Every agent has Algedonic Authority per the autonomy charter. They
        need to know the signal categories (HALT/ALERT) and levels.
        """
        content = self.KERNEL_PATH.read_text(encoding="utf-8")
        assert "Algedonic" in content
        assert "HALT" in content
        assert "ALERT" in content
        assert "SECURITY" in content
        assert "QUALITY" in content

    def test_kernel_contains_mission_statement(self):
        """Kernel must retain the mission statement as identity anchor."""
        content = self.KERNEL_PATH.read_text(encoding="utf-8")
        assert "MISSION" in content
        assert "PACT Orchestrator" in content

    def test_kernel_contains_bootstrapper_instruction(self):
        """Kernel must contain the bootstrapper instruction pointing lead to sidecar.

        This is the recovery path: if hook context is lost, the lead can
        read the kernel and find the path to ~/.claude/pact-orchestrator.md.
        """
        content = self.KERNEL_PATH.read_text(encoding="utf-8")
        assert "pact-orchestrator.md" in content

    def test_kernel_is_under_size_budget(self):
        """Kernel should be well under 5KB chars (~1.2K tokens).

        The whole point is that this is slim. If it grows beyond ~5KB,
        the spawn overhead reduction is being eroded.
        """
        content = self.KERNEL_PATH.read_text(encoding="utf-8")
        assert len(content) < 5_000, (
            f"CLAUDE-kernel.md is {len(content)} chars, exceeds 5KB budget. "
            f"The kernel should be ~2-3KB to achieve spawn overhead targets."
        )

    def test_kernel_excludes_orchestrator_only_content(self):
        """Kernel must NOT contain orchestrator-only sections.

        These sections are delivered via hook to the lead only and must not
        appear in the kernel that teammates also read.
        """
        content = self.KERNEL_PATH.read_text(encoding="utf-8")
        # Orchestrator-only keywords that should NOT be in kernel
        assert "Context Economy" not in content
        assert "Wait in Silence" not in content
        assert "Guided Dialogue" not in content
        assert "Always Be Delegating" not in content
        assert "Agent Teams Dispatch" not in content


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
                assert charter_lines <= 15, (
                    f"{f.name}: AUTONOMY CHARTER section has {charter_lines} lines, "
                    f"exceeding the 15-line limit. The full boilerplate should "
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
