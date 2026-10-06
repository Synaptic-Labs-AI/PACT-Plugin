"""
Tests for the pin-comment repair BEYOND a single line and a single state.

Location: pact-plugin/tests/test_pin_comment_pipeline.py

Summary: the sibling file `test_pin_comment_dominance.py` owns the cross-oracle
property, which is quantified over ONE line and evaluated on ONE parse. This
file owns what that shape cannot reach:

  * the TWO-STATE predicate `compute_deny_reason`, which compares a pre-edit
    parse against a post-edit parse and denies when post is strictly worse;
  * the STALE marker path, which shares the module and was left alone;
  * multi-pin regions, where attribution walks backward over several pins;
  * malformed regions, where the parser promises to fail open;
  * `>` carriers that the enumerated alphabets could not express, such as an
    HTML fragment, an XML close tag and an angle-bracket generic;
  * an anti-exponential tripwire on the changed date field.

WHY A SIBLING FILE. The dominance file counts a corpus and asserts a floor on
it. The cases here build documents and read charges. Mixing them lowers the
signal of both, because a document fixture that fails takes the corpus counts
down with it.

NON-VACUITY. Tests below that CANNOT be red against the previous code are
named as no-change controls in their own docstrings. Their content is that the
repair leaves those cases alone, so a red there would mean the change did
something it promised not to do.

Used with: hooks/pin_caps.py.
"""

import time

import pytest


STALE_MARKER = "<!-- STALE: Last relevant 2026-01-01 -->"


def two_pin_section(body_a, comment_b):
    """Build a two-pin body where pin B's comment sits in pin A's slice.

    `parse_pins(...)[0].body_chars` equals `len(body_a)` exactly when the
    strip removes pin B's comment in full.
    """
    return (
        "<!-- pinned: 2026-01-01 -->\n"
        f"### PinA\n{body_a}\n"
        f"{comment_b}\n"
        "### PinB\nshort\n"
    )


# ---------------------------------------------------------------------------
# `>` carriers.
#
# The enumerated alphabets in this arc were built from `a1-<>!,` plus a space.
# The carriers below hold characters and shapes that alphabet cannot express.
# Each one is a rationale a curator could realistically write in THIS
# repository, which documents markup grammars.
# ---------------------------------------------------------------------------

CARRIERS_WITH_GT = [
    "render <b>bold</b> inline",
    "ends with </tag> here",
    "the List<Map<K,V>> shape",
    "2 > 1 >= 0 > -1",
    "value >> 2 and 1 >> 0",
    "lambda x => x + 1",
    "quote: > cited line",
]

CARRIERS_WITHOUT_GT = [
    "see https://x.example/?a=1&b=2",
    "the rule — and its bound",
    "escape &gt; as an entity",
    "flow a → b then stop",
]


class TestPinCommentCarriers_Pipeline:
    """The trigger is ANY `>`, on shapes the enumerations could not express."""

    @pytest.mark.parametrize("carrier", CARRIERS_WITH_GT)
    def test_carrier_holding_a_greater_than_is_not_charged(self, carrier):
        from fixtures.pin_helpers import parse_pins

        body_a = "Pin A body, which the curator did not touch."
        comment = f"<!-- pinned: 2026-03-01, pin-size-override: {carrier} -->"
        pins = parse_pins(two_pin_section(body_a, comment))

        assert pins[0].body_chars == len(body_a), (
            f"pin A was charged {pins[0].body_chars} chars against a body of "
            f"{len(body_a)}. The excess is pin B's comment, which holds "
            f"{carrier!r}."
        )
        assert pins[1].date_comment == comment

    @pytest.mark.parametrize("carrier", CARRIERS_WITHOUT_GT)
    def test_carrier_without_a_greater_than_is_unaffected(self, carrier):
        """NO-CHANGE CONTROL. These shapes were never broken.

        They answer the other half of the question: the repair must not newly
        refuse a faithful rationale that carries a URL, an em-dash, an HTML
        entity or a Unicode arrow.
        """
        from fixtures.pin_helpers import parse_pins

        body_a = "Pin A body, which the curator did not touch."
        comment = f"<!-- pinned: 2026-03-01, pin-size-override: {carrier} -->"
        pins = parse_pins(two_pin_section(body_a, comment))

        assert pins[0].body_chars == len(body_a)
        assert pins[1].override_rationale == carrier

    def test_reconfirmed_clause_keeps_its_date_and_its_neighbour(self):
        """The reconfirmed shape fails BOTH oracles on the previous code.

        The pin lost its date attribution AND the neighbour was charged, so
        both halves belong in one assertion.
        """
        from fixtures.pin_helpers import parse_pins

        body_a = "Pin A body, which the curator did not touch."
        comment = ("<!-- pinned: 2026-03-01, reconfirmed: 2026-04-01 "
                   "because count > 2 -->")
        pins = parse_pins(two_pin_section(body_a, comment))

        assert pins[1].date_comment == comment
        assert pins[0].body_chars == len(body_a)


