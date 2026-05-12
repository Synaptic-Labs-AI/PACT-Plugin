"""
Tests for commands/ directory structural validation.

Tests cover:
1. All expected command files exist
2. YAML frontmatter is valid with required fields
3. Frontmatter has description field
4. Command body contains substantive content
5. Commands reference $ARGUMENTS where appropriate
6. AskUserQuestion option labels and counts in wrap-up.md and peer-review.md
"""
import re
from pathlib import Path

import pytest

from helpers import parse_frontmatter

COMMANDS_DIR = Path(__file__).parent.parent / "commands"

EXPECTED_COMMANDS = {
    "bootstrap",
    "comPACT",
    "imPACT",
    "orchestrate",
    "pause",
    "peer-review",
    "pin-memory",
    "plan-mode",
    "rePACT",
    "telegram-setup",
    "wrap-up",
}


@pytest.fixture
def command_files():
    """Load all command markdown files."""
    return list(COMMANDS_DIR.glob("*.md"))


class TestCommandFilesExist:
    def test_commands_directory_exists(self):
        assert COMMANDS_DIR.is_dir()

    def test_all_expected_commands_present(self, command_files):
        names = {f.stem for f in command_files}
        for expected in EXPECTED_COMMANDS:
            assert expected in names, f"Missing command: {expected}"


class TestCommandFrontmatter:
    def test_all_have_frontmatter(self, command_files):
        for f in command_files:
            text = f.read_text(encoding="utf-8")
            assert text.startswith("---"), f"{f.name} missing YAML frontmatter"

    def test_has_description(self, command_files):
        for f in command_files:
            text = f.read_text(encoding="utf-8")
            fm = parse_frontmatter(text)
            assert fm is not None, f"{f.name} has invalid frontmatter"
            assert "description" in fm, f"{f.name} missing description"
            assert len(fm["description"]) > 0, f"{f.name} has empty description"


class TestCommandBody:
    def test_has_substantive_content(self, command_files):
        for f in command_files:
            text = f.read_text(encoding="utf-8")
            # After frontmatter, check body
            _, _, body = text.partition("---")
            _, _, body = body.partition("---")
            assert len(body.strip()) > 50, f"{f.name} body too short"

    def test_orchestrate_references_arguments(self, command_files):
        for f in command_files:
            if f.stem == "orchestrate":
                text = f.read_text(encoding="utf-8")
                assert "$ARGUMENTS" in text, "orchestrate.md should reference $ARGUMENTS"


def _extract_option_labels(text):
    """Extract AskUserQuestion option labels from **"Label"** pattern."""
    return re.findall(r'\*\*"([^"]+)"\*\*', text)


