"""
Location: pact-plugin/tests/test_peer_review_prose_pin.py
Summary: Canonical-substring + count pin on the merge-readiness lint-read
         instruction in commands/peer-review.md. That step tells the reviewer
         to run the repository's fail-closed local lint gate where one is
         defined AND read the PR's checks and the inline review threads
         before merge
         readiness; pin the dual-action requirement exactly so it cannot
         silently rot out of the shipped prose, while incidental rewording
         outside the pinned phrase floats. The step-scope scan is BOUNDED by
         the merge-readiness step's own heading anchors, so relocating the
         instruction into a later step fails the pin instead of passing.
Used by: pytest suite. Follows the TestCommandProsePin convention in
         test_import_hygiene.py (exact canonical substring + count).
"""

from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent
PEER_REVIEW = PLUGIN_ROOT / "commands" / "peer-review.md"

MERGE_READINESS_STEP_START = "5. State merge readiness"
NEXT_STEP_HEADING = "6. **Calibration save**"


class TestPeerReviewMergeReadinessProsePin:
    """The merge-readiness step must name BOTH actions — run the repository's
    fail-closed local lint gate and read the PR's checks and the inline
    review threads —
    in one contiguous canonical phrase, exactly once, inside that step alone.
    Dropping either action, splitting the phrase, or moving it out of the
    step reds this test."""

    CANONICAL_INSTRUCTION = (
        "run the repository's fail-closed local lint gate where one is "
        "defined, and read the PR's checks (`gh pr checks <N>`) and the "
        "inline review threads"
    )

    def test_canonical_instruction_present_exactly_once(self):
        content = PEER_REVIEW.read_text(encoding="utf-8")
        assert content.count(self.CANONICAL_INSTRUCTION) == 1, (
            "peer-review.md must carry the lint-read instruction exactly "
            "once; found "
            f"{content.count(self.CANONICAL_INSTRUCTION)}"
        )

    def test_instruction_lives_inside_the_merge_readiness_step(self):
        content = PEER_REVIEW.read_text(encoding="utf-8")
        start = content.find(MERGE_READINESS_STEP_START)
        assert start != -1, "peer-review.md lost the merge-readiness step"
        end = content.find(NEXT_STEP_HEADING, start)
        assert end != -1, (
            "peer-review.md structure changed after the merge-readiness "
            "step — update this pin's step-heading anchors"
        )
        assert self.CANONICAL_INSTRUCTION in content[start:end], (
            "lint-read instruction drifted out of the merge-readiness step"
        )
