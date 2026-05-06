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
    "pact-orchestrator",
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
    """Load all agent markdown files (orchestrator + 12 teammates)."""
    return list(AGENTS_DIR.glob("*.md"))


# Subset of agent files that are TEAMMATES — i.e., spawned via Agent() with
# `skills:` frontmatter preload. Excludes pact-orchestrator.md, which is
# delivered via `claude --agent` and has minimal frontmatter (no skills:).
TEAMMATE_AGENT_NAMES = EXPECTED_AGENTS - {"pact-orchestrator"}


@pytest.fixture
def teammate_agent_files():
    """Load only teammate agent files (excludes pact-orchestrator)."""
    return [
        p for p in AGENTS_DIR.glob("*.md")
        if p.stem in TEAMMATE_AGENT_NAMES
    ]


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


class TestNoSkillInvocationOnFirstAction:
    """Negative-invariant fossilization guard: no TEAMMATE agent body may
    instruct the agent to invoke `Skill("PACT:teammate-bootstrap")` (deleted
    skill) or `Skill("PACT:bootstrap")` (orchestrator-only ritual command).

    The team protocol, teachback rules, and algedonic content arrive via the
    spawn-time skills: frontmatter (preload at Agent() spawn). A fossil
    `Skill("PACT:teammate-bootstrap")` directive in any agent body points at
    a permanently removed command. A `Skill("PACT:bootstrap")` directive in a
    teammate body points at the orchestrator-only session-start ritual; only
    pact-orchestrator.md may carry it (in §2 Session-Start Ritual).

    The class also keeps the canonical skills-frontmatter-baseline guard
    (every teammate carries pact-agent-teams + pact-teachback).
    """

    # teammate-bootstrap.md was permanently removed; no agent (orchestrator
    # included) may reference it.
    FOSSIL_SKILL_INVOCATIONS_ALL_AGENTS = (
        'Skill("PACT:teammate-bootstrap")',
        "Skill('PACT:teammate-bootstrap')",
    )

    # bootstrap.md is the orchestrator-only ritual command. Teammate bodies
    # must not invoke it; pact-orchestrator.md is exempt (§2 Session-Start
    # Ritual relies on this invocation).
    ORCHESTRATOR_ONLY_SKILL_INVOCATIONS = (
        'Skill("PACT:bootstrap")',
        "Skill('PACT:bootstrap')",
    )

    def test_no_bootstrap_skill_invocation_in_any_agent(self, agent_files):
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            for fossil in self.FOSSIL_SKILL_INVOCATIONS_ALL_AGENTS:
                assert fossil not in text, (
                    f"{f.name}: contains permanently-removed skill "
                    f"invocation {fossil!r}. teammate-bootstrap.md was "
                    f"deleted; agents must not instruct invocation of "
                    f"removed skills."
                )
            if f.name == "pact-orchestrator.md":
                continue
            for fossil in self.ORCHESTRATOR_ONLY_SKILL_INVOCATIONS:
                assert fossil not in text, (
                    f"{f.name}: contains orchestrator-only skill invocation "
                    f"{fossil!r}. /PACT:bootstrap is the session-start "
                    f"ritual; only pact-orchestrator.md may invoke it."
                )

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
    def all_agents(self, teammate_agent_files):
        agents = {}
        for f in teammate_agent_files:
            text = f.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            if fm:
                skill_names = self._extract_skill_names(text)
                agents[f.stem] = (fm, text, skill_names)
        return agents

    def test_frontmatter_skill_min_baseline(self, all_agents):
        """F3 (replacement): every agent must carry the canonical 2-skill
        baseline (pact-agent-teams + pact-teachback). The cap was removed
        post-#366 phase 1 because frontmatter skills don't eager-load and
        per-agent variation is the intentional design."""
        for name, (_fm, _text, skill_names) in all_agents.items():
            assert "pact-agent-teams" in skill_names, (
                f"{name}: missing canonical pact-agent-teams baseline skill. "
                f"Skills found: {skill_names!r}"
            )
            assert "pact-teachback" in skill_names, (
                f"{name}: missing canonical pact-teachback baseline skill. "
                f"Skills found: {skill_names!r}"
            )


class TestExtractSkillNamesParser:
    """Edge case tests for _extract_skill_names() parser.

    The parser in TestNoSkillInvocationOnFirstAction._extract_skill_names handles
    YAML-like frontmatter parsing for the skills: block. These tests exercise
    edge cases that could silently break if the parser is changed.

    This is S3 cherry-pick quality scrutiny — the parser was authored by
    Sonnet and needs adversarial verification.
    """

    @staticmethod
    def _extract(text):
        return TestNoSkillInvocationOnFirstAction._extract_skill_names(text)

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


