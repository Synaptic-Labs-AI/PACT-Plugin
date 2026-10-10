"""
Tests for hooks/pin_caps.py — the pin readers, cap predicates, slot-status formatter.

Risk tier: CRITICAL (enforcement layer for CLAUDE.md surgery). Coverage
target: 90%+ with adversarial testing.

Test organization uses scope-suffix naming (TestEvaluateFullState_Smoke, etc.) to
avoid basename collision with other test files per pytest shadow-class
gotcha — duplicate test class basenames across files silently drop the
losing file's tests.
"""


import pytest


class TestParsePins_ParsingSemantics:
    """Parsing entry boundaries, date comments, and stale markers."""

    def test_empty_input_returns_empty_list(self):
        from fixtures.pin_helpers import parse_pins
        assert parse_pins("") == []

    def test_whitespace_only_returns_empty_list(self):
        from fixtures.pin_helpers import parse_pins
        assert parse_pins("   \n\n  \n") == []

    def test_no_headings_returns_empty_list(self):
        from fixtures.pin_helpers import parse_pins
        assert parse_pins("Just prose text without any heading.") == []

    def test_single_pin_without_date_comment(self):
        from fixtures.pin_helpers import parse_pins
        content = "### First Entry\nBody text for the first entry.\n"
        pins = parse_pins(content)
        assert len(pins) == 1
        assert pins[0].heading == "### First Entry"
        assert pins[0].date_comment is None
        assert pins[0].override_rationale is None
        assert pins[0].is_stale is False

    def test_single_pin_with_date_comment(self):
        from fixtures.pin_helpers import parse_pins
        content = "<!-- pinned: 2026-04-11 -->\n### Entry Title\nBody.\n"
        pins = parse_pins(content)
        assert len(pins) == 1
        assert pins[0].date_comment == "<!-- pinned: 2026-04-11 -->"
        assert pins[0].override_rationale is None

    def test_pin_with_stale_marker(self):
        from fixtures.pin_helpers import parse_pins
        content = (
            "<!-- pinned: 2026-01-01 -->\n"
            "### Stale Entry\n"
            "<!-- STALE: Last relevant 2026-01-15 -->\n"
            "Body.\n"
        )
        pins = parse_pins(content)
        assert len(pins) == 1
        assert pins[0].is_stale is True

    def test_multiple_pins_parsed_in_order(self):
        from fixtures.pin_helpers import parse_pins
        content = (
            "### First\nBody 1.\n\n"
            "### Second\nBody 2.\n\n"
            "### Third\nBody 3.\n"
        )
        pins = parse_pins(content)
        assert [p.heading for p in pins] == ["### First", "### Second", "### Third"]

    def test_heading_without_body(self):
        from fixtures.pin_helpers import parse_pins
        pins = parse_pins("### Orphan")
        assert len(pins) == 1
        assert pins[0].heading == "### Orphan"
        assert pins[0].body == ""


class TestParsePins_OverrideComment:
    """Override comment detection — live CLAUDE.md:68 line + adversarial variants."""

    LIVE_OVERRIDE_LINE = (
        "<!-- pinned: 2026-04-11, pin-size-override: "
        "verbatim dispatch form is load-bearing for LLM readers -->"
    )

    def test_live_claude_md_override_line_parses(self):
        """Exact match against the live CLAUDE.md:68 line."""
        from fixtures.pin_helpers import parse_pins
        content = f"{self.LIVE_OVERRIDE_LINE}\n### Entry\nBody.\n"
        pins = parse_pins(content)
        assert len(pins) == 1
        assert pins[0].override_rationale == (
            "verbatim dispatch form is load-bearing for LLM readers"
        )

    def test_override_rationale_extracted_exactly(self):
        from fixtures.pin_helpers import parse_pins
        content = (
            "<!-- pinned: 2026-04-20, pin-size-override: reason here -->\n"
            "### Entry\nBody.\n"
        )
        pins = parse_pins(content)
        assert pins[0].override_rationale == "reason here"

    def test_empty_rationale_rejected(self):
        from fixtures.pin_helpers import parse_pins
        content = (
            "<!-- pinned: 2026-04-20, pin-size-override:  -->\n"
            "### Entry\nBody.\n"
        )
        pins = parse_pins(content)
        # Strict parser: empty rationale → no override captured
        assert pins[0].override_rationale is None

    def test_rationale_exactly_at_limit_accepted(self):
        from fixtures.pin_helpers import parse_pins
        rationale = "x" * 120
        content = (
            f"<!-- pinned: 2026-04-20, pin-size-override: {rationale} -->\n"
            "### Entry\nBody.\n"
        )
        pins = parse_pins(content)
        assert pins[0].override_rationale == rationale

    def test_rationale_over_limit_rejected(self):
        from fixtures.pin_helpers import parse_pins
        rationale = "x" * 121
        content = (
            f"<!-- pinned: 2026-04-20, pin-size-override: {rationale} -->\n"
            "### Entry\nBody.\n"
        )
        pins = parse_pins(content)
        assert pins[0].override_rationale is None

    def test_malformed_override_falls_back_to_date_only(self):
        from fixtures.pin_helpers import parse_pins
        # Missing rationale keyword entirely — treated as no override.
        content = (
            "<!-- pinned: 2026-04-20, pin-size: nope -->\n"
            "### Entry\nBody.\n"
        )
        pins = parse_pins(content)
        assert pins[0].override_rationale is None

    def test_multi_override_first_line_wins(self):
        """Only the line IMMEDIATELY preceding the heading is inspected.

        A second override comment further back is not considered.
        """
        from fixtures.pin_helpers import parse_pins
        content = (
            "<!-- pinned: 2026-04-01, pin-size-override: old rationale -->\n"
            "\n"
            "<!-- pinned: 2026-04-20 -->\n"
            "### Entry\nBody.\n"
        )
        pins = parse_pins(content)
        # Immediate preceding line is date-only — no override captured.
        assert pins[0].override_rationale is None
        assert pins[0].date_comment == "<!-- pinned: 2026-04-20 -->"


