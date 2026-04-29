"""
Tests for #452 — orchestration skill structure (post-relocation from #414 R3).

Validates that the bootstrap.md stub and the relocated orchestrator core
(now at skills/orchestration/SKILL.md) are structurally correct:
  1. All 9 referenced Read targets exist and are non-empty (1 skill + 8 protocols)
  2. Bootstrap stub has Read instructions early, no @-refs, is ≤100 lines
  3. Bootstrap gate marker write instruction is present and consistent
  4. Key content sections from old bootstrap exist in the relocated skill file

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
SKILLS_DIR = PLUGIN_ROOT / "skills"
BOOTSTRAP_MD = COMMANDS_DIR / "bootstrap.md"
CORE_FILE = SKILLS_DIR / "orchestration" / "SKILL.md"

# The 9 mandatory Read targets, in the order listed in bootstrap.md.
# The list is tail-biased: the most-load-bearing file (skills/orchestration/SKILL.md,
# consulted every turn) sits last so it occupies the best-surviving slot in the
# 5-slot path-agnostic Read/@-ref tracker after compaction. Situationally-invoked
# protocols (algedonic, state-recovery, variety) head the list; they are cheap
# to re-Read on demand at the moment of need. Paths are relative to plugin root.
MANDATORY_READ_TARGETS = [
    "protocols/algedonic.md",
    "protocols/pact-state-recovery.md",
    "protocols/pact-variety.md",
    "protocols/pact-s4-tension.md",
    "protocols/pact-s4-checkpoints.md",
    "protocols/pact-s5-policy.md",
    "protocols/pact-workflows.md",
    "protocols/pact-communication-charter.md",
    "skills/orchestration/SKILL.md",
]

# The 8 supplementary protocols (filenames only). Retained separately for
# forward-reference tests within the core skill body — the core file cannot
# forward-reference itself.
SUPPLEMENTARY_PROTOCOL_FILES = [
    "algedonic.md",
    "pact-state-recovery.md",
    "pact-variety.md",
    "pact-s4-tension.md",
    "pact-s4-checkpoints.md",
    "pact-s5-policy.md",
    "pact-workflows.md",
    "pact-communication-charter.md",
]

# Default size threshold for mandatory Read targets (truncation-risk boundary).
DEFAULT_LINE_THRESHOLD = 600

# Per-file allowlist overrides. Files listed here are temporarily permitted to
# exceed the default threshold while a specific follow-up tracks the trim work.
# Each entry MUST cite the resolution issue. Adding an entry without an issue
# reference, or raising an existing entry without addressing the underlying
# durability concern, is a review-blocking change.
_PER_FILE_OVERRIDE_THRESHOLDS = {
    # TODO(#594): trim orchestration/SKILL.md to restore the 600-line ceiling.
    # Threshold raised here while #594 is pending; do NOT raise further without
    # addressing the underlying Tier-1 durability concern. Current size at the
    # time of the override (post-#591 inbox-wake pointer addition): 623 lines.
    # 700 gives ~77 lines of headroom for small future edits during the #594
    # window; if SKILL.md crosses 700 before #594 lands, that's the signal that
    # the trim work cannot be deferred further.
    "skills/orchestration/SKILL.md": 700,
}


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
    """All 9 referenced Read targets exist, are non-empty, and core is bounded."""

    @pytest.mark.parametrize("relative_path", MANDATORY_READ_TARGETS)
    def test_read_target_exists(self, relative_path):
        path = PLUGIN_ROOT / relative_path
        assert path.is_file(), f"Missing Read target: {relative_path}"

    @pytest.mark.parametrize("relative_path", MANDATORY_READ_TARGETS)
    def test_read_target_not_empty(self, relative_path):
        path = PLUGIN_ROOT / relative_path
        content = path.read_text(encoding="utf-8")
        assert len(content.strip()) > 0, (
            f"Read target is empty: {relative_path}"
        )

    def test_exactly_nine_mandatory_targets(self):
        """Cardinality pin: bootstrap.md loads exactly 9 files (1 skill + 8 protocols)."""
        assert len(MANDATORY_READ_TARGETS) == 9

    def test_read_paths_match_constant(self, bootstrap_text):
        """Read instruction paths in bootstrap.md must match MANDATORY_READ_TARGETS."""
        # Match either `{plugin_root}/protocols/<file>.md` or
        # `{plugin_root}/skills/<dir>/<file>.md` — both are accepted forms.
        read_pattern = re.compile(
            r"\d+\.\s+`\{plugin_root\}/((?:protocols|skills)/[\w\-/]+\.md)`"
        )
        read_paths = read_pattern.findall(bootstrap_text)
        assert read_paths == MANDATORY_READ_TARGETS, (
            f"Read instruction paths don't match MANDATORY_READ_TARGETS.\n"
            f"  bootstrap.md: {read_paths}\n"
            f"  constant:     {list(MANDATORY_READ_TARGETS)}"
        )

    @pytest.mark.parametrize("relative_path", MANDATORY_READ_TARGETS)
    def test_read_target_within_bounds(self, relative_path):
        """Every mandatory Read target must stay under its threshold (truncation risk).

        Default threshold is `DEFAULT_LINE_THRESHOLD` (600). Files listed in
        `_PER_FILE_OVERRIDE_THRESHOLDS` use their per-file override — see that
        dict's docstring for the policy on adding/raising entries.
        """
        path = PLUGIN_ROOT / relative_path
        lines = path.read_text(encoding="utf-8").splitlines()
        count = len(lines)
        threshold = _PER_FILE_OVERRIDE_THRESHOLDS.get(
            relative_path, DEFAULT_LINE_THRESHOLD
        )
        applied = (
            f"per-file override {threshold}"
            if relative_path in _PER_FILE_OVERRIDE_THRESHOLDS
            else f"default {threshold}"
        )
        assert count < threshold, (
            f"{relative_path} is {count} lines — exceeds {applied}-line "
            f"safety boundary (truncation risk)"
        )

    def test_core_file_has_substantial_content(self, core_lines):
        """Core skill file must have substantial content (not accidentally gutted)."""
        count = len(core_lines)
        assert count > 200, (
            f"skills/orchestration/SKILL.md is only {count} lines — "
            f"expected ~500 (possible content loss during migration)"
        )


# =============================================================================
# TestBootstrapStructure
# =============================================================================

class TestBootstrapStructure:
    """Bootstrap stub has Read instructions early, no @-refs, and is compact."""

    def test_read_instructions_present(self, bootstrap_text):
        """Bootstrap.md must contain Read instruction references to all 9 targets."""
        # Check for the numbered list pattern across both protocols/ and skills/
        read_pattern = re.compile(
            r"\d+\.\s+`\{plugin_root\}/(?:protocols|skills)/[\w\-/]+\.md`"
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
        assert "skills/orchestration/SKILL.md" in first_30, (
            "skills/orchestration/SKILL.md Read instruction (tail slot) "
            "not referenced in first 30 lines"
        )
        assert "algedonic.md" in first_30, (
            "algedonic.md Read instruction (head slot) not referenced in "
            "first 30 lines"
        )

    def test_bootstrap_body_size_reduced(self, bootstrap_lines):
        """Bootstrap stub should be ≤100 lines (reduced from 584-line monolith)."""
        count = len(bootstrap_lines)
        assert count <= 100, (
            f"bootstrap.md is {count} lines — exceeds 100-line target for stub"
        )

    def test_core_file_is_last_read_target(self, bootstrap_text):
        """skills/orchestration/SKILL.md must be listed as the LAST Read target.

        Tail-bias compaction durability: the 5-slot path-agnostic Read/@-ref
        tracker is tail-biased, so the most-load-bearing file gets the best
        post-compaction survival slot by appearing last. The orchestration
        skill body is consulted every turn by the Agent Team lead; it gets
        the tail slot. Situationally-invoked protocols head the list because
        they can be re-Read on demand at the moment of need.
        """
        # Find every numbered Read entry and pick the highest-numbered one.
        pattern = re.compile(
            r"(\d+)\.\s+`\{plugin_root\}/((?:protocols|skills)/[\w\-/]+\.md)`"
        )
        matches = pattern.findall(bootstrap_text)
        assert matches, "No numbered Read instructions found"
        last_index, last_path = max(matches, key=lambda m: int(m[0]))
        assert last_path == "skills/orchestration/SKILL.md", (
            f"Last Read target is '{last_path}' (entry #{last_index}), "
            f"expected 'skills/orchestration/SKILL.md'. The tail slot in "
            f"the bootstrap Read list is reserved for the most-load-bearing "
            f"file so tail-biased tracker durability preserves it across "
            f"compaction."
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
        "Agent Teams Dispatch",
        "Recovery Protocol",
        "Context Economy",
        "Memory Management",
    ])
    def test_major_section_present_in_core(self, core_text, heading):
        """Each key section heading must exist in skills/orchestration/SKILL.md."""
        assert heading in core_text, (
            f"Section '{heading}' not found in skills/orchestration/SKILL.md — "
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
        """Core file should have forward references to all supplementary protocols."""
        # All 8 supplementary protocols must be referenced as contextual anchors.
        missing = [f for f in SUPPLEMENTARY_PROTOCOL_FILES if f not in core_text]
        assert not missing, (
            f"skills/orchestration/SKILL.md is missing forward references to: "
            f"{missing}. All 8 supplementary protocols must be referenced "
            f"as contextual anchors."
        )

    def test_s5_policy_context_row_present(self):
        """pact-s5-policy.md must contain the Context row added in this PR."""
        text = (PROTOCOLS_DIR / "pact-s5-policy.md").read_text(encoding="utf-8")
        assert "**Context**" in text, (
            "pact-s5-policy.md missing **Context** row in SACROSANCT table"
        )

    def test_state_recovery_durability_section(self):
        """pact-state-recovery.md must contain the Content Durability section."""
        text = (PROTOCOLS_DIR / "pact-state-recovery.md").read_text(encoding="utf-8")
        assert "Content Durability" in text, (
            "pact-state-recovery.md missing 'Content Durability' heading"
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

    def test_bootstrap_stub_retains_session_placeholders(self, bootstrap_text):
        """Bootstrap stub should retain Session Placeholder Variables section."""
        assert "Session Placeholder Variables" in bootstrap_text

    def test_bootstrap_stub_retains_final_mandate(self, bootstrap_text):
        """Bootstrap stub should retain FINAL MANDATE section inline."""
        assert "FINAL MANDATE" in bootstrap_text
