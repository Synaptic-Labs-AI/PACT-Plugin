"""
Tests for #414 R3 — bootstrap.md restructure for compaction durability.

Validates that the restructured bootstrap.md (stub) and the new
pact-orchestrator-core.md (wholesale content move) are structurally correct:
  1. All 9 referenced protocol files exist and are non-empty
  2. Bootstrap stub has Read instructions early, no @-refs, is ≤100 lines
  3. Bootstrap gate marker write instruction is present and consistent
  4. Key content sections from old bootstrap exist in core file

Stage 2 empirical compaction verification is OUT OF SCOPE for this file —
that requires manual fresh-session testing.
"""

import re
import sys
from pathlib import Path

import pytest

# Add hooks directory to path so we can import shared constants
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from shared import BOOTSTRAP_MARKER_NAME

# ----- Paths ----------------------------------------------------------------

PLUGIN_ROOT = Path(__file__).parent.parent
COMMANDS_DIR = PLUGIN_ROOT / "commands"
PROTOCOLS_DIR = PLUGIN_ROOT / "protocols"
BOOTSTRAP_MD = COMMANDS_DIR / "bootstrap.md"
CORE_FILE = PROTOCOLS_DIR / "pact-orchestrator-core.md"

# The 9 mandatory Read targets, in the order listed in bootstrap.md
MANDATORY_PROTOCOL_FILES = [
    "pact-orchestrator-core.md",
    "pact-s5-policy.md",
    "pact-s4-checkpoints.md",
    "pact-s4-tension.md",
    "pact-variety.md",
    "pact-workflows.md",
    "pact-communication-charter.md",
    "pact-state-recovery.md",
    "algedonic.md",
]


# ----- Fixtures --------------------------------------------------------------

@pytest.fixture(scope="module")
def bootstrap_text():
    return BOOTSTRAP_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def bootstrap_lines(bootstrap_text):
    return bootstrap_text.splitlines()


@pytest.fixture(scope="module")
def core_text():
    return CORE_FILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def core_lines(core_text):
    return core_text.splitlines()


# =============================================================================
# TestProtocolFileIntegrity
# =============================================================================

class TestProtocolFileIntegrity:
    """All 9 referenced protocol files exist, are non-empty, and core is bounded."""

    @pytest.mark.parametrize("filename", MANDATORY_PROTOCOL_FILES)
    def test_protocol_file_exists(self, filename):
        path = PROTOCOLS_DIR / filename
        assert path.is_file(), f"Missing protocol file: {filename}"

    @pytest.mark.parametrize("filename", MANDATORY_PROTOCOL_FILES)
    def test_protocol_file_not_empty(self, filename):
        path = PROTOCOLS_DIR / filename
        content = path.read_text(encoding="utf-8")
        assert len(content.strip()) > 0, f"Protocol file is empty: {filename}"

    def test_exactly_nine_mandatory_files(self):
        """Cardinality pin: the plan specifies exactly 9 mandatory protocol files."""
        assert len(MANDATORY_PROTOCOL_FILES) == 9

    def test_core_file_within_bounds(self, core_lines):
        """Core file should be ~500 lines, staying under 600 (truncation risk)."""
        count = len(core_lines)
        assert count < 600, (
            f"pact-orchestrator-core.md is {count} lines — exceeds 600-line "
            f"safety boundary (truncation risk)"
        )
        # Also verify it has substantial content (not accidentally gutted)
        assert count > 200, (
            f"pact-orchestrator-core.md is only {count} lines — expected ~500 "
            f"(possible content loss during migration)"
        )


# =============================================================================
# TestBootstrapStructure
# =============================================================================

class TestBootstrapStructure:
    """Bootstrap stub has Read instructions early, no @-refs, and is compact."""

    def test_read_instructions_present(self, bootstrap_text):
        """Bootstrap.md must contain Read instruction references to protocol files."""
        # Check for the numbered list pattern: "1. `{plugin_root}/protocols/..."
        read_pattern = re.compile(
            r"\d+\.\s+`\{plugin_root\}/protocols/[\w-]+\.md`"
        )
        matches = read_pattern.findall(bootstrap_text)
        assert len(matches) == 9, (
            f"Expected 9 Read instruction entries, found {len(matches)}"
        )

    def test_no_at_refs_remain(self, bootstrap_text):
        """No @-ref patterns should remain in bootstrap.md after restructure."""
        # @-refs look like @${CLAUDE_PLUGIN_ROOT}/... or @./... or @~/ etc
        at_ref_pattern = re.compile(r"@[\$\./~]")
        matches = at_ref_pattern.findall(bootstrap_text)
        assert len(matches) == 0, (
            f"Found {len(matches)} @-ref(s) still in bootstrap.md: {matches}"
        )

    def test_read_instructions_in_first_30_lines(self, bootstrap_lines):
        """Read instructions must appear in the first 30 lines for truncation resilience."""
        first_30 = "\n".join(bootstrap_lines[:30])
        assert "pact-orchestrator-core.md" in first_30, (
            "pact-orchestrator-core.md Read instruction not found in first 30 lines"
        )
        assert "algedonic.md" in first_30, (
            "Last protocol file (algedonic.md) not referenced in first 30 lines"
        )

    def test_bootstrap_body_size_reduced(self, bootstrap_lines):
        """Bootstrap stub should be ≤100 lines (reduced from 584-line monolith)."""
        count = len(bootstrap_lines)
        assert count <= 100, (
            f"bootstrap.md is {count} lines — exceeds 100-line target for stub"
        )

    def test_core_file_is_first_read_target(self, bootstrap_text):
        """pact-orchestrator-core.md must be listed as the FIRST Read target."""
        # Find the numbered list: item 1 should reference the core file
        pattern = re.compile(
            r"1\.\s+`\{plugin_root\}/protocols/([\w-]+\.md)`"
        )
        match = pattern.search(bootstrap_text)
        assert match is not None, "No numbered Read instruction #1 found"
        assert match.group(1) == "pact-orchestrator-core.md", (
            f"First Read target is '{match.group(1)}', expected 'pact-orchestrator-core.md'"
        )

    def test_load_operating_instructions_heading(self, bootstrap_text):
        """Bootstrap should have a 'Load Operating Instructions' heading."""
        assert "## Load Operating Instructions" in bootstrap_text

    def test_required_read_directive(self, bootstrap_text):
        """Bootstrap should contain explicit REQUIRED Read directive."""
        assert "**REQUIRED**" in bootstrap_text
        assert "Read ALL" in bootstrap_text or "Read tool" in bootstrap_text


