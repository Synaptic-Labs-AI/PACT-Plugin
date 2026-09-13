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
from shared.intentional_wait import KNOWN_RESOLVERS

COMMANDS_DIR = Path(__file__).parent.parent / "commands"

# The shutdown loop's call AS IT APPEARS INSIDE THE FENCED BLOCK.
#
# Anchoring on the bare call form instead is satisfied by the PROSE sentence
# that introduces the loop on each of these surfaces, which carries the
# identical literal. Measured: with a bare-call anchor, deleting pause.md's
# and refresh.md's ENTIRE loop body produced 0 failures across the 207-test
# structural set, while the same deletion on wrap-up.md (which names the call
# once) fired 3 pins — so the green was the prose copy absorbing the edit,
# not a dead pin. The predecessor anchor, `then: TaskStop("{teammate_name}")`,
# occurred only inside the loop and DID catch this; dropping the `then: `
# prefix was required when the second tier went away, but it silently widened
# the anchor from loop-scoped to file-wide. This constant restores the
# predecessor's coverage envelope.
#
# BRITTLENESS, stated so a future red is not mistaken for a bug: this pins the
# pseudocode line AND its two-space indent, so a behaviourally-identical reflow
# of the loop turns the pins red. That is intended — on an LLM-loaded surface
# the loop's SHAPE is the instruction — but if you loosen it back to the bare
# call form you reopen the hole described above. Change the surfaces and this
# constant together. 55 bytes, and it occurs exactly once in each of
# pause.md, refresh.md and wrap-up.md; a future edit that breaks that identity
# breaks the pins, which is the point.
SHUTDOWN_LOOP_CALL = 'For each active teammate:\n  TaskStop("{teammate_name}")'

# ---------------------------------------------------------------------------
# KNOWN LIMITS of the shutdown pins below — the SET, in one place, because a
# named limit is a different object from an unknown hole and the difference is
# only legible when they are listed together. Each is deliberate and each
# fails in a direction we chose; none is a TODO.
#
#   1. _section_by_heading cannot exclude a heading DEEPER than its anchor: a
#      #### subsection nested inside the section, carrying a TaskStop,
#      satisfies the assertion even when the section's own call is gone.
#
#   2. test_no_undeclared_teammate_stopping_command scans for the call form
#      "TaskStop(" WITH the paren, so a command instructing a stop in prose
#      only — a bare `TaskStop` — is invisible to it. NOT widened on purpose:
#      bare-word matching flags every cross-reference (orchestrate.md names
#      TaskStop solely to point at imPACT), and a scan that cries wolf gets
#      relaxed.
#
# Both verified by execution, not inferred from reading.
# ---------------------------------------------------------------------------

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
    "refresh",
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

    def test_wrapup_taskstop_confined_to_end_session_branch(self, wrapup_content):
        """The continue branch must keep teammates alive, and the pause branch
        delegates to /PACT:pause which already stops them — a second loop there
        would duplicate it. Only the terminal branch stops teammates."""
        section = wrapup_content.split("Session Decision")[1]
        yes = section.find('**"Yes, continue"**')
        pause = section.find('**"Pause work for now"**')
        end = section.find('**"No, end session"**')
        assert -1 not in (yes, pause, end) and yes < pause < end, (
            "step 8's three options must be present in continue/pause/end order"
        )
        assert SHUTDOWN_LOOP_CALL in section[end:], (
            "the end-session branch must carry the shutdown loop. Anchored on "
            "the LOOP form because the end-session slice contains five prose "
            "mentions of TaskStop against one real call, so a bare-word "
            "assertion stays green with the loop deleted. The two negative "
            "legs below stay on the bare word on purpose — for leak detection "
            "over-sensitivity is the safe direction."
        )
        assert "TaskStop" not in section[yes:pause], "TaskStop leaked into the continue branch"
        assert "TaskStop" not in section[pause:end], "TaskStop leaked into the pause branch"

    # --- peer-review.md step 7 ---

    def test_peer_review_has_three_merge_options(self, peer_review_content):
        """Step 7 merge authorization has 3 options."""
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
# prefix is what distinguishes a spawn literal from a non-spawn prompt, such as
# a prompt that gives an instruction to a teammate that is running. A teammate
# registers on its OWN spawn, so a prompt with no role prelude must NOT carry
# the register directive, and must not be required to.
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


# Matches a description="..." param with a NON-EMPTY value. The empty form
# description="" would satisfy a bare presence check while carrying no spawn
# description, so the value must be non-empty for the guard to see the param.
_DESCRIPTION_PARAM_RE = re.compile(r'description="[^"]+"')