class TestOverrideRationaleText_PublicReader:
    """The override rationale reader the pin-cap gate shares."""

    @staticmethod
    def _read(text, row=0):
        from pin_caps import override_rationale_text
        from shared.claude_md_markers import parse
        return override_rationale_text(parse(text), row)

    @pytest.mark.parametrize("row, text", [
        ("<!-- pinned: 2026-04-20, pin-size-override: reason here -->", "reason here"),
        ("  <!--pinned:2026-04-20,PIN-SIZE-OVERRIDE:  a-b > c  -->\t", "a-b > c"),
        ("<!-- pinned: 2026-04-20, pin-size-override:  -->", ""),
        (f"<!-- pinned: 2026-04-20, pin-size-override: {'x' * 121} -->", "x" * 121),
        ("<!-- pinned: 2026-04-20, reconfirmed: 2026-07-25 because a, b, pin-size-override: reason -->",
         "reason"),
        ("<!-- pinned: 2026-04-20, pin-size-override: reason, reconfirmed: 2026-07-25 because a, b -->",
         "reason"),
        ("<!-- PINNED: 2026-04-20, PIN-SIZE-OVERRIDE: reason , Reconfirmed:2026-07-25 because x -->",
         "reason"),
        ("<!-- pinned: 2026-04-20, pin-size-override: keep a, b, reconfirmed: soon -->",
         "keep a, b, reconfirmed: soon"),
        ("<!-- pinned: 2026-04-20, reconfirmed: 2026-07-01 because a, pin-size-override: reason, "
         "reconfirmed: 2026-07-25 because b -->", "reason"),
        ("<!-- pinned: 2026-04-20, pin-size-override: keep (v2) -->", "keep (v2)"),
        ("<!-- pinned: 2026-04-20, pin-size-override: keep it. -->", "keep it."),
        ("<!-- pinned: 2026-04-20, pin-size-override: keep (v2). - reconfirmed: 2026-07-25 because x -->",
         "keep (v2"),
        ("<!-- pinned: 2026-04-20, pin-size-override: ; reconfirmed: 2026-07-25 because x -->", ""),
        ("<!-- pinned: 2026-04-20, pin-size-override: / reconfirmed: 2026-07-25 because x -->", ""),
        ("<!-- pinned: 2026-04-20, pin-size-override: \U0001f512, reconfirmed: 2026-07-25 because x -->",
         "\U0001f512"),
        ("<!-- pinned: 2026-04-20, pin-size-override: ?!; reconfirmed: 2026-07-25 because x -->", "?!"),
        ("<!-- pinned: 2026-04-20 (reconfirmed: 2026-07-25 because a, b), pin-size-override: reason -->",
         "reason"),
    ], ids=["plain", "spacing and case", "empty field", "over the limit", "reconfirmed before it",
            "reconfirmed after it", "reconfirmed after it, any case", "a reconfirmed word with no date",
            "reconfirmed on both sides", "a closing parenthesis kept", "a closing full stop kept",
            "the punctuation run before a reconfirmation dropped", "only a separator before a reconfirmation",
            "only a slash before a reconfirmation", "a symbol rationale keeps its symbol",
            "a punctuation rationale keeps its punctuation",
            "reconfirmed in parentheses before it, a comma in the reason"])
    def test_it_returns_the_stripped_field_before_any_validity_check(self, row, text):
        assert self._read(f"{row}\n### Entry\nBody.\n") == text

    @pytest.mark.parametrize("row", [
        "<!-- pinned: 2026-04-20 -->",
        "<!-- pinned: 2026-04-20, pin-size: nope -->",
        "<!-- pinned: 2026-04-20, pin-size-override: reason --> then prose",
        "<!-- pinned: 2026-04-20 --> , pin-size-override: reason -->",
        "### Entry",
        "<!-- pinned: 2026-04-20, reconfirmed: 2026-07-25 because x -->",
        "<!-- pinned: 2026-04-20, note: x, pin-size-override: reason -->",
        "<!-- pinned: 2026-04-20, note: x; reconfirmed: 2026-07-25 because y, pin-size-override: reason -->",
    ], ids=["date only", "other field", "prose after it", "field after a closed comment", "heading",
            "reconfirmed only", "another field before the override",
            "another field before a reconfirmation before the override"])
    def test_it_is_none_for_a_row_that_is_not_an_override_comment(self, row):
        assert self._read(f"{row}\n### Entry\nBody.\n") is None

    def test_it_is_none_for_an_override_comment_inside_a_fenced_block(self):
        row = "<!-- pinned: 2026-04-20, pin-size-override: reason here -->"
        assert self._read(f"```\n{row}\n```\n", row=1) is None

    def test_a_row_of_repeated_override_fields_is_read_in_linear_time(self):
        """A row that is not one closed pin comment is no override, however
        many `, pin-size-override:` fields it holds: the date row refuses it in
        one pass, before the override head is read."""
        import time

        from fixtures.pin_helpers import parse_pins

        for tail in (" x", " --> x"):
            row = "<!-- pinned: d" + ", pin-size-override: a" * 2000 + tail
            started = time.perf_counter()
            assert self._read(f"{row}\n### Entry\nBody.\n") is None
            assert parse_pins(f"{row}\n### Entry\nBody.\n")[0].date_comment is None
            assert time.perf_counter() - started < 2.0, tail


