"""Location: pact-plugin/tests/test_peer_review_verify_only_report_pin.py
Summary: Presence pin on the verify-only re-review report contract in
         commands/peer-review.md. The contract is two required report lines —
         Reader false flags and Declined items — the Format-row amendment that
         announces them, and the explicit exclusion of the cold lane.
Used by: pytest suite; guards the contract against silent removal.

CEILING: a presence pin certifies TEXT, never behaviour. It cannot show that
a lead copies the two lines into a dispatch, or that a reviewer writes them.
It can only show that the instruction asking for that still exists, in the
section where the lead reads it, and that the cold lane is still scoped out.
The relay itself is prose and stays unenforceable here.

ORACLE: the expected block is the oracle for its own subject. A single edit
that changes both commands/peer-review.md and EXPECTED_BLOCK together passes
every arm in this module, so the test diff, not the suite, is what a reviewer
reads to catch that. This is inherent to a verbatim pin, not a gap an arm can
close.

SCOPE: the region arms read their own bounded region; the file-wide arm reads
the whole command file. Only the block arms compare whitespace-normalized
text; the FormatRow arms compare raw text, because the row is one table line
whose exact bytes are the shipped contract.

WHITESPACE: the block arms normalize whitespace runs, so a source reflow
cannot redden a pin whose sentences are unchanged. The trade is that a
whitespace-only edit inside the block also passes. The liveness control below
exercises the real path — it reads the command file, re-wraps the verify-only
region, splices it back, and asserts the block arm still counts 1 — and it
asserts the re-wrap defeats RAW comparison, so switching the block arm to raw
text reddens the control rather than passing silently.
"""

from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
PEER_REVIEW = PLUGIN_ROOT / "commands" / "peer-review.md"

VERIFY_ONLY_START = "### Verify-Only Re-Review"
VERIFY_ONLY_END = "### Post-Remediation Incremental Update"
COLD_LANE_START = "### Cold Reviewer Lane"
COLD_LANE_END = "### Reviewer Teachback"

REPORT_BLOCK_START = "**Verify-only report lines.**"
# The block is three paragraphs and ends where the following blockquote note
# begins. A blockquote boundary is structural: unlike the short phrase
# "and why." it replaced, no stray sentence inside the block can reproduce it
# and truncate the extraction.
REPORT_BLOCK_TERMINATOR = "\n> "
# A token that must exist inside the cold-lane section, so the absence arms
# below are checking a live section rather than an empty one.
COLD_LANE_POSITIVE_CONTROL = "RE-REVIEW CHECKLIST"

FORMAT_ROW = (
    "| **Format** | Checklist: finding → resolved / not resolved / new issue, "
    "plus the two warm report lines below | Full review with severity ratings |"
)

EXPECTED_BLOCK = """\
**Verify-only report lines.** Put these two lines in every warm verify-only dispatch, verbatim — in the review task's description when the round creates a fresh review task, and in the `SendMessage` when the reviewer is reused:

- **Reader false flags**: the check you ran, each result it reported as absent or changed that was in fact present, and each correction you made in the check rather than in the artifact. When it reported none, write `Reader false flags: none — <the check you ran>`; otherwise write `Reader false flags: <the check you ran> — <each wrong result>; <each correction in the check>`.
- **Declined items**: each checklist item or review criterion you did not satisfy, with the measured evidence that the artifact is correct. When you satisfied every item, write `Declined items: none`.

Neither line is a finding: journal neither as a `review_finding`, and count neither in a finding class. A warm verify-only report that omits either line is unauditable: send it back for the missing line instead of synthesizing its verdict. Neither line applies to the cold lane: the cold lane's own coverage boundary with its receipt governs there."""

LABELS = ("Reader false flags", "Declined items")

# The two dispatch routes plus the journal exclusion the block must name.
RELAY_TOKENS = (
    ("the review task's description", "the fresh-review-task route"),
    ("SendMessage", "the reused-reviewer route"),
    ("review_finding", "the journal exclusion"),
)


