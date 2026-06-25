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

    def test_wrapup_has_three_options(self, wrapup_content):
        """Step 8 session decision has 3 options (graceful option removed — TeamDelete tool gone)."""
        # Extract only from the Session Decision section (after "Session Decision")
        session_section = wrapup_content.split("Session Decision")[1]
        labels = _extract_option_labels(session_section)
        assert len(labels) == 3, f"wrap-up.md session decision should have 3 options, found {len(labels)}: {labels}"

    def test_wrapup_yes_continue_option(self, wrapup_content):
        assert '"Yes, continue"' in wrapup_content

    def test_wrapup_pause_option(self, wrapup_content):
        assert '"Pause work for now"' in wrapup_content

    def test_wrapup_no_end_session_option(self, wrapup_content):
        assert '"No, end session"' in wrapup_content

    def test_wrapup_no_graceful_end_session_option(self, wrapup_content):
        """Regression guard: graceful option removed (TeamDelete tool gone)."""
        assert '"End session (graceful)"' not in wrapup_content

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


# The teammate self-registration first-action directive, in its delivered
# (unescaped) form. In the source `prompt="..."` literals it is written with
# escaped quotes (`Invoke Skill(\"PACT:pact-team-registration\")`), consistent
# with the escaped `\n\n` in the same literal; the orchestrator unescapes both
# when it constructs the real Agent() call. The presence check below normalizes
# the escaping so it is robust to either representation.
REGISTER_DIRECTIVE = 'Invoke Skill("PACT:pact-team-registration")'


def _normalize_prompt_escaping(text: str) -> str:
    """Collapse source-literal escaped quotes (\\") to plain quotes so a
    presence check matches whether the directive is written escaped (current
    convention, valid inside a `prompt="..."` literal) or unescaped."""
    return text.replace('\\"', '"')


# Every surface that emits a teammate spawn prompt. The register directive must
# be present in EVERY teammate-spawn prompt literal across ALL of them: a single
# literal that drops it silently spawns a teammate that never registers.
# bootstrap.md (the secretary spawn) is NOT a CONSUMER_COMMAND but IS a
# teammate-spawning surface; the persona canonical template
# (agents/pact-orchestrator.md) is the authoritative source form.
SPAWN_PROMPT_SURFACES = [
    ("orchestrate", COMMANDS_DIR / "orchestrate.md"),
    ("peer-review", COMMANDS_DIR / "peer-review.md"),
    ("comPACT", COMMANDS_DIR / "comPACT.md"),
    ("rePACT", COMMANDS_DIR / "rePACT.md"),
    ("plan-mode", COMMANDS_DIR / "plan-mode.md"),
    ("bootstrap", COMMANDS_DIR / "bootstrap.md"),
    ("pact-orchestrator", COMMANDS_DIR.parent / "agents" / "pact-orchestrator.md"),
]

# Expected count of teammate-spawn prompt literals per surface. Pinned so a
# FUTURE literal added to a multi-literal file forces a conscious test update
# (and thereby a conscious "does it carry the directive?" check) instead of
# slipping in unguarded. orchestrate.md has the most (one per dispatched phase).
EXPECTED_SPAWN_LITERAL_COUNTS = {
    "orchestrate": 6,
    "peer-review": 1,
    "comPACT": 2,
    "rePACT": 1,
    "plan-mode": 1,
    "bootstrap": 1,
    "pact-orchestrator": 1,
}

# Matches a double-quoted prompt="..." value, honoring escaped \" inside the
# literal (the spawn prompts embed Invoke Skill(\"...\")). The capture is the
# raw literal value with its source escaping intact.
_PROMPT_LITERAL_RE = re.compile(r'prompt="((?:[^"\\]|\\.)*)"')

# A teammate-spawn prompt literal always opens with the role prelude. This
# prefix is what distinguishes a spawn literal from a non-spawn prompt such as
# Agent(resume=..., prompt="Blocker resolved: ...") — a resumed agent already
# registered on its INITIAL spawn, so its resume prompt must NOT carry (or be
# required to carry) the register directive.
_SPAWN_LITERAL_PREFIX = "YOUR PACT ROLE: teammate ("