class TestPinCommentStale_Pipeline:
    """The STALE path shares the module and was deliberately left alone.

    `is_stale` reads the RAW body with `.search`, while `_extract_body_chars`
    works on a stripped copy. The widened strip therefore cannot reach the
    staleness signal. These tests pin that separation.
    """

    STALE_CASES = [
        ("marker alone", "body\n" + STALE_MARKER),
        ("date comment holding `>` then marker",
         "body\n<!-- pinned: 2026-01-01, a > b -->\n" + STALE_MARKER),
    ]

    # AN UNTERMINATED OPENER BEFORE THE MARKER MAKES THE MARKER UNREADABLE.
    # The fence-aware parser reads `<!--` as the start of an HTML comment that
    # runs to the first `-->`, which is the STALE row's own terminator, so the
    # marker may be text inside the user's comment. A comment closed by a row
    # that itself starts a comment is the parser's uncertain region: every row
    # from the opener on is UNKNOWN, no marker is read there, and in a whole
    # CLAUDE.md the Pinned section is not FOUND, so no reader acts on it.
    UNREADABLE_STALE_CASES = [
        ("unterminated opener holding `>` then marker",
         "body\n<!-- pinned: 2026-01-01, a > b\nmore\n" + STALE_MARKER),
        ("unterminated opener without `>` then marker",
         "body\n<!-- pinned: 2026-01-01 no close\nmore\n" + STALE_MARKER),
    ]

    @pytest.mark.parametrize(
        "label,body", STALE_CASES, ids=[c[0] for c in STALE_CASES]
    )
    def test_stale_signal_survives_the_widened_strip(self, label, body):
        """NO-CHANGE CONTROL on the signal. The charge test below discriminates."""
        from fixtures.pin_helpers import parse_pins

        pins = parse_pins(f"### P\n{body}\n")
        assert pins[0].is_stale is True, (
            f"the STALE marker stopped being seen for case {label!r}. "
            f"`is_stale` must read the RAW body, not the stripped copy."
        )

    @pytest.mark.parametrize(
        "label,body", UNREADABLE_STALE_CASES, ids=[c[0] for c in UNREADABLE_STALE_CASES]
    )
    def test_a_marker_inside_an_unterminated_comment_is_not_read(self, label, body):
        from fixtures.pin_helpers import parse_pins
        from shared.claude_md_markers import Kind, parse

        text = f"### P\n{body}\n"
        assert parse(text).lines[-1].kind is Kind.UNKNOWN, label
        assert parse_pins(text)[0].is_stale is False, label

    def test_a_greater_than_comment_beside_a_stale_marker_is_not_charged(self):
        """Both managed markers must leave the curator's budget alone."""
        from fixtures.pin_helpers import parse_pins

        body = ("body\n"
                "<!-- pinned: 2026-01-01, a > b -->\n"
                f"{STALE_MARKER}")
        pins = parse_pins(f"### P\n{body}\n")

        assert pins[0].body_chars == len("body")
        assert pins[0].is_stale is True

    def test_stale_block_threshold_is_unchanged_by_greater_than_comments(self):
        """A region whose every comment holds `>` still reports stale overflow."""
        from pin_caps import PIN_STALE_BLOCK_THRESHOLD, check_stale_block
        from fixtures.pin_helpers import parse_pins

        region = ""
        for i in range(3):
            region += (
                f"<!-- pinned: 2026-0{i + 1}-01, note > {i} -->\n"
                f"### Pin {i}\nbody {i}\n{STALE_MARKER}\n"
            )
        pins = parse_pins(region)

        assert len(pins) == 3
        assert sum(p.is_stale for p in pins) >= PIN_STALE_BLOCK_THRESHOLD
        violation = check_stale_block(pins)
        assert violation is not None and violation.kind == "stale"


class TestPinCommentMultiPin_Pipeline:
    """Attribution walks BACKWARD over several pins, and must not overreach."""

    def test_every_pin_in_a_six_pin_region_is_charged_only_its_own_body(self):
        from fixtures.pin_helpers import parse_pins

        bodies = [f"{'body ' * 8}pin {i}" for i in range(6)]
        region = ""
        for i, body in enumerate(bodies):
            region += (
                f"<!-- pinned: 2026-01-0{i + 1}, reason a > b for pin {i} -->\n"
                f"### Pin {i}\n{body}\n"
            )
        pins = parse_pins(region)

        assert len(pins) == 6
        assert [p.body_chars for p in pins] == [len(b) for b in bodies], (
            "at least one pin was charged for a neighbouring pin's comment"
        )
        assert all(p.date_comment is not None for p in pins), (
            "a pin lost its date attribution, so its age degrades in silence"
        )

    def test_backward_walk_takes_the_nearest_comment_over_blank_lines(self):
        """Two comments, blank lines between. The NEAREST one must win."""
        from fixtures.pin_helpers import parse_pins

        nearest = "<!-- pinned: 2026-03-03, second > marker -->"
        region = (
            "<!-- pinned: 2026-01-01 -->\n"
            "### PinA\nbody A\n"
            "\n"
            "<!-- pinned: 2026-02-02, first > marker -->\n"
            "\n"
            f"{nearest}\n"
            "\n"
            "### PinB\nbody B\n"
        )
        pins = parse_pins(region)

        assert pins[1].date_comment == nearest
        assert pins[0].body_chars == len("body A")


