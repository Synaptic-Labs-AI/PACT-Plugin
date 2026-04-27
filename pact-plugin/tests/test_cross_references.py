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
"""
from pathlib import Path

import pytest


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
        content = path.read_text(encoding="utf-8")
        assert "CLAUDE.md" in content and "gitignored" in content, (
            f"{label} missing CLAUDE.md worktree scope warning"
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

    def test_no_live_references_to_old_orchestrator_core_path(self):
        """Scan every .py/.md/.json/.txt/.yml/.yaml/.toml file under
        pact-plugin/'s live subdirectories AND top-level plugin files
        for the banned substring. Zero hits expected post-#452."""
        hits = []

        def _scan(path: Path) -> None:
            if not path.is_file():
                return
            if path.suffix not in self.SCANNED_SUFFIXES:
                return
            rel = path.relative_to(self.PLUGIN_ROOT)
            if str(rel) in self.SELF_EXCLUDED_FILES:
                return
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return
            if self.BANNED_SUBSTRING in text:
                hits.append(str(rel))

        # Pass 1: subdirectory walk (agents/, commands/, hooks/, etc.).
        for subdir in self.SEARCH_SUBDIRS:
            root = self.PLUGIN_ROOT / subdir
            if not root.exists():
                continue
            for path in root.rglob("*"):
                _scan(path)

        # Pass 2: top-level plugin files (README.md, pyrightconfig.json,
        # LICENSE-adjacent files, etc.) — files directly under PLUGIN_ROOT
        # that aren't in any SEARCH_SUBDIRS entry.
        for path in self.PLUGIN_ROOT.iterdir():
            _scan(path)

        assert not hits, (
            f"Found {len(hits)} file(s) still referencing the banned "
            f"substring {self.BANNED_SUBSTRING!r} (pre-#452 path). "
            f"These are either missed-update sites from #452 or forked "
            f"copies of the moved content; both break the dual-purpose "
            f"SSOT invariant for the orchestration skill. Hits: {hits}"
        )


class TestLeadSideHaltFanOutSlugStability:
    """L10: Stability of the `lead-side-halt-fan-out` slug.

    The canonical `#### Lead-Side HALT Fan-Out` heading lives in
    skills/orchestration/SKILL.md and is referenced from 4 distinct
    consumer files via the GitHub-rendered slug `lead-side-halt-fan-out`
    (algedonic.md hosts 2 link occurrences, the other three each host 1,
    for 5 link occurrences across 4 files). Renaming the heading
    silently breaks every cross-reference (link still resolves to the
    file, just lands at the page top instead of the section). This test
    pins the slug by asserting:

    - the canonical heading text is present at the SSOT site, and
    - each of the 4 consumer files contains the exact slug fragment.

    Counter-test-by-revert: rename the canonical heading (e.g., to
    "Lead-Side Halt Fanout") — the SSOT assertion fails. Drop the slug
    from any consumer file — that site's assertion fails.
    """

    CANONICAL_HEADING = "#### Lead-Side HALT Fan-Out"
    SLUG = "lead-side-halt-fan-out"

    CROSS_REF_FILES = [
        ("protocols/algedonic.md", ALGEDONIC_PATH),
        ("commands/orchestrate.md", ORCHESTRATE_PATH),
        ("protocols/pact-protocols.md", PACT_PROTOCOLS_PATH),
        ("protocols/pact-communication-charter.md", COMM_CHARTER_PATH),
    ]

    def test_canonical_heading_present_in_orchestration_skill(self):
        content = ORCHESTRATION_SKILL_PATH.read_text(encoding="utf-8")
        assert self.CANONICAL_HEADING in content, (
            f"skills/orchestration/SKILL.md missing canonical heading "
            f"{self.CANONICAL_HEADING!r}. Renaming this heading breaks "
            "every cross-reference to the slug "
            f"{self.SLUG!r}; restore the exact text or update all 4 "
            "consumer files in lockstep."
        )

    def test_canonical_heading_renders_to_expected_slug(self):
        """GitHub auto-slugs `#### Lead-Side HALT Fan-Out` to
        `lead-side-halt-fan-out` (lowercased, spaces → hyphens, special
        chars stripped). Verify the canonical site itself contains the
        slug as a self-anchor in its consumer body — `[…](#lead-side-halt-fan-out)` —
        which transitively confirms the lower-casing/hyphenation rule
        the consumers rely on.
        """
        content = ORCHESTRATION_SKILL_PATH.read_text(encoding="utf-8")
        assert f"#{self.SLUG}" in content, (
            f"skills/orchestration/SKILL.md does not contain the self-anchor "
            f"`#{self.SLUG}` that proves the slug-rendering rule. The "
            "canonical heading must use exactly the casing/punctuation "
            "that GitHub auto-slugs to "
            f"{self.SLUG!r}."
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
            "skills/orchestration/SKILL.md and is referenced from this "
            "file via a slug-link; if the heading was renamed, propagate "
            "the new slug to every consumer site."
        )