def _spawn_literals_missing_description(text: str) -> list[str]:
    """Return every teammate-spawn prompt literal whose Agent( call does not
    carry a non-empty ``description=`` param before the prompt.

    For each spawn-literal match, the span from the nearest preceding
    ``Agent(`` to the literal must contain ``description="<non-empty>"`` —
    the same span that holds name=/team_name=/subagent_type=. A description
    that appears only AFTER the prompt match is inside the prompt string, not
    a sibling param, and does not count. Non-spawn prompts (no role prelude)
    are skipped: a prompt to a running teammate is not a spawn template and
    is not held to the description rule.
    """
    missing = []
    for match in _PROMPT_LITERAL_RE.finditer(text):
        if not match.group(1).startswith(_SPAWN_LITERAL_PREFIX):
            continue
        call_open = text.rfind("Agent(", 0, match.start())
        if call_open == -1 or not _DESCRIPTION_PARAM_RE.search(
            text[call_open:match.start()]
        ):
            missing.append(match.group(1))
    return missing


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

    def test_a_prompt_without_the_role_prelude_is_not_a_spawn_literal(self):
        """The extractor must EXCLUDE a prompt literal that carries no role
        prelude, so a non-spawn prompt is never required to carry the register
        directive.

        The fixture is SYNTHETIC and that is deliberate. The predecessor of
        this test took its fixture from a live "Blocker resolved" prompt in
        orchestrate.md, and it went red when that prompt was removed with the
        resume-recovery example. A fixture that lives in a document can be
        drained by an edit to that document; this one cannot. MEASURED at the
        time of writing: orchestrate.md carries 6 prompt literals and all 6
        are spawn literals, so no document supplies a non-spawn fixture.

        Three arms. Each one was PROVEN red under its own mutation of an
        isolated copy, against a green control on the unmutated copy:
          1. the extractor no longer matches a plain Agent(prompt=...)
          2. the exclusion widens and admits a prompt with no role prelude
          3. the exclusion narrows and drops a genuine spawn literal
        """
        non_spawn = 'Agent(prompt="Blocker resolved: {details}. Continue.")'
        assert _PROMPT_LITERAL_RE.findall(non_spawn) == [
            "Blocker resolved: {details}. Continue."
        ], (
            "the prompt-literal extractor no longer sees a plain "
            "Agent(prompt=...) call. Without this the exclusion below would be "
            "vacuously true, because there would be nothing to exclude."
        )
        assert _spawn_prompt_literals(non_spawn) == [], (
            "a prompt with no role prelude leaked into the teammate-spawn "
            "literal set. It would then be wrongly required to carry the "
            "register directive."
        )

        spawn = (
            'Agent(prompt="YOUR PACT ROLE: teammate (x).\\n\\nYou are joining '
            'team t. As your FIRST action, Invoke Skill(\\"'
            'PACT:pact-team-registration\\") to record your identity.")'
        )
        assert len(_spawn_prompt_literals(spawn)) == 1, (
            "a genuine teammate-spawn literal was dropped by the extractor. "
            "The directive requirement would then cover nothing."
        )