def _normalize(text: str) -> str:
    """Collapse every whitespace run to one space, so wrapping is neutral."""
    return " ".join(text.split())


def _command_text() -> str:
    """Read the shipped command file; fail the test rather than raise."""
    try:
        return PEER_REVIEW.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - guarded lookup
        pytest.fail(f"cannot read {PEER_REVIEW}: {exc}")


def _bounded(text: str, start: str, end: str, label: str) -> str:
    """Return the region from `start` up to, not including, `end`.

    Guarded: a missing anchor or an inverted pair fails the calling test with
    a message naming the boundary, instead of raising from str.find.
    """
    start_at = text.find(start)
    if start_at < 0:
        pytest.fail(f"{label}: missing start anchor {start!r}")
    end_at = text.find(end, start_at + len(start))
    if end_at < 0:
        pytest.fail(f"{label}: missing end anchor {end!r} after {start!r}")
    return text[start_at:end_at]


def _verify_only_region(text: str) -> str:
    return _bounded(text, VERIFY_ONLY_START, VERIFY_ONLY_END, "verify-only region")


def _cold_lane_region(text: str) -> str:
    return _bounded(text, COLD_LANE_START, COLD_LANE_END, "cold lane section")


def _block_count(text: str) -> int:
    """How many times the expected contract block occurs in text's region.

    THE SINGLE PATH for the block arm and its liveness control. The control
    passes only if it is running the same comparison the arm runs, so a
    control that cannot fail is not possible here without the arm failing too.
    """
    region = _verify_only_region(text)
    return _normalize(region).count(_normalize(EXPECTED_BLOCK))


def _report_block(region: str) -> str:
    """The contract block, from its first words to the following blockquote.

    The end is structural: the block's three paragraphs run until the next
    block type, the `> **New issue verification**` note. The suffix check
    below is a post-condition, not the boundary. Both failure messages name
    the observed condition and the live causes, because a blockquote added
    inside the block and a de-blockquoted following note need different
    repairs and neither is a truncation of the contract text.
    """
    start_at = region.find(REPORT_BLOCK_START)
    if start_at < 0:
        pytest.fail(f"contract block: missing start marker {REPORT_BLOCK_START!r}")
    end_at = region.find(REPORT_BLOCK_TERMINATOR, start_at + len(REPORT_BLOCK_START))
    if end_at < 0:
        pytest.fail(
            "contract block boundary not found — no blockquote line ends the "
            "block. Two live causes: the note that follows the block was "
            "de-blockquoted, or the block is now the last content before the "
            "next section heading. Restore the boundary or change "
            "REPORT_BLOCK_TERMINATOR deliberately."
        )
    block = region[start_at:end_at]
    if not _normalize(EXPECTED_BLOCK).endswith(_normalize(block)):
        pytest.fail(
            "contract block boundary mismatch — the extracted span is not the "
            "tail of EXPECTED_BLOCK. Two live causes: a blockquote sits inside "
            "the block, so extraction stopped early; or extraction ran past "
            "the block because the block is no longer immediately followed by "
            "the note. The block's own text is owned by the equality arms, "
            "not here."
        )
    return block


class TestContractBlock:
    """The full contract, exactly once, inside the verify-only section."""

    def test_block_present_once_in_verify_only_region(self):
        text = _command_text()
        assert _block_count(text) == 1, (
            "the verify-only report contract is missing or duplicated inside "
            f"{VERIFY_ONLY_START!r} — the two report lines must appear exactly "
            "once, between the Format table and the New issue verification note"
        )

    def test_block_present_once_in_whole_file(self):
        text = _normalize(_command_text())
        needle = _normalize(EXPECTED_BLOCK)
        assert text.count(needle) == 1, (
            "the verify-only report contract must have a single home: found "
            f"{text.count(needle)} copies in the command file, expected 1"
        )