class TestPinsInRows_PublicReader:
    """The public reader of the pins in a row range of a whole-file parse."""

    TEXT = (
        "# Notes\n<!-- PACT_MEMORY_START -->\n## Pinned Context\n"
        "<!-- pinned: 2026-04-20 -->\n### First\nbody one\n```\n### fenced, not a pin\n```\n"
        "<!-- pinned: 2026-04-21, pin-size-override: reason here -->\n### Second\nbody two\n"
        "## Working Memory\n<!-- PACT_MEMORY_END -->\n"
    )

    def _located(self):
        from shared.claude_md_markers import parse
        from staleness import locate_pinned
        doc = parse(self.TEXT)
        located = locate_pinned(doc)
        return doc, located, located.spans[0]

    def test_over_the_section_body_it_reads_what_section_pins_reads(self):
        from pin_caps import pins_in_rows, section_pins
        doc, located, (heading, last) = self._located()
        pins = pins_in_rows(doc, heading + 1, last)
        assert pins == section_pins(doc, located)
        assert [pin.heading for pin in pins] == ["### First", "### Second"]
        assert "### fenced, not a pin" in pins[0].body
        assert pins[1].override_rationale == "reason here"

    def test_a_narrower_range_reads_only_the_pins_headed_in_it(self):
        from pin_caps import pins_in_rows
        doc, _, (heading, last) = self._located()
        second = next(row for row in range(heading, last + 1) if doc.lines[row].content == "### Second")
        (pin,) = pins_in_rows(doc, second - 1, last)
        assert (pin.heading, pin.override_rationale) == ("### Second", "reason here")

    def test_an_empty_range_reads_no_pins(self):
        from pin_caps import pins_in_rows
        doc, _, (heading, _) = self._located()
        assert pins_in_rows(doc, heading + 1, heading) == []


class TestCharge_StrikeUpToTheLastClose:
    """The charge strikes pin comments only up to a row's last `-->`."""

    FRAGMENTS = ["<!-- pinned: ", "<!--pinned:", "2026-04-11", ", pin-size-override: r", "-->", " -->",
                 "<!-- STALE: Last relevant 2026-01-01 -->", "<!-- STALE: Last relevant ", "text", "-", ">",
                 "--", " ", "\t", "<!--"]

    def test_it_charges_what_striking_the_whole_row_charges(self):
        import random
        from pin_caps import _MANAGED_COMMENT_RE, _charge
        from shared.claude_md_markers import parse
        rnd = random.Random(13)
        for _ in range(5_000):
            row = "".join(rnd.choice(self.FRAGMENTS) for _ in range(rnd.randint(1, 16)))
            expected = len(_MANAGED_COMMENT_RE.sub("", row).rstrip(" \t").strip())
            assert _charge(parse(row), 0, 0) == expected, row

    def test_a_comment_after_the_last_close_is_not_struck_and_one_before_it_is(self):
        from pin_caps import _charge
        from shared.claude_md_markers import parse
        row = "a <!-- pinned: 2026-04-11 --> b <!-- pinned: open"
        assert _charge(parse(row), 0, 0) == len("a  b <!-- pinned: open")


class TestPinCountCap_EveryPinOccupiesASlot:
    """The count axis counts every pin, whatever it carries. A size override
    exempts a pin from the size cap only, and a STALE pin still holds its
    slot until it is archived."""

    def _pins(self, n, *, override=False, stale=False):
        from pin_caps import Pin
        return [
            Pin(heading=f"### P{i}", body="x" * 2000, body_chars=2000,
                date_comment=None,
                override_rationale="verbatim" if override else None,
                is_stale=stale)
            for i in range(n)
        ]

    @pytest.mark.parametrize("override,stale", [(True, False), (False, True), (True, True)])
    def test_a_13th_pin_is_refused_whatever_the_pins_carry(self, override, stale):
        from pin_caps import compute_deny_reason
        pre = self._pins(12, override=override, stale=stale)
        post = self._pins(13, override=override, stale=stale)
        reason = compute_deny_reason(pre, post, growth=1)
        assert reason is not None and "13/12" in reason

    def test_an_override_on_the_added_pin_does_not_lift_the_count_cap(self):
        from pin_caps import compute_deny_reason
        pre = self._pins(12, override=True)
        post = pre + self._pins(1, override=True)
        assert "13/12" in (compute_deny_reason(pre, post, growth=1) or "")