class TestDescriptionParamPresentInEverySpawnLiteral:
    """Per-literal presence guard for the required ``description=`` param.

    The canonical Agent() teammate spawn carries a ``description`` param
    (the form documented in agents/pact-orchestrator.md §11 step 5). This
    guard pins it per literal, so a future edit that drops ``description=``
    from ONE template in a multi-template file (orchestrate.md has 6) goes
    red here — where a whole-file substring check would stay green on the
    other templates' occurrences.

    Non-vacuity (counter-test, documented): removing the description param
    from one template of an isolated copy reddens that surface's arm, and an
    empty description="" value reddens it too. The synthetic-fixture test
    below keeps that proof in-suite on fixtures that a document edit cannot
    drain.
    """

    @pytest.mark.parametrize(
        "label,path",
        SPAWN_PROMPT_SURFACES,
        ids=[label for label, _ in SPAWN_PROMPT_SURFACES],
    )
    def test_every_spawn_literal_carries_description_param(self, label, path):
        assert path.is_file(), f"Spawn-prompt surface missing: {label} ({path})"
        raw = path.read_text(encoding="utf-8")
        missing = _spawn_literals_missing_description(raw)
        assert not missing, (
            f"{label}: {len(missing)} teammate-spawn literal(s) lack a "
            "non-empty description= param in their Agent( call. The "
            "description param is part of the canonical spawn form "
            "(agents/pact-orchestrator.md §11 step 5) — restore "
            'description="<spawn description>" as a sibling param before '
            f"prompt=. First offender (truncated): {missing[0][:90]!r}"
        )

    def test_checker_flags_missing_and_empty_description(self):
        """The description checker must flag BOTH a dropped param and an
        empty value, and must NOT flag a canonical template or a non-spawn
        prompt.

        The fixtures are SYNTHETIC and deliberately so (same reasoning as
        test_a_prompt_without_the_role_prelude_is_not_a_spawn_literal): a
        fixture taken from a live template can be drained by an edit to the
        document; these cannot. MEASURED at the time of writing: every
        canonical template across SPAWN_PROMPT_SURFACES carries a
        non-empty description=, so no document supplies a missing-description
        fixture.

        Four arms:
          1. a canonical multi-line template WITH description= -> not flagged
          2. the same template with the description line REMOVED -> flagged
          3. the same template with description="" (empty value) -> flagged
          4. a non-spawn prompt (no role prelude) with no description ->
             not flagged — the exclusion is not widened
        """
        template = (
            "Agent(\n"
            '  name="x",\n'
            '  team_name="t",\n'
            '  subagent_type="pact-x",\n'
            '  description="Spawn x specialist",\n'
            '  prompt="YOUR PACT ROLE: teammate (x).\\n\\nregister first."\n'
            ")"
        )
        assert _spawn_literals_missing_description(template) == [], (
            "a canonical template carrying a non-empty description= was "
            "flagged. The guard would be over-blocking."
        )
        assert _spawn_literals_missing_description(
            template.replace('  description="Spawn x specialist",\n', "")
        ) != [], (
            "a template with the description line dropped was NOT flagged. "
            "The guard is vacuous — the exact drop it exists to catch passes."
        )
        assert _spawn_literals_missing_description(
            template.replace('description="Spawn x specialist"', 'description=""')
        ) != [], (
            'a template with an EMPTY description="" value was NOT flagged. '
            "A bare presence check passes while carrying no description."
        )
        assert _spawn_literals_missing_description(
            'Agent(prompt="Blocker resolved: {details}. Continue.")'
        ) == [], (
            "a non-spawn prompt with no role prelude was flagged. The "
            "exclusion is widened — a prompt to a running teammate is not a "
            "spawn template."
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

    # 9 per-loop dispatch sites, keyed by CONTENT rather than by line number.
    # Each entry is (relative_command_path, anchor_substring, label), and the
    # anchor is text on the SAME LINE as the lead-in.
    #
    # LINE NUMBERS WERE THE PREVIOUS KEY AND THEY BROKE ON EVERY INSERTION.
    # Six of these nine needed re-locating after one arc's edits, with drifts
    # constant per file (orchestrate +8, comPACT +5) — the anchors were fine,
    # the file moved underneath them. A content key does not move.
    #
    # THE ANCHOR IS NOT THE LEAD-IN, AND THAT IS THE WHOLE DESIGN. The lead-in
    # occurs FOUR times in orchestrate.md and TWICE in comPACT.md, so keying on
    # it alone would collapse those sites onto whichever came first and quietly
    # test one block several times. Each anchor below is the phrase that makes
    # its own lead-in line unique, and `_locate` REQUIRES EXACTLY ONE MATCH —
    # which is what preserves the property the line numbers had: a site
    # pointing at the wrong block is still detected, now as 0 or 2 matches
    # rather than as a stale integer.
    SITES = [
        ("orchestrate.md", "**Dispatch `pact-preparer`**", "PREPARE"),
        ("orchestrate.md", "**Dispatch `pact-architect`**", "ARCHITECT"),
        ("orchestrate.md", "**Dispatch coder(s)**", "CODE"),
        ("orchestrate.md", "**Dispatch `pact-test-engineer`**", "TEST"),
        ("comPACT.md", "invoke multiple specialists together", "MultipleSpecialists"),
        ("comPACT.md", "**Dispatch the specialist**", "SingleSpecialist"),
        ("peer-review.md", "**Dispatch reviewers**", "Reviewers"),
        ("plan-mode.md", "**Dispatch each consultant**", "Consultants"),
        ("rePACT.md", "For each specialist needed,", "SubScopeSpecialists"),
    ]

    @classmethod
    def _locate(cls, lines, anchor: str) -> int:
        """1-based line number of the one line carrying BOTH the lead-in and
        `anchor`, or raise AssertionError naming how many were found.

        EXACTLY ONE is the requirement, not "at least one". Zero means the site
        was deleted or reworded; two means the anchor stopped discriminating
        and the arm would silently test one block twice. Both are drift and
        both must be loud.
        """
        hits = [
            number
            for number, line in enumerate(lines, 1)
            if cls.LEAD_IN in line and anchor in line
        ]
        assert len(hits) == 1, (
            f"anchor {anchor!r} matched {len(hits)} dispatch lead-in lines "
            f"(expected exactly 1) at lines {hits}. Zero means the site moved "
            f"or was reworded; more than one means the anchor no longer "
            f"discriminates and SITES needs a narrower one."
        )
        return hits[0]

    @staticmethod
    def _site_block(text: str, lead_in_line: int) -> str:
        """Return the block of lines following the lead-in.

        Reads up to 55 lines after the lead-in (1-based). The canonical
        Teachback-Gated Dispatch sequence fits comfortably within that
        window across all observed sites; oversized windows that span into
        adjacent sections produce false positives, undersized windows miss
        steps. 55 covers the widest site (comPACT's concurrent-dispatch
        loop, whose per-dispatch journal-events step — the variety_assessed
        mirror plus agent_dispatch — sits between the wiring writes and
        the spawn) with margin; the next content past the spawn block
        carries no TaskCreate/TaskUpdate/Agent( tokens, so the wider
        window cannot inflate the counts.
        """
        lines = text.splitlines()
        start = lead_in_line  # 0-based index for the line AFTER the lead-in (lead_in_line is 1-based).
        end = min(len(lines), start + 55)
        return "\n".join(lines[start:end])

    def test_sites_accounts_for_every_dispatch_lead_in_in_the_tree(self):
        """The table and the tree must have the same cardinality.

        WITHOUT THIS, THE TABLE IS SELF-RATIFYING. Every other arm here starts
        from a SITES row, so a row DELETED from the table simply stops being
        tested — the suite goes greener, not redder — and a NEW dispatch site
        added to a command file is never checked at all. Neither is visible
        from inside a parametrized run over the table itself.

        Counting from the FILES rather than from the table is what makes this
        a control instead of a restatement: it fails in both directions, and
        the positive fact it asserts — that the lead-in literal is present in
        the tree at all — could not be true if the command files had been
        reorganised out from under the pin.
        """
        # THE POPULATION COMES FROM THE DIRECTORY, NOT FROM SITES, AND THAT IS
        # THE WHOLE CONTROL. Deriving it from the table under audit made this
        # blind to the deletion it exists to catch: remove a file's ONLY row
        # and the file left BOTH dicts, so they still matched and the suite
        # went greener. It caught a deletion in orchestrate.md only because
        # three sibling rows kept that file in the census. Measured — the
        # sole-row files (peer-review, plan-mode, rePACT) all survived.
        # Globbing also covers a command file that has no row at all.
        found = {}
        for path in sorted(COMMANDS_DIR.glob("*.md")):
            count = sum(
                1 for line in path.read_text(encoding="utf-8").splitlines()
                if self.LEAD_IN in line
            )
            if count:
                found[path.name] = count

        assert sum(found.values()) > 0, (
            "no dispatch lead-in found anywhere — the literal was reworded and "
            "every arm in this class is now vacuous"
        )

        expected = {}
        for rel, _anchor, _label in self.SITES:
            expected[rel] = expected.get(rel, 0) + 1
        assert found == expected, (
            f"SITES disagrees with the tree. In the files: {found}. In the "
            f"table: {expected}. A file with MORE is a dispatch site nobody "
            f"pins; a file with FEWER is a table row pointing at a site that "
            f"no longer exists."
        )

    @pytest.mark.parametrize(
        "rel_path,anchor,label",
        SITES,
        ids=[f"{rel}:{lbl}" for rel, _anchor, lbl in SITES],
    )
    def test_dispatch_site_has_canonical_shape(self, rel_path, anchor, label):
        path = COMMANDS_DIR / rel_path
        assert path.is_file(), f"Command file missing: {rel_path}"
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()

        # The test finds its own line. A reorganisation that moves the site
        # is followed silently; a reorganisation that DELETES or duplicates it
        # is loud, which is the drift worth surfacing.
        lead_in_line = self._locate(lines, anchor)

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


# =============================================================================
# refresh.md + bootstrap.md structural pins — mid-workstream checkpoint command
# =============================================================================


class TestRefreshCommandStructure:
    """Structural pins for commands/refresh.md (LLM-loaded).

    Pins the load-bearing invariant statements, the canonical journal-write
    template markers, the write-ban on the legacy ~/.claude/pact-refresh/
    directory, and the zero-planning-artifact rule for LLM-loaded files.
    """

    @pytest.fixture
    def refresh_text(self):
        return (COMMANDS_DIR / "refresh.md").read_text(encoding="utf-8")

    def test_file_exists_with_frontmatter_description(self, refresh_text):
        fm = parse_frontmatter(refresh_text)
        assert fm is not None
        assert fm["description"] == (
            "Mid-workstream context checkpoint — harvest, persist, shut down "
            "teammates, stand by for /compact + /PACT:bootstrap"
        )

    def test_canonical_write_template_present(self, refresh_text):
        """The journal write mirrors pause.md byte-conventions: shell-clamped
        case for the conditional session_consolidated pre-emit, quoted-heredoc
        --stdin for the session_refreshed write, set -e + ERR trap."""
        assert "case '{true_or_false}' in" in refresh_text
        assert "--type session_consolidated" in refresh_text
        assert "--type session_refreshed" in refresh_text
        assert "--stdin <<'JSON'" in refresh_text
        assert "set -e" in refresh_text
        assert "trap 'rc=$?" in refresh_text

    @pytest.mark.parametrize(
        "invariant",
        [
            "LEAD-FRAME-ONLY",
            "Never clean worktrees, branches, or commits",
            "Never complete or delete tasks",
            "RESPAWN-BEFORE-SEND",
            "resurrects its stale transcript",
            "Expect and IGNORE stragglers",
            "Drain-confirmation gate (MUST)",
            "Do NOT delete the team",
        ],
        ids=["lead-frame-only", "never-clean-worktrees", "never-complete-tasks",
             "respawn-before-send", "resume-on-send-hazard", "ignore-stragglers",
             "drain-gate", "no-team-delete"],
    )
    def test_invariant_statement_present(self, refresh_text, invariant):
        assert invariant in refresh_text

    def test_pact_refresh_dir_appears_only_as_write_ban(self, refresh_text):
        """The legacy ~/.claude/pact-refresh/ directory may appear ONLY in
        the constraints block's prohibition — never as a write target."""
        occurrences = [
            line for line in refresh_text.splitlines()
            if "pact-refresh/" in line
        ]
        assert len(occurrences) == 1
        assert "Never write under" in occurrences[0]

    def test_zero_planning_artifacts(self, refresh_text):
        """LLM-loaded files carry no issue/PR numbers, task ids, or
        version-roadmap markers."""
        assert not re.search(r"#\d+", refresh_text)
        assert "Task #" not in refresh_text
        assert not re.search(r"v4\.\d", refresh_text)


class TestBootstrapRefreshClauses:
    """Structural pins for the refresh-related bootstrap.md clauses."""

    @pytest.fixture
    def bootstrap_text(self):
        return (COMMANDS_DIR / "bootstrap.md").read_text(encoding="utf-8")

    def test_secretary_respawn_carveout(self, bootstrap_text):
        assert "respawn it after `/PACT:refresh`" in bootstrap_text
        assert "resurrects its stale pre-refresh transcript" in bootstrap_text

    def test_refreshed_state_paragraph(self, bootstrap_text):
        assert "Refreshed workstream detected" in bootstrap_text
        assert "DECLARED CONTINUATION" in bootstrap_text
        assert "AUTO-PROCEED" in bootstrap_text
        assert "A HALT line always surfaces to the user regardless." in bootstrap_text

    def test_consumption_write_template(self, bootstrap_text):
        assert "--type session_refresh_consumed" in bootstrap_text
        assert '{"refresh_ts": "{refresh_ts}"}' in bootstrap_text
        assert "refresh_ts=UNAVAILABLE" in bootstrap_text
        assert (
            "Never write a consumption event when no refresh prompt surfaced."
            in bootstrap_text
        )

    def test_team_config_expectations_h1_h2(self, bootstrap_text):
        # H2 (bidirectional re-identify)
        assert "An ABSENT config is the NORM" in bootstrap_text
        assert "neither is corruption" in bootstrap_text
        # H1 (ghost-detection)
        assert "do NOT blind-retry `Agent()`" in bootstrap_text
        assert "Check for inbound messages" in bootstrap_text


class TestBootstrapPauseConsumptionClauses:
    """Structural pins for bootstrap.md's PAUSED-state consumption write.

    A sibling class rather than an addition to `TestBootstrapRefreshClauses`
    above: that class is named for the refresh clauses, and a pin filed under a
    name that does not describe it sends the next audit to the wrong place.

    WHY THESE EXIST — MEASURED, NOT ASSUMED. Deleting the entire
    `**Paused-state consumption write (fire-once)**` block from bootstrap.md
    left the whole suite at its baseline: 15474 passed, 85 skipped, exit 0,
    zero failures and zero errors. The refresh half of the same paragraph was
    pinned by `test_consumption_write_template`; the pause half was pinned by
    nothing.

    THE CONSEQUENCE IS THE DANGEROUS SHAPE, not a missing feature.
    `_pause_is_spent` in `hooks/shared/session_resume.py` works correctly and
    is never reached, because this block is the only thing that emits the event
    it consumes. The paused claim then never retires, so the prompt re-surfaces
    on every later resumption that still reads this journal.

    WHAT THE CONSUMPTION EVENT BOUNDS, stated precisely because an earlier
    version of this docstring named the wrong mechanism and a first correction
    then overshot in the other direction. Two bounds operate, and they are not
    the same bound.

    THE STRUCTURAL ONE NEEDS NO EVENT. `session_init` writes the marker into
    the CURRENT session's journal via the implicit `append_event`, while
    `check_resume_state` reads the claim from `prev_session_dir`, and
    `_journal_path_from` joins the directory it is handed with no ancestor
    resolution. So a single freeze cannot outlive its own session, and a claim
    is visible one hop back and no further — with or without any consumption.

    THE EVENT'S OWN BOUND IS REAL AND PATH-DEPENDENT. It retires the claim, so
    it decides whether the prompt re-surfaces AND whether a NEW freeze is minted
    from that same claim in the next session. That only works where the
    consumption lands in the SAME journal as the claim, which is the `/compact`
    and same-session `--resume` case: measured, a later session reading that
    journal one hop back surfaces nothing and does not freeze, where without the
    consumption it surfaces and freezes. On the quit-then-new-session path the
    consumption lands in the NEW session's journal and retires nothing; there
    the one-hop bound alone ends it.

    THAT BOUND IS PER CLAIM, AND CLAIMS ARE MINTED PER SESSION — do not read
    it as a bound on what a user experiences. `wrap-up` branch C writes a
    `session_paused` event every time a session ends with the PR still open,
    so the next session surfaces a NEW claim and freezes on that. A sequence
    of one-session freezes is indistinguishable from a persistent one from
    outside, and while the PR stays open it IS the steady state. Each freeze
    expiring on its own is what makes the mechanism safe to reason about; it
    is not a promise that the block gets rebuilt soon.

    WHY THAT CORRECTION MATTERS HERE. The failure this docstring now exists to
    prevent is a reader concluding the freeze is unbounded. Told the expiry is
    an instruction-surface write an agent may skip, a careful reader correctly
    judges it unreliable and restores the unconditional rebuild — reopening the
    hole. The mechanism is more robust than the old wording claimed, and the
    old wording is what supplied the revert argument.

    WHAT THIS DOES NOT CLAIM. That the block was clean when the freeze began.
    The freeze holds whatever the block held at the resumption boundary; a
    dispatch that propagated earlier in the arc contaminates it before any
    freeze starts, and nothing here detects or repairs that.

    SCOPE, stated so a later green is not over-read: these are DELETION
    detectors on the literals a reader must be able to copy. Each literal below
    occurs exactly once in the file, so a red here names this block rather than
    firing on a neighbour. A rewrite that preserves every literal while
    changing the surrounding instruction is NOT caught, and no fragment pin
    catches it.
    """

    @pytest.fixture
    def bootstrap_text(self):
        return (COMMANDS_DIR / "bootstrap.md").read_text(encoding="utf-8")

    def test_pause_consumption_write_template(self, bootstrap_text):
        """Detects: loss of the write itself, or of either condition governing
        it. Mirrors `test_consumption_write_template` literal for literal, on
        the paused side of the same paragraph."""
        assert "--type session_pause_consumed" in bootstrap_text
        assert '{"pause_ts": "{pause_ts}"}' in bootstrap_text
        assert "pause_ts=UNAVAILABLE" in bootstrap_text
        assert (
            "never write a consumption event when no paused prompt surfaced"
            in bootstrap_text
        )

    def test_every_surfaced_paused_prompt_is_retired(self, bootstrap_text):
        """Detects: the write narrowing to the branch that resumes something.

        This clause has no refresh counterpart and is the reason the pin above
        is not sufficient on its own. The paused prompt surfaces on three
        branches — normal, staleness-downgraded, and merged-or-closed PR — and
        ALL THREE freeze the block, because the marker records that a claim
        SURFACED, never that work resumed. A write narrowed to the first branch
        leaves the other two unretirable: correct-looking, green, and a freeze
        that never ends."""
        assert "Write it for EVERY paused prompt that surfaces" in bootstrap_text

    def test_pause_consumption_trigger_is_not_resumption_keyed(
        self, bootstrap_text
    ):
        """The paused write fires on SURFACING, never on confirming resumption.

        Detects: a trigger that reads "after confirming resumption" above a
        correct closing enumeration. Step 3 lets the user DECLINE a paused
        prompt ("or start fresh"), unlike its refresh sibling which calls a
        refresh a declared continuation and auto-proceeds. So a resumption-
        keyed trigger leaves the ordinary open-PR path unretired: the claim
        survives, the prompt re-surfaces, and the briefing reports "resuming an
        interrupted workstream" for a session the user chose not to resume.

        WHY THE SIBLING PIN DOES NOT COVER THIS. `test_every_surfaced_paused_
        prompt_is_retired` asserts the closing enumeration, and that literal
        can be present while the trigger above it is still resumption-keyed —
        which was the state on disk when this arm was written, with the suite
        green. A pin on an enumeration does not reach the condition governing
        it.

        SLICED TO THE PAUSED BLOCK, not the file. The refresh block above it
        legitimately says "immediately after confirming resumption" and must
        keep saying so, so a file-wide assertion would be wrong in both
        directions: green when the paused trigger is broken, red when the
        refresh trigger is correct.
        """
        block = bootstrap_text.split("**Paused-state consumption write")[1]
        block = block.split("## Step 4")[0]
        assert "confirming resumption" not in block, (
            "the paused consumption trigger is keyed on confirming resumption; "
            "a user who declines the prompt never retires the claim"
        )


# =============================================================================
# pause.md teammate-shutdown structural pins + cross-surface termination
# skeleton — deliberate drift detectors, anchored on stable tokens agreed
# with the doc surfaces. Surrounding prose is intentionally unpinned.
# =============================================================================


class TestPauseShutdownStructure:
    """Structural pins for commands/pause.md's teammate-shutdown step (LLM-loaded).

    Each assertion is a drift detector: it anchors on a stable token and its
    docstring names the regression it catches. Prose around the tokens is
    deliberately unpinned so wording can evolve without touching these tests.
    """

    @pytest.fixture
    def pause_text(self):
        return (COMMANDS_DIR / "pause.md").read_text(encoding="utf-8")

    def test_taskstop_present_in_loop(self, pause_text):
        """Detects: the shutdown loop losing its only call. The loop is now
        one line, so dropping TaskStop does not fall back to a graceful
        request — it leaves pause with no termination step at all, and
        teammates would survive every pause. The anchor lost its `then: `
        prefix along with the second tier that prefix sequenced.

        Anchored on the LOOP form: pause.md names the call in the prose
        sentence introducing the loop as well as in the loop itself, so a
        bare-call anchor stays green when the loop body is deleted."""
        assert SHUTDOWN_LOOP_CALL in pause_text

    def test_expected_post_state_note_present(self, pause_text):
        """Detects: removal of the post-state expectation. A lead-only
        roster after the stops is the correct post-pause state, not
        corruption — losing this note invites 'repair' behavior (team
        deletion or blind respawns) on resume."""
        assert "EXPECTED post-state" in pause_text

    def test_no_team_delete_invariant_survives(self, pause_text):
        """Detects: the team-deletion prohibition being dropped while the
        shutdown step is edited — the config file and team identity must
        survive a pause for resume to reuse them."""
        assert "Do NOT delete the team" in pause_text

    def test_taskstop_error_idempotency_present(self, pause_text):
        """Detects: loss of the already-stopped-success rule. Without it, a
        TaskStop not-found / already-exited error mid-loop reads as a
        failure and can abort the remaining teammate shutdowns instead of
        continuing the loop."""
        assert "already-stopped success" in pause_text


# The termination skeleton is THREE statements, not one, because they carry
# three different kinds of warrant and a single sentence cannot bound them
# separately:
#   A — why the loops send no graceful request. DOCUMENTARY: a claim about
#       our own files (no loop read the response, so a reject could not take
#       effect; approving buys no flush). Needs no empirical bound.
#   B — what the primitives were measured to do. EMPIRICAL, so its bound is
#       welded inline — which is what the two tokens pinned per row below
#       actually anchor.
#   C — the deviation notice. RELATIONAL to platform documentation, which
#       describes a cooperative flow and names no TaskStop for it.
# Presence-only assertions — occurrence counts and surrounding prose are not
# pinned.
#
# Only B is pinned. A and C are deliberately unpinned prose — pinning a
# rationale freezes wording that should be free to improve, and the tokens
# below are chosen to anchor the claim that can go WRONG, not every claim
# that matters. Dropping A or C is a review catch, not a test catch.
#
# Role-declared membership. A surface earns a row by its ROLE — it instructs or
# reasons about teammate shutdown — NOT by whether it currently contains the
# words. Deriving membership from CONTENT is what let the prior sweep miss a
# file whose count was already zero: a presence assertion is vacuous on a file
# with no text to assert over, so content-derived membership can never reach
# the file it most needs to.
#
# Two groups, because NO SINGLE PREDICATE COVERS BOTH and saying so is the
# point. A command-flow predicate cannot reach a skill or an agent body; a
# "reasons about shutdown" predicate cannot be evaluated mechanically at all.
# The second group is therefore an EXPLICIT, JUSTIFIED EXCEPTION LIST — not
# derivation, and not an unexplained hand-list.

# Group 1 — lifecycle commands whose flow stops teammates. Cross-checked in the
# additive direction by test_no_undeclared_teammate_stopping_command below.
TERMINATION_COMMAND_SURFACES = [
    "commands/pause.md",
    "commands/refresh.md",
    "commands/wrap-up.md",
]

# Group 2 — surfaces that REASON about shutdown without running a loop. Each
# row states why no command-flow predicate reaches it.
#   imPACT.md            — a command, but terminates ONE unrecoverable agent;
#                          not a session-terminating flow, so deliberately NOT
#                          in SESSION_TERMINATING_FLOWS below.
#   pact-agent-teams     — a skill: the teammate-side receiving protocol.
#   pact-orchestrator.md — an agent body: lead-side guidance.
TERMINATION_REASONING_SURFACES = [
    "commands/imPACT.md",
    "skills/pact-agent-teams/SKILL.md",
    "agents/pact-orchestrator.md",
]

TERMINATION_SKELETON_SURFACES = (
    TERMINATION_COMMAND_SURFACES + TERMINATION_REASONING_SURFACES
)


GRACEFUL_REQUEST_CALL_FORM = '"type": "shutdown_request"'


@pytest.mark.parametrize("relpath", TERMINATION_COMMAND_SURFACES)
def test_no_graceful_request_in_shutdown_loops(relpath):
    """Detects RE-ADDITION of the graceful tier to a shutdown loop.

    Direction matters: a presence pin defends existing text against deletion
    and cannot fire on omission. After a deliberate REMOVAL the drift vector
    inverts to re-addition, which only an absence assertion catches.

    Targets the CALL FORM, not the word: prose on these surfaces legitimately
    names `shutdown_request` when explaining why none is sent, so a bare
    "shutdown_request" not-in-text pin would fail on correct text.
    """
    text = (COMMANDS_DIR.parent / relpath).read_text(encoding="utf-8")
    # Non-vacuity control: the fixture must be live and the file must be the
    # one we think it is. Without this, a bad path or an empty read passes.
    assert 'TaskStop("{teammate_name}")' in text, (
        f"non-vacuity control failed: {relpath} does not contain the shutdown "
        f"loop's TaskStop call — the fixture is wrong, so the absence "
        f"assertion below would pass vacuously"
    )
    assert GRACEFUL_REQUEST_CALL_FORM not in text, (
        f"{relpath} re-introduced a graceful shutdown_request into its "
        f"shutdown loop. The request was removed deliberately: no loop read "
        f"the response, so a teammate's reject could not take effect, and "
        f"approving buys no flush."
    )


# A deprecation word is permitted ONLY inside a denial. Polarity, not presence:
# the rule is semantic ("no surface may ASSERT deprecation"), so a substring
# test is the wrong instrument — every denial contains the word.
# Surfaces carrying BLOCK C's foreclosure. imPACT.md is deliberately absent:
# its wording carries no deprecation clause, so requiring one would invent
# content.
# The issue's requested predicate, made concrete: for each lifecycle command
# whose flow terminates teammate participation, the TERMINAL section carries a
# real TaskStop call. SECTION-SCOPED, so a TaskStop elsewhere in the file
# cannot satisfy it. This is the assertion shape a presence pin cannot express:
# it fails on a file that SHOULD carry the instruction and does not — the
# direction the prior guard was structurally blind to.
SESSION_TERMINATING_FLOWS = {
    "commands/pause.md":   "### 6. Shut Down Teammates",
    "commands/refresh.md": "### 5. SHUTDOWN teammates",
    "commands/wrap-up.md": "### Teammate shutdown — end-session branch only",
}


def _section_by_heading(text, heading):
    """Body of `heading`, ending at the next heading of the SAME OR SHALLOWER
    depth.

    Depth-aware on purpose. The three commands do not agree on heading level:
    pause.md and refresh.md put their steps at `###` under a `## Steps`
    parent, while wrap-up.md puts its steps at `##`. A hard-coded "slice to
    the next `## `" would therefore run to end-of-file on two of the three,
    swallowing the following step and letting a TaskStop anywhere downstream
    satisfy the assertion. Section-scoping is the entire point of this test,
    so the slice has to actually scope.

    Residual, stated rather than hidden — and stated as the mechanism that
    actually holds, because a residual naming the wrong cause is worse than
    none: the slice terminates on headings at the anchor's depth OR SHALLOWER,
    so a later `## ` step correctly ends it even when the anchor is the last
    section in the file. What it cannot exclude is a heading DEEPER than the
    anchor. A `#### ` subsection nested inside this section, carrying a
    TaskStop, satisfies the assertion even if the section's own call is gone.
    Verified by execution in both directions.
    """
    idx = text.find(heading)
    assert idx != -1, f"section heading not found: {heading!r}"
    depth = len(heading) - len(heading.lstrip("#"))
    body_start = idx + len(heading)
    nxt = re.search(rf"^#{{1,{depth}}} ", text[body_start:], re.M)
    return text[body_start:] if nxt is None else text[body_start:body_start + nxt.start()]


@pytest.mark.parametrize(
    ("relpath", "heading"),
    sorted(SESSION_TERMINATING_FLOWS.items()),
    ids=sorted(SESSION_TERMINATING_FLOWS),
)
def test_terminal_flow_carries_taskstop(relpath, heading):
    text = (COMMANDS_DIR.parent / relpath).read_text(encoding="utf-8")
    section = _section_by_heading(text, heading)
    assert SHUTDOWN_LOOP_CALL in section, (
        f"{relpath}'s terminal flow ({heading}) does not call TaskStop. A "
        f"lifecycle command that ends teammate participation must stop them; "
        f"reporting that they will stop is not stopping them."
    )


def test_no_undeclared_teammate_stopping_command():
    """Detects the omission shape in the ADDITIVE direction: a command that
    instructs TaskStop but carries no row in the role table. A presence pin can
    only fail on deletion; this fails on an undeclared addition, which is how
    wrap-up.md was missed.

    IRREDUCIBLE RESIDUAL: a command that SHOULD stop teammates, does not, and
    is undeclared stays invisible — the evidence for that omission is exactly
    the text that is absent. The role table makes it a visible authoring
    decision; it does not make it detectable.

    KNOWN LIMIT (limit 2 in the set at the top of this file), measured: the
    scan matches the CALL FORM "TaskStop(" with its paren, so a command that
    instructs a stop in prose alone — a bare `TaskStop` — is not seen. Left
    narrow deliberately: widening to the bare word flags every
    cross-reference, and orchestrate.md carries one solely to point at
    imPACT. A scan that cries wolf gets relaxed, and a relaxed scan detects
    nothing.
    """
    declared = set(TERMINATION_SKELETON_SURFACES)
    found = [
        f"commands/{p.name}"
        for p in sorted(COMMANDS_DIR.glob("*.md"))
        if "TaskStop(" in p.read_text(encoding="utf-8")
    ]
    # Non-vacuity control: an empty scan would make the assertion below pass
    # for free.
    assert found, "non-vacuity control failed: no command contains TaskStop("
    undeclared = sorted(set(found) - declared)
    assert not undeclared, (
        f"{undeclared} instruct TaskStop but are not declared in "
        f"TERMINATION_SKELETON_SURFACES. Add a row — and decide explicitly "
        f"whether the file also belongs in SESSION_TERMINATING_FLOWS "
        f"(it does if its flow ends teammate participation; imPACT.md does "
        f"not, because it terminates one unrecoverable agent)."
    )


class TestResolverVocabularyIsSingleSourced:
    """No instruction surface may name a resolver outside KNOWN_RESOLVERS.

    `metadata.intentional_wait.expected_resolver` is FREE-FORM: `validate_wait`
    checks only that it is a non-empty string. A wrong value therefore
    validates, warns nothing, and looks correct forever. The shipped dispatch
    templates instructed `team-lead` — absent from the vocabulary — and nothing
    reported it; a teammate noticed by hand. A template is a COPY SOURCE, so
    compliance REPRODUCES such a value once per consumer rather than bounding
    it, and the field quietly stops being usable for grouping or filtering.

    SCOPED TO INSTRUCTION SURFACES, NOT THE REPOSITORY, and `tests/` is
    excluded deliberately. `test_intentional_wait.test_custom_resolver_accepted`
    passes a non-canonical value ON PURPOSE: that test IS the pin on free-form
    acceptance. A repository-wide version of this guard would fail it, and the
    natural repair would be to delete the very test that makes the property
    true. A guard that eats the guard below it is worse than no guard.

    WHAT THIS DOES NOT SEE — do NOT widen the pattern to chase it. The
    extractor keys on the literal token `expected_resolver`, so it is blind to
    a surface that instructs the value WITHOUT naming the key ("tell the
    teammate the resolver is the team-lead"), and to a phrase split across
    adjacent string literals, which no line-oriented search matches in its
    rendered form. Both were measured absent when this pin was written: every
    prose site that names a resolver already spells it `lead`. Pushing the
    pattern into prose would buy those cases at the price of false reds that
    cost more than the class is worth.
    """

    PLUGIN_ROOT = Path(__file__).parent.parent
    SCAN_DIRS = ("agents", "commands", "protocols", "skills")
    SCAN_SUFFIXES = (".md", ".py", ".json")

    # Captures the VALUE in both spellings that ship: the template form
    # `expected_resolver=lead` and the JSON form `"expected_resolver": "lead"`.
    #
    # THE VALUE CLASS INCLUDES `-` DELIBERATELY, and membership is tested AFTER
    # the match rather than excluded inside it. A pattern that tried to reject a
    # bad value inline — a negative lookahead for `team-`, say — does not simply
    # fail to match: the engine backtracks and succeeds on a TRUNCATED token, so
    # the failure would name a value nobody wrote. Capture the whole token, then
    # compare it against the imported vocabulary.
    ASSIGNMENT_RE = re.compile(
        r"expected_resolver[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_-]+)"
    )

    def _sites(self):
        """Return [(relpath, lineno, value)] for every assignment found."""
        found = []
        for subdir in self.SCAN_DIRS:
            root = self.PLUGIN_ROOT / subdir
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                if not path.is_file() or path.suffix not in self.SCAN_SUFFIXES:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                rel = path.relative_to(self.PLUGIN_ROOT)
                for lineno, line in enumerate(text.splitlines(), 1):
                    for match in self.ASSIGNMENT_RE.finditer(line):
                        found.append((str(rel), lineno, match.group(1)))
        return found

    def test_extractor_still_reaches_the_instruction_surfaces(self):
        """Non-vacuity control for the arm below.

        An extractor that matches nothing passes the membership check forever,
        and its output on success is byte-identical to its output when it found
        nothing to check. Only this arm separates the two.
        """
        sites = self._sites()
        assert sites, (
            "no `expected_resolver` assignment found under "
            f"{list(self.SCAN_DIRS)}. Either the dispatch templates stopped "
            "instructing the field, or ASSIGNMENT_RE no longer matches the "
            "spelling they use. Until that is resolved the membership arm is "
            "VACUOUS — it passes because it checks nothing."
        )

    def test_no_instruction_surface_names_a_resolver_outside_the_vocabulary(self):
        sites = self._sites()
        # Repeated so this arm cannot pass vacuously when run in isolation.
        assert sites, "non-vacuity control failed: no assignment extracted"
        offenders = [s for s in sites if s[2] not in KNOWN_RESOLVERS]
        assert not offenders, (
            "instruction surface(s) tell a teammate to write a resolver value "
            f"that KNOWN_RESOLVERS does not contain "
            f"({sorted(KNOWN_RESOLVERS)}):\n"
            + "\n".join(f"  - {p}:{n} -> {v!r}" for p, n, v in offenders)
            + "\n\nFix the SURFACE, not the vocabulary. Widening "
            "KNOWN_RESOLVERS to admit the spelling keeps two spellings of one "
            "vocabulary alive, which is the generator of exactly this defect. "
            "The field is free-form, so nothing else will ever redden to tell "
            "you."
        )


class TestNextRoutesTheBacklogToolsActualExitCodes:
    """`commands/next.md`'s exit-code branches against `backlog.py`'s constants.

    WHY THIS EXISTS. The tool's refusal code moved and the command file kept
    routing the old one. Nothing reddened: the tool's own tests reference the
    CONSTANT, so they pass at any value, and nothing related the command file's
    branches to the tool's actual codes. An external reviewer found it after
    four internal lanes and a verify-only pass, from a commit that had shipped.

    BOTH SIDES ARE DERIVED, NEITHER IS TYPED. The codes come from the module's
    `_EXIT_*` constants by attribute scan, so a renamed or added constant is
    picked up rather than missed, and the routed set comes from the command
    file's own text. A literal on either side would be a value pin that reddens
    on the next legitimate move — which is the failure this file has argued
    against elsewhere.

    THE EXTRACTION IS THE FRAGILE PART AND IS DELIBERATELY NARROW. A sweep for
    integers over the section does not work: the prose contains "Step 5" many
    times and an "exit 2" that is the INTERPRETER's code, named precisely
    because it is not one the tool chooses. So the arm keys on the file's own
    completeness sentence, anchored on the clause that carries its meaning
    rather than on a phrasing. Whitespace is collapsed first, so re-wrapping
    the paragraph cannot break it. If a future author states the set some other
    way the anchor stops matching and the assertion fires on the empty
    extraction — loudly, not silently.

    WHAT THIS DOES NOT COVER. It relates TWO files. Any other surface that
    routes on these codes is invisible to it, and the census that found the
    known sites could not see a router naming neither the tool nor its exit
    behaviour. It does not check that each branch's ADVICE is correct, only
    that the codes agree. And the catch-all branch is not a number: it is
    excluded by construction, never matched as one.
    """

    _CLAIM = "are the only ones the tool chooses"

    def _defined(self):
        from shared import backlog

        codes = {v for k, v in vars(backlog).items() if k.startswith("_EXIT_")}
        assert codes, (
            "no _EXIT_* constants found on shared.backlog — the extraction "
            "failed, which is not the same as agreement"
        )
        return codes, backlog

    def test_next_md_routes_exactly_the_codes_the_tool_defines(self):
        defined, backlog = self._defined()
        text = (COMMANDS_DIR / "next.md").read_text(encoding="utf-8")
        flat = re.sub(r"\s+", " ", text)

        assert flat.count(self._CLAIM) == 1, (
            f"expected exactly one {self._CLAIM!r} clause in next.md, found "
            f"{flat.count(self._CLAIM)} — the anchor this arm keys on has moved"
        )
        span = re.search(rf"—([^—]*?){self._CLAIM}", flat)
        assert span, "found the clause but not its enumeration — anchor moved"
        routed = {int(n) for n in re.findall(r"\d+", span.group(1))}
        assert routed, "the enumeration extracted no codes — extraction failure"

        assert routed == defined, (
            f"next.md routes {sorted(routed)} but backlog.py defines "
            f"{sorted(defined)}; the unrouted/unknown codes are "
            f"{sorted(defined ^ routed)}"
        )

        # Every non-success code also carries its own branch, so the
        # enumeration and the branches cannot drift apart from each other.
        headers = {int(m) for m in re.findall(r"^Exit code (\d+)\b", text, re.M)}
        assert headers, "no 'Exit code N' branch headers found — extraction failure"
        assert headers == defined - {backlog._EXIT_OK}, (
            f"branch headers cover {sorted(headers)} but the tool's non-success "
            f"codes are {sorted(defined - {backlog._EXIT_OK})}"
        )
