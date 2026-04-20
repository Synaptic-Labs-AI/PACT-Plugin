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