class TestPinCommentMalformed_Pipeline:
    """`parse_pins` promises fail-open. A malformed region must not raise."""

    MALFORMED = [
        ("unterminated comment, nothing after",
         "### P\nbody\n<!-- pinned: 2026-01-01, a > b\n", 1),
        ("comment with no heading after it",
         "### P\nbody\n<!-- pinned: 2026-01-01, a > b -->\n", 1),
        ("heading with no comment before it",
         "### P\nbody only\n### Q\nbody two\n", 2),
        ("comment before the first heading",
         "<!-- pinned: 2026-01-01, a > b -->\n### P\nbody\n", 1),
        ("empty region", "", 0),
        ("only a comment, no heading at all",
         "<!-- pinned: 2026-01-01, a > b -->\n", 0),
        ("closer with no opener", "### P\nbody --> tail\n", 1),
        ("opener inside prose, closer far away",
         "### P\nprose <!-- pinned: a > b more prose\nyet more\n--> tail\n", 1),
        ("nested openers, one closer",
         "### P\n<!-- pinned: <!-- pinned: x > y -->\n", 1),
        ("comment split across two lines",
         "### P\nbody\n<!-- pinned: 2026-01-01,\na > b -->\n", 1),
    ]

    @pytest.mark.parametrize(
        "label,region,expected_pins", MALFORMED, ids=[c[0] for c in MALFORMED]
    )
    def test_malformed_region_parses_without_raising(
        self, label, region, expected_pins
    ):
        """NO-CHANGE CONTROL on the pin count. The charge test below discriminates."""
        from fixtures.pin_helpers import parse_pins

        pins = parse_pins(region)
        assert len(pins) == expected_pins, (
            f"case {label!r} changed the pin count, which changes the count cap"
        )

    def test_a_trailing_comment_with_no_heading_is_still_stripped(self):
        """A pin whose comment has no following heading still loses it.

        This is the case that discriminates: the previous body class refused
        the `>` and charged the whole comment to the pin.
        """
        from fixtures.pin_helpers import parse_pins

        pins = parse_pins("### P\nbody\n<!-- pinned: 2026-01-01, a > b -->\n")
        assert pins[0].body_chars == len("body")


class TestPinCommentTwoState_Pipeline:
    """`compute_deny_reason` compares TWO parses, not one.

    A per-line property cannot reach this predicate, because the quantity it
    compares does not exist until two documents have been parsed. An edit is
    denied when the post state is strictly worse than the pre state, so a
    LOWER pre-state charge is not a safety margin. It is a lower bar for the
    post state to clear.
    """

    PROSE1 = "Alpha prose that belongs to pin A. " * 37
    PROSE2 = "Beta prose that also belongs to pin A. " * 18
    PIN_B_COMMENT = "<!-- pinned: 2026-03-03 -->"

    def _region(self, stray):
        lines = ["<!-- pinned: 2026-01-01 -->", "### PinA", self.PROSE1]
        if stray is not None:
            lines.append(stray)
        lines += [self.PROSE2, self.PIN_B_COMMENT, "### PinB", "short"]
        return "\n".join(lines) + "\n"

    def _verdict(self, stray):
        from pin_caps import compute_deny_reason
        from fixtures.pin_helpers import parse_pins

        pre = parse_pins(self._region(stray))
        post = parse_pins(self._region(None))
        return compute_deny_reason(pre, post)

    def test_repairing_a_stray_opener_is_not_denied(self):
        """The fault this file was written for. It is fixed; keep it fixed.

        A curator deletes a stray unterminated pin-comment opener. The edit
        removes characters and adds none, so no pin grew. Before the per-line
        strip the whole-body strip ran past the `>` to a terminator belonging
        to the NEXT pin, which under-counted the PRE state and made the
        unchanged POST state look like a regression.
        """
        stray = "<!-- pinned: 2026-02-02, note > threshold"
        assert self._verdict(stray) is None, (
            "the curator's repair of their own file was denied. The edit only "
            "removes characters, so no pin grew."
        )

    def test_a_stray_opener_without_a_greater_than_is_also_not_denied(self):
        """The PRE-EXISTING half of the same fault, and it is fixed too.

        This case shares every ingredient with the one above EXCEPT the `>`.
        That difference used to decide everything: without a `>` the older
        body class also ran forward, so BOTH code versions under-counted the
        pre-state and BOTH denied this repair. It was therefore not a
        regression introduced by the `>` widening — it was already there.

        The per-line strip closes both halves at once, which is why the
        remediation was scoped to the root cause rather than to the widening.
        The two tests now agree, and the `>` no longer decides the verdict.
        """
        stray = "<!-- pinned: 2026-02-02, note above threshold"
        assert self._verdict(stray) is None

    def test_removing_a_wellformed_comment_holding_a_greater_than_is_allowed(self):
        """The repair this change exists to make does NOT open a deny.

        A WELL-FORMED comment carrying a `>` is stripped by the new code and
        was charged by the old one. Deleting it lowers the charge on both, so
        no new deny appears. NO-CHANGE CONTROL, and it bounds the finding
        above to the malformed route.
        """
        stray = "<!-- pinned: 2026-02-02, note > threshold -->"
        assert self._verdict(stray) is None

    def test_a_non_pin_comment_opener_is_allowed_on_both_versions(self):
        """CONTROL. The strip needs the literal `pinned:` prefix.

        A stray opener that is not a pin comment is stripped by neither code
        version, so both read the same pre-state and both allow.
        """
        stray = "<!-- note: 2026-02-02, count > threshold"
        assert self._verdict(stray) is None


