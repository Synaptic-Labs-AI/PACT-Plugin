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

ORACLE: the expected block is the oracle for the two block-equality arms.
Coordinated edits to commands/peer-review.md and EXPECTED_BLOCK stay green
on those arms; the independent LABELS, RELAY_TOKENS and unauditable arms
still catch changes to their own tokens. Semantic drift that preserves all
of those tokens needs review of the test diff, not another equality arm.

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
# The warm dispatch paragraph follows the lane. Its position, rather than the
# first horizontal rule, anchors the lane's closing rule and excludes the warm
# dispatch labels without truncating a lane that contains its own rules.
COLD_LANE_FOLLOWER = "**Dispatch reviewers**"
COLD_LANE_SEPARATOR = "\n---\n"

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
TABLE_END_ROW = (
    "| **Duration** | Significantly faster than initial review | Full review cycle |"
)
NEW_ISSUE_NOTE = "> **New issue verification**"

EXPECTED_BLOCK = """\
**Verify-only report lines.** Put these two lines in every warm verify-only dispatch, verbatim — in the description of the warm reviewer task the round creates for the review work, and in the `SendMessage` when the reviewer is reused:

- **Reader false flags**: for each check you ran, give its relevant input scope and outcome — its exit status, or its test summary with passed, failed, and error counts, including the error when it failed — each result it reported as absent or changed that was in fact present, and each correction you made in the check rather than in the artifact. A check that errors, has empty input or an empty test selection, cannot read its target, or targets the wrong input makes verification incomplete: it cannot use the clean empty form or support `resolved`. When a completed, correctly scoped check reported no false flags, write `Reader false flags: none — <the check, its input scope, and its outcome>`; otherwise write `Reader false flags: <the check, its input scope, and its outcome> — <each wrong result>; <each correction in the check>`.
- **Declined items**: each checklist item or review criterion you did not satisfy, with the measured evidence that the artifact is correct. When you satisfied every item, write `Declined items: none`.

Neither line is a finding: journal neither as a `review_finding`, and count neither in a finding class. A warm verify-only report that omits either line is unauditable: send it back for the missing line instead of synthesizing its verdict. Neither line applies to the cold lane: its existing finding-status `RE-REVIEW CHECKLIST` and coverage boundary with its receipt govern there."""

LABELS = ("Reader false flags", "Declined items")

# The two dispatch routes plus the journal exclusion the block must name.
RELAY_TOKENS = (
    ("for the review work", "the review-work route"),
    ("SendMessage", "the reused-reviewer route"),
    ("review_finding", "the journal exclusion"),
)


def _normalize(text: str) -> str:
    """Collapse every whitespace run to one space, so wrapping is neutral."""
    return " ".join(text.split())


def _command_text(path: Path = PEER_REVIEW) -> str:
    """Read the shipped command file; fail the test rather than raise."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        pytest.fail(f"cannot read {path}: {exc}")


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
    """The cold lane, excluding its closing rule and the warm follower.

    Anchoring on the follower keeps internal horizontal rules inside the lane.
    Requiring the closing rule immediately before that follower fails loudly
    when the boundary moves instead of swallowing the warm dispatch block.
    """
    start_at = text.find(COLD_LANE_START)
    if start_at < 0:
        pytest.fail(f"cold lane section: missing start anchor {COLD_LANE_START!r}")
    follower_at = text.find(COLD_LANE_FOLLOWER, start_at + len(COLD_LANE_START))
    if follower_at < 0:
        pytest.fail(
            "cold lane section: missing warm dispatch follower "
            f"{COLD_LANE_FOLLOWER!r} — the region bound has changed"
        )
    body = text[start_at:follower_at].rstrip()
    if not body.endswith("\n---"):
        pytest.fail(
            "the cold-lane separator is missing or no longer immediately "
            f"before {COLD_LANE_FOLLOWER!r} — re-anchor the boundary deliberately"
        )
    return body[: -len("\n---")]


def _assert_contract_placement(region: str) -> None:
    """The contract immediately follows the table and precedes the note."""
    table_at = region.find(TABLE_END_ROW)
    if table_at < 0:
        pytest.fail(f"verify-only region: missing table end row {TABLE_END_ROW!r}")
    table_end = table_at + len(TABLE_END_ROW)
    block_at = region.find(REPORT_BLOCK_START)
    note_at = region.find(NEW_ISSUE_NOTE)
    if block_at < 0 or note_at < 0:
        pytest.fail("verify-only region: contract block or New-issue note is missing")
    if not table_end <= block_at < note_at:
        pytest.fail(
            "contract block must follow the comparison table and precede the "
            "New-issue note; its current order breaks the Format row's 'below' route"
        )
    if region[table_end:block_at].strip():
        pytest.fail(
            "contract block must immediately follow the comparison table; "
            "intervening content separates it from the Format row"
        )


def _block_count(text: str) -> int:
    """How many times the expected contract block occurs in text's region.

    THE SINGLE PATH for the block arm and its liveness control. The control
    passes only if it is running the same comparison the arm runs, so a
    control that cannot fail is not possible here without the arm failing too.
    """
    region = _verify_only_region(text)
    return _normalize(region).count(_normalize(EXPECTED_BLOCK))


def _report_block(region: str, *, check_end: bool = True) -> str:
    """The contract block, from its first words to the following blockquote.

    The end is structural: the block's three paragraphs run until the next
    block type, the `> **New issue verification**` note. Both failure messages
    name the observed condition and the live causes, because a blockquote
    added inside the block and a de-blockquoted following note need different
    repairs and neither is a truncation of the contract text.

    `check_end=False` skips the suffix post-condition ONLY. The extraction is
    still bounded by REPORT_BLOCK_TERMINATOR, so a caller that opts out gets
    the same block span, never the rest of the region. The token arms opt out
    so their own named message is reachable when a relay phrase is removed;
    the post-conditioned path stays alive in TestBlockBoundaryPaths, which is
    what keeps the truncation diagnosis from disappearing.
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
    if check_end and not _normalize(EXPECTED_BLOCK).endswith(_normalize(block)):
        pytest.fail(
            "contract block boundary mismatch — the extracted span is not the "
            "tail of EXPECTED_BLOCK. Three live causes: a blockquote sits "
            "inside the block, so extraction stopped early; extraction ran "
            "past the block because the block is no longer immediately "
            "followed by the note; or the block's own text was edited, which "
            "the equality arms report."
        )
    return block


