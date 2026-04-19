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
        assert "secretary" in skill_content.lower() and "Custom start flows" in skill_content, (
            "Custom start flows note must reference the secretary as an example"
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