class TestAskUserQuestionOptions:
    """Validate AskUserQuestion option labels and counts in session-decision commands."""

    @pytest.fixture
    def wrapup_content(self):
        return (COMMANDS_DIR / "wrap-up.md").read_text(encoding="utf-8")

    @pytest.fixture
    def peer_review_content(self):
        return (COMMANDS_DIR / "peer-review.md").read_text(encoding="utf-8")

    # --- wrap-up.md Step 8 ---

    def test_wrapup_has_four_options(self, wrapup_content):
        """Step 8 session decision has 4 options."""
        # Extract only from the Session Decision section (after "Session Decision")
        session_section = wrapup_content.split("Session Decision")[1]
        labels = _extract_option_labels(session_section)
        assert len(labels) == 4, f"wrap-up.md session decision should have 4 options, found {len(labels)}: {labels}"

    def test_wrapup_yes_continue_option(self, wrapup_content):
        assert '"Yes, continue"' in wrapup_content

    def test_wrapup_pause_option(self, wrapup_content):
        assert '"Pause work for now"' in wrapup_content

    def test_wrapup_no_end_session_option(self, wrapup_content):
        assert '"No, end session"' in wrapup_content

    def test_wrapup_graceful_end_session_option(self, wrapup_content):
        assert '"End session (graceful)"' in wrapup_content

    def test_wrapup_pause_invokes_pause_command(self, wrapup_content):
        """Pause option should invoke /PACT:pause."""
        assert "/PACT:pause" in wrapup_content

    # --- peer-review.md step 6 ---

    def test_peer_review_has_three_merge_options(self, peer_review_content):
        """Step 6 merge authorization has 3 options."""
        # Extract only from the merge authorization section (after "Merge Authorization")
        merge_section = peer_review_content.split("Merge Authorization")[1]
        labels = _extract_option_labels(merge_section)
        assert len(labels) == 3, (
            f"peer-review.md merge section should have 3 options, found {len(labels)}: {labels}"
        )

    def test_peer_review_yes_merge_option(self, peer_review_content):
        assert '"Yes, merge"' in peer_review_content

    def test_peer_review_continue_reviewing_option(self, peer_review_content):
        assert '"Continue reviewing"' in peer_review_content

    def test_peer_review_pause_option(self, peer_review_content):
        assert '"Pause work for now"' in peer_review_content

    # --- Shared Pause option consistency ---

    def test_pause_option_label_consistent(self, wrapup_content, peer_review_content):
        """Both commands should use the same Pause option label."""
        label = "Pause work for now"
        assert label in wrapup_content, "wrap-up.md missing shared Pause label"
        assert label in peer_review_content, "peer-review.md missing shared Pause label"

    def test_pause_description_consistent(self, wrapup_content, peer_review_content):
        """Both commands should use the same Pause description."""
        desc = "Save session knowledge and pause"
        assert desc in wrapup_content, "wrap-up.md missing shared Pause description"
        assert desc in peer_review_content, "peer-review.md missing shared Pause description"


CONSUMER_COMMANDS = [
    "orchestrate",
    "peer-review",
    "comPACT",
    "rePACT",
    "plan-mode",
]

# Canonical-form components that must appear in at least one consumer command
# under v4.0.0. The PACT ROLE marker is structural (load-bearing for
# session_init detection); the team-join note orients the spawned teammate.
# The teachback directive is intentionally absent from this list — under
# v4.0.0 teachback is delivered via the spawn-time skills: frontmatter
# (pact-teachback skill), not a per-prompt instruction.
CANONICAL_FORM_COMPONENTS = [
    ("PACT_ROLE_marker", "YOUR PACT ROLE: teammate ("),
    ("team_join_note", "joining team"),
    ("teachback_gated_anchor", "Teachback-Gated Dispatch"),
    ("addBlockedBy_call", "addBlockedBy"),
]


class TestPactRoleTeammateInConsumerCommandsByFile:
    """Class A: parametrize over consumer-command files.

    Diagnostic axis = WHICH FILE leaked. Each consumer command file
    (orchestrate, peer-review, comPACT, rePACT, plan-mode) must contain the
    canonical YOUR PACT ROLE: teammate ( marker. A test failure points
    directly at the file that lost the marker, regardless of which canonical-
    form component drifted.
    """

    @pytest.mark.parametrize("name", CONSUMER_COMMANDS)
    def test_contains_canonical_pact_role(self, name):
        path = COMMANDS_DIR / f"{name}.md"
        assert path.exists(), f"Consumer command file missing: {name}.md"
        text = path.read_text(encoding="utf-8")
        assert "YOUR PACT ROLE: teammate (" in text, (
            f"{name}.md must contain canonical 'YOUR PACT ROLE: teammate (' "
            "marker — load-bearing for the routing chain that detects a "
            "teammate spawn at session_init time."
        )


