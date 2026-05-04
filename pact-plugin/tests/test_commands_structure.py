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
    ("two_task_anchor", "Two-Task Dispatch Shape"),
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
    two-task anchor, addBlockedBy call), assert it appears in at least one
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

