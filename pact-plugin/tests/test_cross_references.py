"""
Tests for cybernetics cross-references across PACT command and protocol files.

Tests cover:
L2. Conversation Failure Taxonomy in pact-workflows.md
L3. Progress monitoring dispatch instructions in orchestrate.md, comPACT.md, pact-workflows.md
L4. Environment drift cross-references in orchestrate.md, comPACT.md
L5. Review calibration save step in peer-review.md
L6. Agent state model cross-reference in pact-agent-stall.md
L7. Worktree CLAUDE.md scope warnings in dispatch templates and agent-teams skill
L8. Custom start flows note in agent-teams SKILL.md cross-references pact-secretary.md
L9. Dead-reference guard for #452 file relocation
L10. Lead-Side HALT Fan-Out slug stability — canonical heading + 4 consumer files
L11. Archive step's resolution pointer resolves to a step that is present
"""
import re
import subprocess
from pathlib import Path

import pytest

from fixtures.tracked_files import tracked_files as _tracked_files
from fixtures.tracked_files import tracked_glob as _tracked_glob


PROTOCOLS_DIR = Path(__file__).parent.parent / "protocols"
COMMANDS_DIR = Path(__file__).parent.parent / "commands"
SKILLS_DIR = Path(__file__).parent.parent / "skills"
AGENTS_DIR = Path(__file__).parent.parent / "agents"

WORKFLOWS_PATH = PROTOCOLS_DIR / "pact-workflows.md"
ORCHESTRATE_PATH = COMMANDS_DIR / "orchestrate.md"
COMPACT_PATH = COMMANDS_DIR / "comPACT.md"
REPACT_PATH = COMMANDS_DIR / "rePACT.md"
PEER_REVIEW_PATH = COMMANDS_DIR / "peer-review.md"
AGENT_STALL_PATH = PROTOCOLS_DIR / "pact-agent-stall.md"
AGENT_TEAMS_SKILL_PATH = SKILLS_DIR / "pact-agent-teams" / "SKILL.md"
HARVEST_SKILL_PATH = SKILLS_DIR / "pact-handoff-harvest" / "SKILL.md"
SECRETARY_PATH = AGENTS_DIR / "pact-secretary.md"
ORCHESTRATION_SKILL_PATH = SKILLS_DIR / "orchestration" / "SKILL.md"
ALGEDONIC_PATH = PROTOCOLS_DIR / "algedonic.md"
PACT_PROTOCOLS_PATH = PROTOCOLS_DIR / "pact-protocols.md"
COMM_CHARTER_PATH = PROTOCOLS_DIR / "pact-communication-charter.md"


class TestConversationFailureTaxonomy:
    """L2: Conversation Failure Taxonomy exists in pact-workflows.md."""

    @pytest.fixture
    def workflows_content(self):
        return WORKFLOWS_PATH.read_text(encoding="utf-8")

    def test_taxonomy_section_exists(self, workflows_content):
        assert "Conversation Failure Taxonomy" in workflows_content

    def test_taxonomy_types_present(self, workflows_content):
        assert "Misunderstanding" in workflows_content
        assert "Derailment" in workflows_content
        assert "Discontinuity" in workflows_content
        assert "Absence" in workflows_content


class TestProgressMonitoringDispatch:
    """L3: Progress monitoring dispatch instructions in key files."""

    @pytest.fixture
    def orchestrate_content(self):
        return ORCHESTRATE_PATH.read_text(encoding="utf-8")

    @pytest.fixture
    def compact_content(self):
        return COMPACT_PATH.read_text(encoding="utf-8")

    @pytest.fixture
    def workflows_content(self):
        return WORKFLOWS_PATH.read_text(encoding="utf-8")

    def test_orchestrate_has_progress_monitoring(self, orchestrate_content):
        assert "progress monitoring" in orchestrate_content.lower()

    def test_compact_has_progress_monitoring(self, compact_content):
        assert "Send progress signals" in compact_content

    def test_workflows_has_progress_signals(self, workflows_content):
        assert "Send progress signals" in workflows_content