class TestPactRoleTeammateInConsumerCommandsByComponent:
    """Class B: parametrize over canonical-form components.

    Diagnostic axis = WHICH COMPONENT of the canonical dispatch form leaked.
    For each canonical-form component (PACT ROLE marker, team-join note,
    Teachback-Gated Dispatch anchor, addBlockedBy call), assert it appears in at least one
    consumer command. Together with Class A (file axis), Class A failures
    isolate the file while Class B failures isolate the component —
    independent diagnostic signal.
    """

    @pytest.mark.parametrize("label,substr", CANONICAL_FORM_COMPONENTS)
    def test_canonical_form_component_present_in_at_least_one_consumer(
        self, label, substr
    ):
        bodies = {
            name: (COMMANDS_DIR / f"{name}.md").read_text(encoding="utf-8")
            for name in CONSUMER_COMMANDS
        }
        if not any(substr in body for body in bodies.values()):
            pytest.fail(
                f"canonical-form component {label!r} (substring {substr!r}) "
                f"not present in any consumer command file. The component is "
                f"load-bearing for the dispatch contract; if no file carries "
                f"it, every spawned teammate loses the corresponding signal."
            )


class TestNoFirstActionFossilInConsumerCommands:
    """Negative-invariant fossilization guard: consumer command dispatch-prompt
    templates must not contain the v3.x FIRST-ACTION + Skill("PACT:teammate-bootstrap")
    pattern. The skill was deleted in C9; any surviving invocation produces
    teammates whose first tool call dead-ends. Guards the post-C5b state.
    """

    FORBIDDEN_FOSSIL_PATTERNS = (
        "YOUR FIRST ACTION (YOU MUST DO THIS IMMEDIATELY)",
        'Skill(\\"PACT:teammate-bootstrap\\")',
        'Skill("PACT:teammate-bootstrap")',
    )

    @pytest.mark.parametrize("name", CONSUMER_COMMANDS)
    def test_no_first_action_fossil_in_dispatch_prompt(self, name):
        path = COMMANDS_DIR / f"{name}.md"
        text = path.read_text(encoding="utf-8")
        offenders = [p for p in self.FORBIDDEN_FOSSIL_PATTERNS if p in text]
        assert not offenders, (
            f"{name}.md contains v3.x dispatch-prompt fossil(s): {offenders}. "
            f"The bootstrap command was deleted in C9; any surviving "
            f"invocation directs spawned teammates at a non-existent slash "
            f"command and silently fails."
        )