class TestPinCommentTwoStateWellFormed_Pipeline:
    """The same two-state SHAPE reached from a WELL-FORMED pre-state.

    The sibling class above needs a malformed pre-state. This one does not,
    and the two look identical until you ask WHICH SIDE IS MIS-MEASURING.

    The shared condition is that the stripping ADVANTAGE must SHRINK across
    the edit. That is NECESSARY for the two versions to disagree, and it is
    NOT SUFFICIENT for the disagreement to be a fault. A fault also needs the
    version holding the advantage to be the one measuring WRONGLY:

      * malformed route: the advantage comes from running past an
        unterminated marker and swallowing REAL PROSE. The current code is
        the wrong one, and its refusal is a defect.
      * this route: the advantage comes from correctly excluding a
        PLUGIN-MANAGED comment. The previous code is the wrong one, and the
        refusal is the cap working.

    So a repair should remove the first advantage and KEEP the second. This
    class therefore holds a RULED ACCEPTANCE rather than a pin on a defect.

    WHY IT REMAINS A SEPARATE CLASS. It was staged as a second tripwire
    because a repair could close the malformed route and leave this one, at
    which point the sibling marker would come off and this behaviour would
    ship unexamined. That is exactly what happened. The pin did its job, the
    behaviour was examined and then ruled correct, and the pin stays as a
    live record of the decision rather than being deleted.
    """

    PROSE = "Alpha prose that belongs to pin A. " * 22
    TAIL = "Beta prose that also belongs to pin A. " * 18
    COMMENT = "<!-- pinned: 2026-02-02, note > threshold -->"

    def _region(self, comment):
        return (
            "<!-- pinned: 2026-01-01 -->\n"
            f"### PinA\n{self.PROSE}\n"
            f"{comment}\n"
            f"{self.TAIL}\n"
            "### PinB\nshort\n"
        )

    def _verdict(self, post_comment):
        from pin_caps import compute_deny_reason
        from fixtures.pin_helpers import parse_pins

        pre = parse_pins(self._region(self.COMMENT))
        post = parse_pins(self._region(post_comment))
        return compute_deny_reason(pre, post)

    def test_demoting_a_pin_comment_to_a_plain_comment_is_denied(self):
        """RULED ACCEPTANCE. This deny is CORRECT and is kept deliberately.

        A curator renames the marker so a pin comment becomes an ordinary
        comment. The file gets two characters shorter and the edit is refused.
        That reads like an over-block and is not one, for a reason that only
        appears when the two charges are compared:

        Before the edit the comment is PLUGIN-MANAGED, and
        `_extract_body_chars` documents that managed markers MUST NOT count
        against the curator's budget. The comment holds a `>`, so the previous
        code failed to strip it and counted it; the current code strips it.
        The current pre-state figure is therefore the CORRECT one and the
        previous figure was inflated by a mis-measurement.

        After the edit the text is no longer a pin comment, so both versions
        agree it counts. The pin then genuinely sits above the cap with no
        override, and the refusal is the cap doing its job. The previous
        version allowed the edit only because its inflated pre-state figure
        made the post-state look no worse.

        So this is a RULED ACCEPTANCE and not a tolerated defect. It was
        reported, measured, and decided. If a later change makes this test
        fail, the pin has stopped being refused — re-open the ruling rather
        than adjusting the test.
        """
        verdict = self._verdict("<!-- note: 2026-02-02, note > threshold -->")
        assert verdict is not None, (
            "the ruled acceptance no longer holds: this edit is now allowed"
        )

    def test_renaming_a_marker_on_a_comment_without_a_greater_than_denies_alike(self):
        """CONTROL, and it denies. The `>` is again the discriminator.

        Without a `>` the previous code also stripped the comment, so both
        code versions read the same pre-state and neither treats the rename
        as a regression peculiar to one of them.
        """
        from pin_caps import compute_deny_reason
        from fixtures.pin_helpers import parse_pins

        pre = parse_pins(self._region("<!-- pinned: 2026-02-02, note ok -->"))
        post = parse_pins(self._region("<!-- note: 2026-02-02, note ok -->"))
        assert compute_deny_reason(pre, post) is not None

    def test_editing_the_rationale_without_removing_the_marker_is_allowed(self):
        """CONTROL. The advantage must SHRINK, not merely change.

        Editing the rationale leaves the comment a pin comment, so it is
        stripped in both states, the advantage is unchanged, and no deny
        appears. This is what separates the fault from ordinary comment edits.
        """
        assert self._verdict(
            "<!-- pinned: 2026-02-02, note gt threshold -->"
        ) is None