class TestEnvironmentDriftReferences:
    """L4: Environment drift cross-references in key files."""

    @pytest.fixture
    def orchestrate_content(self):
        return ORCHESTRATE_PATH.read_text(encoding="utf-8")

    @pytest.fixture
    def compact_content(self):
        return COMPACT_PATH.read_text(encoding="utf-8")

    def test_orchestrate_has_environment_drift(self, orchestrate_content):
        content_lower = orchestrate_content.lower()
        assert "environment drift" in content_lower

    def test_compact_has_environment_drift(self, compact_content):
        assert "Environment drift" in compact_content or "file-edits.json" in compact_content


class TestReviewCalibration:
    """L5: Review calibration save step in peer-review.md."""

    @pytest.fixture
    def peer_review_content(self):
        return PEER_REVIEW_PATH.read_text(encoding="utf-8")

    def test_peer_review_has_review_calibration(self, peer_review_content):
        assert "review_calibration" in peer_review_content


class TestAgentStallCrossReference:
    """L6: Agent state model cross-reference in pact-agent-stall.md."""

    @pytest.fixture
    def stall_content(self):
        return AGENT_STALL_PATH.read_text(encoding="utf-8")

    def test_stall_has_agent_state_model_reference(self, stall_content):
        assert "agent state model" in stall_content.lower()


class TestWorktreeScopeWarnings:
    """L7: Worktree CLAUDE.md scope warnings in dispatch templates and agent-teams skill."""

    SCOPE_WARNING_FILES = [
        ("orchestrate.md", ORCHESTRATE_PATH),
        ("comPACT.md", COMPACT_PATH),
        ("rePACT.md", REPACT_PATH),
        ("peer-review.md", PEER_REVIEW_PATH),
        ("pact-agent-teams/SKILL.md", AGENT_TEAMS_SKILL_PATH),
    ]

    @pytest.mark.parametrize(
        "label,path",
        SCOPE_WARNING_FILES,
        ids=[label for label, _ in SCOPE_WARNING_FILES],
    )
    def test_has_claudemd_scope_warning(self, label, path):
        """Anchor on the INSTRUCTION, not a word from its rationale.

        The previous anchor was `gitignored`, taken from a justification that
        turned out false and was deleted — so this failed while the warning it
        guards was intact. A rationale can be corrected; the prohibition is
        what must not disappear. Lowercased because the files differ on
        `do NOT` / `do not`.
        """
        content = path.read_text(encoding="utf-8").lower()
        assert "do not write a `claude.md` file in a project directory" in content, (
            f"{label} missing the CLAUDE.md write prohibition"
        )


class TestCustomStartFlowsCrossReference:
    """L8: Custom start flows note in agent-teams SKILL.md references pact-secretary.md."""

    @pytest.fixture
    def skill_content(self):
        return AGENT_TEAMS_SKILL_PATH.read_text(encoding="utf-8")

    @pytest.fixture
    def secretary_content(self):
        return SECRETARY_PATH.read_text(encoding="utf-8")

    def test_skill_has_custom_start_flows_note(self, skill_content):
        assert "Custom start flows" in skill_content, (
            "SKILL.md missing 'Custom start flows' note"
        )

    def test_custom_start_flows_references_secretary(self, skill_content):
        lines = skill_content.splitlines()
        anchor_idx = next(
            (i for i, line in enumerate(lines) if "Custom start flows" in line),
            None,
        )
        assert anchor_idx is not None, (
            "SKILL.md missing 'Custom start flows' line"
        )
        window_start = max(0, anchor_idx - 5)
        window_end = min(len(lines), anchor_idx + 6)
        window = "\n".join(lines[window_start:window_end]).lower()
        assert "secretary" in window, (
            "Custom start flows note must reference the secretary as an example "
            "within \u00b15 lines of the anchor; a global substring check is not "
            "sufficient because unrelated mentions elsewhere in the file would "
            "silently satisfy it."
        )

    def test_secretary_has_after_briefing_section(self, secretary_content):
        assert "After Session Briefing" in secretary_content, (
            "pact-secretary.md missing 'After Session Briefing' section "
            "referenced by SKILL.md custom start flows note"
        )