class TestFormatRow:
    """The amended Format cell names the two warm report lines, once."""

    def test_amended_format_row_present_once(self):
        text = _command_text()
        assert text.count(FORMAT_ROW) == 1, (
            "the Format row must carry ', plus the two warm report lines "
            f"below' exactly once: found {text.count(FORMAT_ROW)} copies"
        )

    def test_amended_format_row_lives_in_verify_only_region(self):
        region = _verify_only_region(_command_text())
        assert region.count(FORMAT_ROW) == 1, (
            "the amended Format row must sit inside the verify-only region, "
            "where the lead reads it"
        )


class TestRegionBoundary:
    """The verify-only region is exactly the section its anchors name."""

    def test_region_contains_no_other_heading(self):
        region = _verify_only_region(_command_text())
        # The region string begins AT its own "### " heading, so a heading
        # inserted inside the section is the NEWLINE-PREFIXED form only.
        assert "\n### " not in region, (
            "a '###' section was inserted inside the verify-only region, so "
            "the region is now wider than the section its anchors name. "
            "Review the insertion and move the bound deliberately."
        )


class TestLabelOracle:
    """The absence arms' subjects must exist, or their pass is vacuous."""

    def test_each_label_occurs_in_the_expected_block(self):
        expected = _normalize(EXPECTED_BLOCK)
        for label in LABELS:
            assert label in expected, (
                f"LABELS contains {label!r} but EXPECTED_BLOCK does not. The "
                "cold-lane absence arms would then search for a string that "
                "no longer exists anywhere, and pass without testing a "
                "boundary. Update LABELS with the contract wording."
            )


class TestColdLaneExclusion:
    """The contract's labels stay out of the cold lane, so the boundary is real."""

    @pytest.mark.parametrize("label", LABELS)
    def test_label_absent_from_cold_lane(self, label):
        cold = _cold_lane_region(_command_text())
        assert COLD_LANE_POSITIVE_CONTROL in cold, (
            f"the cold-lane section no longer contains its known-present token "
            f"{COLD_LANE_POSITIVE_CONTROL!r}. The absence arm below would pass "
            "on an empty or gutted section, so its result would mean nothing."
        )
        assert label not in cold, (
            f"{label!r} leaked into the cold lane section — the contract is "
            "warm-only, and the cold lane receives findings rather than a "
            "checklist"
        )


class TestRelayAndJournalExclusion:
    """The block names both relay routes and the finding-class exclusion."""

    @pytest.mark.parametrize("token,lost", RELAY_TOKENS)
    def test_token_present_in_block(self, token, lost):
        block = _normalize(_report_block(_verify_only_region(_command_text())))
        assert token in block, (
            f"the contract block lost {token!r} — it carries {lost}"
        )

    def test_unauditable_trigger_present(self):
        block = _normalize(_report_block(_verify_only_region(_command_text())))
        assert "unauditable" in block, (
            "the contract block lost its 'unauditable' trigger — a report "
            "missing either line must be sent back, not synthesized"
        )


class TestNormalizationLiveness:
    """The block arm's normalization must be live, and this control must be
    able to fail: switching the arm to a raw comparison reddens it."""

    def test_live_region_rewrapped_still_matches(self):
        text = _command_text()
        region = _verify_only_region(text)
        rewrapped = _rewrap(region)
        spliced = text.replace(region, rewrapped, 1)

        assert spliced != text, (
            "the re-wrap changed nothing, so this control is vacuous"
        )
        assert _block_count(spliced) == 1, (
            "the block arm no longer matches the live command file once its "
            "verify-only region is re-wrapped — a harmless reflow would "
            "redden the pin"
        )
        # THE FAILURE DIRECTION: if _block_count switched to a raw comparison,
        # the re-wrapped region would not contain EXPECTED_BLOCK verbatim and
        # the arm above would read 0. This assertion is what makes that switch
        # redden here instead of passing silently.
        assert EXPECTED_BLOCK not in rewrapped, (
            "the re-wrap left EXPECTED_BLOCK byte-identical, so it cannot "
            "prove a raw comparison would fail; this control is vacuous"
        )


def _rewrap(text: str, width: int = 60) -> str:
    """Break at spaces only, so no word is split."""
    lines: list[str] = []
    line = ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    if line:
        lines.append(line)
    return "\n".join(lines)