class TestPinCommentCorruptedMarker_RuledAcceptance:
    """RULED ACCEPTANCE for a deny that NOTHING SHIPPING TODAY produces.

    The curator deletes the terminator of a well-formed comment holding a
    `>`, and a LATER terminator exists further down the same pin. Three
    arms, and the two that exist today allow for DIFFERENT WRONG REASONS:

      previous code   pre 2050  post 2046  ALLOW  — pre is WRONG. It counts
                      an intact plugin-managed comment against the curator.
      current code    pre 1996  post 1019  ALLOW  — post is WRONG. Once the
                      terminator is gone it runs forward to the LATER
                      terminator and eats about a thousand characters of
                      real prose.
      per-line strip  pre 1996  post 2046  DENY   — correct in BOTH states.

    So the refusal is correct enforcement. The comment's 54 characters were
    exempt while it was plugin-managed; corrupting the marker makes them
    ordinary text, and the counted body genuinely reaches 2046 against a cap
    of 1500 with no override.

    THIS WAS RULED, NOT ASSUMED, AND THE RULING WAS NOT OBVIOUS. Unlike the
    sibling acceptance above, this deny is new relative to BOTH the previous
    code AND the current branch — nothing in existence produces it today. It
    was measured, escalated twice, and decided deliberately.

    DO NOT "FIX" THIS DENY. It looks like a bug pinned by accident and it is
    not. If you are about to relax it, read the three-arm arithmetic above
    first: any change that makes this pair allowed again is re-introducing
    one of the two measurement errors it was chosen over.

    THE PER-LINE STRIP HAS LANDED, so this now asserts live behaviour. It
    was written as a strict xfail while the arm was still a proposal, and the
    marker came off in the same change that made it pass — a ruling and the
    tripwire recording it must not ship one commit apart.
    """

    PROSE = "Alpha prose that belongs to pin A. " * 37
    TAIL = "Beta prose that also belongs to pin A. " * 18
    WELL_FORMED = "<!-- pinned: 2026-02-02, note > threshold -->"

    def _region(self, comment):
        return (
            "<!-- pinned: 2026-01-01 -->\n"
            f"### PinA\n{self.PROSE}\n"
            f"{comment}\n"
            f"{self.TAIL}\n"
            "<!-- pinned: 2026-03-03 -->\n"
            "### PinB\nshort\n"
        )

    def test_corrupting_a_managed_marker_is_denied(self):
        from pin_caps import compute_deny_reason
        from fixtures.pin_helpers import parse_pins

        unterminated = self.WELL_FORMED.replace(" -->", "")
        assert len(self.WELL_FORMED) - len(unterminated) == 4

        pre = parse_pins(self._region(self.WELL_FORMED))
        post = parse_pins(self._region(unterminated))
        assert compute_deny_reason(pre, post) is not None, (
            "the corrupted marker's characters are counted, so the body grew "
            "past the cap and the edit must be refused"
        )

    def test_the_current_code_allows_it_only_by_running_forward(self):
        """The ruling's premise, checked rather than remembered.

        The current code allows the pair ONLY because its post-state charge
        FALLS — and a four-character deletion cannot legitimately reduce a
        counted body by hundreds. That fall IS the forward run over real
        prose, and it is the measurement error the ruling weighed.

        Deliberately asserted on the CHARGES rather than on the verdict, and
        deliberately tolerant of both arms: it holds while the forward run
        exists and holds again once a per-line strip removes it. So it
        records the premise without adding a second red at remediation time.
        The single intended red is the XPASS above.
        """
        from fixtures.pin_helpers import parse_pins

        unterminated = self.WELL_FORMED.replace(" -->", "")
        pre = parse_pins(self._region(self.WELL_FORMED))[0].body_chars
        post = parse_pins(self._region(unterminated))[0].body_chars
        deleted = len(self.WELL_FORMED) - len(unterminated)

        assert post >= pre, (
            "a four-character deletion fell the counted body: the forward run "
            "the ruling weighed is back"
        )

    def test_the_parser_reads_no_pin_past_the_corrupted_marker(self):
        """THE PARSER'S ARM, a fourth beside the three above.

        The unterminated comment is closed, for the fence-aware parser, by the
        next row that ends in `-->`, and that row (pin B's date comment) itself
        starts a comment. That is the parser's uncertain region: every row
        from the corrupted marker on is UNKNOWN. So pin B is not read as a pin,
        and its rows, charged in full like every UNKNOWN row, join pin A's
        body. The charge only rises, so the refusal above stands. In a whole
        CLAUDE.md the Pinned section is not FOUND here, so the gate allows with
        its advisory and the readers stay silent.
        """
        from fixtures.pin_helpers import parse_pins

        unterminated = self.WELL_FORMED.replace(" -->", "")
        pre = parse_pins(self._region(self.WELL_FORMED))
        post = parse_pins(self._region(unterminated))
        assert [p.heading for p in pre] == ["### PinA", "### PinB"]
        assert [p.heading for p in post] == ["### PinA"]
        assert post[0].body_chars > pre[0].body_chars + len(unterminated)