class TestDeadReferencesToMovedOrchestratorCore:
    """L9 (T13): Dead-reference guard for #452 file relocation.

    The #452 refactor moved pact-orchestrator-core.md from
    pact-plugin/protocols/ to pact-plugin/skills/orchestration/SKILL.md.
    No live text under pact-plugin/ (commands, protocols, agents, skills,
    tests, hooks, templates, reference) may reference the old path
    'pact-orchestrator-core' — such a reference is either a missed-update
    from #452 or a forked copy of the content (both of which silently
    break the dual-purpose SSOT invariant).

    Counter-test-by-revert: reinstate a reference to
    'pact-orchestrator-core' anywhere under pact-plugin/ — this test
    fails, catching the drift before merge.
    """

    PLUGIN_ROOT = Path(__file__).parent.parent
    SEARCH_SUBDIRS = (
        "agents",
        "commands",
        "hooks",
        "protocols",
        "reference",
        "skills",
        "telegram",
        "templates",
        "tests",
    )
    SCANNED_SUFFIXES = (".py", ".md", ".json", ".txt", ".yml", ".yaml", ".toml")
    BANNED_SUBSTRING = "pact-orchestrator-core"
    # Self-exclusion: this file must reference the banned substring to
    # define the guard. Listing it here keeps the exclusion explicit.
    SELF_EXCLUDED_FILES = frozenset({"tests/test_cross_references.py"})

    def _scan_root(self, plugin_root: Path) -> list[str]:
        """Banned-substring hits under one plugin root, tracked files only.

        A working-tree walk reads an untracked scratch file as shipped.
        """
        hits = []

        def _scan(path: Path) -> None:
            if not path.is_file():
                return
            if path.suffix not in self.SCANNED_SUFFIXES:
                return
            rel = path.relative_to(plugin_root)
            if str(rel) in self.SELF_EXCLUDED_FILES:
                return
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return
            if self.BANNED_SUBSTRING in text:
                hits.append(str(rel))

        # Pass 1: tracked files in the live subdirectories.
        for subdir in self.SEARCH_SUBDIRS:
            if not (plugin_root / subdir).exists():
                continue
            for path in _tracked_glob(plugin_root, f"{subdir}/**/*",
                                      allow_empty=True):
                _scan(path)

        # Pass 2: tracked top-level plugin files (README.md,
        # pyrightconfig.json, LICENSE-adjacent files, etc.) — files directly
        # under the root that aren't in any SEARCH_SUBDIRS entry.
        for path in _tracked_files(plugin_root, ".", "*", allow_empty=True):
            _scan(path)
        return hits

    def test_no_live_references_to_old_orchestrator_core_path(self):
        """Scan every .py/.md/.json/.txt/.yml/.yaml/.toml file under
        pact-plugin/'s live subdirectories AND top-level plugin files
        for the banned substring. Zero hits expected post-#452."""
        hits = self._scan_root(self.PLUGIN_ROOT)
        assert not hits, (
            f"Found {len(hits)} file(s) still referencing the banned "
            f"substring {self.BANNED_SUBSTRING!r} (pre-#452 path). "
            f"These are either missed-update sites from #452 or forked "
            f"copies of the moved content; both break the dual-purpose "
            f"SSOT invariant for the orchestration skill. Hits: {hits}"
        )

    def test_an_untracked_command_needle_is_not_scanned(self, tmp_path):
        """An untracked scratch command must not join the reference scan.

        Positive control: a TRACKED file carrying the banned substring IS
        reported, so an empty scan cannot pass this arm.
        """
        root = tmp_path / "plugin"
        (root / "commands").mkdir(parents=True)
        tracked = root / "commands" / "tracked.md"
        tracked.write_text(f"{self.BANNED_SUBSTRING}\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(root)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "add", "-A"],
                       check=True, capture_output=True)
        needle = root / "commands" / "zzprobe.md"
        needle.write_text(f"{self.BANNED_SUBSTRING}\n", encoding="utf-8")

        population = _tracked_glob(root, "commands/**/*", allow_empty=True)

        assert tracked in population, "the tracked control is missing from the population"
        assert needle not in population, (
            f"untracked needle entered the scan population: {needle}")
        assert self._scan_root(root) == ["commands/tracked.md"], (
            f"the untracked needle was scanned: {needle}")