def _spawn_prompt_literals(text: str) -> list[str]:
    """Return every teammate-spawn prompt literal value (raw, escaping intact)
    in ``text`` — the ``prompt="YOUR PACT ROLE: teammate (..."`` sites. Excludes
    non-spawn prompts (e.g. the Agent(resume=...) 'Blocker resolved' prompt),
    which carry no role prelude and must NOT be held to the directive rule."""
    return [
        value
        for value in _PROMPT_LITERAL_RE.findall(text)
        if value.startswith(_SPAWN_LITERAL_PREFIX)
    ]


class TestRegisterDirectivePresentInEverySpawnLiteral:
    """Present-in-EVERY-LITERAL drop-guard for the self-registration first-action
    (literal-granular — supersedes the prior file-granular substring check).

    Every teammate-spawn prompt literal MUST carry the register directive, not
    merely SOMEWHERE in each file. A whole-file substring check is blind to a
    single literal dropping the directive inside a MULTI-literal file
    (orchestrate.md has 6 spawn literals, comPACT.md has 2): the directive still
    appears in the file's other literals, so the drop passes undetected — the
    exact LEG-4 register-delivery failure class (a teammate spawned via the
    stripped literal never registers). This guard iterates PER literal.

    Non-vacuity (counter-test-by-revert, documented): dropping the directive from
    ONE of orchestrate.md's 6 literals turns the [orchestrate] row RED here,
    where the prior file-granular test stayed GREEN.
    """

    @pytest.mark.parametrize(
        "label,path",
        SPAWN_PROMPT_SURFACES,
        ids=[label for label, _ in SPAWN_PROMPT_SURFACES],
    )
    def test_every_spawn_literal_carries_register_directive(self, label, path):
        assert path.is_file(), f"Spawn-prompt surface missing: {label} ({path})"
        raw = path.read_text(encoding="utf-8")
        literals = _spawn_prompt_literals(raw)

        expected = EXPECTED_SPAWN_LITERAL_COUNTS[label]
        assert len(literals) == expected, (
            f"{label}: found {len(literals)} teammate-spawn prompt literal(s), "
            f"expected {expected}. If you ADDED a teammate-spawn literal, update "
            f"EXPECTED_SPAWN_LITERAL_COUNTS AND ensure the new literal carries "
            f"{REGISTER_DIRECTIVE!r}; if you REMOVED one, update the count. The "
            f"count guard makes a new spawn literal a conscious directive "
            f"decision (the file-granular check could not see a per-literal drop)."
        )

        for index, value in enumerate(literals, start=1):
            normalized = _normalize_prompt_escaping(value)
            assert REGISTER_DIRECTIVE in normalized, (
                f"{label}: teammate-spawn prompt literal #{index} is missing the "
                f"register first-action directive {REGISTER_DIRECTIVE!r}. A "
                f"teammate spawned via THIS literal would never record its "
                f"name@team — the LEG-4 register-delivery failure class. A "
                f"whole-file substring check would miss this when the file has "
                f"other literals that carry it; EVERY literal must carry it. "
                f"Offending literal (truncated): {value[:90]!r}"
            )

    def test_resume_prompt_excluded_from_directive_requirement(self):
        """Regression: a non-spawn prompt — Agent(resume=..., prompt="Blocker
        resolved: ...") — is NOT a teammate-spawn literal (no role prelude) and
        is correctly EXCLUDED from the directive requirement. Pins the exclusion
        so a future extractor change can't start demanding the directive in a
        resume prompt (wrong: a resumed agent already registered on its initial
        spawn). orchestrate.md is the surface that carries the resume example."""
        orch = (COMMANDS_DIR / "orchestrate.md").read_text(encoding="utf-8")
        all_prompts = _PROMPT_LITERAL_RE.findall(orch)
        resume_prompts = [v for v in all_prompts if v.startswith("Blocker resolved")]
        assert resume_prompts, (
            "expected at least one Agent(resume=...) 'Blocker resolved' prompt in "
            "orchestrate.md — the resume-recovery example. If it was removed, drop "
            "this test; if the extractor regex changed shape, fix it. Without this "
            "fixture the exclusion below would be vacuously true."
        )
        # The resume prompt must NOT be classified as a spawn literal.
        spawn = _spawn_prompt_literals(orch)
        assert all(not v.startswith("Blocker resolved") for v in spawn), (
            "a resume prompt leaked into the teammate-spawn literal set — it "
            "would then be wrongly required to carry the register directive."
        )