class TestCommandReader:
    def test_invalid_utf8_uses_named_diagnostic(self, tmp_path):
        path = tmp_path / "invalid-peer-review.md"
        path.write_bytes(b"\xff")
        with pytest.raises(pytest.fail.Exception) as excinfo:
            _command_text(path)
        message = str(excinfo.value)
        assert "cannot read" in message and str(path) in message and "utf-8" in message, (
            "invalid UTF-8 must produce the reader's named path diagnostic, "
            f"not an unhandled UnicodeError; got: {message!r}"
        )


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

    def test_block_immediately_follows_table_and_precedes_note(self):
        _assert_contract_placement(_verify_only_region(_command_text()))

    def test_relocation_above_table_fails_placement(self):
        region = _verify_only_region(_command_text())
        table_at = region.index("| Aspect | Verify-Only | Full Review |")
        block_at = region.index(REPORT_BLOCK_START)
        relocated = (
            region[:table_at]
            + region[block_at:].rstrip()
            + "\n\n"
            + region[table_at:block_at]
        )
        assert relocated != region, "the relocation did not change the region"
        with pytest.raises(pytest.fail.Exception) as excinfo:
            _assert_contract_placement(relocated)
        assert "must follow the comparison table" in str(excinfo.value)


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
            f"{label!r} leaked into the cold lane section — the two new report "
            "lines are warm-only; the cold lane keeps its finding-status "
            "RE-REVIEW CHECKLIST and coverage-boundary receipt"
        )


class TestColdLaneBound:
    """The cold-lane region must end at the lane's own separator.

    The lane is followed by the warm `**Dispatch reviewers**` block, which the
    contract tells the lead to populate with exactly the labels the absence
    arms forbid. A region bounded by the next heading instead would fire on
    the contract's own instruction — the wrong-reason red this bound closes.
    """

    def test_region_excludes_the_warm_dispatch_block(self):
        region = _cold_lane_region(_command_text())
        assert "**Dispatch reviewers**" not in region, (
            "the cold-lane region swallowed the warm dispatch block — a label "
            "written there would redden the absence arms for the wrong reason"
        )

    def test_region_keeps_the_positive_control(self):
        region = _cold_lane_region(_command_text())
        assert COLD_LANE_POSITIVE_CONTROL in region, (
            f"the narrower bound lost {COLD_LANE_POSITIVE_CONTROL!r}, so the "
            "absence arms' positive control no longer proves the section is live"
        )

    def test_label_in_the_warm_dispatch_does_not_reach_the_region(self):
        text = _command_text()
        start = text.index(COLD_LANE_START)
        follower = text.index(COLD_LANE_FOLLOWER, start) + len(COLD_LANE_FOLLOWER)
        injected = text[:follower] + "Reader false flags in the warm dispatch\n" + text[follower:]
        assert injected != text, "the injection did not change the text"
        assert "Reader false flags" not in _cold_lane_region(injected), (
            "a label written in the warm dispatch block reached the cold-lane "
            "region, so the absence arm fires on what the contract asks for"
        )

    def test_label_inside_the_cold_lane_reaches_the_region(self):
        text = _command_text()
        start = text.index(COLD_LANE_START) + len(COLD_LANE_START)
        injected = text[:start] + "\n\nReader false flags leaked here.\n" + text[start:]
        assert injected != text, "the injection did not change the text"
        assert "Reader false flags" in _cold_lane_region(injected), (
            "a label written inside the cold lane did not reach the region, "
            "so the absence arms have stopped testing the lane"
        )

    def test_internal_rule_does_not_hide_a_later_cold_label(self):
        text = _command_text()
        start = text.index(COLD_LANE_START)
        follower = text.index(COLD_LANE_FOLLOWER, start)
        insert_at = text.index("Send the re-check message by resume:", start, follower)
        injected = (
            text[:insert_at]
            + "---\n\nReader false flags after an internal cold-lane rule.\n\n"
            + text[insert_at:]
        )
        cold = _cold_lane_region(injected)
        assert COLD_LANE_POSITIVE_CONTROL in cold, "the mutation lost the live-section control"
        assert "Reader false flags after an internal cold-lane rule" in cold, (
            "an internal horizontal rule truncated the cold-lane region and "
            "hid a later label from the absence arms"
        )

    def test_missing_separator_fails_loudly(self):
        text = _command_text()
        start = text.index(COLD_LANE_START)
        follower = text.index(COLD_LANE_FOLLOWER, start)
        sep_at = text.rfind(COLD_LANE_SEPARATOR, start, follower)
        mutated = text[:sep_at] + "\n\n" + text[sep_at + len(COLD_LANE_SEPARATOR):]
        with pytest.raises(pytest.fail.Exception) as excinfo:
            _cold_lane_region(mutated)
        assert "separator is missing" in str(excinfo.value), (
            "a lost separator must fail loudly on its own name, not widen the "
            f"region silently; got: {str(excinfo.value)!r}"
        )