# =============================================================================
# TestBootstrapGateCompatibility
# =============================================================================

class TestBootstrapGateCompatibility:
    """Marker write instruction is present and consistent with shared constant."""

    def test_marker_write_instruction_present(self, bootstrap_text):
        """Bootstrap must contain mkdir + touch instruction for marker file."""
        assert "mkdir -p" in bootstrap_text, (
            "bootstrap.md missing 'mkdir -p' directory creation instruction"
        )
        assert "touch" in bootstrap_text, (
            "bootstrap.md missing 'touch' marker write instruction"
        )

    def test_marker_name_consistent(self, bootstrap_text):
        """Marker name in bootstrap.md must match BOOTSTRAP_MARKER_NAME constant."""
        assert BOOTSTRAP_MARKER_NAME in bootstrap_text, (
            f"bootstrap.md does not contain marker name '{BOOTSTRAP_MARKER_NAME}'"
        )

    def test_coupling_comment_present(self, bootstrap_text):
        """Bootstrap should document the coupling with shared/__init__.py."""
        assert "BOOTSTRAP_MARKER_NAME" in bootstrap_text, (
            "bootstrap.md missing coupling comment referencing BOOTSTRAP_MARKER_NAME"
        )
        assert "shared" in bootstrap_text.lower(), (
            "bootstrap.md missing reference to shared module in coupling comment"
        )

    def test_bootstrap_confirmation_section(self, bootstrap_text):
        """Bootstrap must have a BOOTSTRAP CONFIRMATION section."""
        assert "## BOOTSTRAP CONFIRMATION" in bootstrap_text


# =============================================================================
# TestContentCompleteness
# =============================================================================

class TestContentCompleteness:
    """Major sections from old bootstrap.md exist in the core file."""

    @pytest.mark.parametrize("heading", [
        "S5 POLICY",
        "GUIDELINES",
        "Always Be Delegating",
        "PACT AGENT ORCHESTRATION",
    ])
    def test_major_section_present_in_core(self, core_text, heading):
        """Each key section heading must exist in pact-orchestrator-core.md."""
        assert heading in core_text, (
            f"Section '{heading}' not found in pact-orchestrator-core.md — "
            f"possible content loss during migration"
        )

    def test_sacrosanct_table_in_core(self, core_text):
        """Core file should contain the SACROSANCT non-negotiables table."""
        assert "SACROSANCT" in core_text
        # Verify key SACROSANCT rules are present
        assert "Security" in core_text
        assert "Quality" in core_text
        assert "Ethics" in core_text

    def test_context_economy_in_core(self, core_text):
        """Core file should contain the Context Economy guidance."""
        assert "Context Economy" in core_text or "context window is sacred" in core_text

    def test_delegation_principle_in_core(self, core_text):
        """Core file should contain delegation principle."""
        assert "delegate" in core_text.lower()

    def test_inline_summary_anchors_in_core(self, core_text):
        """Core file should have forward references to supplementary protocols."""
        # Check for references to at least some supplementary protocol files
        supplementary = [
            "pact-s5-policy.md",
            "pact-variety.md",
            "pact-workflows.md",
            "algedonic.md",
        ]
        found = sum(1 for f in supplementary if f in core_text)
        assert found >= 3, (
            f"Only {found} of {len(supplementary)} supplementary protocol "
            f"forward references found in core file — expected contextual anchors"
        )

    def test_bootstrap_stub_retains_mission(self, bootstrap_text):
        """Bootstrap stub should retain MISSION and MOTTO inline."""
        assert "# MISSION" in bootstrap_text
        assert "MOTTO" in bootstrap_text

    def test_bootstrap_stub_retains_instructions(self, bootstrap_text):
        """Bootstrap stub should retain INSTRUCTIONS section inline."""
        assert "## INSTRUCTIONS" in bootstrap_text

    def test_bootstrap_stub_retains_sacrosanct_failsafe(self, bootstrap_text):
        """Bootstrap stub should retain inline SACROSANCT fail-safe summary."""
        assert "SACROSANCT" in bootstrap_text

    def test_bootstrap_stub_retains_final_mandate(self, bootstrap_text):
        """Bootstrap stub should retain FINAL MANDATE section inline."""
        assert "FINAL MANDATE" in bootstrap_text