class TestTeamRegistrationSkillLiveness:
    """Anti-fossil liveness guard: the register directive points at a LIVE skill.

    The register first-action directs every spawned teammate to invoke the
    pact-team-registration skill. If that skill directory is ever renamed or
    removed, the directive dead-ends — the exact failure that killed the
    deleted teammate-bootstrap skill (guarded by
    TestNoFirstActionFossilInConsumerCommands). This makes the new directive
    fossil-proof: a future skill rename that breaks it fails CI.
    """

    SKILL_PATH = (
        COMMANDS_DIR.parent / "skills" / "pact-team-registration" / "SKILL.md"
    )

    def test_team_registration_skill_exists(self):
        assert self.SKILL_PATH.is_file(), (
            "skills/pact-team-registration/SKILL.md does not exist, but the "
            "canonical spawn prompt directs every teammate to invoke "
            "PACT:pact-team-registration as its first action. The directive "
            "would dead-end — the failure that killed teammate-bootstrap. "
            "Restore the skill or update the directive."
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
        ("orchestrate.md", 463, "PREPARE"),
        ("orchestrate.md", 558, "ARCHITECT"),
        ("orchestrate.md", 681, "CODE"),
        ("orchestrate.md", 816, "TEST"),
        ("comPACT.md", 233, "MultipleSpecialists"),
        ("comPACT.md", 293, "SingleSpecialist"),
        ("peer-review.md", 192, "Reviewers"),
        ("plan-mode.md", 236, "Consultants"),
        ("rePACT.md", 262, "SubScopeSpecialists"),
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


# --- Teachback-gated dispatch ordering (A-before-B) invariants ---------------
#
# Design intent (do NOT delete this guard as "brittle"):
#   The teachback-gated dispatch templates pair a teachback gate (Task A) with a
#   primary-work task (Task B, blockedBy=[A]). Task A's free-text description must
#   NOT forward-reference Task B by task id. The forward-reference form
#   "Mission for Task B: see Task #{B_id}." forced the orchestrator to create
#   Task B FIRST (so its id is known before Task A's description is written),
#   which gave the teachback gate the HIGHER task id — inverting the intuitive
#   "lower id = earlier" reading of the task list.
#
#   The fixed form points the teammate at Task B by its SUBJECT pattern
#   ("the '{role}: {mission}' work task in your TaskList"), a role-neutral
#   cross-reference that needs no pre-known id, so Task A can be created first.
#   Each dispatch block also carries an explicit "A-FIRST ORDERING (required)"
#   note marking B-first creation as wrong.
#
#   These guards FAIL if the id-forward-reference anti-pattern is re-introduced
#   into any Task-A description, and confirm the A-first ordering discipline is
#   present. They are content-based drift detectors, not line/anchor coupled.
#
# CRITICAL PRECISION — anti-pattern vs legitimate wiring:
#   The id forward-reference inside a Task-A *description* is the anti-pattern.
#   The block-edge WIRING variables `B_id = TaskCreate(...)`, `addBlocks=[B_id]`,
#   and `addBlockedBy=[A_id]` are LEGITIMATE — they run AFTER both tasks exist and
#   MUST remain. The negative guard therefore targets the description-forward-
#   reference LITERAL ("see Task #{B_id}" / "Mission for Task B: see Task #")
#   specifically, never a bare "{B_id}" substring. A guard that tripped on the
#   wiring variables would be a false-positive defect.

AGENTS_DIR = COMMANDS_DIR.parent / "agents"
PERSONA_PATH = AGENTS_DIR / "pact-orchestrator.md"

# Every LLM-loaded surface that carries the Teachback-Gated Dispatch sequence:
# the 5 consumer-command templates plus the orchestrator persona's canonical
# §Agent Teams Dispatch sequence. CONSUMER_COMMANDS (defined above) enumerates
# the command surfaces; the persona is the authoritative source form.
DISPATCH_ORDERING_SURFACES = [
    (name, COMMANDS_DIR / f"{name}.md") for name in CONSUMER_COMMANDS
] + [("pact-orchestrator", PERSONA_PATH)]

# The id-forward-reference anti-pattern, in the two stable forms it took. Both
# name Task B by a (yet-unassigned) task id inside a Task-A description; either
# one forces B-first creation. Matching these literals — and NOT a bare "{B_id}"
# — is what distinguishes the anti-pattern from the legitimate wiring variables.
FORBIDDEN_ID_FORWARD_REFERENCES = (
    "see Task #{B_id}",
    "Mission for Task B: see Task #",
)

# Paraphrase-tolerant form of the same anti-pattern. The two literals above catch
# the historical phrasings, but a re-introduction could paraphrase the pointer —
# e.g. "Mission for Task B: refer to Task #{B_id}." or "...look at #{B_id}." — and
# slip past an exact-literal match. This regex generalises: the Task-B mission
# pointer ("Mission for Task B" ...) followed, within the same logical line and a
# short window, by an id-form token ("#" then an optional "{" then a word char).
#
# Precision boundaries (must NOT match — verified against the live surfaces):
#   - the fixed role-based pointer ("...identified by its subject...") has no "#";
#   - the upstream backref "TEACHBACK Task #{A_id}" names A (not the Task-B
#     pointer) and is not preceded by "Mission for Task B";
#   - downstream phase refs ("Preparer task: #{taskId}", "Coder tasks: #{id1}")
#     point at already-created earlier-phase tasks, not the forward Task B, and
#     are likewise not introduced by the "Mission for Task B" carrier.
# The 80-char window keeps the match local to the pointer so it cannot span into
# unrelated later prose. Anchoring on the "Mission for Task B" carrier is what
# scopes the regex to the forward-reference site specifically.
FORBIDDEN_ID_FORWARD_REFERENCE_RE = re.compile(
    r"Mission for Task B[^\n]{0,80}?#\s*\{?\s*[A-Za-z0-9_]"
)


class TestNoIdForwardReferenceInTaskADescription:
    """Negative invariant: no dispatch surface forward-references Task B by id.

    The teachback gate (Task A) must be creatable BEFORE the primary-work task
    (Task B), so Task A's description cannot name Task B by an id that does not
    yet exist. This guards against re-introducing the "see Task #{B_id}"
    forward-reference into any Task-A description across all dispatch surfaces.

    Two layers, defence-in-depth:
      - test_no_id_forward_reference: exact-literal match of the historical forms;
      - test_no_paraphrased_id_forward_reference: a paraphrase-tolerant regex over
        the same Task-B mission-pointer carrier, so a reworded re-introduction
        ("refer to Task #{B_id}", "look at #{B_id}", ...) cannot slip past.
    """

    @pytest.mark.parametrize(
        "label,path",
        DISPATCH_ORDERING_SURFACES,
        ids=[label for label, _ in DISPATCH_ORDERING_SURFACES],
    )
    def test_no_id_forward_reference(self, label, path):
        assert path.is_file(), f"Dispatch-ordering surface missing: {label} ({path})"
        text = path.read_text(encoding="utf-8")
        offenders = [f for f in FORBIDDEN_ID_FORWARD_REFERENCES if f in text]
        assert not offenders, (
            f"{label}: Task-A description contains id-forward-reference "
            f"anti-pattern(s): {offenders}. Naming Task B by its id in Task A's "
            f"description forces B-first creation, giving the teachback gate the "
            f"HIGHER id and inverting the 'lower id = earlier' reading. Point the "
            f"teammate at Task B by its subject pattern instead. (Note: the "
            f"block-edge wiring vars addBlocks=[B_id] / addBlockedBy=[A_id] are "
            f"legitimate and are NOT what this guard targets.)"
        )

    @pytest.mark.parametrize(
        "label,path",
        DISPATCH_ORDERING_SURFACES,
        ids=[label for label, _ in DISPATCH_ORDERING_SURFACES],
    )
    def test_no_paraphrased_id_forward_reference(self, label, path):
        assert path.is_file(), f"Dispatch-ordering surface missing: {label} ({path})"
        text = path.read_text(encoding="utf-8")
        match = FORBIDDEN_ID_FORWARD_REFERENCE_RE.search(text)
        assert match is None, (
            f"{label}: Task-A description contains a paraphrased id-forward-"
            f"reference to Task B: {match.group(0)!r}. The Task-B mission pointer "
            f"must NOT name Task B by a task id (in any phrasing) — that forces "
            f"B-first creation, giving the teachback gate the HIGHER id and "
            f"inverting the 'lower id = earlier' reading. Point the teammate at "
            f"Task B by its subject pattern instead. (Note: the block-edge wiring "
            f"vars addBlocks=[B_id] / addBlockedBy=[A_id], the upstream "
            f"'TEACHBACK Task #{{A_id}}' backref, and downstream phase task refs "
            f"are legitimate and are NOT matched by this guard.)"
        )


class TestLegitimateBlockWiringSurvives:
    """Precision pin: the legitimate block-edge wiring variables must REMAIN.

    The fix removed only the id forward-reference from Task-A descriptions; the
    `addBlocks=[B_id]` / `addBlockedBy=[A_id]` wiring (which runs after both
    tasks exist) is correct and load-bearing. This pin documents and protects
    the anti-pattern/legitimate boundary: it asserts the wiring is still present
    so that a future over-correction (stripping the wiring along with the
    forward-reference) is caught, and so the negative guard above is understood
    to target the description literal, not the wiring.
    """

    @pytest.mark.parametrize("name", CONSUMER_COMMANDS)
    def test_block_edge_wiring_present(self, name):
        text = (COMMANDS_DIR / f"{name}.md").read_text(encoding="utf-8")
        for wiring in ("addBlocks=[B_id]", "addBlockedBy=[A_id]"):
            assert wiring in text, (
                f"{name}.md is missing legitimate block-edge wiring {wiring!r}. "
                f"This wiring runs AFTER both tasks exist and is correct — the "
                f"A-before-B fix removed only the id forward-reference from "
                f"Task-A descriptions, never the wiring."
            )


class TestAFirstOrderingDisciplinePresentInCommands:
    """Positive invariant: each command template carries the A-first discipline.

    Two stable anchors per command file:
      - the explicit "A-FIRST ORDERING" note marking B-first creation as wrong;
      - the role-based subject-pattern cross-reference ("identified by its
        subject") that replaced the id forward-reference.
    Substring anchors (not whole-sentence) keep the guard robust to benign
    rewording while still detecting deletion of either discipline element.
    """

    A_FIRST_NOTE_ANCHOR = "A-FIRST ORDERING"
    ROLE_BASED_XREF_ANCHOR = "identified by its subject"

    @pytest.mark.parametrize("name", CONSUMER_COMMANDS)
    def test_a_first_note_present(self, name):
        text = (COMMANDS_DIR / f"{name}.md").read_text(encoding="utf-8")
        assert self.A_FIRST_NOTE_ANCHOR in text, (
            f"{name}.md is missing the {self.A_FIRST_NOTE_ANCHOR!r} dispatch note. "
            f"Each teachback-gated dispatch block must explicitly state that "
            f"creating Task A (the gate) before Task B is required and that "
            f"B-first creation is wrong."
        )

    @pytest.mark.parametrize("name", CONSUMER_COMMANDS)
    def test_role_based_cross_reference_present(self, name):
        text = (COMMANDS_DIR / f"{name}.md").read_text(encoding="utf-8")
        assert self.ROLE_BASED_XREF_ANCHOR in text, (
            f"{name}.md is missing the role-based subject-pattern cross-reference "
            f"({self.ROLE_BASED_XREF_ANCHOR!r}). Task A's description must point "
            f"the teammate at Task B by its subject pattern, not by a task id."
        )


class TestAFirstOrderingDisciplinePresentInPersona:
    """Positive invariant: the orchestrator persona carries the A-first discipline.

    The persona expresses the discipline in PROSE (not the command files' code
    comment), so it has its own stable anchors:
      - "Create Task A FIRST" — the ordering directive;
      - "NOT by a forward task id" — the explicit prohibition that replaced the
        id forward-reference with a subject-pattern cross-reference.
    """

    CREATE_A_FIRST_ANCHOR = "Create Task A FIRST"
    NO_FORWARD_ID_ANCHOR = "NOT by a forward task id"

    def test_persona_states_create_a_first(self):
        text = PERSONA_PATH.read_text(encoding="utf-8")
        assert self.CREATE_A_FIRST_ANCHOR in text, (
            f"pact-orchestrator.md is missing the {self.CREATE_A_FIRST_ANCHOR!r} "
            f"dispatch directive. The persona §Agent Teams Dispatch sequence must "
            f"reinforce creating the teachback gate (Task A) before Task B."
        )

    def test_persona_prohibits_forward_id_reference(self):
        text = PERSONA_PATH.read_text(encoding="utf-8")
        assert self.NO_FORWARD_ID_ANCHOR in text, (
            f"pact-orchestrator.md is missing the {self.NO_FORWARD_ID_ANCHOR!r} "
            f"prohibition. The persona must direct the orchestrator to point the "
            f"teammate at Task B by its subject pattern, not by a forward task id."
        )