class TestPinCommentMixedLine_RuledAcceptance:
    """RULED ACCEPTANCE for a comment that SHARES A LINE WITH PROSE.

    Attribution reads only whole rows: `pin_caps._date_comment_row` matches
    the row patterns from the row's start to its end, so a line carrying prose
    either side of a comment is NEVER attributed — it sets no date and grants
    no override. The strip is a
    `.sub`, so it removes that comment's substring from the line anyway.

    SO THE STRIP REMOVES WHAT ATTRIBUTION NEVER ATTRIBUTES, and the counted
    body comes out LOWER than an attribution-consistent count. A lower charge
    is a lower bar for the post state to clear, which is the same mechanism as
    the stray-opener fault above.

    Measured on one mixed line, comment interior varied:

      comment holds `>`     previous code  charges it in full (its `[^>]`
                            class cannot cross the `>`, so it never matches)
                            per-line strip REMOVES it
      comment holds no `>`  BOTH remove it — so this is PRE-EXISTING on the
                            previous code and not introduced here

    The widening EXTENDED an existing asymmetry to `>`-bearing comments; it
    did not create one. A line bound cannot reach it, because the whole shape
    lives inside a single line.

    WHY IT IS ACCEPTED RATHER THAN CLOSED. Closing it means making the strip
    agree with attribution about what a comment IS — a per-line `fullmatch`
    instead of a `sub`. That RAISES charges, and raising charges is the
    direction measured to push pins over the cap and manufacture fresh denies.
    It would trade a known, bounded residual for an unmeasured one. Live
    reachability was measured at ZERO across 50 project files, behind a
    positive control of 41 pin-comment openers, so no real file reaches it.

    DO NOT "FIX" THIS. It reads like an oversight and it is a decision.

    STANDING ORDER: if these tests ever fail, RE-OPEN THE RULING. Do not
    adjust the test to match new behaviour — a failure here means the strip
    and the attribution path have changed their relationship, which is the
    thing the ruling was made about.
    """

    LEAD = "lead prose "
    TRAIL = " trail prose"
    COMMENT_GT = "<!-- pinned: 2026-02-02, note > threshold -->"
    COMMENT_PLAIN = "<!-- pinned: 2026-02-02, note ok here -->"
    MIXED_GT = LEAD + COMMENT_GT + TRAIL
    MIXED_PLAIN = LEAD + COMMENT_PLAIN + TRAIL
    ALONE = COMMENT_GT

    def test_the_strip_removes_a_mixed_line_comment_anyway(self):
        """The accepted behaviour, asserted as a DIRECT OBSERVATION.

        The residue is the prose ALONE. Deliberately not compared against a
        computed attribution-consistent count: computing one would put a
        SECOND IMPLEMENTATION of the code under test inside this file, and
        the test would then check two implementations against each other
        rather than check correctness.

        Deliberately no verdict and no character budget either — whether a
        given pin is denied depends on where it sits relative to the cap, so
        pinning a verdict would make an unrelated size change look like a
        regression.
        """
        from pin_caps import _DATE_COMMENT_RE

        for line in (self.MIXED_GT, self.MIXED_PLAIN):
            assert _DATE_COMMENT_RE.sub("", line) == self.LEAD + self.TRAIL, (
                f"expected the strip to remove the embedded comment from "
                f"{line!r} and leave the prose alone, which is the accepted "
                f"behaviour recorded above"
            )

    def test_attribution_refuses_a_mixed_line(self):
        """The ruling's PREMISE, checked rather than remembered.

        The acceptance rests on attribution never attributing a mixed line.
        If that stopped being true the two paths would agree and the residual
        would not exist, so the ruling would need re-reading rather than
        re-asserting.
        """
        from pin_caps import _date_comment_row
        from shared.claude_md_markers import parse

        for line in (self.MIXED_GT, self.MIXED_PLAIN):
            assert _date_comment_row(parse(f"{line}\n### P\n"), 0, 1) is None, line

    def test_the_same_comment_alone_on_its_line_is_attributed(self):
        """CONTROL, and it must PASS for the class above to mean anything.

        Same comment text, nothing else on the line. Attribution accepts it
        and the strip removes it completely. Without this the two assertions
        above would also hold for a pattern that simply matched nothing.
        """
        from pin_caps import _date_comment_row
        from shared.claude_md_markers import parse
        from fixtures.pin_helpers import _extract_body_chars

        assert _date_comment_row(parse(f"{self.ALONE}\n### P\n"), 0, 1) == 0
        assert _extract_body_chars(self.ALONE) == 0

    def test_a_pin_body_holding_a_mixed_line_is_charged_the_prose_alone(self):
        """The acceptance, through the function that computes the charge.

        The assertion above pins the REGEX the ruling was about; this one pins
        the body-size path a pin actually goes through, so a body-size change
        that stopped striking a mixed-line comment fails here even while the
        regex is untouched.
        """
        from fixtures.pin_helpers import parse_pins

        for line in (self.MIXED_GT, self.MIXED_PLAIN):
            pin = parse_pins(f"### P\n{line}\n")[0]
            assert pin.body_chars == len((self.LEAD + self.TRAIL).strip()), line