class TestRequiredSkillsCondensed:
    """Structural tests for the REQUIRED SKILLS section.

    The aspirational #366 S5 line-count and subsection-condensing tests were
    removed because Phase 1 did not include that condensing pass. What
    remains here are the structural invariants that hold regardless of
    condensing: every non-secretary agent must still have a REQUIRED SKILLS
    section with a lookup table, and the secretary uses frontmatter skills.
    """

    # Secretary uses frontmatter skills, not REQUIRED SKILLS section
    _SECRETARY = "pact-secretary.md"

    def test_required_skills_section_and_table_preserved(self, teammate_agent_files):
        """Every non-secretary agent must have BOTH (1) a REQUIRED SKILLS
        section header AND (2) at least one skill lookup table row in the
        body of that section.

        Previous form used very permissive substring matching (`"| Task"`
        or `"| Any"` anywhere in the body) which would pass even if the
        REQUIRED SKILLS section had been accidentally deleted entirely,
        as long as some markdown table elsewhere happened to contain
        one of those substrings. The tightened form anchors on the
        `# REQUIRED SKILLS` heading and checks for a table body inside
        the section region.
        """
        for f in teammate_agent_files:
            if f.name == self._SECRETARY:
                continue
            text = f.read_text(encoding="utf-8")

            # Requirement 1: the section header itself must exist
            section_header_idx = text.find("# REQUIRED SKILLS")
            assert section_header_idx != -1, (
                f"{f.name}: missing `# REQUIRED SKILLS` section header. "
                f"Non-secretary agents must document which skills to "
                f"invoke at the start of their work."
            )

            # Requirement 2: the section must contain a markdown table
            # body row (a line starting with `|` that contains a backtick-
            # delimited skill name like `pact-coding-standards`). Slice
            # the text starting at the header to the next top-level `#`
            # heading, so the table check is constrained to the section.
            #
            # Find the next `# ` (h1) heading after the REQUIRED SKILLS
            # header. If none, take the rest of the file.
            region_start = section_header_idx
            next_h1 = text.find("\n# ", region_start + 1)
            region_end = next_h1 if next_h1 != -1 else len(text)
            section_body = text[region_start:region_end]

            # A skill lookup table row should contain a backtick-quoted
            # skill name pattern like `` `pact-coding-standards` `` or
            # `` `pact-security-patterns` `` somewhere inside the section.
            has_skill_ref = (
                "`pact-coding-standards`" in section_body
                or "`pact-security-patterns`" in section_body
                or "`pact-testing-strategies`" in section_body
                or "`pact-prepare-research`" in section_body
                or "`pact-architecture-patterns`" in section_body
                or "`n8n-" in section_body
            )
            assert has_skill_ref, (
                f"{f.name}: REQUIRED SKILLS section is present but does "
                f"not reference any recognized skill name (e.g., "
                f"`pact-coding-standards`, `pact-security-patterns`, "
                f"`pact-testing-strategies`, `pact-prepare-research`, "
                f"`pact-architecture-patterns`, or an `n8n-*` skill). "
                f"Either the section is empty or the skill names have "
                f"drifted — agents need a concrete skill table to know "
                f"what to invoke."
            )

    def test_secretary_uses_frontmatter_skills(self, teammate_agent_files):
        """Secretary should use frontmatter skills instead of REQUIRED SKILLS section."""
        for f in teammate_agent_files:
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
    # Measured in characters (not bytes). Budget tracks legitimate growth
    # of the protocol surface. Current value (4000) accommodates the
    # 4-field structured payload (understanding / most_likely_wrong /
    # least_confident_item / first_action), the Task A / Task B dispatch
    # framing, and the idle-on-awaiting_lead_completion contract that
    # replaced the prior SendMessage-prose teachback delivery.
    MAX_SKILL_CHARS = 4000

    # Key protocol elements that must be in the extracted skill.
    # Presence-only checks are deliberately strict — any drop indicates
    # the skill has shed a load-bearing piece of the protocol.
    REQUIRED_PROTOCOL_ELEMENTS = [
        "SendMessage",                  # Communication tool reference (notify path)
        "teachback_submit",             # Metadata field name (team-lead-readable payload)
        "gate",                         # Gate semantics (teachback is a gate)
        "Teachback submitted",          # Notify-message marker
        "before any Edit/Write/Bash",   # Ordering rule literal
        'TaskUpdate(taskId, metadata={"teachback_submit":',  # Storage literal
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

    def test_all_agents_have_teachback_in_frontmatter(self, teammate_agent_files):
        """T4: every agent must eager-load pact-teachback via frontmatter."""
        for f in teammate_agent_files:
            text = f.read_text(encoding="utf-8")
            skill_names = TestNoSkillInvocationOnFirstAction._extract_skill_names(text)
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


class TestNoFirstActionPreludeFossil:
    """Negative-invariant fossilization guard: no agent body may contain
    the v3.x YOUR FIRST ACTION prelude.

    Under v4.0.0 the orchestrator persona is delivered via `claude --agent
    PACT:pact-orchestrator` and teammate skill content arrives via the
    spawn-time skills: frontmatter; the per-body FIRST-ACTION dispatch
    directive that v3.x relied on is now noise. A reintroduction would
    fight the new lazy-load convention silently — agents would carry both
    a stale dispatch prelude and the v4.0.0 cross-references, and human
    readers reviewing a regression would have no signal to flag.
    """

    FOSSIL_HEADING = "# YOUR FIRST ACTION (YOU MUST DO THIS IMMEDIATELY)"
    FOSSIL_HEADING_VARIANTS = (
        "# YOUR FIRST ACTION",
        "## YOUR FIRST ACTION",
        "### YOUR FIRST ACTION",
    )

    def test_no_first_action_heading_in_any_agent(self, agent_files):
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            for fossil in self.FOSSIL_HEADING_VARIANTS:
                assert fossil not in text, (
                    f"{f.name}: contains v3.x fossil heading {fossil!r}. "
                    f"Under v4.0.0 the FIRST-ACTION dispatch convention is "
                    f"removed — agent bodies must not carry it. Delete the "
                    f"section."
                )

    def test_no_first_action_heading_canonical_form(self, agent_files):
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            assert self.FOSSIL_HEADING not in text, (
                f"{f.name}: contains canonical v3.x FIRST-ACTION heading. "
                f"Delete it."
            )


class TestAgentFrontmatterSkills:
    """Every agent's frontmatter must eager-load the team protocol skills.

    Post-#366 the team protocol is delivered via frontmatter (eager) PLUS
    the teammate-bootstrap command (loaded via YOUR FIRST ACTION). This test
    pins the frontmatter contract: pact-agent-teams AND pact-teachback must
    both be present in skills:.

    Presence-only — no cardinality or exclusivity checks. Other skills may
    be added (e.g., the secretary's pact-memory + pact-handoff-harvest, the
    auditor's pact-architecture-patterns).
    """

    @pytest.fixture
    def agent_skills(self, teammate_agent_files):
        out = {}
        for f in teammate_agent_files:
            text = f.read_text(encoding="utf-8")
            out[f.stem] = TestNoSkillInvocationOnFirstAction._extract_skill_names(text)
        return out

    def test_pact_agent_teams_in_frontmatter(self, agent_skills):
        for name, skills in agent_skills.items():
            assert "pact-agent-teams" in skills, (
                f"{name}: pact-agent-teams must be eager-loaded via "
                f"frontmatter post-#366. Found skills: {skills!r}"
            )

    def test_pact_teachback_in_frontmatter(self, agent_skills):
        for name, skills in agent_skills.items():
            assert "pact-teachback" in skills, (
                f"{name}: pact-teachback must be eager-loaded via frontmatter "
                f"so the teachback format is always available at spawn. "
                f"Found skills: {skills!r}"
            )


class TestAgentDomainSkillVariations:
    """Post-#366 phase 1, each specialist agent's frontmatter declares a
    domain-specific skill set in addition to the canonical baseline. This
    class pins the per-agent expected skills so removal of a load-bearing
    domain skill (e.g., pact-architecture-patterns from the auditor, or any
    of the n8n skills from the n8n agent) is caught by CI.

    The mapping is the explicit per-agent design — there is no uniform cap
    or pattern. Each agent's skill set was chosen based on its specialist
    function. See docs/architecture/366-phase1-kernel-elimination.md
    Section 6 for the full mapping rationale.

    Note on eager-loading: frontmatter skills do NOT eager-load at agent
    spawn (empirically verified during the #366 Phase 1 planning session).
    The per-agent variation is catalog metadata and discoverability, not a
    runtime cost. Agents still invoke Skill() explicitly when they need a
    skill loaded into context.
    """

    EXPECTED_SKILLS = {
        "pact-preparer": {
            "pact-agent-teams",
            "pact-teachback",
            "request-more-context",
            "pact-prepare-research",
        },
        "pact-architect": {
            "pact-agent-teams",
            "pact-teachback",
            "request-more-context",
            "pact-architecture-patterns",
        },
        "pact-backend-coder": {
            "pact-agent-teams",
            "pact-teachback",
            "request-more-context",
            "pact-coding-standards",
            "pact-security-patterns",
        },
        "pact-frontend-coder": {
            "pact-agent-teams",
            "pact-teachback",
            "request-more-context",
            "pact-coding-standards",
            "pact-security-patterns",
        },
        "pact-database-engineer": {
            "pact-agent-teams",
            "pact-teachback",
            "request-more-context",
            "pact-coding-standards",
        },
        "pact-devops-engineer": {
            "pact-agent-teams",
            "pact-teachback",
            "request-more-context",
            "pact-coding-standards",
        },
        "pact-test-engineer": {
            "pact-agent-teams",
            "pact-teachback",
            "request-more-context",
            "pact-testing-strategies",
        },
        "pact-qa-engineer": {
            "pact-agent-teams",
            "pact-teachback",
            "request-more-context",
            "pact-testing-strategies",
        },
        "pact-security-engineer": {
            "pact-agent-teams",
            "pact-teachback",
            "request-more-context",
            "pact-security-patterns",
        },
        "pact-n8n": {
            "pact-agent-teams",
            "pact-teachback",
            "request-more-context",
            "n8n-workflow-patterns",
            "n8n-validation-expert",
            "n8n-mcp-tools-expert",
            "n8n-node-configuration",
            "n8n-code-javascript",
            "n8n-code-python",
            "n8n-expression-syntax",
        },
        "pact-auditor": {
            "pact-agent-teams",
            "pact-teachback",
            "request-more-context",
            "pact-architecture-patterns",
        },
        "pact-secretary": {
            "pact-agent-teams",
            "pact-teachback",
            "pact-memory",
            "pact-handoff-harvest",
        },
    }

    @pytest.fixture
    def agent_skills(self, teammate_agent_files):
        out = {}
        for f in teammate_agent_files:
            text = f.read_text(encoding="utf-8")
            out[f.stem] = set(
                TestNoSkillInvocationOnFirstAction._extract_skill_names(text)
            )
        return out

    def test_all_12_agents_have_pinned_expected_skills(self, agent_skills):
        """Every PACT agent must have a pinned expected skill set in
        EXPECTED_SKILLS. Catches new agents added without an entry."""
        actual_agents = set(agent_skills.keys())
        expected_agents = set(self.EXPECTED_SKILLS.keys())
        missing_from_expected = actual_agents - expected_agents
        missing_from_actual = expected_agents - actual_agents
        assert not missing_from_expected, (
            f"Agent(s) found on disk but not pinned in EXPECTED_SKILLS: "
            f"{missing_from_expected}. Add an entry to "
            f"TestAgentDomainSkillVariations.EXPECTED_SKILLS."
        )
        assert not missing_from_actual, (
            f"Agent(s) pinned in EXPECTED_SKILLS but not found on disk: "
            f"{missing_from_actual}. Either remove the entry or restore "
            f"the agent file."
        )

    def test_each_agent_has_exactly_expected_skills(self, agent_skills):
        """Each agent's frontmatter skills set must match EXPECTED_SKILLS
        exactly. Both directions: missing expected skills fail, and
        unexpected extra skills also fail. This tightness is intentional
        for documentation and catalog hygiene — adding or removing a skill
        from any agent requires a deliberate update to this test, which
        forces the change to be reviewed."""
        for name, expected in self.EXPECTED_SKILLS.items():
            actual = agent_skills.get(name, set())
            missing = expected - actual
            extra = actual - expected
            assert not missing, (
                f"{name}: missing expected frontmatter skills: {missing}. "
                f"Expected {expected}, got {actual}."
            )
            assert not extra, (
                f"{name}: unexpected extra frontmatter skills: {extra}. "
                f"Expected {expected}, got {actual}. If the new skill is "
                f"intentional, update EXPECTED_SKILLS to match."
            )


class TestAgentAutonomyCharterInline:
    """Post-#366, the autonomy charter content lives inline in each agent's
    body (not extracted to a shared skill). The pact-autonomy-charter skill
    was removed; the boilerplate is per-agent now so domain-specific authority
    extensions can be expressed naturally.

    These tests verify the inline content is present and substantive.
    """

    def test_autonomy_charter_section_present(self, teammate_agent_files):
        """Every agent must carry an AUTONOMY CHARTER section in its body."""
        for f in teammate_agent_files:
            text = f.read_text(encoding="utf-8")
            assert "AUTONOMY CHARTER" in text, (
                f"{f.name}: missing 'AUTONOMY CHARTER' section. Post-#366 "
                f"the autonomy charter is inline (not extracted)."
            )

    def test_autonomy_charter_contains_authority_clause(self, teammate_agent_files):
        """The inline charter should grant authority and define escalation."""
        for f in teammate_agent_files:
            text = f.read_text(encoding="utf-8")
            idx = text.find("AUTONOMY CHARTER")
            assert idx >= 0
            section = text[idx:idx + 2000]
            assert "authority" in section.lower(), (
                f"{f.name}: AUTONOMY CHARTER missing 'authority' clause. "
                f"Inline charter should grant the agent authority to act."
            )
            assert "escalate" in section.lower(), (
                f"{f.name}: AUTONOMY CHARTER missing 'escalate' clause. "
                f"Inline charter should define when to escalate."
            )

    def test_no_pact_autonomy_charter_skill_invocation(self, teammate_agent_files):
        """The pact-autonomy-charter skill no longer exists. Verify no agent
        references it via Skill() invocation."""
        for f in teammate_agent_files:
            text = f.read_text(encoding="utf-8")
            assert 'Skill("PACT:pact-autonomy-charter")' not in text, (
                f"{f.name}: still invokes pact-autonomy-charter skill which "
                f"was removed post-#366. The charter content is now inline."
            )

    def test_pact_autonomy_charter_skill_dir_absent(self):
        """The pact-autonomy-charter skill directory should be absent."""
        skill_dir = (
            Path(__file__).parent.parent / "skills" / "pact-autonomy-charter"
        )
        assert not skill_dir.exists(), (
            "pact-autonomy-charter/ skill directory still exists. It should "
            "have been removed post-#366 — the charter is now inline."
        )


class TestAgentAlgedonicTriggersInline:
    """Each agent's body should reference algedonic.md and document its
    domain-specific algedonic triggers inline. This complements the autonomy
    charter — algedonic authority is part of the charter conceptually but is
    typically formatted as its own subsection.
    """

    def test_algedonic_protocol_referenced(self, agent_files):
        """Every agent must point at the algedonic.md protocol."""
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            assert "algedonic.md" in text, (
                f"{f.name}: missing reference to algedonic.md. Every agent "
                f"must know where to find the full algedonic signal format."
            )

    def test_algedonic_signal_keyword_present(self, agent_files):
        """Every agent must mention HALT or ALERT — the two signal levels."""
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            has_halt_or_alert = "HALT" in text or "ALERT" in text
            assert has_halt_or_alert, (
                f"{f.name}: missing HALT/ALERT mention. Agents should know "
                f"the two algedonic signal levels they can emit."
            )


class TestNoVestigialAgentTeamsProtocolSection:
    """Post-#366 the `# AGENT TEAMS PROTOCOL` lazy-load pointer block is
    gone. The protocol is delivered via frontmatter eager-load instead.

    This is the inverse of the old TestAgentTeamsProtocolConsistency — we
    now ensure the section is ABSENT. A reintroduction would mean someone
    re-added the lazy-load indirection that #366 removed.
    """

    def test_no_agent_teams_protocol_heading(self, agent_files):
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            assert "AGENT TEAMS PROTOCOL" not in text, (
                f"{f.name}: contains a vestigial 'AGENT TEAMS PROTOCOL' "
                f"section. Post-#366 the lazy-load pointer block was "
                f"removed in favor of frontmatter eager-load."
            )

    def test_no_lazy_load_skill_invocation_for_agent_teams(self, agent_files):
        """No agent should invoke pact-agent-teams via Skill() — it's
        eager-loaded now."""
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            assert 'Skill("PACT:pact-agent-teams")' not in text, (
                f"{f.name}: still invokes pact-agent-teams via Skill(). "
                f"Post-#366 it is eager-loaded via frontmatter and should "
                f"not be lazy-invoked."
            )