class TestParsePins_HeadingShape:
    """Only a `### ` line is a pin heading."""

    def test_an_h4_line_is_not_a_pin(self):
        from fixtures.pin_helpers import parse_pins
        pins = parse_pins("### Real\nBody with subsection:\n#### H4 Title\nnested content\n")
        assert [p.heading for p in pins] == ["### Real"]

    def test_prose_mentioning_a_pin_comment_is_not_a_pin(self):
        from fixtures.pin_helpers import parse_pins
        body = "Look at the <!-- pinned: x --> line in CLAUDE.md for the canonical form\n"
        assert parse_pins(body) == []


class TestExtractBodyChars:
    """body_chars excludes auto-generated markers (date comment, STALE marker)."""

    def test_plain_body_counted_in_full(self):
        from fixtures.pin_helpers import parse_pins
        body = "x" * 500
        content = f"### Entry\n{body}\n"
        pins = parse_pins(content)
        # Trailing newline gets stripped by _extract_body_chars
        assert pins[0].body_chars == 500

    def test_stale_marker_excluded_from_count(self):
        from fixtures.pin_helpers import parse_pins
        body_text = "x" * 100
        content = (
            "### Entry\n"
            "<!-- STALE: Last relevant 2026-01-15 -->\n"
            f"{body_text}\n"
        )
        pins = parse_pins(content)
        # STALE marker stripped before counting
        assert pins[0].body_chars == 100

    def test_date_comment_inside_body_excluded(self):
        from fixtures.pin_helpers import parse_pins
        body_text = "y" * 50
        content = (
            "### Entry\n"
            f"{body_text}\n"
            "<!-- pinned: 2026-04-20 -->\n"
        )
        pins = parse_pins(content)
        # Inline date-comment pattern stripped
        assert pins[0].body_chars == 50


class TestCheckStaleBlock_Threshold:
    """SessionStart stale-block signal at threshold={0,1,2,3}."""

    def _stale_pins(self, stale_count, total=5):
        from pin_caps import Pin
        return [
            Pin(
                heading=f"### P{i}", body="x", body_chars=1,
                date_comment=None, override_rationale=None,
                is_stale=(i < stale_count),
            )
            for i in range(total)
        ]

    def test_zero_stale_returns_none(self):
        from pin_caps import check_stale_block
        assert check_stale_block(self._stale_pins(0)) is None

    def test_one_stale_below_threshold_returns_none(self):
        from pin_caps import check_stale_block
        # PIN_STALE_BLOCK_THRESHOLD = 2 → 1 stale still silent
        assert check_stale_block(self._stale_pins(1)) is None

    def test_two_stale_triggers_signal(self):
        from pin_caps import check_stale_block
        result = check_stale_block(self._stale_pins(2))
        assert result is not None
        assert result.kind == "stale"
        assert "2 stale" in result.detail

    def test_three_stale_triggers_signal(self):
        from pin_caps import check_stale_block
        result = check_stale_block(self._stale_pins(3))
        assert result is not None
        assert result.kind == "stale"

    def test_custom_threshold_respected(self):
        from pin_caps import check_stale_block
        # Custom threshold of 3 → 2 stale is silent
        assert check_stale_block(self._stale_pins(2), threshold=3) is None
        assert check_stale_block(self._stale_pins(3), threshold=3) is not None


class TestFormatSlotStatus_Idempotent:
    """Slot-status formatter is pure; idempotent on repeated calls."""

    def test_empty_pins_shows_zero(self):
        from pin_caps import format_slot_status
        assert format_slot_status([]) == "Pin slots: 0/12 used"

    def test_full_pins_shows_full(self):
        from pin_caps import Pin, format_slot_status
        pins = [
            Pin(heading=f"### P{i}", body="x", body_chars=10,
                date_comment=None, override_rationale=None, is_stale=False)
            for i in range(12)
        ]
        assert format_slot_status(pins) == "Pin slots: 12/12 used (FULL)"

    def test_partial_pins_shows_headroom(self):
        from pin_caps import Pin, format_slot_status
        pins = [
            Pin(heading="### P1", body="x", body_chars=100,
                date_comment=None, override_rationale=None, is_stale=False),
            Pin(heading="### P2", body="y", body_chars=500,
                date_comment=None, override_rationale=None, is_stale=False),
        ]
        result = format_slot_status(pins)
        assert "2/12" in result
        # Largest-pin remaining: 1500 - 500 = 1000
        assert "1000 chars remaining" in result

    def test_oversized_existing_pin_clamps_report(self):
        """Existing pin > cap (presumably override-carrying) does not
        report negative headroom."""
        from pin_caps import Pin, format_slot_status
        pins = [
            Pin(heading="### Over", body="x", body_chars=2000,
                date_comment=None, override_rationale="reason", is_stale=False),
        ]
        result = format_slot_status(pins)
        assert "remaining" not in result
        assert "1/12" in result

    def test_idempotent_pure_function(self):
        """Calling twice returns identical string — P0 for SessionStart."""
        from pin_caps import Pin, format_slot_status
        pins = [
            Pin(heading="### P", body="x", body_chars=42,
                date_comment=None, override_rationale=None, is_stale=False),
        ]
        assert format_slot_status(pins) == format_slot_status(pins)


