"""
Tests for hooks/pin_caps.py — parse_pins, cap predicates, slot-status formatter.

Risk tier: CRITICAL (enforcement layer for CLAUDE.md surgery). Coverage
target: 90%+ with adversarial testing.

Test organization uses scope-suffix naming (TestPinCapCount_Gate, etc.) to
avoid basename collision with other test files per pytest shadow-class
gotcha — duplicate test class basenames across files silently drop the
losing file's tests.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestParsePins_ParsingSemantics:
    """Parsing entry boundaries, date comments, and stale markers."""

    def test_empty_input_returns_empty_list(self):
        from pin_caps import parse_pins
        assert parse_pins("") == []

    def test_whitespace_only_returns_empty_list(self):
        from pin_caps import parse_pins
        assert parse_pins("   \n\n  \n") == []

    def test_no_headings_returns_empty_list(self):
        from pin_caps import parse_pins
        assert parse_pins("Just prose text without any heading.") == []

    def test_single_pin_without_date_comment(self):
        from pin_caps import parse_pins
        content = "### First Entry\nBody text for the first entry.\n"
        pins = parse_pins(content)
        assert len(pins) == 1
        assert pins[0].heading == "### First Entry"
        assert pins[0].date_comment is None
        assert pins[0].override_rationale is None
        assert pins[0].is_stale is False

    def test_single_pin_with_date_comment(self):
        from pin_caps import parse_pins
        content = "<!-- pinned: 2026-04-11 -->\n### Entry Title\nBody.\n"
        pins = parse_pins(content)
        assert len(pins) == 1
        assert pins[0].date_comment == "<!-- pinned: 2026-04-11 -->"
        assert pins[0].override_rationale is None

    def test_pin_with_stale_marker(self):
        from pin_caps import parse_pins
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
        from pin_caps import parse_pins
        content = (
            "### First\nBody 1.\n\n"
            "### Second\nBody 2.\n\n"
            "### Third\nBody 3.\n"
        )
        pins = parse_pins(content)
        assert [p.heading for p in pins] == ["### First", "### Second", "### Third"]

    def test_heading_without_body(self):
        from pin_caps import parse_pins
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
        from pin_caps import parse_pins
        content = f"{self.LIVE_OVERRIDE_LINE}\n### Entry\nBody.\n"
        pins = parse_pins(content)
        assert len(pins) == 1
        assert pins[0].override_rationale == (
            "verbatim dispatch form is load-bearing for LLM readers"
        )

    def test_override_rationale_extracted_exactly(self):
        from pin_caps import parse_pins
        content = (
            "<!-- pinned: 2026-04-20, pin-size-override: reason here -->\n"
            "### Entry\nBody.\n"
        )
        pins = parse_pins(content)
        assert pins[0].override_rationale == "reason here"

    def test_empty_rationale_rejected(self):
        from pin_caps import parse_pins
        content = (
            "<!-- pinned: 2026-04-20, pin-size-override:  -->\n"
            "### Entry\nBody.\n"
        )
        pins = parse_pins(content)
        # Strict parser: empty rationale → no override captured
        assert pins[0].override_rationale is None

    def test_rationale_exactly_at_limit_accepted(self):
        from pin_caps import parse_pins
        rationale = "x" * 120
        content = (
            f"<!-- pinned: 2026-04-20, pin-size-override: {rationale} -->\n"
            "### Entry\nBody.\n"
        )
        pins = parse_pins(content)
        assert pins[0].override_rationale == rationale

    def test_rationale_over_limit_rejected(self):
        from pin_caps import parse_pins
        rationale = "x" * 121
        content = (
            f"<!-- pinned: 2026-04-20, pin-size-override: {rationale} -->\n"
            "### Entry\nBody.\n"
        )
        pins = parse_pins(content)
        assert pins[0].override_rationale is None

    def test_malformed_override_falls_back_to_date_only(self):
        from pin_caps import parse_pins
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
        from pin_caps import parse_pins
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


class TestPinCapCount_Gate:
    """Count-cap boundary matrix — strict predicate len(existing) >= 12."""

    def _mk_pins(self, n):
        from pin_caps import Pin
        return [
            Pin(heading=f"### P{i}", body="x", body_chars=1,
                date_comment=None, override_rationale=None, is_stale=False)
            for i in range(n)
        ]

    def test_count_below_cap_allows_add(self):
        """At 11/12, adding is allowed."""
        from pin_caps import check_add_allowed
        assert check_add_allowed(self._mk_pins(11), "new", False) is None

    def test_count_at_cap_refuses_add(self):
        """At 12/12, off-by-one hazard — predicate is >=."""
        from pin_caps import check_add_allowed
        result = check_add_allowed(self._mk_pins(12), "new", False)
        assert result is not None
        assert result.kind == "count"
        assert result.current_count == 12
        assert "12/12" in result.detail

    def test_count_above_cap_refuses_add(self):
        from pin_caps import check_add_allowed
        result = check_add_allowed(self._mk_pins(13), "new", False)
        assert result is not None
        assert result.kind == "count"
        assert result.current_count == 13

    def test_count_zero_allows_add(self):
        from pin_caps import check_add_allowed
        assert check_add_allowed([], "new", False) is None

    def test_count_cap_ignores_override_flag(self):
        """Override is a SIZE bypass only, not a count bypass."""
        from pin_caps import check_add_allowed
        result = check_add_allowed(self._mk_pins(12), "new", True)
        assert result is not None
        assert result.kind == "count"


class TestPinSizeCap_Gate:
    """Size-cap boundary matrix × override state — 1499 / 1500 / 1501."""

    @pytest.mark.parametrize("body_chars,expected_violation", [
        (1499, False),
        (1500, False),  # predicate is > not >=, so exactly at cap is allowed
        (1501, True),
    ])
    def test_size_boundary_without_override(self, body_chars, expected_violation):
        from pin_caps import check_add_allowed
        body = "x" * body_chars
        result = check_add_allowed([], body, False)
        if expected_violation:
            assert result is not None
            assert result.kind == "size"
            assert result.offending_pin_chars == body_chars
        else:
            assert result is None

    @pytest.mark.parametrize("body_chars", [1499, 1500, 1501, 5000])
    def test_override_bypasses_size_cap(self, body_chars):
        """Valid override → any size is allowed."""
        from pin_caps import check_add_allowed
        body = "x" * body_chars
        assert check_add_allowed([], body, True) is None

    def test_size_violation_detail_includes_cap(self):
        from pin_caps import check_add_allowed
        result = check_add_allowed([], "x" * 2000, False)
        assert result is not None
        assert "1500" in result.detail
        assert "2000" in result.detail


class TestCheckAddAllowed_EmbeddedHeading:
    """Embedded-pin cap-bypass defense: candidate bodies containing a
    level-3 heading (`### `) are rejected because parse_pins on reload
    would count them as additional pins, defeating the count cap.

    Conservative by design (per team-lead direction 2026-04-21): rejects ANY
    embedded pin structure detected by parse_pins, whether accompanied
    by a date-comment or not. Legitimate pin bodies can use `#### ` or
    bold/italic for in-body structure.

    Counter-test-by-revert: remove the `if parse_pins(new_body): ...`
    branch and the `test_embedded_pin_structure_rejected` +
    `test_lone_heading_rejected_conservative` cases go red.
    """

    def test_normal_body_allowed(self):
        """Plain body with no heading or pin-comment — allowed."""
        from pin_caps import check_add_allowed
        assert check_add_allowed([], "regular content with no headings", False) is None

    def test_embedded_pin_structure_rejected(self):
        """Body smuggling a full `<!-- pinned:-->\\n### Heading` pair — rejected."""
        from pin_caps import check_add_allowed
        body = "<!-- pinned: 2026-04-21 -->\n### Embedded Pin\nbody"
        result = check_add_allowed([], body, False)
        assert result is not None
        assert result.kind == "embedded_pin"
        assert "smuggle" in result.detail.lower()

    def test_prose_mentioning_pin_syntax_allowed(self):
        """Body referencing pin comment inline with no `### ` heading — allowed."""
        from pin_caps import check_add_allowed
        body = "Look at the <!-- pinned: x --> line in CLAUDE.md for the canonical form"
        assert check_add_allowed([], body, False) is None

    def test_lone_heading_rejected_conservative(self):
        """Body with lone `### ` heading (no preceding date-comment) — REJECTED.

        parse_pins returns a Pin for heading-only entries (date_comment=None),
        and that Pin counts toward the cap on reload. Conservative check
        closes the smuggle vector regardless of date-comment presence.
        """
        from pin_caps import check_add_allowed
        body = "### Just a heading\nbody content"
        result = check_add_allowed([], body, False)
        assert result is not None
        assert result.kind == "embedded_pin"

    def test_h4_heading_in_body_allowed(self):
        """`#### ` (H4) does not match the `^### ` pin-heading pattern — allowed."""
        from pin_caps import check_add_allowed
        body = "Body with subsection:\n#### H4 Title\nnested content"
        assert check_add_allowed([], body, False) is None

    def test_embedded_pin_ignores_other_cap_paths(self):
        """Embedded-pin check fires after count + size caps; fresh state passes those."""
        from pin_caps import check_add_allowed
        # Zero existing pins, small body, but contains embedded structure.
        body = "### Smuggle\nx"
        result = check_add_allowed([], body, False)
        assert result is not None
        assert result.kind == "embedded_pin"
        assert result.current_count == 0

    def test_embedded_pin_not_bypassed_by_override(self):
        """Override flag is a SIZE-cap bypass only — must not bypass embedded check."""
        from pin_caps import check_add_allowed
        body = "<!-- pinned: 2026-04-21 -->\n### Embedded\nbody"
        result = check_add_allowed([], body, True)
        assert result is not None
        assert result.kind == "embedded_pin"


class TestExtractBodyChars:
    """body_chars excludes auto-generated markers (date comment, STALE marker)."""

    def test_plain_body_counted_in_full(self):
        from pin_caps import parse_pins
        body = "x" * 500
        content = f"### Entry\n{body}\n"
        pins = parse_pins(content)
        # Trailing newline gets stripped by _extract_body_chars
        assert pins[0].body_chars == 500

    def test_stale_marker_excluded_from_count(self):
        from pin_caps import parse_pins
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
        from pin_caps import parse_pins
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


def _managed_content(pinned_section_body: str) -> str:
    """Wrap pinned-section body in the PACT_MANAGED region so
    _parse_pinned_section can extract it. Matches the structure produced
    by claude_md_manager.
    """
    return (
        "# PACT\n\n"
        "<!-- PACT_MANAGED_START -->\n"
        "## Pinned Context\n"
        f"{pinned_section_body}"
        "<!-- PACT_MANAGED_END -->\n"
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


class TestApplyEditAndParse_Smoke:
    """apply_edit_and_parse — Edit/Write simulation + section-bounded parse."""

    def test_write_full_replacement_parses_pins(self):
        from pin_caps import apply_edit_and_parse
        new = _managed_content(
            "<!-- pinned: 2026-04-21 -->\n### A\nBody A.\n\n"
        )
        pins = apply_edit_and_parse(current_content="", tool_input={"content": new})
        assert len(pins) == 1
        assert pins[0].heading == "### A"

    def test_edit_single_match_adds_pin(self):
        from pin_caps import apply_edit_and_parse
        before = _managed_content("<!-- pinned: 2026-04-21 -->\n### A\nBody.\n\n")
        tool_input = {
            "old_string": "### A\nBody.\n",
            "new_string": "### A\nBody.\n\n<!-- pinned: 2026-04-21 -->\n### B\nBody B.\n",
            "replace_all": False,
        }
        pins = apply_edit_and_parse(current_content=before, tool_input=tool_input)
        assert [p.heading for p in pins] == ["### A", "### B"]

    def test_edit_replace_all_applies_all_matches(self):
        from pin_caps import apply_edit_and_parse
        before = _managed_content(
            "<!-- pinned: 2026-04-21 -->\n### A\nKEEP.\n\n"
            "<!-- pinned: 2026-04-21 -->\n### B\nKEEP.\n\n"
        )
        tool_input = {
            "old_string": "KEEP.",
            "new_string": "REPLACED.",
            "replace_all": True,
        }
        pins = apply_edit_and_parse(current_content=before, tool_input=tool_input)
        assert all("REPLACED" in p.body for p in pins)

    def test_missing_pinned_section_returns_empty(self):
        from pin_caps import apply_edit_and_parse
        # No Pinned Context section at all.
        pins = apply_edit_and_parse(
            current_content="",
            tool_input={"content": "# Some\nRandom content.\n"},
        )
        assert pins == []

    def test_write_non_string_content_raises(self):
        from pin_caps import apply_edit_and_parse
        with pytest.raises(TypeError):
            apply_edit_and_parse(
                current_content="", tool_input={"content": 42},
            )

    def test_edit_non_string_strings_raise(self):
        from pin_caps import apply_edit_and_parse
        with pytest.raises(TypeError):
            apply_edit_and_parse(
                current_content="x",
                tool_input={"old_string": 1, "new_string": "y"},
            )

    def test_edit_empty_old_string_replace_all_no_op(self):
        """F6 #492 cycle-4: Edit with empty old_string + replace_all=True must
        simulate as a no-op (return pre-state parse), not run str.replace which
        would interleave new_string between every character and produce gibberish.

        Pre-fix behavior: `str.replace(current, "", JUNK)` yields
        `JUNKjJUNKuJUNK...` etc.; _parse_pinned_section returns None;
        parse_pins returns []; compute_deny_reason compares post=[] vs pre,
        sees no net-worse violation, allows. A curator could weaponize
        this to pass the gate while the real Edit tool's behavior on
        empty old_string is undefined/harmful.

        Post-fix: empty old_string is treated as a no-op -> post-state
        equals pre-state -> gate's net-worse contract kicks in normally.
        """
        from pin_caps import apply_edit_and_parse
        before = _managed_content(
            "<!-- pinned: 2026-04-21 -->\n### A\nBody A.\n\n"
            "<!-- pinned: 2026-04-21 -->\n### B\nBody B.\n\n"
        )
        tool_input = {
            "old_string": "",
            "new_string": "JUNK",
            "replace_all": True,
        }
        pins = apply_edit_and_parse(current_content=before, tool_input=tool_input)
        # Pre-state has 2 pins; post-state must match.
        assert len(pins) == 2
        assert [p.heading for p in pins] == ["### A", "### B"]
        # Bodies must not contain any JUNK interleaving.
        assert all("JUNK" not in p.body for p in pins), (
            "F6 regressed: empty-old-string with replace_all=True interleaved "
            "new_string into pin bodies (str.replace behavior leaked through)."
        )

    def test_edit_empty_old_string_replace_all_false_no_op(self):
        """F6 counter: empty old_string with replace_all=False must also
        no-op. str.replace(s, "", new, 1) prepends `new` once at position
        0 — which is BEFORE the managed-region markers. A naive assertion
        on pin body content would be trivially satisfied because
        _parse_pinned_section isolates the managed region (phantom-green
        #492 F6.1).

        Differentiating probe: craft new_string as a FULL synthetic
        managed region containing a smuggled pin. Under str.replace
        revert, the prepend creates a second managed region at position
        0 that extract_managed_region picks up first → parsed pins are
        the SMUGGLED pin, not the original. Under the F6 no-op guard,
        the original managed region is preserved → parsed pins are the
        ORIGINAL pin.
        """
        from pin_caps import apply_edit_and_parse
        before = _managed_content(
            "<!-- pinned: 2026-04-21 -->\n### A\nBody A.\n\n"
        )
        # Smuggle payload: a full synthetic managed region wrapping an
        # "### Evil" pin. If str.replace runs (revert), this prepends a
        # spurious managed region at position 0 that extract_managed_region
        # captures first — parsed pins become [Evil], not [A].
        smuggle_payload = (
            "<!-- PACT_MANAGED_START -->\n"
            "## Pinned Context\n"
            "<!-- pinned: 2026-04-21 -->\n### Evil\nevil body.\n\n"
            "<!-- PACT_MANAGED_END -->\n"
        )
        tool_input = {
            "old_string": "",
            "new_string": smuggle_payload,
            "replace_all": False,
        }
        pins = apply_edit_and_parse(current_content=before, tool_input=tool_input)
        assert len(pins) == 1
        # Load-bearing assertion: the ORIGINAL pin heading survives, NOT
        # the smuggled one. Under revert, this assertion fails because
        # the smuggled managed region is the one parsed.
        assert pins[0].heading == "### A", (
            f"F6 regressed: empty-old-string + replace_all=False allowed "
            f"str.replace to prepend a synthetic managed region, smuggling "
            f"a pin past the no-op guard. Got heading {pins[0].heading!r}."
        )


class TestComputeDenyReason_Smoke:
    """compute_deny_reason — net-worse predicate over pre/post pin states."""

    def test_pre_clean_post_clean_allows(self):
        from pin_caps import compute_deny_reason
        pre = [_make_pin() for _ in range(3)]
        post = [_make_pin() for _ in range(4)]
        assert compute_deny_reason(pre, post, new_body="") is None

    def test_pre_clean_post_count_violation_denies(self):
        from pin_caps import PIN_COUNT_CAP, compute_deny_reason
        pre = [_make_pin(heading=f"### P{i}") for i in range(PIN_COUNT_CAP)]
        post = pre + [_make_pin(heading="### Extra")]
        reason = compute_deny_reason(pre, post, new_body="")
        assert reason is not None
        assert "Pin count cap" in reason
        assert "prune-memory" in reason

    def test_pre_over_cap_post_same_count_allows(self):
        # F1 livelock precedent — pre-malformed state must not block remediation.
        from pin_caps import PIN_COUNT_CAP, compute_deny_reason
        pre = [_make_pin(heading=f"### P{i}") for i in range(PIN_COUNT_CAP + 3)]
        post = list(pre)  # Refactor Edit — count unchanged.
        assert compute_deny_reason(pre, post, new_body="") is None

    def test_pre_over_cap_post_decreases_allows(self):
        from pin_caps import PIN_COUNT_CAP, compute_deny_reason
        pre = [_make_pin(heading=f"### P{i}") for i in range(PIN_COUNT_CAP + 3)]
        post = pre[:-1]  # Archival Edit — count down by 1.
        assert compute_deny_reason(pre, post, new_body="") is None

    def test_pre_over_cap_post_even_worse_denies(self):
        from pin_caps import PIN_COUNT_CAP, compute_deny_reason
        pre = [_make_pin(heading=f"### P{i}") for i in range(PIN_COUNT_CAP + 1)]
        post = pre + [_make_pin(heading="### MoreWorse")]
        reason = compute_deny_reason(pre, post, new_body="")
        assert reason is not None
        assert "Pin count cap" in reason

    def test_embedded_pin_in_body_denies_regardless_of_state(self):
        from pin_caps import compute_deny_reason
        pre = []
        post = [_make_pin()]
        reason = compute_deny_reason(
            pre, post, new_body="### Sneaky Heading\nBody.\n"
        )
        assert reason is not None
        assert "embedded pin structure" in reason

    def test_pre_clean_post_size_violation_denies(self):
        from pin_caps import PIN_SIZE_CAP, compute_deny_reason
        pre = [_make_pin(body_chars=100)]
        post = [_make_pin(body_chars=PIN_SIZE_CAP + 50, override=False)]
        reason = compute_deny_reason(pre, post, new_body="")
        assert reason is not None
        assert "New pin body" in reason

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
        assert compute_deny_reason(pre, post, new_body="") is None

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
        reason = compute_deny_reason(pre, post, new_body="")
        assert reason is not None
        assert "New pin body" in reason

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
        reason = compute_deny_reason(pre, post, new_body="")
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
        assert compute_deny_reason(pre, post, new_body="") is None

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
        reason = compute_deny_reason(pre, post, new_body="")
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
        reason = compute_deny_reason(pre, post, new_body="")
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
        assert compute_deny_reason(pre, post, new_body="") is None


class TestDenyReasonTemplates_Constants:
    """Deny-reason templates are shared; test they render with expected shape."""

    def test_count_template_renders(self):
        from pin_caps import DENY_REASON_COUNT, PIN_COUNT_CAP
        rendered = DENY_REASON_COUNT.format(count=PIN_COUNT_CAP + 1, cap=PIN_COUNT_CAP)
        assert str(PIN_COUNT_CAP) in rendered
        assert "prune-memory" in rendered

    def test_size_template_renders(self):
        from pin_caps import DENY_REASON_SIZE, PIN_SIZE_CAP
        rendered = DENY_REASON_SIZE.format(chars=PIN_SIZE_CAP + 100, cap=PIN_SIZE_CAP)
        assert str(PIN_SIZE_CAP) in rendered

    def test_embedded_pin_template_is_static(self):
        from pin_caps import DENY_REASON_EMBEDDED_PIN
        assert "### " in DENY_REASON_EMBEDDED_PIN

    def test_override_missing_template_renders(self):
        from pin_caps import DENY_REASON_OVERRIDE_MISSING, PIN_SIZE_CAP
        rendered = DENY_REASON_OVERRIDE_MISSING.format(
            chars=PIN_SIZE_CAP + 10, cap=PIN_SIZE_CAP
        )
        assert "pin-size-override" in rendered