class TestImperativeSoftPhrasingConvention:
    """v4.0.0 lazy-load cross-reference convention guard.

    Orchestrator agent body uses two phrasing templates, both built around
    the unified tool-call shape Read(file_path="../protocols/<file>.md"):

      IMPERATIVE: **You MUST Read(file_path="...") before answering** whenever <trigger>
        for decision-blocking protocols. Compels Read on trigger detection.

      SOFT: For full detail, Read(file_path="...") when <use case>
        for reference-only protocols. Read fires on operator demand.

    Both forms include the explicit file_path= kwarg to match the actual
    Read tool's call signature for maximum tool-call pattern recognition.

    The IMPERATIVE form is reinforced by a top-of-body pre-commitment
    paragraph (file-anchored, with scope-boundary defensive clause) that
    compels compliance with each "You MUST" instruction below.
    """

    AGENTS_DIR = Path(__file__).parent.parent / "agents"
    ORCHESTRATOR_PATH = AGENTS_DIR / "pact-orchestrator.md"

    IMPERATIVE_PROTOCOLS = [
        "algedonic.md",
        "pact-s4-tension.md",
        "pact-s5-policy.md",
        "pact-state-recovery.md",
        "pact-completion-authority.md",
    ]

    SOFT_PROTOCOLS = [
        "pact-variety.md",
        "pact-s4-checkpoints.md",
        "pact-workflows.md",
        "pact-communication-charter.md",
    ]

    @pytest.mark.parametrize("protocol", IMPERATIVE_PROTOCOLS)
    def test_imperative_protocol_uses_imperative_phrasing(self, protocol):
        text = self.ORCHESTRATOR_PATH.read_text(encoding="utf-8")
        tool_call = f'Read(file_path="../protocols/{protocol}")'
        assert tool_call in text, (
            f"pact-orchestrator.md missing tool-call cross-reference to {protocol}."
        )
        idx = 0
        found_imperative = False
        while True:
            i = text.find(tool_call, idx)
            if i == -1:
                break
            window_before = text[max(0, i - 30):i]
            window_after = text[i + len(tool_call):i + len(tool_call) + 60]
            if "You MUST" in window_before and "before answering" in window_after:
                found_imperative = True
                break
            idx = i + 1
        assert found_imperative, (
            f"pact-orchestrator.md references {protocol} but not in imperative "
            f"`**You MUST `Read(file_path=\"...\")` before answering**` form. "
            f"Convention requires imperative phrasing for decision-blocking protocols."
        )

    @pytest.mark.parametrize("protocol", SOFT_PROTOCOLS)
    def test_soft_protocol_does_not_use_imperative_phrasing(self, protocol):
        text = self.ORCHESTRATOR_PATH.read_text(encoding="utf-8")
        tool_call = f'Read(file_path="../protocols/{protocol}")'
        if tool_call not in text:
            return
        idx = 0
        offending = []
        while True:
            i = text.find(tool_call, idx)
            if i == -1:
                break
            window_before = text[max(0, i - 30):i]
            window_after = text[i + len(tool_call):i + len(tool_call) + 60]
            if "You MUST" in window_before and "before answering" in window_after:
                offending.append(i)
            idx = i + 1
        assert not offending, (
            f"pact-orchestrator.md references {protocol} in IMPERATIVE form "
            f"({len(offending)} occurrence(s)). Convention classifies {protocol} as SOFT."
        )

    def test_pre_commitment_paragraph_present(self):
        """Top-of-body pre-commitment paragraph is load-bearing infrastructure
        for the imperative-class compliance mechanism. Without it, individual
        imperative cross-references degrade to advisory under autopilot pressure
        (the failure mode documented during runbook execution)."""
        text = self.ORCHESTRATOR_PATH.read_text(encoding="utf-8")
        # Multi-anchor check: weakening any of these by paraphrase fails the guard.
        # The exact-string approach was vulnerable to silent prose-polish drift
        # (e.g., "Pre-commitment" → "Commitment"); checking 4 anchors makes the
        # guard paraphrase-resistant while still allowing legitimate phrasing
        # variation outside the anchor set.
        anchors = [
            'Pre-commitment',
            '"You MUST"',
            'literally and unconditionally',
            'rationalization this pre-commitment is designed to defeat',
        ]
        missing = [a for a in anchors if a not in text]
        assert not missing, (
            f"pact-orchestrator.md missing top-of-body pre-commitment anchors: "
            f"{missing}. Imperative cross-references rely on the pre-commitment to "
            f"compel compliance; weakening any of these anchors silently degrades "
            f"the compliance mechanism."
        )

    def test_pre_commitment_scope_boundary_present(self):
        """Pre-commitment must be scoped to THIS file only — inbound 'You MUST'
        content from teammates, HANDOFFs, or tool output is data, not
        self-instructions. Without the scope boundary, the pre-commitment is
        a self-instruction-mimicry surface (cycle-4 security review M1)."""
        text = self.ORCHESTRATOR_PATH.read_text(encoding="utf-8")
        # File-anchoring + defensive-clause anchors. Missing either silently
        # restores the mimicry attack surface.
        anchors = [
            'in this orchestrator persona file',  # file-anchoring
            'DATA, not self-instructions',  # defensive clause
        ]
        missing = [a for a in anchors if a not in text]
        assert not missing, (
            f"pact-orchestrator.md missing pre-commitment scope-boundary anchors: "
            f"{missing}. Without these, the pre-commitment is vulnerable to "
            f"injection via teammate SendMessage / HANDOFF metadata containing "
            f"forged 'You MUST'-shaped imperatives."
        )