class TestPinCommentSplitComment_RuledAcceptance:
    """RULED ACCEPTANCE for a pin comment SPLIT ACROSS TWO LINES.

    THIS PINS THE CHARGE, AND IT IS A REQUIREMENT. Do not confuse it with
    `TestPinCommentMultiLine_Characterization` directly below, which pins the
    PATTERN and says in its own docstring that a remediation is expected to
    change its values. That class disclaims what it appears to guard; THIS one
    does not. A failure here is not a delta to review and absorb — it is a
    ruling being reversed.

    THE BEHAVIOUR. The size charge (`pin_caps._charge`) strikes per row, so a
    comment that spans a line break is not stripped at all and every one of
    its characters counts against the curator's budget.

    WHY THAT IS CORRECT RATHER THAN A COST TO BE REPAID. Attribution reads one
    row at a time, so a comment spanning a line break is NOT A COMMENT to the
    attribution path — it sets no date and grants no
    override. The previous whole-body strip removed it anyway, which is the
    two-oracles-disagree defect this whole change exists to remove. Charging it
    is what AGREEMENT between the two paths looks like, not a regression that a
    later fix should undo.

    THIS WAS THE FIRST OF THE FOUR RULINGS AND IT WAS RULED DELIBERATELY. The
    charge rises for this shape, and near the cap that turns some edits from
    allowed into denied. Live reachability was measured at ZERO. The ruling
    weighed a bounded, measured cost against re-opening the defect.

    DO NOT "FIX" THIS. The repair someone reaches for first is to keep the line
    bound and add a second whole-body pass that re-strips the split comment.
    That silently reverses this ruling, and before this class existed it passed
    the entire suite.

    STANDING ORDER: if these tests fail, RE-OPEN THE RULING. Do not adjust the
    test to match new behaviour.
    """

    OPEN = "<!-- pinned: 2026-02-02,"
    CLOSE = "ratio > 3 -->"
    SPLIT = OPEN + "\n" + CLOSE
    JOINED = OPEN + " " + CLOSE

    def test_a_split_comment_is_not_stripped_and_its_characters_count(self):
        """The ruled behaviour, as a DIRECT OBSERVATION with no magic number.

        Nothing is removed, so the charge is the whole string. Asserted against
        the fixture's own length rather than a literal, so re-wording the
        fixture cannot make this a false regression.
        """
        from fixtures.pin_helpers import _extract_body_chars

        assert _extract_body_chars(self.SPLIT) == len(self.SPLIT), (
            "a comment spanning a line break must NOT be stripped; its "
            "characters count, which is the ruled acceptance recorded above"
        )

    def test_the_same_text_on_one_line_is_stripped_to_nothing(self):
        """CONTROL, and it must PASS for the assertion above to mean anything.

        Identical text, one line instead of two. Here the strip and attribution
        agree that it IS a comment, and it costs nothing. The CONTRAST between
        the two is the finding: the line break is the only difference, and it
        is what decides whether the characters count.
        """
        from fixtures.pin_helpers import _extract_body_chars

        assert _extract_body_chars(self.JOINED) == 0

    def test_a_split_comment_inside_a_pin_body_is_charged_to_that_pin(self):
        """The same fact where a curator meets it, rather than on a bare string.

        Without this the class would pin a helper's behaviour on an input no
        document produces.
        """
        from fixtures.pin_helpers import parse_pins

        body = "prose line one"
        region = (
            "<!-- pinned: 2026-01-01 -->\n"
            f"### Alpha\n{body}\n{self.SPLIT}\n"
        )
        pin = parse_pins(region)[0]
        assert pin.body_chars == len(body) + 1 + len(self.SPLIT), (
            "the split comment's characters must be charged to the pin whose "
            "body contains them"
        )