class TestHasSizeOverride:
    def test_pin_with_rationale_returns_true(self):
        from pin_caps import Pin, has_size_override
        pin = Pin(heading="### X", body="", body_chars=0,
                  date_comment=None, override_rationale="reason",
                  is_stale=False)
        assert has_size_override(pin) is True

    def test_pin_without_rationale_returns_false(self):
        from pin_caps import Pin, has_size_override
        pin = Pin(heading="### X", body="", body_chars=0,
                  date_comment=None, override_rationale=None,
                  is_stale=False)
        assert has_size_override(pin) is False


class TestConstants:
    """Lock down constant values — changes must be deliberate."""

    def test_count_cap_is_twelve(self):
        from pin_caps import PIN_COUNT_CAP
        assert PIN_COUNT_CAP == 12

    def test_size_cap_is_1500(self):
        from pin_caps import PIN_SIZE_CAP
        assert PIN_SIZE_CAP == 1500

    def test_stale_block_threshold_is_two(self):
        from pin_caps import PIN_STALE_BLOCK_THRESHOLD
        assert PIN_STALE_BLOCK_THRESHOLD == 2

    def test_override_rationale_max_is_120(self):
        from pin_caps import OVERRIDE_RATIONALE_MAX
        assert OVERRIDE_RATIONALE_MAX == 120


# ---------------------------------------------------------------------------
# Phase A smoke coverage for hook-primary cap helpers (cycle-8).
#
# Exhaustive matrix (count ladder, size ladder, Unicode, replace_all variants,
# counter-test-by-revert) lives in TEST phase via test_pin_caps_gate.py.
# These tests verify the helpers' contracts at the unit-function level so the
# suite stays green at HEAD across the phase sequence.
# ---------------------------------------------------------------------------


def _make_pin(heading="### X", body_chars=100, override=False):
    from pin_caps import Pin
    return Pin(
        heading=heading,
        body="x" * body_chars,
        body_chars=body_chars,
        date_comment=None,
        override_rationale="load-bearing" if override else None,
        is_stale=False,
    )


def _pin_text(*names, undated=()):
    """A pinned body holding one pin per name, dated unless named in `undated`."""
    return "".join(
        ("" if name in undated else "<!-- pinned: 2026-04-21 -->\n")
        + f"### {name}\nbody of {name}\n\n"
        for name in names
    )


class TestEvaluateFullState_Smoke:
    """evaluate_full_state — post-state (>, strict) cap predicate."""

    def test_empty_pins_allows(self):
        from pin_caps import evaluate_full_state
        assert evaluate_full_state([]) is None

    def test_at_count_cap_allows(self):
        from pin_caps import PIN_COUNT_CAP, evaluate_full_state
        pins = [_make_pin(heading=f"### P{i}") for i in range(PIN_COUNT_CAP)]
        # > is strict — count == cap is NOT a violation at post-state.
        assert evaluate_full_state(pins) is None

    def test_over_count_cap_denies(self):
        from pin_caps import PIN_COUNT_CAP, evaluate_full_state
        pins = [_make_pin(heading=f"### P{i}") for i in range(PIN_COUNT_CAP + 1)]
        violation = evaluate_full_state(pins)
        assert violation is not None
        assert violation.kind == "count"
        assert violation.current_count == PIN_COUNT_CAP + 1

    def test_at_size_cap_allows(self):
        from pin_caps import PIN_SIZE_CAP, evaluate_full_state
        pins = [_make_pin(body_chars=PIN_SIZE_CAP)]
        assert evaluate_full_state(pins) is None

    def test_over_size_cap_without_override_denies(self):
        from pin_caps import PIN_SIZE_CAP, evaluate_full_state
        pins = [_make_pin(body_chars=PIN_SIZE_CAP + 1, override=False)]
        violation = evaluate_full_state(pins)
        assert violation is not None
        assert violation.kind == "size"
        assert violation.offending_pin_chars == PIN_SIZE_CAP + 1

    def test_over_size_cap_with_override_allows(self):
        from pin_caps import PIN_SIZE_CAP, evaluate_full_state
        pins = [_make_pin(body_chars=PIN_SIZE_CAP + 500, override=True)]
        assert evaluate_full_state(pins) is None


