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


class TestPactRoleTeammateInConsumerCommands:
    """All consumer command files must inline the canonical YOUR PACT ROLE: teammate
    marker (#366 R5 L3).

    Background: bootstrap.md is the canonical source for the Task() dispatch
    form, but five consumer commands (orchestrate, peer-review, comPACT,
    rePACT, plan-mode) inline the same form because LLM readers under token
    pressure don't follow cross-references reliably. The "YOUR PACT ROLE: teammate ("
    substring is the load-bearing marker that the multi-layer routing
    mechanism (session_init.py + peer_inject.py) uses to detect a teammate
    spawn and inject the bootstrap directive.

    If a consumer file silently drops the marker — for example a future
    refactor that compresses the dispatch template into a "see bootstrap.md"
    pointer — teammates spawned via that command will not load the bootstrap
    skill and will be silently demoted from PACT specialists to plain Claude
    Code agents. This test pins the marker as a structural invariant.
    """

    CONSUMER_COMMANDS = [
        "orchestrate",
        "peer-review",
        "comPACT",
        "rePACT",
        "plan-mode",
    ]

    @pytest.mark.parametrize("name", CONSUMER_COMMANDS)
    def test_contains_canonical_pact_role(self, name):
        path = COMMANDS_DIR / f"{name}.md"
        assert path.exists(), f"Consumer command file missing: {name}.md"
        text = path.read_text(encoding="utf-8")
        assert "YOUR PACT ROLE: teammate (" in text, (
            f"{name}.md must contain canonical 'YOUR PACT ROLE: teammate (' "
            "marker — load-bearing for the routing chain that promotes a "
            "freshly spawned teammate to a PACT specialist via "
            "Skill('PACT:teammate-bootstrap'). See skills/orchestration/SKILL.md and the "
            "'Canonical Task() dispatch is mirrored inline at every consumer "
            "site' pinned-context entry for context."
        )


class TestTwoTaskDispatchShapeInConsumerCommands:
    """The Task A + Task B dispatch shape must be encoded in every consumer
    command file that spawns teammates.

    Why this is structural, not behavioral: the dispatch shape is described
    in skills/orchestration/SKILL.md but consumer commands inline it because
    LLM readers under token pressure don't follow cross-references reliably
    (same rationale as the canonical PACT ROLE marker test above). If a
    consumer command silently drops the inline anchor, lead-side dispatch
    for that workflow degrades to single-task form and the teachback gate
    becomes optional rather than mandatory.

    Pinned literals:
    - "Two-Task Dispatch Shape" — the section heading anchor in each file.
    - "addBlockedBy" — the API call shape that creates the blockedBy chain.

    Distinct from `addBlockedBy=[A]` which the architect spec used as
    illustrative shorthand; production form is the API call name itself
    (e.g. `addBlockedBy=[A_id]` or `addBlockedBy=[<Task A id>]`).
    """

    CONSUMER_COMMANDS = [
        "orchestrate",
        "peer-review",
        "comPACT",
        "rePACT",
        "plan-mode",
    ]

    @pytest.mark.parametrize("name", CONSUMER_COMMANDS)
    def test_contains_two_task_dispatch_shape_anchor(self, name):
        path = COMMANDS_DIR / f"{name}.md"
        text = path.read_text(encoding="utf-8")
        assert "Two-Task Dispatch Shape" in text, (
            f"{name}.md must contain 'Two-Task Dispatch Shape' section anchor "
            "(inline per architect D7 — canonical Task() prompt preserved, "
            "dispatch shape mirrored at consumer sites). Drop signals "
            "either reverted #491 work or a refactor that compressed the "
            "anchor into a cross-reference; both fail the agent-reader-primary "
            "axiom (LLM readers under token pressure don't follow xrefs)."
        )

    @pytest.mark.parametrize("name", CONSUMER_COMMANDS)
    def test_contains_add_blocked_by_call(self, name):
        path = COMMANDS_DIR / f"{name}.md"
        text = path.read_text(encoding="utf-8")
        assert "addBlockedBy" in text, (
            f"{name}.md must contain 'addBlockedBy' (the API call that wires "
            "Task B's blockedBy=[A] dependency). Without this, the data-layer "
            "unblock that resolves on Task A completion is never created — "
            "Task B becomes immediately claimable and the teachback gate "
            "is bypassed."
        )
