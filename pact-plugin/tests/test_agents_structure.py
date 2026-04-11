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
    Regression guards for #366 Phase 1 (kernel elimination).

    Post-#366, `pact-agent-teams` is back in agent frontmatter (eager-loaded
    at spawn) because the AGENT TEAMS PROTOCOL pointer block was removed —
    every agent's FIRST ACTION is `Skill("PACT:teammate-bootstrap")` which
    delivers the team protocol via @-references in the command body.

    Per-agent skill counts vary by domain: the n8n agent carries 10 skills
    (canonical 3 + all 7 n8n-specific skills), coders that handle multiple
    concerns carry 5 (canonical 3 + 2 domain skills), and most other
    specialists carry 4 (canonical 3 + 1 domain skill). Per-agent variation
    is the intentional design — there is no uniform cap on frontmatter skill
    count. Each agent's domain skill set is pinned individually in
    TestAgentDomainSkillVariations below.

    Note on eager-loading: as of the #366 Phase 1 empirical findings,
    frontmatter `skills:` entries are NOT eagerly loaded at agent spawn.
    They populate the lazy skill catalog (which materializes after the
    first Skill() invocation), so the per-agent variation is catalog
    metadata and discoverability, not a runtime cost. Adding skills to
    frontmatter is therefore safe at any cardinality.
    """

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


class TestRequiredSkillsCondensed:
    """Structural tests for the REQUIRED SKILLS section.

    The aspirational #366 S5 line-count and subsection-condensing tests were
    removed because Phase 1 did not include that condensing pass. What
    remains here are the structural invariants that hold regardless of
    condensing: every agent must still have a skill lookup table, and the
    secretary uses frontmatter skills.
    """

    # Secretary uses frontmatter skills, not REQUIRED SKILLS section
    _SECRETARY = "pact-secretary.md"

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
    # Measured in characters (not bytes). Bumped from 1500 to 2500 post-#366
    # because the skill was rewritten in command-style form (it now contains
    # the full SendMessage template, ordering rule, post-send behavior, and
    # consultant-question exception). The previous stub form was too terse to
    # function as the standalone teachback gate it now serves.
    MAX_SKILL_CHARS = 2500

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

    def test_bootstrap_has_exactly_eight_protocol_references(self):
        """bootstrap.md must contain EXACTLY 8 @${CLAUDE_PLUGIN_ROOT}/protocols/
        references — one per critical protocol, no duplicates, no extras.

        The presence check above confirms each of the 8 is mentioned at least
        once. This cardinality check catches the inverse failure mode: a
        stray duplicate, an accidentally-added 9th protocol reference, or a
        copy/paste that doubles an existing reference. Eager-load cost scales
        with reference count, so the 8-reference budget is deliberate.
        """
        import re
        text = self.BOOTSTRAP_PATH.read_text(encoding="utf-8")
        # Count every @${CLAUDE_PLUGIN_ROOT}/protocols/<anything>.md occurrence.
        # Uses a non-greedy match on the filename segment so each reference is
        # counted exactly once regardless of surrounding punctuation.
        pattern = re.compile(
            r"@\$\{CLAUDE_PLUGIN_ROOT\}/protocols/[A-Za-z0-9_.\-]+\.md"
        )
        matches = pattern.findall(text)
        assert len(matches) == len(self.CRITICAL_PROTOCOLS), (
            f"bootstrap.md must contain exactly "
            f"{len(self.CRITICAL_PROTOCOLS)} @${{CLAUDE_PLUGIN_ROOT}}/protocols/ "
            f"references (one per critical protocol). "
            f"Found {len(matches)}: {matches}"
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


class TestAgentFirstActionPrelude:
    """Every agent must lead its body with a `# FIRST ACTION` section that
    invokes `Skill("PACT:teammate-bootstrap")` before any other work.

    This is the load-bearing instruction that delivers the team protocol,
    teachback standards, and algedonic reference to spawned teammates. Drift
    here is silent — an agent without it would skip the bootstrap entirely
    and operate without team coordination context.
    """

    def test_first_action_section_present(self, agent_files):
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            assert "# FIRST ACTION" in text, (
                f"{f.name}: missing '# FIRST ACTION' section. Every agent "
                f"must lead with a FIRST ACTION block that invokes the "
                f"teammate bootstrap skill."
            )

    def test_first_action_invokes_teammate_bootstrap(self, agent_files):
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            assert 'Skill("PACT:teammate-bootstrap")' in text, (
                f"{f.name}: FIRST ACTION must invoke "
                f'`Skill("PACT:teammate-bootstrap")`. Without it the agent '
                f"would not load the team protocol."
            )

    def test_first_action_precedes_other_headings(self, agent_files):
        """The FIRST ACTION section must come before any other H1 in the body."""
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            # Strip frontmatter
            if text.startswith("---"):
                end = text.index("---", 3) + 3
                body = text[end:]
            else:
                body = text
            first_action_idx = body.find("# FIRST ACTION")
            assert first_action_idx >= 0
            # Find first H1 in body
            lines = body.split("\n")
            first_h1_line = None
            for i, line in enumerate(lines):
                if line.startswith("# ") and not line.startswith("## "):
                    first_h1_line = line
                    break
            assert first_h1_line == "# FIRST ACTION", (
                f"{f.name}: first H1 in body is {first_h1_line!r}, expected "
                f"'# FIRST ACTION'. The bootstrap invocation must precede "
                f"all other top-level sections."
            )

    def test_first_action_mentions_recovery_after_compaction(self, agent_files):
        """The FIRST ACTION should remind the agent to re-invoke bootstrap
        after compaction. This catches drift where the recovery hint gets
        deleted during agent edits."""
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            # Find FIRST ACTION block
            idx = text.find("# FIRST ACTION")
            assert idx >= 0
            # Look at the next ~400 chars
            section = text[idx:idx + 500]
            assert "compact" in section.lower(), (
                f"{f.name}: FIRST ACTION section should mention "
                f"compaction/re-invocation so agents recover after compact."
            )


class TestAgentFrontmatterSkills:
    """Every agent's frontmatter must eager-load the team protocol skills.

    Post-#366 the team protocol is delivered via frontmatter (eager) PLUS
    the teammate-bootstrap command (loaded via FIRST ACTION). This test
    pins the frontmatter contract: pact-agent-teams AND pact-teachback must
    both be present in skills:.

    Presence-only — no cardinality or exclusivity checks. Other skills may
    be added (e.g., the secretary's pact-memory + pact-handoff-harvest, the
    auditor's pact-architecture-patterns).
    """

    @pytest.fixture
    def agent_skills(self, agent_files):
        out = {}
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            out[f.stem] = TestLazyLoadedAgentTeams._extract_skill_names(text)
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
    def agent_skills(self, agent_files):
        out = {}
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            out[f.stem] = set(
                TestLazyLoadedAgentTeams._extract_skill_names(text)
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

    def test_autonomy_charter_section_present(self, agent_files):
        """Every agent must carry an AUTONOMY CHARTER section in its body."""
        for f in agent_files:
            text = f.read_text(encoding="utf-8")
            assert "AUTONOMY CHARTER" in text, (
                f"{f.name}: missing 'AUTONOMY CHARTER' section. Post-#366 "
                f"the autonomy charter is inline (not extracted)."
            )

    def test_autonomy_charter_contains_authority_clause(self, agent_files):
        """The inline charter should grant authority and define escalation."""
        for f in agent_files:
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

    def test_no_pact_autonomy_charter_skill_invocation(self, agent_files):
        """The pact-autonomy-charter skill no longer exists. Verify no agent
        references it via Skill() invocation."""
        for f in agent_files:
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


class TestTeammateBootstrapCommand:
    """The /PACT:teammate-bootstrap slash command is the teammate analog of
    /PACT:bootstrap. It is invoked by every spawned teammate as its FIRST
    ACTION (see TestAgentFirstActionPrelude) and must exist on disk with
    valid @-references to the team protocol skills and algedonic protocol.
    """

    PLUGIN_ROOT = Path(__file__).parent.parent
    COMMAND_PATH = PLUGIN_ROOT / "commands" / "teammate-bootstrap.md"

    # Required @-references — at least these four must be present so the
    # spawned teammate gets team-tools, teachback, on-demand context, and
    # algedonic content loaded into context at command-invocation time.
    REQUIRED_REFS = (
        "skills/pact-agent-teams/SKILL.md",
        "skills/pact-teachback/SKILL.md",
        "skills/request-more-context/SKILL.md",
        "protocols/algedonic.md",
    )

    def test_command_file_exists(self):
        assert self.COMMAND_PATH.exists(), (
            f"teammate-bootstrap.md not found at {self.COMMAND_PATH}"
        )
        assert self.COMMAND_PATH.is_file()

    def test_command_has_frontmatter_with_description(self):
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        assert fm is not None, (
            "teammate-bootstrap.md is missing YAML frontmatter"
        )
        assert "description" in fm and fm["description"].strip(), (
            "teammate-bootstrap.md frontmatter must contain a non-empty "
            "'description' field"
        )

    def test_command_contains_required_at_references(self):
        """Each required reference must appear via @${CLAUDE_PLUGIN_ROOT}/..."""
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        missing = []
        for ref in self.REQUIRED_REFS:
            expected = f"@${{CLAUDE_PLUGIN_ROOT}}/{ref}"
            if expected not in text:
                missing.append(expected)
        assert not missing, (
            f"teammate-bootstrap.md missing eager-load references: "
            f"{missing}. Required for the teammate to receive the team "
            f"protocol, teachback, request-more-context, and algedonic "
            f"content at command invocation."
        )

    def test_command_referenced_files_exist(self):
        """Each @-referenced file must exist on disk."""
        for ref in self.REQUIRED_REFS:
            path = self.PLUGIN_ROOT / ref
            assert path.exists(), (
                f"teammate-bootstrap.md references {ref} but the file "
                f"does not exist at {path}."
            )

    def test_command_contains_exactly_four_at_references(self):
        """Spec Section 6.8 requires exactly 4 @${CLAUDE_PLUGIN_ROOT}/ references
        in teammate-bootstrap.md — no more, no less.

        Presence-only checks (test_command_contains_required_at_references)
        would silently accept the addition of a 5th or 6th ref, which would
        cost every spawned teammate extra context tokens on every bootstrap
        call. The cardinality pin locks the eager-load footprint.
        """
        text = self.COMMAND_PATH.read_text(encoding="utf-8")
        count = text.count("@${CLAUDE_PLUGIN_ROOT}/")
        assert count == 4, (
            f"teammate-bootstrap.md must contain exactly 4 "
            f"@${{CLAUDE_PLUGIN_ROOT}}/ references (spec Section 6.8). "
            f"Found {count}. The eager-load footprint is load-bearing — "
            f"every spawned teammate pays the cost of each extra ref on "
            f"every invocation."
        )


class TestTeammateBootstrapRegisteredInPluginJson:
    """The teammate-bootstrap command must be registered in plugin.json so
    Claude Code exposes it as a slash command. Without registration the
    FIRST ACTION invocation in every agent would silently fail."""

    PLUGIN_JSON_PATH = (
        Path(__file__).parent.parent / ".claude-plugin" / "plugin.json"
    )

    def test_teammate_bootstrap_in_commands_list(self):
        import json

        assert self.PLUGIN_JSON_PATH.exists()
        data = json.loads(self.PLUGIN_JSON_PATH.read_text(encoding="utf-8"))
        commands = data.get("commands", [])
        assert isinstance(commands, list)
        registered = any(
            isinstance(entry, str)
            and entry.endswith("commands/teammate-bootstrap.md")
            for entry in commands
        )
        assert registered, (
            f"teammate-bootstrap.md not registered in plugin.json commands. "
            f"Found: {commands}"
        )


class TestBootstrapGuardSection:
    """Spec Section 8 requirement: bootstrap.md must open with a Bootstrap
    Guard section near the top of the file.

    The guard is an idempotency check — the lead re-invokes the bootstrap
    skill after compaction to reload protocols, and the guard tells the
    lead to short-circuit if the full orchestrator instructions are still
    in context. Without the guard the lead re-loads ~600 lines on every
    invocation, defeating the purpose of compaction-aware re-invocation.
    """

    BOOTSTRAP_PATH = (
        Path(__file__).parent.parent / "commands" / "bootstrap.md"
    )

    def test_bootstrap_md_exists(self):
        assert self.BOOTSTRAP_PATH.exists(), (
            f"bootstrap.md not found at {self.BOOTSTRAP_PATH}"
        )

    # Spec Section 6.9: the guard must list these recognition markers
    # verbatim so the lead can check its own context for their presence
    # before deciding whether to re-load the bootstrap.
    REQUIRED_RECOGNITION_MARKERS = (
        "S5 Non-Negotiables",
        "algedonic protocol",
        "HALT/ALERT",
        "communication charter",
        "variety assessment",
        "S4 checkpoint",
        "workflow command",
    )

    def test_bootstrap_guard_heading_present(self):
        """The literal `## Bootstrap Guard` heading must be in the file."""
        text = self.BOOTSTRAP_PATH.read_text(encoding="utf-8")
        assert "## Bootstrap Guard" in text, (
            "bootstrap.md is missing the `## Bootstrap Guard` heading. "
            "Spec Section 6.9 requires the guard for compaction-aware "
            "re-invocation."
        )

    def test_bootstrap_guard_within_first_30_lines(self):
        """Spec Section 6.9: the guard must appear within the first 30
        lines of the file so the lead encounters it before reading the
        main MISSION content on re-invocation."""
        text = self.BOOTSTRAP_PATH.read_text(encoding="utf-8")
        lines = text.splitlines()
        head = lines[:30]
        guard_found = any("## Bootstrap Guard" in line for line in head)
        assert guard_found, (
            f"bootstrap.md `## Bootstrap Guard` heading must appear within "
            f"the first 30 lines (spec Section 6.9). First 30 lines:\n"
            f"{chr(10).join(head)}"
        )

    def _guard_section(self, text: str) -> str:
        """Extract the Bootstrap Guard section body: everything from the
        `## Bootstrap Guard` heading to the next `---` horizontal rule
        (which in this file separates the guard from the main MISSION
        content).
        """
        start_marker = "## Bootstrap Guard"
        start_idx = text.find(start_marker)
        if start_idx == -1:
            return ""
        tail = text[start_idx:]
        end_idx = tail.find("\n---\n")
        if end_idx == -1:
            return "\n".join(tail.splitlines()[:40])
        return tail[:end_idx]

    def test_bootstrap_guard_contains_all_recognition_markers(self):
        """Spec Section 6.9: the guard must enumerate the specific
        recognition markers the lead looks for to decide whether the
        full orchestrator instructions are already loaded. Each marker
        must appear literally inside the guard section body, not merely
        somewhere else in the file.
        """
        text = self.BOOTSTRAP_PATH.read_text(encoding="utf-8")
        guard_body = self._guard_section(text)
        assert guard_body, (
            "Failed to extract the Bootstrap Guard section body from "
            "bootstrap.md — the `## Bootstrap Guard` heading may be "
            "missing or the section structure may have changed."
        )
        missing = [
            marker
            for marker in self.REQUIRED_RECOGNITION_MARKERS
            if marker not in guard_body
        ]
        assert not missing, (
            f"Bootstrap Guard section is missing required recognition "
            f"markers: {missing}. Spec Section 6.9 requires the guard "
            f"to enumerate these so the lead can check its own context "
            f"before re-loading. Guard section body was:\n{guard_body}"
        )


class TestDispatchTemplatePrelude:
    """Spec Section 8 requirement: the Agent Teams Dispatch template in
    bootstrap.md must embed the teammate bootstrap prelude inside the
    `prompt=` parameter.

    This is load-bearing because the dispatch template is what the lead
    reads when spawning a specialist — if the template is missing the
    `PACT ROLE: teammate (` marker or the `Skill("PACT:teammate-bootstrap")`
    call, spawned teammates will not self-bootstrap and will lack the
    team-protocol / teachback / algedonic context.
    """

    BOOTSTRAP_PATH = (
        Path(__file__).parent.parent / "commands" / "bootstrap.md"
    )

    def _dispatch_region(self, text: str) -> str:
        """Extract the region around the Agent Teams Dispatch callout.
        Returns the chunk starting at the MANDATORY callout and extending
        ~40 lines forward — enough to cover the dispatch pattern block.
        """
        marker = "MANDATORY"
        idx = text.find(marker)
        if idx == -1:
            return ""
        # Take ~80 lines of context after the marker to cover the
        # dispatch pattern block.
        tail = text[idx:]
        lines = tail.splitlines()[:80]
        return "\n".join(lines)

    def test_dispatch_template_contains_pact_role_teammate(self):
        """Spec Section 6.6 / Section 8: the dispatch template must
        contain the literal placeholder form `PACT ROLE: teammate ({name})`
        — not just the prefix. The `{name}` placeholder is load-bearing
        because at dispatch time the lead substitutes the teammate's
        actual name, which is what the routing block searches for and
        what appears in the spawned teammate's context.
        """
        text = self.BOOTSTRAP_PATH.read_text(encoding="utf-8")
        region = self._dispatch_region(text)
        assert region, (
            "bootstrap.md missing the Agent Teams Dispatch MANDATORY "
            "callout anchor."
        )
        assert "PACT ROLE: teammate ({name})" in region, (
            "Agent Teams Dispatch template in bootstrap.md must contain "
            "literal `PACT ROLE: teammate ({name})` (with the exact "
            "placeholder form) so the lead substitutes the teammate's "
            "name at dispatch time. Spec Section 6.6."
        )

    def test_dispatch_template_contains_teammate_bootstrap_skill_call(self):
        """The dispatch template shows Python source code, so the Skill call
        appears with backslash-escaped quotes inside the outer prompt="..."
        literal. Match the on-disk escaped form.
        """
        text = self.BOOTSTRAP_PATH.read_text(encoding="utf-8")
        region = self._dispatch_region(text)
        assert region, (
            "bootstrap.md missing the Agent Teams Dispatch MANDATORY "
            "callout anchor."
        )
        assert 'Skill(\\"PACT:teammate-bootstrap\\")' in region, (
            "Agent Teams Dispatch template in bootstrap.md must invoke "
            "`Skill(\\\"PACT:teammate-bootstrap\\\")` inside the prompt= "
            "parameter (escaped because the call is nested inside the "
            "outer Python prompt string literal). Spec Section 8."
        )

    def test_dispatch_template_prelude_inside_prompt_parameter(self):
        """Both markers must co-occur inside the `prompt=` parameter — not
        just anywhere in the file. The spec explicitly requires the
        prelude to be embedded in the dispatch prompt so the teammate
        sees it at spawn.

        The Skill call appears with backslash-escaped quotes because
        it is nested inside the outer Python prompt= string literal.
        """
        text = self.BOOTSTRAP_PATH.read_text(encoding="utf-8")
        region = self._dispatch_region(text)
        assert "prompt=" in region, (
            "Agent Teams Dispatch template in bootstrap.md must expose "
            "a `prompt=` parameter near the MANDATORY callout."
        )
        # Find the prompt= substring and walk forward to locate both
        # markers within the same prompt literal.
        prompt_idx = region.find("prompt=")
        prompt_tail = region[prompt_idx:]
        assert "PACT ROLE: teammate (" in prompt_tail, (
            "`PACT ROLE: teammate (` must appear inside the dispatch "
            "prompt= parameter, not merely elsewhere in bootstrap.md."
        )
        assert 'Skill(\\"PACT:teammate-bootstrap\\")' in prompt_tail, (
            "`Skill(\\\"PACT:teammate-bootstrap\\\")` must appear inside "
            "the dispatch prompt= parameter (escaped form — nested inside "
            "the outer Python prompt string literal), not merely elsewhere "
            "in bootstrap.md."
        )

    def test_dispatch_template_contains_recovery_after_compaction_language(self):
        """Spec Section 6.6: the dispatch template must include guidance
        telling the spawned teammate to re-invoke the teammate bootstrap
        if its context is compacted and the bootstrap content is no
        longer present. Without this language, teammates that get
        compacted mid-task lose their team-protocol / teachback /
        algedonic content and cannot recover it.

        Literal fragments asserted:
          - "compacted" — the trigger condition
          - "re-invoke" — the recovery action
        Both must appear inside the prompt= parameter, not just
        elsewhere in bootstrap.md.
        """
        text = self.BOOTSTRAP_PATH.read_text(encoding="utf-8")
        region = self._dispatch_region(text)
        prompt_idx = region.find("prompt=")
        assert prompt_idx != -1, (
            "Dispatch template missing prompt= parameter anchor."
        )
        prompt_tail = region[prompt_idx:]
        assert "compacted" in prompt_tail, (
            "Dispatch template in bootstrap.md is missing the "
            "compaction-trigger language ('compacted'). Spec Section 6.6 "
            "requires the template to tell spawned teammates what to do "
            "if their context is compacted."
        )
        assert "re-invoke" in prompt_tail, (
            "Dispatch template in bootstrap.md is missing the recovery "
            "action language ('re-invoke'). Spec Section 6.6 requires "
            "the template to tell spawned teammates to re-invoke the "
            "bootstrap skill after compaction."
        )