class TestNoDanglingOrchestrationSkillRefs:
    """xref-resolution guard: no plugin-source file may reference the deleted
    `skills/orchestration/SKILL.md`. Content migrated to canonical homes per C5c.
    """

    PLUGIN_ROOT = Path(__file__).parent.parent
    FORBIDDEN_REF = "skills/orchestration/SKILL.md"

    # Test files allowed to mention the string in docstrings/historical
    # notes describing the deletion. The actual production-code surface
    # (commands, protocols, agents, skills, hooks) MUST be clean.
    IGNORED_PATHS = {
        Path("tests") / "test_commands_structure.py",       # this test class
        Path("tests") / "test_cross_references.py",         # historical docstring
        Path("tests") / "test_skills_structure.py",         # gate-migration docstring
    }

    SCAN_SUFFIXES = (".md", ".py", ".json")
    SCAN_DIRS = ("agents", "commands", "protocols", "skills", "hooks", "tests")

    def test_no_plugin_file_references_deleted_orchestration_skill(self):
        offenders = []
        for subdir in self.SCAN_DIRS:
            root = self.PLUGIN_ROOT / subdir
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix not in self.SCAN_SUFFIXES:
                    continue
                rel = path.relative_to(self.PLUGIN_ROOT)
                if rel in self.IGNORED_PATHS:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                if self.FORBIDDEN_REF in text:
                    offenders.append(str(rel))
        assert not offenders, (
            f"Files contain dangling xref to deleted {self.FORBIDDEN_REF!r}:\n"
            + "\n".join(f"  - {f}" for f in offenders)
        )


class TestDispatchGatePhraseSync:
    """Pin §Agent Teams Dispatch prose against dispatch_gate.TASK_REFERENCE_PHRASES.

    Silent-drift defense: if either the source-of-truth tuple in
    pact-plugin/hooks/dispatch_gate.py or the persona §Agent Teams Dispatch
    section in pact-plugin/agents/pact-orchestrator.md is edited without the
    other, this test fails. The two surfaces must enumerate the same accepted
    phrases — the persona prose tells the orchestrator which phrases satisfy
    the inline-mission heuristic, the runtime tuple decides what actually
    counts.
    """

    PERSONA_FILE = COMMANDS_DIR.parent / "agents" / "pact-orchestrator.md"

    def _agent_teams_dispatch_section(self) -> str:
        """Return the body of the §Agent Teams Dispatch section.

        Section-scope (not step-5-paragraph-scope): tolerates phrase
        enumeration moving within the section while still catching deletion
        of any phrase from the section entirely.
        """
        text = self.PERSONA_FILE.read_text(encoding="utf-8")
        start_match = re.search(r"^## \d+\. Agent Teams Dispatch\b", text, re.MULTILINE)
        assert start_match is not None, (
            "pact-orchestrator.md missing §Agent Teams Dispatch section header"
        )
        start = start_match.start()
        end_match = re.search(r"^## \d+\. ", text[start_match.end():], re.MULTILINE)
        end = start_match.end() + (end_match.start() if end_match else len(text) - start_match.end())
        return text[start:end]

    def test_persona_section_contains_all_runtime_phrases(self):
        import dispatch_gate

        runtime_phrases = getattr(dispatch_gate, "TASK_REFERENCE_PHRASES")
        assert isinstance(runtime_phrases, tuple) and runtime_phrases, (
            "dispatch_gate.TASK_REFERENCE_PHRASES must be a non-empty tuple"
        )

        section = self._agent_teams_dispatch_section()
        missing = [p for p in runtime_phrases if p not in section]
        assert not missing, (
            "Persona §Agent Teams Dispatch section is missing phrase(s) from "
            f"dispatch_gate.TASK_REFERENCE_PHRASES: {missing!r}. Update either "
            "pact-plugin/agents/pact-orchestrator.md §Agent Teams Dispatch "
            "step 5 prose or pact-plugin/hooks/dispatch_gate.py "
            "TASK_REFERENCE_PHRASES so they enumerate the same accepted phrases."
        )