class TestComputeDenyReason_Smoke:
    """compute_deny_reason — net-worse predicate over pre/post pin states."""

    def test_pre_clean_post_clean_allows(self):
        from pin_caps import compute_deny_reason
        pre = [_make_pin() for _ in range(3)]
        post = [_make_pin() for _ in range(4)]
        assert compute_deny_reason(pre, post) is None

    def test_pre_clean_post_count_violation_denies(self):
        from pin_caps import PIN_COUNT_CAP, compute_deny_reason
        pre = [_make_pin(heading=f"### P{i}") for i in range(PIN_COUNT_CAP)]
        post = pre + [_make_pin(heading="### Extra")]
        reason = compute_deny_reason(pre, post)
        assert reason is not None
        assert "Pin count cap" in reason
        assert "prune-memory" in reason

    def test_pre_over_cap_post_same_count_allows(self):
        # F1 livelock precedent — pre-malformed state must not block remediation.
        from pin_caps import PIN_COUNT_CAP, compute_deny_reason
        pre = [_make_pin(heading=f"### P{i}") for i in range(PIN_COUNT_CAP + 3)]
        post = list(pre)  # Refactor Edit — count unchanged.
        assert compute_deny_reason(pre, post) is None

    def test_pre_over_cap_post_decreases_allows(self):
        from pin_caps import PIN_COUNT_CAP, compute_deny_reason
        pre = [_make_pin(heading=f"### P{i}") for i in range(PIN_COUNT_CAP + 3)]
        post = pre[:-1]  # Archival Edit — count down by 1.
        assert compute_deny_reason(pre, post) is None

    def test_pre_over_cap_post_even_worse_denies(self):
        from pin_caps import PIN_COUNT_CAP, compute_deny_reason
        pre = [_make_pin(heading=f"### P{i}") for i in range(PIN_COUNT_CAP + 1)]
        post = pre + [_make_pin(heading="### MoreWorse")]
        reason = compute_deny_reason(pre, post)
        assert reason is not None
        assert "Pin count cap" in reason

    # NO EMBEDDED-PIN CHECK. A `### ` line smuggled into a pin body is a pin
    # once the body is parsed, so the pin-growth rule counts it and the count
    # axis denies it. A fenced one is not a pin. The check's only remaining
    # effect was to refuse renames, swaps, moves and add-one-delete-one changes
    # of an undated pin, which add no pin. The rows below pin its removal: each
    # change was DENIED by the removed check and is ALLOWED now. `growth` is the
    # value the pin-growth rule gives each change.

    def test_the_embedded_pin_check_and_its_parameter_are_gone(self):
        import inspect
        import typing

        import pin_caps
        assert "new_body" not in inspect.signature(pin_caps.compute_deny_reason).parameters
        assert not hasattr(pin_caps, "DENY_REASON_EMBEDDED_PIN")
        assert not hasattr(pin_caps, "check_add_allowed")
        kinds = typing.get_args(typing.get_type_hints(pin_caps.CapViolation)["kind"])
        assert "count" in kinds and "embedded_pin" not in kinds

    def test_an_undated_rename_below_the_cap_is_allowed(self):
        from pin_caps import compute_deny_reason
        from fixtures.pin_helpers import parse_pins
        pre = parse_pins(_pin_text("A", "B", "C"))
        post = parse_pins(_pin_text("A", "Renamed", "C", undated=("Renamed",)))
        assert post[1].date_comment is None
        assert compute_deny_reason(pre, post, growth=0) is None

    @pytest.mark.parametrize("change", ["rename", "swap", "move", "add one, delete one"])
    def test_an_undated_change_that_adds_no_pin_is_allowed_at_13(self, change):
        from pin_caps import compute_deny_reason
        from fixtures.pin_helpers import parse_pins
        names = [f"P{i}" for i in range(13)]
        if change == "rename":
            after, undated = names[:12] + ["Renamed"], ("Renamed",)
        elif change == "swap":
            after, undated = [names[1], names[0]] + names[2:], (names[0],)
        elif change == "move":
            after, undated = names[1:] + [names[0]], (names[0],)
        else:
            after, undated = names[1:] + ["Brand new"], ("Brand new",)
        pre = parse_pins(_pin_text(*names))
        post = parse_pins(_pin_text(*after, undated=undated))
        assert len(pre) == len(post) == 13
        assert compute_deny_reason(pre, post, growth=0) is None

    def test_a_prose_heading_smuggled_into_a_body_at_12_is_denied_on_count(self):
        from pin_caps import compute_deny_reason
        from fixtures.pin_helpers import parse_pins
        names = [f"P{i}" for i in range(12)]
        before = _pin_text(*names)
        after = before.replace("body of P3\n", "body of P3\n### smuggled\nmore\n")
        pre, post = parse_pins(before), parse_pins(after)
        assert (len(pre), len(post)) == (12, 13)
        reason = compute_deny_reason(pre, post, growth=1)
        assert reason is not None and "Pin count cap reached (13/12)" in reason

    def test_pre_clean_post_size_violation_denies(self):
        from pin_caps import PIN_SIZE_CAP, compute_deny_reason
        pre = [_make_pin(body_chars=100)]
        post = [_make_pin(body_chars=PIN_SIZE_CAP + 50, override=False)]
        reason = compute_deny_reason(pre, post)
        assert reason is not None
        assert f"Pin size cap ({PIN_SIZE_CAP} chars) exceeded: 'X' is {PIN_SIZE_CAP + 50} chars." in reason

    def test_multi_kind_pre_count_plus_size_reducing_count_allows(self):
        """Pre-state has BOTH count AND size violations; Edit reduces count
        below cap, pre-existing size violation remains unchanged.

        Pre-fix regression (#492 cycle-8 F2 / architect-1): evaluate_full_state
        returned pre.kind="count" (first-wins precedence). Post-state after
        count-reduction surfaced size, so post.kind="size" != pre.kind="count"
        → compute_deny_reason denied via the kind-swap branch, livelocking the
        curator into the pre-malformed state. Net-worse predicate's whole job
        is to prevent exactly this.

        Post-fix (via _violation_for_kind lookup): the kind-swap branch asks
        whether pre-state ALSO violates post.kind. When yes, it falls through
        to numeric comparison — a size violation present at pre-state and
        unchanged at post-state is NOT strictly worse, so the remediation Edit
        is allowed.
        """
        from pin_caps import PIN_COUNT_CAP, PIN_SIZE_CAP, compute_deny_reason
        # Pre-state: (cap+1) pins, the last one is oversize.
        pre = [_make_pin(heading=f"### P{i}", body_chars=100)
               for i in range(PIN_COUNT_CAP)]
        pre.append(_make_pin(heading="### Huge",
                             body_chars=PIN_SIZE_CAP + 50, override=False))
        assert len(pre) == PIN_COUNT_CAP + 1  # count-violation
        # Post-state: archival Edit drops two pins; size violation on Huge unchanged.
        post = pre[:-2] + [pre[-1]]
        assert len(post) <= PIN_COUNT_CAP  # count-violation resolved
        # Remediation must be allowed — the size violation is net-equivalent.
        assert compute_deny_reason(pre, post) is None

    def test_multi_kind_pre_count_plus_size_worsening_size_denies(self):
        """Same pre-state (count + size) but the Edit WORSENS size while
        reducing count below cap. Net change on size axis is strictly worse,
        so the predicate must still deny via the fall-through numeric path.
        Counter-test to test_multi_kind_pre_count_plus_size_reducing_count_allows
        — verifies the fix didn't over-relax.
        """
        from pin_caps import PIN_COUNT_CAP, PIN_SIZE_CAP, compute_deny_reason
        pre = [_make_pin(heading=f"### P{i}", body_chars=100)
               for i in range(PIN_COUNT_CAP)]
        pre.append(_make_pin(heading="### Huge",
                             body_chars=PIN_SIZE_CAP + 50, override=False))
        # Post: drops two pins AND enlarges the offending one.
        post = pre[:-2] + [_make_pin(heading="### Huge",
                                     body_chars=PIN_SIZE_CAP + 200,
                                     override=False)]
        reason = compute_deny_reason(pre, post)
        assert reason is not None
        assert f"exceeded: 'Huge' is {PIN_SIZE_CAP + 200} chars." in reason

    def test_multi_kind_pre_count_plus_size_same_kind_size_worsens_denies(self):
        """F4 Pareto positive: pre and post both have count violation
        (first-wins kind), count unchanged, BUT size on the hidden axis
        worsened. Must deny on size.

        blind-backend-coder-2 #492 F4 PoC:
          pre  = 13 pins + Huge body 1550 (count wins, size=1550 hidden)
          post = 13 pins + Huge body 1700 (count unchanged, size=1700)
        Pre-fix `compute_deny_reason` returned None (same-kind count
        numeric compare: 13==13, not worse -> allow). Pareto fix queries
        the OTHER axis via `_pareto_other_axis_deny`; post size exceeds
        pre size -> deny on the worsened axis.
        """
        from pin_caps import PIN_COUNT_CAP, PIN_SIZE_CAP, compute_deny_reason
        pre = [_make_pin(heading=f"### P{i}", body_chars=100)
               for i in range(PIN_COUNT_CAP)]
        pre.append(_make_pin(heading="### Huge",
                             body_chars=PIN_SIZE_CAP + 50, override=False))
        assert len(pre) == PIN_COUNT_CAP + 1  # count violation
        post = [_make_pin(heading=f"### P{i}", body_chars=100)
                for i in range(PIN_COUNT_CAP)]
        post.append(_make_pin(heading="### Huge",
                              body_chars=PIN_SIZE_CAP + 200, override=False))
        assert len(post) == len(pre)  # count unchanged
        reason = compute_deny_reason(pre, post)
        assert reason is not None
        assert f"{PIN_SIZE_CAP + 200}" in reason, (
            f"deny-reason should reference the worsened size "
            f"{PIN_SIZE_CAP + 200}: {reason!r}"
        )

    def test_multi_kind_pre_count_plus_size_same_kind_size_unchanged_allows(self):
        """F4 Pareto negative counter: pre and post both count violation
        (unchanged), size also unchanged. Not strictly worse on ANY axis ->
        allow. Guards against Pareto over-relaxation: a state exactly equal
        to pre must not deny.
        """
        from pin_caps import PIN_COUNT_CAP, PIN_SIZE_CAP, compute_deny_reason
        pre = [_make_pin(heading=f"### P{i}", body_chars=100)
               for i in range(PIN_COUNT_CAP)]
        pre.append(_make_pin(heading="### Huge",
                             body_chars=PIN_SIZE_CAP + 50, override=False))
        post = list(pre)  # identical state
        assert compute_deny_reason(pre, post) is None

    def test_count_improves_size_worsens_denies(self):
        """F4 asymmetry cover: count IMPROVES (still violating but fewer
        pins) while size WORSENS on the hidden axis. Pareto: strictly worse
        on ANY axis -> deny. One axis improving does not offset another
        axis worsening under Pareto semantics.
        """
        from pin_caps import PIN_COUNT_CAP, PIN_SIZE_CAP, compute_deny_reason
        pre = [_make_pin(heading=f"### P{i}", body_chars=100)
               for i in range(PIN_COUNT_CAP + 1)]
        pre.append(_make_pin(heading="### Huge",
                             body_chars=PIN_SIZE_CAP + 50, override=False))
        assert len(pre) == PIN_COUNT_CAP + 2  # count violates
        post = [_make_pin(heading=f"### P{i}", body_chars=100)
                for i in range(PIN_COUNT_CAP)]
        post.append(_make_pin(heading="### Huge",
                              body_chars=PIN_SIZE_CAP + 200, override=False))
        assert len(post) == PIN_COUNT_CAP + 1 and len(post) < len(pre)
        reason = compute_deny_reason(pre, post)
        assert reason is not None
        assert f"{PIN_SIZE_CAP + 200}" in reason

    def test_multi_size_non_first_violator_worsens_denies(self):
        """F5 positive: pre and post both have multiple size violators,
        the FIRST-in-list improves while a LATER violator worsens.

        Pre-fix `evaluate_full_state` / `_violation_for_kind` returned the
        first-in-list violator, so the numeric compare at the same-kind
        size branch saw pre=A (1600) vs post=A (1590) -> not worse -> allow,
        silently letting B's 2000->2500 worsening through.

        blind-backend-coder-2 #492 F5 PoC:
          pre  = [A@1600, B@2000]   (first-wins returns A)
          post = [A@1590, B@2500]   (first-wins returns A)
        Post-F5 fix returns MAX violator: pre max=B@2000, post max=B@2500
        -> deny on the genuinely-worsened axis.
        """
        from pin_caps import PIN_SIZE_CAP, compute_deny_reason
        pre = [
            _make_pin(heading="### A", body_chars=PIN_SIZE_CAP + 100),
            _make_pin(heading="### B", body_chars=PIN_SIZE_CAP + 500),
        ]
        post = [
            _make_pin(heading="### A", body_chars=PIN_SIZE_CAP + 90),
            _make_pin(heading="### B", body_chars=PIN_SIZE_CAP + 1000),
        ]
        reason = compute_deny_reason(pre, post)
        assert reason is not None, (
            "F5 regression: non-first-violator worsening must deny via "
            "max-violator scalar"
        )
        assert f"{PIN_SIZE_CAP + 1000}" in reason, (
            f"deny-reason should reference the worsened non-first-violator "
            f"body size {PIN_SIZE_CAP + 1000}: {reason!r}"
        )

    def test_multi_size_non_first_violator_unchanged_allows(self):
        """F5 negative counter: pre and post share multiple size violators;
        the first-in-list improves while the LATER violator is unchanged.
        Max-violator scalar did not worsen -> allow. Guards against F5
        over-strict denial: if no violator is strictly worse than the
        prior worst, the state is not Pareto-worse on the size axis.
        """
        from pin_caps import PIN_SIZE_CAP, compute_deny_reason
        pre = [
            _make_pin(heading="### A", body_chars=PIN_SIZE_CAP + 100),
            _make_pin(heading="### B", body_chars=PIN_SIZE_CAP + 500),
        ]
        post = [
            _make_pin(heading="### A", body_chars=PIN_SIZE_CAP + 90),
            _make_pin(heading="### B", body_chars=PIN_SIZE_CAP + 500),
        ]
        assert compute_deny_reason(pre, post) is None


