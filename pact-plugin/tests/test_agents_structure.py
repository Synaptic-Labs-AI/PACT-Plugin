"""
Tests for agents/ directory structural validation.

Tests cover:
1. All agent files exist and are readable
2. YAML frontmatter is valid and contains required fields
3. Agent names follow pact-{role} convention
4. Required frontmatter keys: name, description
5. Skills reference exists
6. Agent body contains expected sections
"""
import re
from pathlib import Path

import pytest

from helpers import frontmatter_block, parse_frontmatter

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


class TestTeammateModelInheritance:
    """Every teammate agent def must pin `model: inherit` in frontmatter.

    Teammates are spawned via Agent() from the lead's session; without an
    explicit `model:` key the harness falls back to its own default model
    instead of the lead's, silently splitting the team across models.
    `inherit` resolves the lead's full model ID at spawn time.

    This is the positive half of a deliberate frontmatter asymmetry: the
    orchestrator is launched via `claude --agent` with no parent to inherit
    from, so pact-orchestrator.md must NOT carry the key — that negative
    half is enforced by test_pact_orchestrator_agent.py::
    test_pact_orchestrator_omits_model_permissionmode_tools.

    The value check is deliberately byte-form-strict: YAML-equivalent
    spellings such as a quoted "inherit" or a trailing comment fail by
    design (false-fail direction), enforcing one uniform authoring shape
    across all teammate defs.
    """

    def test_all_teammates_declare_model_inherit(self, teammate_agent_files):
        # Addition-ratchet: the set-equality leg alone already fails on a
        # missing/unexpected def and on a vacuous fixture glob. The `== 12`
        # literal is therefore not a vacuity guard — it exists to force a
        # conscious edit HERE when a teammate def is added, because
        # set-equality would silently auto-track an EXPECTED_AGENTS
        # addition.
        names = {f.stem for f in teammate_agent_files}
        assert names == TEAMMATE_AGENT_NAMES and len(names) == 12, (
            f"Expected exactly the 12 teammate agent defs, got {len(names)}: "
            f"{sorted(names)}. If a teammate def was deliberately added or "
            f"removed, update the count literal (and EXPECTED_AGENTS) with a "
            f"comment naming which def changed and why — this failure is the "
            f"ratchet prompting that conscious update, not a defect."
        )
        for f in teammate_agent_files:
            text = f.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            assert fm is not None, f"{f.name}: frontmatter failed to parse"
            fm_block = frontmatter_block(text)
            model_lines = [
                ln for ln in fm_block.splitlines()
                if ln.startswith("model:")
            ]
            assert len(model_lines) == 1, (
                f"{f.name}: expected exactly one `model:` line in "
                f"frontmatter, got {len(model_lines)} — duplicate keys "
                f"resolve last-wins and can silently shadow the intended "
                f"value"
            )
            assert fm.get("model") == "inherit", (
                f"{f.name}: frontmatter `model` must be 'inherit' so the "
                f"spawned teammate inherits the lead session's model "
                f"(got {fm.get('model')!r})"
            )


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
        try:
            fm_text = frontmatter_block(text)
        except ValueError:
            return []
        if fm_text is None:
            return []
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

    def test_frontmatter_includes_team_registration(self, all_agents):
        """Invocability insurance: every teammate-spawnable agent-def must list
        pact-team-registration in its skills: frontmatter so the spawn-prompt
        Invoke Skill("PACT:pact-team-registration") first-action directive is
        guaranteed discoverable/invocable — we do not assume a non-frontmatter
        skill is invocable. Frontmatter preloads NAME+DESCRIPTION only (the body
        still loads on the explicit invoke), so this does not re-trigger the
        per-spawn skill-overhead concern. Scoped to the teammates;
        pact-orchestrator (the lead, not spawned as a teammate) is excluded by
        the teammate_agent_files fixture."""
        for name, (_fm, _text, skill_names) in all_agents.items():
            assert "pact-team-registration" in skill_names, (
                f"{name}: missing pact-team-registration in skills: frontmatter. "
                f"The register first-action directive in the spawn prompt would "
                f"not be guaranteed invocable. Skills found: {skill_names!r}"
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
    # of the protocol surface. Earlier value (5000) accommodated the
    # 4-field structured payload (understanding / most_likely_wrong /
    # least_confident_item / first_action), the Task A / Task B dispatch
    # framing, the idle-on-awaiting_lead_completion contract, and the
    # Read-Trigger Precondition audit-anchor cross-ref added in v4.1.7
    # (load-bearing wake-signal SendMessage callout to anchor the
    # ordering invariant for the lead-side raw-metadata-read decision —
    # wording-reviewed and approved during the Bug C dispatch).
    #
    # Bumped to 7500 to accommodate reasoning_reconstruction field
    # schema + variety-band gate + L1.5 paragraph; budget ceiling
    # provides ~480-char headroom for future small edits.
    #
    # Bumped to 16000 to accommodate the schema-clarity restructure:
    # canonical-shape block at top of file (combined-payload reading
    # example showing both top-level metadata keys as siblings), four
    # inline anti-pattern callouts (variety_acknowledgment STRING shape,
    # reasoning_reconstruction in handoff slot, wrong sub-key names,
    # intentional_wait nested in teachback_submit), a four-row Common
    # Mistakes table whose rows align 1:1 with the runtime advisory rules
    # in task_lifecycle_gate.py, and a four-band threshold table folded
    # into the When-to-Method-Reconstruct section. Budget ceiling
    # provides ~620-char headroom for future small edits.
    #
    # Bumped to 17000 to accommodate the Step 1 shallow-merge callout:
    # TaskUpdate merges metadata at the top level only, so a partial write
    # to a nested sub-object replaces it and erases the omitted fields
    # silently. The callout states the mechanism, the one-call rule, the
    # re-send-the-full-object rule for a later correction, and the
    # read-back check. It also states that two different TOP-LEVEL keys do
    # not overwrite each other, which keeps a reader from "fixing" the
    # hazard by merging Step 1 and Step 3 into one call (Common Mistakes
    # row 4). Before this bump the file sat 22 chars below the 16000
    # ceiling, so no callout of any useful length fit. Budget ceiling
    # provides ~470-char headroom for future small edits.
    #
    # Bumped to 17500 to accommodate the dual-channel teachback notify:
    # the Step 2 lightweight-notice template became the canonical
    # payload carried verbatim — field-labeled single line inside
    # TEACHBACK-PAYLOAD-BEGIN/END delimiters, dotted nested keys for
    # variety_acknowledgment and reasoning_reconstruction, the
    # absent-optional omission note, and the one-line encoding
    # instruction under the fence. Measured size after the edit:
    # 17498 chars. Budget ceiling provides 2-char headroom.
    #
    # Bumped to 18000 to accommodate the payload read-back instruction
    # under the Step 2 template (one operative sentence: after writing,
    # read the task JSON back and confirm every field is present,
    # non-empty, and ends on its intended final content — a sender-side
    # output cut lands mid-JSON and surfaces as a write error). Measured
    # size after that instruction replaced the unmeasured "<5KB" bound:
    # 17834 chars. Budget ceiling provides ~166-char headroom.
    #
    # RAISED from 18000. The skill instructed the reader to read Task B's
    # `metadata.variety` and named NO mechanism, while `TaskGet` is
    # metadata-blind — so the variety-acknowledgment check the teachback
    # protocol REQUIRES was unperformable from the moment it shipped.
    # Naming the raw-JSON read is the minimum text that makes an existing
    # instruction executable, not new scope. Absorbing it was tried first
    # and recovered 21 chars by adopting this repo's own "metadata-blind"
    # phrasing; the rest would have come out of load-bearing text.
    # Measured after that addition: 18020 chars. The ceiling preserves the
    # 133-char headroom measured at this branch's base (17867 of 18000).
    #
    # Tighten-back trigger: if a future PR removes optional content
    # (e.g., if a future PR removes the transitional permissiveness
    # paragraph), reduce MAX_SKILL_CHARS to keep this budget a
    # meaningful ceiling and not a ratchet.
    #
    # RAISED 18347 -> 18600 for the background-wait line in Post-store
    # behavior. JUSTIFICATION, since this budget is a ceiling and not a
    # ratchet: the line is a one-sentence instruction placed in the file
    # teammates demonstrably load, addressing a failure mode where an agent
    # backgrounds work and ends the turn unflagged because it expects the
    # tool to wake it. The old ceiling had FOUR chars of headroom, so any
    # addition at all would have tripped it — the raise is not evidence the
    # addition was large. It was written twice: the first version cost 286
    # chars, was cut to 221, and the ceiling is set to the resulting size
    # plus 36 rather than to a round number, so the next addition is
    # deliberate rather than absorbed.
    # RAISED 18600 -> 18800 for the covers_since field the wait templates now carry (18722 then).
    MAX_SKILL_CHARS = 18800

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

    # Teachback MESSAGE-FORMAT / content-writing phrases the full teachback
    # protocol carries but a pointer-stub never would. If pact-agent-teams
    # contains any of these, the full protocol has leaked back into the stub
    # (the extraction is incomplete). These deliberately avoid the
    # intentional_wait / lifecycle vocabulary that pact-agent-teams
    # legitimately owns as a stub (awaiting_lead_completion, the acceptance
    # ordering TaskUpdate(A, status="completed")) — that territory is a muddy
    # discriminator; teachback message-authoring guidance is not.
    #
    # The markers are PARTITIONED into LIVE (present in the current protocol
    # source) and HISTORICAL (pre-extraction fossils that appear nowhere in the
    # current source). LIVE markers get BOTH guards — absent-from-stub AND
    # present-in-source (drift-rot: if a live marker's source phrasing drifts it
    # silently rots to a dead fossil and stops guarding, the exact failure the
    # canary refresh in this PR fixed). HISTORICAL markers get ONLY the absent-
    # from-stub guard — asserting their presence would fail, since they are
    # intentionally dead in the source and retained purely to detect a re-dump
    # of the OLD (pre-extraction) full protocol into the stub.

    # marker -> the full-protocol source file (relative to the plugin root) that
    # must contain it. pact-agent-teams stubs this source, so a full-protocol
    # re-dump into the stub would carry the marker. The "Building:" bullet is the
    # teachback-template line from pact-ct-teachback.md "Teachback Format" (also
    # byte-mirrored into pact-protocols.md); it replaced a dead non-blocking-era
    # fossil that appeared nowhere after the reconciliation to the blocking model.
    LIVE_PROTOCOL_MARKERS = {
        "Building: {what I understand I'm building}": "protocols/pact-ct-teachback.md",
    }

    # Pre-extraction phrasings that appear NOWHERE in the current source
    # (verified 0-hit). Retained purely as leak-detectors for a re-dump of the
    # OLD full protocol into the stub, so they are exempt from present-in-source.
    HISTORICAL_PROTOCOL_MARKERS = [
        "Send as your **first message**",
        "Keep concise: 3-6 bullet points",
    ]

    # Canonical union consumed by the absent-from-stub canary (behavior
    # unchanged). Deriving it from LIVE + HISTORICAL construction-enforces that
    # every marker is classified — a new marker cannot enter FULL without a
    # conscious live-vs-fossil decision (see test_protocol_markers_partition).
    FULL_PROTOCOL_MARKERS = list(LIVE_PROTOCOL_MARKERS) + HISTORICAL_PROTOCOL_MARKERS

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
        """T2: micro-skill must stay under MAX_SKILL_CHARS."""
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
        # Backticks stripped: required elements pin WORDS; the tool-name
        # backtick convention (e.g. `Edit`/`Write`/`Bash`) must not fail
        # the presence check.
        text = teachback_skill.read_text(encoding="utf-8").replace("`", "")
        for element in self.REQUIRED_PROTOCOL_ELEMENTS:
            assert element in text, (
                f"pact-teachback missing required protocol element: "
                f"{element!r}. The skill must contain the actual teachback "
                f"protocol, not just a pointer."
            )

    def test_teachback_skill_canonical_schema_at_a_glance_block(self, teachback_skill):
        """T3b: pin the `## Canonical schema at a glance` first-read anchor
        section + its JSON block content. The block is the load-bearing
        skim-target at the top of the skill — drift here silently degrades
        the pre-write learning surface for the 4 wrong-shape failure modes.

        Pins (anchored by line content, not row index):
          - Section header `## Canonical schema at a glance` exists
          - A fenced code block follows the section header before the next
            `##` section
          - All 5 canonical teachback_submit field names appear inside the
            block (understanding / most_likely_wrong / least_confident_item /
            first_action / variety_acknowledgment)
          - `intentional_wait` appears AFTER the teachback_submit close brace
            (sibling top-level metadata key, NOT nested), pinning the
            cross-key invariant the runtime advisory R10
            `intentional_wait_nested_in_teachback_submit` enforces
        """
        text = teachback_skill.read_text(encoding="utf-8")
        lines = text.splitlines()

        # Section header presence
        header_idx = next(
            (
                i for i, line in enumerate(lines)
                if line.strip() == "## Canonical schema at a glance"
            ),
            None,
        )
        assert header_idx is not None, (
            "pact-teachback SKILL.md missing `## Canonical schema at a glance` "
            "section header (first-read anchor for the canonical payload shape)."
        )

        # Slice the section body up to the next `## ` header
        body_lines = []
        for line in lines[header_idx + 1:]:
            if line.startswith("## "):
                break
            body_lines.append(line)
        body = "\n".join(body_lines)

        # Fenced code block present
        assert "```" in body, (
            "Canonical schema section missing a fenced code block — the "
            "first-read anchor must show the literal payload shape."
        )

        # All 5 canonical teachback_submit field names appear inside the block
        for field in (
            "understanding",
            "most_likely_wrong",
            "least_confident_item",
            "first_action",
            "variety_acknowledgment",
        ):
            assert field in body, (
                f"Canonical schema block missing required teachback_submit "
                f"field {field!r}. The 5-field shape is the load-bearing "
                f"first-read anchor; drift triggers wrong-shape failure modes."
            )

        # intentional_wait sibling-vs-nested invariant: the JSON block must
        # show intentional_wait AFTER teachback_submit's closing brace, not
        # inside it. Approximate via index ordering of two marker substrings
        # within the block body.
        tb_close_idx = body.find('"teachback_submit"')
        iw_idx = body.find('"intentional_wait"')
        assert tb_close_idx != -1, (
            "Canonical schema block missing teachback_submit key — block "
            "must show the canonical payload structure."
        )
        assert iw_idx != -1, (
            "Canonical schema block missing intentional_wait — the block "
            "must pin the sibling-top-level placement that "
            "is_self_complete_exempt requires."
        )
        assert iw_idx > tb_close_idx, (
            "Canonical schema block must place intentional_wait AFTER "
            "teachback_submit (sibling top-level key), not nested inside it. "
            "The runtime advisory `intentional_wait_nested_in_teachback_submit` "
            "fires on the nested placement; this block must teach the "
            "correct sibling shape."
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

    def test_live_protocol_markers_present_in_source(self):
        """T7 (drift-rot guard): each LIVE marker must still appear in its
        full-protocol source. A marker only asserted `not in stub` (the T5
        canary) silently rots to a dead fossil if the source phrasing drifts —
        it keeps passing while guarding nothing. That is exactly the failure the
        canary refresh in this PR corrected (the prior GEN-1 marker had rotted to
        a phrase present nowhere after the blocking reconciliation). Asserting
        present-in-source turns that silent rot into a red test.
        """
        plugin_root = Path(__file__).parent.parent
        for marker, rel_source in self.LIVE_PROTOCOL_MARKERS.items():
            source = plugin_root / rel_source
            assert source.is_file(), f"live-marker source missing: {rel_source}"
            text = source.read_text(encoding="utf-8")
            assert marker in text, (
                f"LIVE protocol marker {marker!r} no longer appears in its "
                f"source {rel_source} — it has rotted to a dead fossil and no "
                f"longer guards against a full-protocol dump. Refresh it to a "
                f"live phrase from the current protocol, or reclassify it as "
                f"HISTORICAL if the phrasing was intentionally removed."
            )

    def test_protocol_markers_partition(self):
        """T8 (classification guard): every FULL_PROTOCOL_MARKERS entry is
        classified as exactly one of LIVE (present-in-source, drift-rot-guarded)
        or HISTORICAL (fossil, absent-from-stub only). Forces a future marker
        addition to make the live-vs-fossil decision consciously; an unclassified
        live marker would silently skip the present-in-source guard.
        """
        live = set(self.LIVE_PROTOCOL_MARKERS)
        historical = set(self.HISTORICAL_PROTOCOL_MARKERS)
        assert live.isdisjoint(historical), (
            f"markers classified as BOTH live and historical: "
            f"{sorted(live & historical)}"
        )
        assert set(self.FULL_PROTOCOL_MARKERS) == live | historical, (
            "FULL_PROTOCOL_MARKERS must equal LIVE union HISTORICAL so every "
            "marker is classified; an unclassified marker would skip the "
            "present-in-source drift-rot guard"
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
            "pact-team-registration",
            "request-more-context",
            "pact-prepare-research",
        },
        "pact-architect": {
            "pact-agent-teams",
            "pact-teachback",
            "pact-team-registration",
            "request-more-context",
            "pact-architecture-patterns",
        },
        "pact-backend-coder": {
            "pact-agent-teams",
            "pact-teachback",
            "pact-team-registration",
            "request-more-context",
            "pact-coding-standards",
            "pact-security-patterns",
        },
        "pact-frontend-coder": {
            "pact-agent-teams",
            "pact-teachback",
            "pact-team-registration",
            "request-more-context",
            "pact-coding-standards",
            "pact-security-patterns",
        },
        "pact-database-engineer": {
            "pact-agent-teams",
            "pact-teachback",
            "pact-team-registration",
            "request-more-context",
            "pact-coding-standards",
        },
        "pact-devops-engineer": {
            "pact-agent-teams",
            "pact-teachback",
            "pact-team-registration",
            "request-more-context",
            "pact-coding-standards",
        },
        "pact-test-engineer": {
            "pact-agent-teams",
            "pact-teachback",
            "pact-team-registration",
            "request-more-context",
            "pact-testing-strategies",
        },
        "pact-qa-engineer": {
            "pact-agent-teams",
            "pact-teachback",
            "pact-team-registration",
            "request-more-context",
            "pact-testing-strategies",
        },
        "pact-security-engineer": {
            "pact-agent-teams",
            "pact-teachback",
            "pact-team-registration",
            "request-more-context",
            "pact-security-patterns",
        },
        "pact-n8n": {
            "pact-agent-teams",
            "pact-teachback",
            "pact-team-registration",
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
            "pact-team-registration",
            "request-more-context",
            "pact-architecture-patterns",
        },
        "pact-secretary": {
            "pact-agent-teams",
            "pact-teachback",
            "pact-team-registration",
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


# Bodies whose DATA-tier trigger is keyed on RECOVERABILITY. Deliberately not
# every agent: see TestDataTriggersKeyOnRecoverability for why
# pact-devops-engineer.md is excluded.
RECOVERABILITY_KEYED_AGENTS = ("pact-database-engineer", "pact-n8n")

# A line states a DATA-tier trigger if it carries one of these markers. The
# forms are conventional in the agent bodies: a `**HALT DATA**` bullet, and the
# `Read algedonic.md ...` line naming "a DATA risk" or "a DATA-integrity threat".
DATA_TRIGGER_MARKERS = ("HALT DATA", "DATA risk", "DATA-integrity")

# The property, expressed as two token families that must BOTH appear IN THE SAME
# CLAUSE. "rollback" is deliberately ABSENT from the recovery family: the
# pre-re-key wording already said "destructive operation without rollback", so
# accepting it would make this detector pass on the very text it exists to reject.
#
# WORD-ANCHORED, and that anchoring is the load-bearing half. A bare substring
# test counts "recover" inside "unrecoverable", which is how a trigger reading
# "...verify with the user before anything unrecoverable" was CERTIFIED as
# recoverability-keyed while keying on deployment status alone. `\b` refuses the
# match because "unrecoverable" has a word character before "recover".
RECOVERY_PATTERN = re.compile(r"\b(?:restor\w*|recover\w*|backup\w*)\b", re.IGNORECASE)
VERIFICATION_PATTERN = re.compile(r"\bverif\w*", re.IGNORECASE)

# Clause scope: a comma, semicolon, period, or spaced dash. Used INSTEAD of a
# character-distance window, so the boundary is a unit of meaning rather than a
# number fitted to whichever counter-example happened to be in hand.
CLAUSE_BOUNDARY = re.compile(r"[.;,]|\s--\s|—")

# Only a trigger about DESTROYING or irreversibly changing data owes a recovery
# criterion. A DATA trigger can legitimately be about EXPOSURE instead -- PII in
# build artifacts, sensitive data in container layers -- and demanding a restore
# path there would be nonsense. Without this gate the check below states a
# property that is merely ACCIDENTALLY true of the in-scope bodies, and the first
# exposure-only DATA bullet added to either would redden it on correct text.
DESTRUCTION_TOKENS = (
    "delete", "drop", "truncate", "destructive", "irreversible", "corrupt",
    "overwrite",
)


def _is_data_trigger(line: str) -> bool:
    """True if `line` states a DATA-tier algedonic trigger."""
    return any(marker in line for marker in DATA_TRIGGER_MARKERS)


def _names_destructive_operation(line: str) -> bool:
    """True if `line` describes destroying or irreversibly changing data."""
    lowered = line.lower()
    return any(token in lowered for token in DESTRUCTION_TOKENS)


def _keys_on_recoverability(line: str) -> bool:
    """True if one CLAUSE of `line` names a recovery criterion AND requires it
    be verified.

    Both halves are load-bearing. A recovery noun alone ("missing backup") says
    a backup is absent, not that its restorability was ever established;
    requiring the verification token is what makes UNKNOWN fail CLOSED into the
    trigger rather than out of it.

    ⚠️ WHAT THIS MEASURES IS SAME-CLAUSE ADJACENCY, NOT CO-REFERENCE, and the
    name of the thing matters more than the comfort of the name. Requiring the
    two tokens to share a clause makes it harder for unrelated words to combine
    into a false certificate, but NOTHING HERE ESTABLISHES THAT THE TWO TOKENS
    ARE ABOUT THE SAME DATA. Any clause carrying one of each satisfies this,
    whatever they refer to. Two accepted wordings, deliberately different in
    shape so the gap reads as a class rather than an edge case:

        "...PII stored unencrypted -- verify with the user before you restore
        anything"
        "...PII stored unencrypted -- always verify your rollback and restore
        procedure with the team lead"

    The second is ordinary database-engineering boilerplate a maintainer could
    add for reasons having nothing to do with this check, and its presence
    silently certifies a trigger keyed on deployment status. Note also that
    PARENTHESES ARE NOT IN `CLAUSE_BOUNDARY` AS IT CURRENTLY STANDS, so a
    parenthetical aside rides inside its host clause and can supply the second
    token from there -- and like the instance further down, that is a statement
    about the constant's PRESENT VALUE: widen `CLAUSE_BOUNDARY` and this example
    goes stale, while the adjacency point above is unaffected.

    THOSE WORDINGS ILLUSTRATE THE MECHANISM; THEY DO NOT SURVEY IT. Because the
    only thing established is adjacency, ANY clause pairing the two families
    passes, whatever the words are about -- so the accepted set is as wide as
    the set of clauses that happen to contain both, which is a fact about
    English prose rather than about this predicate.

    WHY CLAUSE SCOPE RATHER THAN A CHARACTER WINDOW. A distance threshold would
    have been fitted to whichever counter-example was in hand; a clause is a
    unit of meaning that exists independently of the example. Across the
    DATA-trigger lines PRESENT WHEN THIS WAS WRITTEN, clause scope and a
    character window agreed everywhere, so the split cost nothing measurable.
    That is a census rather than an invariant: a body added or a trigger
    reworded can separate them, and nothing reddens when it does. RE-MEASURE
    RATHER THAN TRUST IT.

    ⚠️ DO NOT DELETE THE CLAUSE SPLIT AS DEAD WEIGHT, and read this before
    concluding that it is. The word-anchoring alone rejects the wording that
    FIRST prompted this check, so measured against that one case the split
    changes nothing -- which is the observation that makes it look removable.

    IT IS NOT REMOVABLE. THE SPLIT REJECTS A SHAPE THE ANCHORING ACCEPTS: a
    genuine recovery token and a genuine verification token in DIFFERENT
    clauses, each unrelated to the other and to the trigger. Anchoring asks
    only whether both tokens appear somewhere in the line and passes; the split
    asks whether they share a clause and refuses. Delete the split and every
    line of that shape becomes a silent false certificate.

    An instance, SUBORDINATE TO THE SHAPE ABOVE -- if a later widening of
    `RECOVERY_PATTERN` or `CLAUSE_BOUNDARY` changes this example's verdict, the
    SHAPE is still the claim and the example is merely stale:

        "- **HALT DATA**: DROP TABLE on production data, PII stored
        unencrypted; the nightly backup runs at 02:00, and you should verify
        the migration plan with the DBA"

    "backup" lands in one clause and "verify" in the next.

    The reason that trade is worth taking is not this check specifically.
    Several false results here were caught by a REDUNDANT check disagreeing
    with a primary one rather than by any control: a search flag that silently
    matched nothing, a database cursor already consumed, a case-sensitivity
    flag off by one letter. In each of those THE CONTROLS THAT EXISTED did not
    catch it -- only two paths to the same answer disagreeing did. And in each
    of those the redundant path was the one that looked like duplication.

    A second path that NO TEST YET EXERCISES is not evidence it is useless: the
    instance above is a wording this split rejects and the anchoring accepts,
    demonstrated but not pinned. And a control only excludes what it was BUILT
    to exclude, so where the FIRST path is wrong in a way nobody anticipated,
    disagreement between two paths is what surfaces it.
    """
    return any(
        RECOVERY_PATTERN.search(clause) and VERIFICATION_PATTERN.search(clause)
        for clause in CLAUSE_BOUNDARY.split(line)
    )


# The DATA-trigger wordings as they read BEFORE the re-key, kept verbatim as
# literal fixtures. They are the non-vacuity control: the detector must RECOGNIZE
# each as a DATA trigger and REJECT it. Feeding them in as literals rather than
# reverting the working tree keeps the control available on every run, including
# runs where the tree is mid-edit.
PRE_REKEY_DATA_TRIGGERS = (
    "- **HALT DATA**: DELETE without WHERE clause, DROP TABLE on production "
    "data, PII stored unencrypted, foreign key violations risking data integrity",

    "Read [algedonic.md](../protocols/algedonic.md) immediately on detecting a "
    "DATA-integrity threat (destructive operation without rollback, schema "
    "violation, foreign-key breach, PII exposure in unencrypted columns or "
    "logs) or any irreversible change to production data flowing from a "
    "migration or query you are authoring.",

    "- **HALT DATA**: Workflow could corrupt or delete production data, PII "
    "handled without encryption",

    "Read [algedonic.md](../protocols/algedonic.md) immediately on detecting a "
    "SECURITY flaw (webhook accepting unauthenticated requests, credential "
    "exposure in workflow JSON or expressions, plaintext API key in HTTP node) "
    "or a DATA risk (irreversible workflow operation against production data, "
    "missing rate-limit on external write, destructive bulk update without "
    "dry-run).",
)


class TestDataTriggersKeyOnRecoverability:
    """A DATA-tier trigger must hand the agent a RECOVERABILITY criterion,
    not only a deployment-status one.

    THE DEFECT THIS CLOSES. `algedonic.md` states the DATA tier
    consequence-neutrally -- "PII exposure, data corruption risk, integrity
    violation". A body that glosses that tier as "production data" ALONE
    substitutes deployment status for the property that actually matters, and
    it misleads in BOTH directions: an agent working on an irreplaceable store
    that nobody calls "production" reads the trigger as not applying, while an
    agent working on production data with a tested restore path treats a HALT
    as automatic. Deployment status is not what makes an operation
    unrecoverable.

    WHAT IS PINNED IS THE PROPERTY, NOT THE PROSE. Any wording naming a
    recovery criterion and requiring it be VERIFIED satisfies this. A verbatim
    pin would redden on every innocent rewrite and would teach maintainers to
    bump a string instead of thinking about the trigger.

    THIS DOES NOT REQUIRE "production data" TO SURVIVE. A body that drops the
    phrase entirely and keys purely on recoverability still passes -- that is a
    correct trigger, and a detector that reddened on it would be worse than no
    detector.

    SCOPE, and why it is not every agent. `pact-devops-engineer.md` is
    deliberately excluded: for a devops agent a production deployment is a
    genuine domain concept, and its sibling clauses ("destructive infra change
    without rollback", "missing backup before migration") already carry the
    recoverability criterion, so deployment status is not the sole
    discriminator there. Forcing uniformity would make that body worse.

    ⚠️ THE DESTRUCTION GATE'S FAIL DIRECTION IS EXEMPT, stated rather than
    implied. `_names_destructive_operation` recognizes a fixed vocabulary, and
    a trigger that describes a destructive operation WITHOUT any of those words
    -- "schema change against production data" and "UPDATE without a WHERE
    clause" are measured examples -- is exempted from the recovery requirement
    rather than caught by it. The second example differs from a wording the gate
    DOES catch only in its VERB, since `delete` sits in DESTRUCTION_TOKENS above
    and `update` does not, which is why reaching this gap needs no unusual
    phrasing. That comparison is a statement about the CURRENT vocabulary: add
    `update` to that tuple and the example goes stale while the exemption itself
    stands. It replaces a claim about prose in another file, which could go
    equally false and could not be checked from here. That gate exists to stop false positives on exposure-only triggers, so
    its errors run toward silence by construction. What this class DOES
    guarantee is the thing it was built for: reverting either body's DATA
    trigger to the wording that keyed on deployment status alone turns it RED,
    verified per-file rather than only in combination. Widening the vocabulary
    to chase the gap would trade a bounded silence for unbounded false positives
    on correct bodies, which is the worse failure for a detector nobody is
    watching.

    HOW FAR THE SILENCE EXTENDS. A STRUCTURAL PROPERTY OF THE ASSERT CHAIN,
    true regardless of what any body contains: an escaping trigger stays silent
    ONLY while at least one SIBLING DATA trigger in the SAME FILE still
    classifies destructive. Once none does, `assert triggers` or `assert
    destructive` fires. So A FILE CANNOT GO FULLY DARK WITHOUT FAILING, and the
    bound is PER-FILE rather than per-site.

    WHAT THAT COSTS DEPENDS ON A COUNT, AND A COUNT IS NOT A PROPERTY -- stated
    separately because the two are true in different ways and the sentence that
    joined them lent the second the first one's authority. Each body in scope
    CURRENTLY carries TWO DATA-trigger sites, so one of the two may silently
    revert while the other holds the check up: a half-dark file rather than a
    dark one. That figure is a function of the site count. Add a third site and
    the arithmetic changes; remove one and the floor moves. RE-COUNT BEFORE
    RELYING ON THE "ONE OF TWO" FIGURE -- it describes today's two bodies, not
    the mechanism.

    ⚠️ A REFORMAT CAN DISARM THIS WITHOUT CHANGING A WORD OF MEANING, and it is
    the likeliest way the guard dies, because it requires no bad intent. Expand
    a `- **HALT DATA**:` bullet into a marker line plus sub-bullets and the
    marker line keeps the marker but loses every destruction token, while the
    sub-bullets carry no marker and are not triggers at all. Do that while
    KEEPING the correct recoverability wording and everything stays green, with
    no signal that the bullet is now unwatched -- then a later edit reverts the
    wording from that state and also stays green. Routine markdown housekeeping
    today, silent removal of the guard tomorrow. If you reformat a DATA trigger,
    re-read this class and confirm the marker line still carries the criterion.
    """

    def test_data_triggers_name_a_verified_recovery_criterion(self):
        """The live bodies satisfy the property."""
        for name in RECOVERABILITY_KEYED_AGENTS:
            path = AGENTS_DIR / f"{name}.md"
            triggers = [
                line for line in path.read_text(encoding="utf-8").splitlines()
                if _is_data_trigger(line)
            ]
            assert triggers, (
                f"{name}.md: no DATA-tier trigger line found at all. Either the "
                f"body lost its DATA trigger, or the marker convention changed "
                f"and DATA_TRIGGER_MARKERS no longer matches it -- in which case "
                f"this whole check has been silently measuring nothing."
            )
            destructive = [ln for ln in triggers if _names_destructive_operation(ln)]
            assert destructive, (
                f"{name}.md: DATA triggers exist but none describes destroying or "
                f"irreversibly changing data, so the requirement below is exempt "
                f"everywhere and this check is measuring nothing."
            )
            for line in destructive:
                assert _keys_on_recoverability(line), (
                    f"{name}.md: a DATA-tier trigger about destroying or "
                    f"irreversibly changing data names no VERIFIED recovery "
                    f"criterion, so it keys on deployment status alone. An agent "
                    f"holding irreplaceable data that nobody calls 'production' "
                    f"will read this as not applying to them. Name a recovery "
                    f"criterion and require it be verified, so an unverified "
                    f"restore path still fires the HALT. Offending line: {line!r}"
                )

    def test_detector_rejects_the_pre_rekey_wordings(self):
        """NON-VACUITY. The detector must reject the wordings it replaced.

        Each fixture is asserted to be IN SCOPE on BOTH gates first -- recognized
        as a DATA trigger, and recognized as describing a destructive operation.
        A fixture failing either gate would be "rejected" for the wrong reason:
        it would be exempt rather than caught, and the control would prove
        nothing about a revert it is supposed to stop.
        """
        for wording in PRE_REKEY_DATA_TRIGGERS:
            assert _is_data_trigger(wording), (
                f"control fixture is not recognized as a DATA trigger, so its "
                f"rejection below would be vacuous: {wording!r}"
            )
            assert _names_destructive_operation(wording), (
                f"control fixture is not recognized as describing a destructive "
                f"operation, so the recovery requirement would EXEMPT it rather "
                f"than catch it, and its rejection below would be vacuous: "
                f"{wording!r}"
            )
            assert not _keys_on_recoverability(wording), (
                f"the detector ACCEPTS a pre-re-key wording that keys on "
                f"deployment status alone. It would not catch a revert, which "
                f"is the only thing it exists to catch: {wording!r}"
            )

    def test_detector_does_not_certify_a_deployment_keyed_trigger(self):
        """A FALSE GREEN ON A TRUE NEGATIVE IS NOT A SILENCE.

        The destruction gate's exemption licenses the detector to MISS a bad
        trigger it cannot classify. It does not license the detector to
        AFFIRMATIVELY CERTIFY one. This wording keys on deployment status alone
        and was certified as recoverability-keyed, because a substring test
        found "recover" inside "unrecoverable" and paired it with an unrelated
        "verify" from a different clause.

        Both halves of the repair are exercised here: word-anchoring refuses
        "recover" inside "unrecoverable", and clause scope refuses to pair
        tokens across a boundary.
        """
        certified_but_deployment_keyed = (
            "- **HALT DATA**: DELETE without WHERE clause, DROP TABLE on "
            "production data, PII stored unencrypted -- verify with the user "
            "before anything unrecoverable"
        )
        assert _is_data_trigger(certified_but_deployment_keyed)
        assert _names_destructive_operation(certified_but_deployment_keyed), (
            "fixture is not classified destructive, so the recovery requirement "
            "would EXEMPT it and this control would prove nothing"
        )
        assert not _keys_on_recoverability(certified_but_deployment_keyed), (
            "the detector CERTIFIES a trigger that keys on deployment status "
            "alone. Missing a bad trigger is the ruled-acceptable failure; "
            "green-lighting one is not."
        )

    def test_recovery_token_is_word_anchored(self):
        """The specific mechanism, pinned apart from the wording it defeats.

        "unrecoverable" must not supply a recovery token. Kept separate from the
        test above so a regression names the CAUSE rather than only the symptom.
        """
        assert not RECOVERY_PATTERN.search("anything unrecoverable")
        assert RECOVERY_PATTERN.search("a verified restore path")
        assert RECOVERY_PATTERN.search("recoverable within an hour"), (
            "word-anchoring must reject 'unrecoverable' WITHOUT also rejecting "
            "legitimate recovery vocabulary that merely shares a stem"
        )

    def test_exposure_only_data_triggers_are_exempt(self):
        """A DATA trigger about EXPOSURE owes no recovery criterion.

        This exemption is not hypothetical. `pact-devops-engineer.md` carries
        exactly this shape -- a DATA trigger about PII in build artifacts and
        sensitive data in container layers, where a restore path is a category
        error. Without the destruction gate, the requirement would state a
        property that is only ACCIDENTALLY true of the in-scope bodies, and the
        first exposure-only DATA bullet added to either would redden a correct
        body.
        """
        exposure_only = (
            "- **HALT DATA**: PII in build artifacts, sensitive data exposed "
            "through container layers"
        )
        assert _is_data_trigger(exposure_only)
        assert not _names_destructive_operation(exposure_only)
        assert not _keys_on_recoverability(exposure_only), (
            "fixture accidentally satisfies the recovery predicate, so it "
            "cannot demonstrate that the destruction gate is what exempts it"
        )

    def test_rollback_alone_does_not_satisfy_the_predicate(self):
        """The recovery family excludes 'rollback' on purpose.

        One pre-re-key wording already said "destructive operation without
        rollback". Had the family accepted that token, the detector would have
        passed on unmodified deployment-keyed text -- non-vacuous-looking and
        wrong. This pins the exclusion so a later widening of RECOVERY_PATTERN
        has to confront it.
        """
        assert not _keys_on_recoverability(
            "- **HALT DATA**: destructive operation without rollback"
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