class TestLeadSideHaltFanOutSlugStability:
    """L10: Stability of the `lead-side-halt-fan-out` slug.

    The canonical `### Lead-Side HALT Fan-Out` heading lives in
    protocols/algedonic.md (post-v4.0.0 migration) and is referenced from
    4 consumer files via the GitHub-rendered slug `lead-side-halt-fan-out`.
    Renaming the heading silently breaks every cross-reference (link still
    resolves to the file, just lands at the page top instead of the
    section). This test pins the slug by asserting:

    - the canonical heading text is present at the SSOT site (algedonic.md), and
    - each of the consumer files contains the exact slug fragment.
    """

    CANONICAL_HEADING = "### Lead-Side HALT Fan-Out"
    SLUG = "lead-side-halt-fan-out"

    # algedonic.md hosts the canonical heading AND 2 inline self-anchors;
    # 3 other consumer files each carry one external xref.
    CROSS_REF_FILES = [
        ("protocols/algedonic.md", ALGEDONIC_PATH),
        ("commands/orchestrate.md", ORCHESTRATE_PATH),
        ("protocols/pact-protocols.md", PACT_PROTOCOLS_PATH),
        ("protocols/pact-communication-charter.md", COMM_CHARTER_PATH),
    ]

    def test_canonical_heading_present_in_algedonic(self):
        content = ALGEDONIC_PATH.read_text(encoding="utf-8")
        assert self.CANONICAL_HEADING in content, (
            f"protocols/algedonic.md missing canonical heading "
            f"{self.CANONICAL_HEADING!r}. Renaming this heading breaks "
            "every cross-reference to the slug "
            f"{self.SLUG!r}; restore the exact text or update all "
            "consumer files in lockstep."
        )

    def test_canonical_heading_renders_to_expected_slug(self):
        """GitHub auto-slugs `### Lead-Side HALT Fan-Out` to
        `lead-side-halt-fan-out`. Verify the canonical site contains the
        slug as a self-anchor (`[...](#lead-side-halt-fan-out)`) which
        transitively confirms the lower-casing/hyphenation rule the
        consumers rely on.
        """
        content = ALGEDONIC_PATH.read_text(encoding="utf-8")
        assert f"#{self.SLUG}" in content, (
            f"protocols/algedonic.md does not contain the self-anchor "
            f"`#{self.SLUG}` that proves the slug-rendering rule. The "
            "canonical heading must use exactly the casing/punctuation "
            f"that GitHub auto-slugs to {self.SLUG!r}."
        )

    @pytest.mark.parametrize(
        "label,path",
        CROSS_REF_FILES,
        ids=[label for label, _ in CROSS_REF_FILES],
    )
    def test_cross_ref_uses_slug(self, label, path):
        content = path.read_text(encoding="utf-8")
        assert f"#{self.SLUG}" in content, (
            f"{label} missing cross-reference to slug "
            f"{self.SLUG!r}. The HALT fan-out idiom lives at "
            "protocols/algedonic.md and is referenced from this file via "
            "a slug-link; if the heading was renamed, propagate the new "
            "slug to every consumer site."
        )


# L11: the archive step's resolution pointer.
#
# The pointer text in the agent body. It is DESCRIPTIVE. It names the skill and
# the ROLE of the step. It does not name the subcommand, so a rename of the
# subcommand cannot dangle it.
RESOLUTION_POINTER = "the resolution step your `pact-handoff-harvest` skill defines"

# What identifies the step the pointer aims at. The step is a fenced code block
# that calls the harvest CLI and passes the context file.
HARVEST_CLI_FILE = "pact_harvest.py"
RESOLUTION_FLAG = "--context-file"