class TestPerLoopDispatchSites:
    """Structural pin for the 9 per-loop dispatch sites across command files.

    Each site is identified by the literal lead-in
    `follow the steps for [Teachback-Gated Dispatch]`. The block following
    the lead-in must contain the canonical Teachback-Gated Dispatch shape:
    at least two TaskCreate calls, at least two TaskUpdate calls, and at
    least one Agent( spawn. Substring counting (not strict regex) tolerates
    minor stylistic edits while still catching real drift such as a missing
    TaskCreate, a single TaskUpdate, or an omitted Agent spawn.
    """

    LEAD_IN = "follow the steps for [Teachback-Gated Dispatch]"

    # 9 per-loop dispatch sites. Each entry is
    # (relative_command_path, lead_in_line_number_1based, role_or_phase_label).
    SITES = [
        ("orchestrate.md", 449, "PREPARE"),
        ("orchestrate.md", 544, "ARCHITECT"),
        ("orchestrate.md", 667, "CODE"),
        ("orchestrate.md", 802, "TEST"),
        ("comPACT.md", 209, "MultipleSpecialists"),
        ("comPACT.md", 253, "SingleSpecialist"),
        ("peer-review.md", 173, "Reviewers"),
        ("plan-mode.md", 218, "Consultants"),
        ("rePACT.md", 244, "SubScopeSpecialists"),
    ]

    @staticmethod
    def _site_block(text: str, lead_in_line: int) -> str:
        """Return the block of lines following the lead-in.

        Reads up to 40 lines after the lead-in (1-based). The canonical
        Teachback-Gated Dispatch sequence fits comfortably within that
        window across all observed sites; oversized windows that span into
        adjacent sections produce false positives, undersized windows miss
        steps. 40 lines is empirically sufficient.
        """
        lines = text.splitlines()
        start = lead_in_line  # 0-based index for the line AFTER the lead-in (lead_in_line is 1-based).
        end = min(len(lines), start + 40)
        return "\n".join(lines[start:end])

    @pytest.mark.parametrize(
        "rel_path,lead_in_line,label",
        SITES,
        ids=[f"{rel}:line{ln}:{lbl}" for rel, ln, lbl in SITES],
    )
    def test_dispatch_site_has_canonical_shape(self, rel_path, lead_in_line, label):
        path = COMMANDS_DIR / rel_path
        assert path.is_file(), f"Command file missing: {rel_path}"
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()

        # Verify the lead-in is at the recorded line (1-based). If the
        # command file was reorganized, the line anchor must be updated in
        # SITES — the test surfaces the drift instead of silently passing
        # against a different block.
        assert 1 <= lead_in_line <= len(lines), (
            f"{rel_path}:{label}: SITES line anchor {lead_in_line} out of "
            f"range (file has {len(lines)} lines)."
        )
        assert self.LEAD_IN in lines[lead_in_line - 1], (
            f"{rel_path}:{label}: SITES line anchor {lead_in_line} no longer "
            f"contains lead-in {self.LEAD_IN!r}. Re-locate the per-loop "
            "dispatch site and update SITES."
        )

        block = self._site_block(text, lead_in_line)
        task_create_count = block.count("TaskCreate")
        task_update_count = block.count("TaskUpdate")
        agent_spawn_count = block.count("Agent(")

        problems = []
        if task_create_count < 2:
            problems.append(
                f"expected ≥2 TaskCreate occurrences, found {task_create_count}"
            )
        if task_update_count < 2:
            problems.append(
                f"expected ≥2 TaskUpdate occurrences, found {task_update_count}"
            )
        if agent_spawn_count < 1:
            problems.append(
                f"expected ≥1 Agent( occurrence, found {agent_spawn_count}"
            )

        assert not problems, (
            f"{rel_path}:line{lead_in_line}:{label} per-loop dispatch site is "
            f"missing canonical Teachback-Gated Dispatch shape:\n  - "
            + "\n  - ".join(problems)
            + f"\n\nBlock under test:\n{block}"
        )