class TestDenyReasonTemplates_Constants:
    """Deny-reason templates are shared; test they render with expected shape."""

    def test_count_template_renders(self):
        from pin_caps import DENY_REASON_COUNT, PIN_COUNT_CAP
        rendered = DENY_REASON_COUNT.format(count=PIN_COUNT_CAP + 1, cap=PIN_COUNT_CAP)
        assert str(PIN_COUNT_CAP) in rendered
        assert "prune-memory" in rendered

    def test_count_template_frames_removal_as_demotion_not_deletion(self):
        """AC-B3: the cap deny is where a curator decides whether to give up
        a pin, so it must say what happens to the content.

        `demote` names the destination; `evict` named only the removal and
        read as loss. The negative arm is the load-bearing half — a template
        that says "demote" while still threatening deletion would satisfy a
        keyword check and defeat the intent.
        """
        from pin_caps import DENY_REASON_COUNT, PIN_COUNT_CAP
        rendered = DENY_REASON_COUNT.format(
            count=PIN_COUNT_CAP + 1, cap=PIN_COUNT_CAP
        ).lower()
        assert "demote" in rendered or "demotion" in rendered
        assert "long-term memory" in rendered
        assert "evict" not in rendered, (
            "deletion framing reintroduced — AC-B3 requires demotion framing"
        )
        assert "delete" not in rendered

    def test_size_template_renders(self):
        from pin_caps import DENY_REASON_SIZE, PIN_SIZE_CAP
        rendered = DENY_REASON_SIZE.format(pins="'P' is 1600 chars", cap=PIN_SIZE_CAP)
        assert rendered.startswith(f"Pin size cap ({PIN_SIZE_CAP} chars) exceeded: 'P' is 1600 chars. ")


    def test_override_missing_template_renders(self):
        from pin_caps import DENY_REASON_OVERRIDE_MISSING, PIN_SIZE_CAP
        rendered = DENY_REASON_OVERRIDE_MISSING.format(
            chars=PIN_SIZE_CAP + 10, cap=PIN_SIZE_CAP
        )
        assert "pin-size-override" in rendered