def _fenced_blocks(text):
    """Every fenced code block body in a markdown document."""
    return re.findall(r"^```[^\n]*\n(.*?)^```", text, re.MULTILINE | re.DOTALL)


def _resolution_blocks(skill_text):
    """Blocks that call the harvest CLI and pass the context file."""
    return [
        body
        for body in _fenced_blocks(skill_text)
        if HARVEST_CLI_FILE in body and RESOLUTION_FLAG in body
    ]


class TestArchiveResolutionPointerResolves:
    """L11: the archive step points at a resolution step that is present.

    `agents/pact-secretary.md` tells the secretary to resolve its session
    directory with the resolution step that the `pact-handoff-harvest` skill
    defines. Nothing pinned that pointer. Measured before this pin shipped:
    delete the resolution paragraph and its code block from the skill, and the
    whole suite stayed green. The pointer aimed at nothing.

    WHAT THIS KEYS ON, AND WHY NOT THE TWO OBVIOUS THINGS.

    Not the SUBCOMMAND name. A rename of it is maintenance and not breakage,
    the pointer is descriptive so a rename cannot dangle it, and
    `test_pact_harvest_cli.py` guards the name at 10 sites.

    Not the step HEADING. A heading is prose. An ordinary reword turns a
    heading pin red against correct text, and nothing else in this repository
    reads that heading.

    This keys on the code block that calls the harvest CLI and passes
    `--context-file`. A change to that flag is large, the CLI consumes it, and
    the CLI tests pin it at 8 sites. The flag appears once in the skill, so the
    predicate selects one block and not the artifact-resolution block beside it.

    MEASURED ACROSS FIVE MUTATIONS of the skill, before this pin was written:
    removal RED, subcommand rename green, `SESSION_DIR` rename green, heading
    reword green, pointer deleted RED.

    The same pattern for another rule is
    `test_index_upkeep_pointer_resolves_to_a_rule_that_exists` in
    `test_agent_memory_cap_single_source.py`. This copy sits here rather than
    beside it because that module has one subject, the index limits, and this
    pin shares its technique and not its subject.
    """

    @pytest.fixture
    def secretary_content(self):
        return SECRETARY_PATH.read_text(encoding="utf-8")

    @pytest.fixture
    def harvest_skill_content(self):
        return HARVEST_SKILL_PATH.read_text(encoding="utf-8")

    def test_pointer_is_present_in_the_agent_body(self, secretary_content):
        """Half one. The pointer must be there for the target half to matter."""
        assert RESOLUTION_POINTER in secretary_content, (
            f"agents/pact-secretary.md no longer carries the resolution "
            f"pointer {RESOLUTION_POINTER!r}. If the archive step was changed "
            f"to name the command directly, delete this class with it. If the "
            f"wording drifted, update the constant. Do NOT delete the pointer "
            f"and leave the step unreferenced: a hand-built path from a slug "
            f"and a session id is the failure that step prevents."
        )

    def test_pointer_resolves_to_one_resolution_step(self, harvest_skill_content):
        """Half two. The step the pointer aims at must be present, and be one.

        The count carries the two directions. Zero means the step went away and
        the pointer dangles. More than one means the predicate lost the
        specificity that makes it name the resolution step and not another
        call to the same CLI.
        """
        blocks = _resolution_blocks(harvest_skill_content)
        assert len(blocks) == 1, (
            f"the pointer in agents/pact-secretary.md aims at the resolution "
            f"step in skills/pact-handoff-harvest/SKILL.md, and that skill now "
            f"holds {len(blocks)} block(s) that call {HARVEST_CLI_FILE} with "
            f"{RESOLUTION_FLAG}.\n"
            f"ZERO means the step was removed and the pointer dangles. The "
            f"secretary is told to follow a step that is no longer written, so "
            f"restore the step or re-point the agent body.\n"
            f"MORE THAN ONE means a second block now matches, so this pin no "
            f"longer names one step. Narrow the predicate rather than raise "
            f"the count."
        )