class TestRelayAndJournalExclusion:
    """The block names both relay routes and the finding-class exclusion."""

    @pytest.mark.parametrize("token,lost", RELAY_TOKENS)
    def test_token_present_in_block(self, token, lost):
        block = _normalize(
            _report_block(_verify_only_region(_command_text()), check_end=False)
        )
        assert token in block, (
            f"the contract block lost {token!r} — it carries {lost}"
        )

    def test_unauditable_trigger_present(self):
        block = _normalize(
            _report_block(_verify_only_region(_command_text()), check_end=False)
        )
        assert "unauditable" in block, (
            "the contract block lost its 'unauditable' trigger — a report "
            "missing either line must be sent back, not synthesized"
        )


class TestBlockBoundaryPaths:
    """The post-conditioned and post-condition-free paths must stay distinct.

    The token arms opt out of the suffix check so their own named message is
    reachable when a relay phrase is removed. This class keeps the opt-out from
    swallowing the truncation diagnosis: the same truncated-block input must
    REDDEN the post-conditioned path and RETURN on the opt-out path, and the
    opt-out path must still stop at the terminator rather than searching the
    whole region.
    """

    def _truncated_region(self) -> str:
        """The live region with a blockquote injected inside the block."""
        region = _verify_only_region(_command_text())
        marker = "Neither line is a finding:"
        assert marker in region, "the block's final paragraph marker moved"
        injected = region.replace(marker, "> injected blockquote\n\n" + marker, 1)
        assert injected != region, "the injection did not change the region"
        return injected

    def test_live_block_satisfies_the_boundary_post_condition(self):
        """The post-conditioned path must stay alive on the LIVE command file.

        Without this arm every caller of _report_block opts out, so a real
        blockquote inserted inside the block would red the equality arms
        without ever printing the boundary diagnosis they exist to give.

        WHAT THIS ARM DOES NOT PROVE: it supplies the LIVE call site, not the
        deletion detection. It passes if the post-condition is disabled. The
        sibling `test_post_conditioned_path_names_the_cause_on_a_truncated_block`
        is the arm that catches a dead post-condition — cite that one, not this
        canary, as evidence the post-condition exists.
        """
        block = _report_block(_verify_only_region(_command_text()))
        assert block, "the post-conditioned path returned nothing"

    def test_post_conditioned_path_names_the_cause_on_a_truncated_block(self):
        with pytest.raises(pytest.fail.Exception) as excinfo:
            _report_block(self._truncated_region())
        message = str(excinfo.value)
        assert "not the tail of EXPECTED_BLOCK" in message, (
            "the post-conditioned path no longer reports the boundary "
            f"mismatch; got: {message!r}"
        )
        assert "blockquote sits inside the block" in message, (
            "the post-conditioned failure lost its named cause; got: "
            f"{message!r}"
        )

    def test_opt_out_path_returns_the_truncated_block_without_the_post_condition(self):
        block = _normalize(_report_block(self._truncated_region(), check_end=False))
        assert block, "the opt-out path returned nothing"
        # The two paths differ on exactly this input: one raises, one returns.
        assert "Neither line is a finding:" not in block, (
            "the opt-out extraction ran past the terminator — 'raw' must skip "
            "the suffix check only, never search the whole region"
        )
        assert "New issue verification" not in block, (
            "the opt-out extraction swallowed the note that follows the "
            "block, so a token arm could pass on text outside the block"
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