class TestPinCommentMultiLine_Characterization:
    """CHARACTERIZATION OF CURRENT BEHAVIOUR. This is NOT a requirement.

    The cross-oracle property in the sibling file is quantified over `every
    single-line string L`, but nothing ENFORCES single-line-ness, and the
    patterns do match a multi-line string. The property is therefore silent
    about multi-line input, and this class records what the code does there
    TODAY so a later change has a shared reference to compare against.

    A REMEDIATION IS EXPECTED TO CHANGE THESE VALUES. A repair that stops the
    pattern matching across a newline CLOSES this blind spot and is correct.
    If these assertions fail, do NOT treat that as a regression: review the
    delta, confirm it is the intended change, and update this class as part
    of the same commit. Written as a requirement it would be a false tripwire
    that a correct fix has to break.
    """

    MULTI_LINE = (
        "<!-- pinned: 2026-01-01, opener here\n"
        "a middle line of real prose\n"
        "and another -->"
    )

    def test_strip_pattern_currently_matches_across_newlines(self):
        from pin_caps import _DATE_COMMENT_RE

        assert _DATE_COMMENT_RE.fullmatch(self.MULTI_LINE) is not None

    def test_strip_currently_removes_a_multi_line_comment_entirely(self):
        from pin_caps import _DATE_COMMENT_RE

        assert _DATE_COMMENT_RE.sub("", self.MULTI_LINE) == ""

    def test_the_per_line_discipline_lives_in_the_caller_not_the_pattern(self):
        """The distinction that decides what a line-bounded repair must bind.

        The parser splits the text into rows before attribution matches, so
        the attribution PATH is per-row. The pattern is not. A repair may bind
        either one, and the two are different changes.
        """
        from fixtures.pin_helpers import parse_pins

        pins = parse_pins(f"### P\n{self.MULTI_LINE}\n")
        assert pins[0].date_comment is None


class TestPinCommentBacktracking_Pipeline:
    """An anti-exponential tripwire on the changed date field.

    NO-CHANGE CONTROL against the previous code, which is faster on these
    inputs. The guard is against a FUTURE edit that makes the alternation
    ambiguous, because the two branches are disjoint today and a run of
    characters therefore has exactly one decomposition.

    The budget is deliberately loose. Measured growth is quadratic and the
    worst case here costs tens of milliseconds, so a budget in seconds cannot
    flake, and an exponential would blow through it by orders of magnitude.
    """

    BUDGET_SECONDS = 5.0

    @pytest.mark.parametrize("length", [500, 1000, 2000])
    @pytest.mark.parametrize("tail", ["x", "x -->"], ids=["open", "closed"])
    @pytest.mark.parametrize("prefix", [
        "<!-- pinned:",
        "<!-- pinned: 2026-01-01, reconfirmed: 2026-07-25",
        "<!-- pinned: 2026-01-01, pin-size-override: r, reconfirmed: 2026-07-25",
    ], ids=["date field", "reconfirmed before the override", "reconfirmed after it"])
    def test_interior_whitespace_run_stays_within_budget(self, prefix, tail, length):
        from pin_caps import _OVERRIDE_COMMENT_HEAD, override_rationale_text
        from shared.claude_md_markers import parse

        # An interior whitespace run inside one row, which is the shape
        # attribution can actually receive. The character after it ends it.
        line = prefix + " " * length + tail
        assert line.strip() == line

        started = time.perf_counter()
        _OVERRIDE_COMMENT_HEAD.match(line)
        override_rationale_text(parse(line + "\n"), 0)
        elapsed = time.perf_counter() - started

        assert elapsed < self.BUDGET_SECONDS, (
            f"a {len(line)}-char line took {elapsed:.3f}s. The date field is a "
            f"quantified alternation, so check whether its branches have "
            f"stopped being disjoint."
        )

    def test_many_unterminated_openers_stay_within_budget(self):
        from pin_caps import _DATE_COMMENT_RE

        body = ("<!-- pinned: a > b " * 400) + ("word " * 40)

        started = time.perf_counter()
        _DATE_COMMENT_RE.sub("", body)
        elapsed = time.perf_counter() - started

        assert elapsed < self.BUDGET_SECONDS, (
            f"a {len(body)}-char body took {elapsed:.3f}s to strip. Each "
            f"unterminated opener is a fresh start position for the scan."
        )
