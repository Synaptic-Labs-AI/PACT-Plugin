"""Location: pact-plugin/tests/test_peer_review_verify_only_report_pin.py
Summary: Presence pin on the verify-only re-review report contract in
         commands/peer-review.md. The contract is two required report lines —
         Reader false flags and Declined items — the Format-row amendment
         that announces them, and the explicit exclusion of the cold lane.
         Every arm is bounded to its own region, so a token living in a
         sibling section cannot satisfy it.
Used by: pytest suite; guards the contract against silent removal.

CEILING: a presence pin certifies TEXT, never behaviour. It cannot show that
a lead copies the two lines into a dispatch, or that a reviewer writes them.
It can only show that the instruction asking for that still exists, in the
section where the lead reads it, and that the cold lane is still scoped out.
The relay itself is prose and stays unenforceable here.

WHITESPACE: arms compare whitespace-normalized text, so source re-wrapping
cannot redden a pin whose sentences are unchanged. The trade is that a
whitespace-only edit inside the block also passes; the liveness control
below proves the normalization is doing that job rather than making an arm
vacuous.
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
# "and why." it replaces, no stray sentence inside the block can reproduce it
# and truncate the extraction.
REPORT_BLOCK_TERMINATOR = "\n> "
# The block's final sentence, asserted as a post-condition so a truncation
# names its cause rather than surfacing as an unexplained missing token.
COLD_LANE_EXCLUSION = (
    "Neither line applies to the cold lane: it receives findings rather than a "
    "checklist, and its coverage-boundary receipt already requires what it "
    "read, what it did not, and why."
)

FORMAT_ROW = (
    "| **Format** | Checklist: finding → resolved / not resolved / new issue, "
    "plus the two report lines below | Full review with severity ratings |"
)

EXPECTED_BLOCK = """\
**Verify-only report lines.** Put these two lines in every warm verify-only dispatch, verbatim — in the Task B description when the round creates a fresh review task, and in the `SendMessage` when the reviewer is reused:

- **Reader false flags**: the check you ran, each result it reported as absent or changed that was in fact present, and each correction you made in the check rather than in the artifact. When it reported none, write `Reader false flags: none — <the check you ran>`.
- **Declined items**: each checklist item you did not satisfy, with the measured evidence that the artifact is correct. When you satisfied every item, write `Declined items: none`.

Neither line is a finding: journal neither as a `review_finding`, and count neither in a finding class. A warm verify-only report that omits either line is unauditable: send it back for the missing line instead of synthesizing its verdict. Neither line applies to the cold lane: it receives findings rather than a checklist, and its coverage-boundary receipt already requires what it read, what it did not, and why."""

LABELS = ("Reader false flags", "Declined items")

# The two dispatch routes plus the journal exclusion the block must name.
RELAY_TOKENS = (
    ("Task B description", "the fresh-review-task route"),
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


def _report_block(region: str) -> str:
    """The contract block, from its first words to the following blockquote.

    The end is structural: the block's three paragraphs run until the next
    block type, the `> **New issue verification**` note. The final-sentence
    check below is a post-condition, not the boundary — a blockquote added
    inside the block is what could still cut the extraction short, and the
    message names that cause rather than leaving a truncated relay failure.
    """
    block = _bounded(
        region, REPORT_BLOCK_START, REPORT_BLOCK_TERMINATOR, "contract block"
    )
    if not _normalize(block).endswith(COLD_LANE_EXCLUSION):
        pytest.fail(
            "report block truncated — expected the cold-lane exclusion "
            "sentence at the end"
        )
    return block


class TestContractBlock:
    """The full contract, exactly once, inside the verify-only section."""

    def test_block_present_once_in_verify_only_region(self):
        text = _command_text()
        region = _normalize(_verify_only_region(text))
        needle = _normalize(EXPECTED_BLOCK)
        assert region.count(needle) == 1, (
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
    """The amended Format cell names the two report lines, once."""

    def test_amended_format_row_present_once(self):
        text = _command_text()
        assert text.count(FORMAT_ROW) == 1, (
            "the Format row must carry ', plus the two report lines below' "
            f"exactly once: found {text.count(FORMAT_ROW)} copies"
        )

    def test_amended_format_row_lives_in_verify_only_region(self):
        region = _verify_only_region(_command_text())
        assert region.count(FORMAT_ROW) == 1, (
            "the amended Format row must sit inside the verify-only region, "
            "where the lead reads it"
        )


class TestColdLaneExclusion:
    """The contract's labels stay out of the cold lane, so the boundary is real."""

    @pytest.mark.parametrize("label", LABELS)
    def test_label_absent_from_cold_lane(self, label):
        cold = _bounded(
            _command_text(), COLD_LANE_START, COLD_LANE_END, "cold lane section"
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
    """A wrapping-only change must not be able to make the block arm vacuous."""

    def test_wrapped_copy_of_block_still_matches(self):
        wrapped = _rewrap(EXPECTED_BLOCK)
        assert wrapped != EXPECTED_BLOCK, "the re-wrap produced no line breaks"
        assert _normalize(wrapped) == _normalize(EXPECTED_BLOCK), (
            "whitespace normalization no longer makes a re-wrapped contract "
            "match — the block arm would redden on a harmless reflow"
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