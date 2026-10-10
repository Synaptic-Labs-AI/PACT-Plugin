"""
Location: pact-plugin/tests/test_pinned_section_parser_migration.py

The Pinned section readers on the fence-aware parser: the one Pinned locator
(`staleness.locate_pinned`), the pin parse (`pin_caps.section_pins`, and the
test helper `parse_pins`), the body charge, the staleness marks, the archive block, the
pin age, the slot status, and the growth-driven deny predicate
(`pin_caps.compute_deny_reason(..., growth=)`).

Every document is built from the shipped marker constants, so a renamed marker
moves the fixtures with it.
"""

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    PINNED_END_MARKER,
    PINNED_START_MARKER,
)
from shared.claude_md_markers import Kind, State, parse

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
FENCE = "```"


def _pins(n, prefix="P"):
    """n dated pins, each with a one-line body."""
    return "".join(
        f"<!-- pinned: 2026-04-21 -->\n### {prefix}{i}\nbody of {prefix}{i}\n\n"
        for i in range(n)
    )


def _doc(pinned_body, *, markers=False, memory=True, above_heading="",
         below_pins="", working="## Working Memory\n\n### 2026-01-02\nentry\n"):
    """A canonical CLAUDE.md: managed block, memory block, the three sections."""
    start = f"{PINNED_START_MARKER}\n" if markers else ""
    end = f"{PINNED_END_MARKER}\n" if markers else ""
    inner = (
        "## Retrieved Context\n\n"
        f"{above_heading}{start}"
        "## Pinned Context\n\n"
        f"{pinned_body}{end}{below_pins}"
        f"{working}"
    )
    if memory:
        inner = f"{MEMORY_START_MARKER}\n{inner}{MEMORY_END_MARKER}\n"
    return (
        "# My notes\n\n"
        f"{MANAGED_START_MARKER}\n"
        "# PACT Framework and Managed Project Memory\n\n"
        f"{inner}"
        f"{MANAGED_END_MARKER}\n"
        "user prose below\n"
    )


def _locate(text, unique=True):
    from staleness import locate_pinned

    doc = parse(text)
    return doc, locate_pinned(doc, unique=unique)


def _count(text, unique=True):
    from pin_caps import section_pins

    doc, located = _locate(text, unique)
    assert located.state is State.FOUND, located
    return len(section_pins(doc, located))


# ---------------------------------------------------------------------------
# The one Pinned locator
# ---------------------------------------------------------------------------

class TestTheOnePinnedLocator:

    def test_the_span_is_heading_row_to_last_body_row(self):
        doc, located = _locate(_doc(_pins(2)))
        heading, last = located.spans[0]
        assert doc.lines[heading].content == "## Pinned Context"
        assert doc.lines[last + 1].content == "## Working Memory"

    def test_the_gate_keeps_counting_without_the_optional_pinned_markers(self):
        """With 12 pins and no marker pair, adding a 13th is refused."""
        from pin_caps import compute_deny_reason, section_pins

        before, after = _doc(_pins(12)), _doc(_pins(13))
        doc_b, loc_b = _locate(before)
        doc_a, loc_a = _locate(after)
        pre, post = section_pins(doc_b, loc_b), section_pins(doc_a, loc_a)
        assert (len(pre), len(post)) == (12, 13)
        reason = compute_deny_reason(pre, post, growth=1)
        assert reason is not None and "13/12" in reason

    def test_the_marker_pair_narrows_the_search_to_its_interior(self):
        text = _doc(_pins(2), markers=True, below_pins="### Below the end marker\nx\n\n")
        assert _count(text) == 2

    @pytest.mark.parametrize("indent", [" ", "  ", "   "])
    def test_an_end_marker_indented_1_to_3_spaces_ends_the_section(self, indent):
        """The S15 widening, signed off as an under-block."""
        body = _pins(1) + f"{indent}{PINNED_END_MARKER}\n" + _pins(1, prefix="Q")
        text = _doc(body, above_heading=f"{PINNED_START_MARKER}\n")
        assert _count(text) == 1

    def test_a_notes_heading_below_the_pins_adds_nothing(self):
        """`# Notes` is a terminator, so its `### ` lines are not pins."""
        notes = "# Notes\n\n### not a pin\n### nor this\n\n"
        assert _count(_doc(_pins(3), below_pins=notes)) == 3

    def test_a_commented_out_old_heading_above_leaves_the_real_one_found(self):
        old = "<!--\n## Pinned Context\n\n### Old pin\n-->\n"
        doc, located = _locate(_doc(_pins(2), above_heading=old))
        assert located.state is State.FOUND
        assert doc.lines[located.spans[0][0]].in_html is False
        assert _count(_doc(_pins(2), above_heading=old)) == 2

    def test_a_fenced_heading_is_not_the_section(self):
        fenced = f"{FENCE}\n## Pinned Context\n### Example\n{FENCE}\n"
        doc, located = _locate(_doc(_pins(2), above_heading=fenced))
        assert located.state is State.FOUND
        assert doc.lines[located.spans[0][0]].kind is Kind.PROSE
        assert _count(_doc(_pins(2), above_heading=fenced)) == 2

    def test_an_unclosed_fence_in_a_pin_body_reads_unknown_naming_the_line(self):
        """The GE/GG path: the gate allows with its advisory, readers are silent."""
        body = _pins(1) + f"### Open\n{FENCE}\nnever closed\n\n" + _pins(2, prefix="Q")
        _, located = _locate(_doc(body))
        assert located.state is State.UNKNOWN
        assert "line " in located.reason

    def test_two_visible_headings_are_duplicate_for_the_cap_only(self):
        body = _pins(1) + "## Pinned Context\n\n" + _pins(1, prefix="Q")
        text = _doc(body)
        assert _locate(text, unique=True)[1].state is State.DUPLICATE
        assert _locate(text, unique=False)[1].state is State.FOUND

    def test_an_absent_memory_block_falls_back_for_readers_only(self):
        text = _doc(_pins(2), memory=False)
        assert _locate(text, unique=True)[1].state is State.ABSENT
        assert _count(text, unique=False) == 2

    def test_a_malformed_pair_is_reported_not_guessed(self):
        body = _pins(2) + f"{PINNED_END_MARKER}\n"
        _, located = _locate(_doc(body))
        assert located.state is State.MALFORMED


# ---------------------------------------------------------------------------
# Readers stay silent when the section cannot be read
# ---------------------------------------------------------------------------

UNCLOSED = _doc(_pins(1) + f"### Open\n{FENCE}\nnever closed\n\n" + _pins(2, prefix="Q"))


class TestReadersStaySilentOnAnUncertainSection:

    def test_the_offset_view_returns_none(self):
        from staleness import _parse_pinned_section

        assert _parse_pinned_section(UNCLOSED) is None
        assert _parse_pinned_section(UNCLOSED, allow_empty_section=True) is None

    def test_the_slot_status_says_nothing_rather_than_0_used(self, tmp_path, monkeypatch):
        import session_init

        path = tmp_path / "CLAUDE.md"
        path.write_text(UNCLOSED, encoding="utf-8")
        monkeypatch.setattr(session_init, "_get_project_claude_md_path", lambda: path)
        assert session_init.check_pin_slot_status() is None
        path.write_text(_doc("", working=""), encoding="utf-8")
        assert session_init.check_pin_slot_status() == "Pin slots: 0/12 used"

    def test_the_pin_status_cli_names_the_line_it_cannot_read(self, tmp_path, monkeypatch):
        import check_pin_caps
        path = tmp_path / "CLAUDE.md"
        path.write_text(UNCLOSED, encoding="utf-8")
        monkeypatch.setattr(check_pin_caps, "get_project_claude_md_path", lambda: path)
        pins, reason = check_pin_caps._resolve_pins()
        assert pins == [] and reason is not None
        assert reason.startswith("pinned section unreadable: ")
        assert "a code fence is not closed" in reason
        path.write_text(_doc(_pins(2)), encoding="utf-8")
        assert [p.heading for p in check_pin_caps._resolve_pins()[0]] == ["### P0", "### P1"]

    def test_the_staleness_pass_writes_nothing(self, tmp_path):
        from staleness import check_pinned_staleness

        old = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
        text = UNCLOSED.replace("### Q0", f"### Q0 (PR #1, merged {old})")
        path = tmp_path / "CLAUDE.md"
        path.write_text(text, encoding="utf-8")
        assert check_pinned_staleness(claude_md_path=path) is None
        assert path.read_text(encoding="utf-8") == text


# ---------------------------------------------------------------------------
# The pin parse
# ---------------------------------------------------------------------------

class TestThePinParseIsFenceAware:

    def test_a_fenced_heading_is_body_text_of_the_pin_above(self):
        from fixtures.pin_helpers import parse_pins

        pins = parse_pins(f"### Real\nintro\n{FENCE}\n### step one\n### step two\n{FENCE}\n")
        assert [p.heading for p in pins] == ["### Real"]
        assert "### step one" in pins[0].body

    def test_a_fenced_override_example_grants_nothing(self):
        """G6: a fenced override comment directly before a fenced heading."""
        from pin_caps import PIN_SIZE_CAP
        from fixtures.pin_helpers import parse_pins

        example = (f"{FENCE}\n<!-- pinned: 2026-01-01, pin-size-override: example -->\n"
                   f"### Fake\n{'y' * (PIN_SIZE_CAP + 10)}\n{FENCE}\n")
        pins = parse_pins("<!-- pinned: 2026-01-01 -->\n### Real\n" + example)
        assert [p.heading for p in pins] == ["### Real"]
        assert pins[0].override_rationale is None
        assert pins[0].body_chars > PIN_SIZE_CAP

    def test_a_real_override_is_read_and_its_rationale_sliced(self):
        from fixtures.pin_helpers import parse_pins

        pins = parse_pins("  <!-- Pinned:  2026-01-01,  PIN-SIZE-OVERRIDE:  verbatim form  -->\n"
                          "### Big\nbody\n")
        assert pins[0].override_rationale == "verbatim form"
        assert (pins[0].date_comment or "").startswith("<!-- Pinned:")

    def test_a_comment_row_holding_a_splitlines_break_is_not_attributed(self):
        from fixtures.pin_helpers import parse_pins

        row = "<!-- pinned: 2026-01-01, pin-size-override: a\vb -->"
        pins = parse_pins(f"{row}\n### P\nbody\n")
        assert pins[0].date_comment is None and pins[0].override_rationale is None

    def test_a_fenced_stale_marker_does_not_make_a_pin_stale(self):
        from fixtures.pin_helpers import parse_pins

        stale = "<!-- STALE: Last relevant 2026-01-01 -->"
        assert parse_pins(f"### P\n{stale}\n")[0].is_stale is True
        assert parse_pins(f"### P\n{FENCE}\n{stale}\n{FENCE}\n")[0].is_stale is False

    def test_a_fenced_comment_line_is_charged_as_body_text(self):
        from fixtures.pin_helpers import parse_pins

        comment = "<!-- pinned: 2026-01-01 -->"
        plain = parse_pins(f"### P\nx\n{comment}\n")[0].body_chars
        fenced = parse_pins(f"### P\nx\n{FENCE}\n{comment}\n{FENCE}\n")[0].body_chars
        assert plain == 1
        assert fenced == len(f"x\n{FENCE}\n{comment}\n{FENCE}")

    def test_the_heading_carries_no_line_terminator(self):
        from fixtures.pin_helpers import parse_pins

        assert parse_pins("### P\r\nbody\r\n")[0].heading == "### P"

    def test_section_pins_reads_the_documents_own_rows(self):
        from pin_caps import section_pins
        from fixtures.pin_helpers import parse_pins
        from staleness import _parse_pinned_section

        text = _doc(_pins(2) + f"### Third\n{FENCE}\n### fenced\n{FENCE}\n\n")
        doc, located = _locate(text)
        parsed = _parse_pinned_section(text)
        assert parsed is not None
        assert section_pins(doc, located) == parse_pins(parsed[2])
        with pytest.raises(ValueError):
            section_pins(doc, located._replace(state=State.UNKNOWN))

    def test_a_comment_on_the_span_s_first_row_is_outside_it(self):
        """A caller-built span (a region of the text before an edit) may start
        on a pin's comment row; the pins read from the rows after it do not
        claim that comment."""
        from pin_caps import section_pins
        from shared.claude_md_markers import Located

        doc = parse("<!-- pinned: 2026-01-01 -->\n### A\nbody\n")
        whole = Located(State.FOUND, ((0, 2),), "", None)
        assert section_pins(doc, whole)[0].date_comment is None
        assert section_pins(doc, whole._replace(spans=((-1, 2),)))[0].date_comment is not None


# ---------------------------------------------------------------------------
# The growth-driven deny predicate
# ---------------------------------------------------------------------------

class TestTheGrowthDrivenDeny:

    def _pins_list(self, n, chars=10):
        from pin_caps import Pin

        return [Pin(f"### P{i}", "x" * chars, chars, None, None, False) for i in range(n)]

    def test_count_denies_only_past_the_cap_and_only_on_growth(self):
        from pin_caps import compute_deny_reason

        at_13 = self._pins_list(13)
        assert compute_deny_reason(at_13, at_13, growth=0) is None
        assert compute_deny_reason(at_13, at_13, growth=-1) is None
        assert compute_deny_reason(self._pins_list(11), self._pins_list(12), growth=1) is None
        assert "13/12" in (compute_deny_reason(self._pins_list(12), at_13, growth=1) or "")

    def test_the_pre_pin_count_is_never_read(self):
        """Extra pins in pre_pins only lift the size axis's pre worst."""
        from pin_caps import compute_deny_reason

        post = self._pins_list(13)
        for pre in (self._pins_list(0), self._pins_list(13), self._pins_list(40)):
            assert compute_deny_reason(pre, post, growth=1) is not None
            assert compute_deny_reason(pre, post, growth=0) is None

    def test_the_size_axis_compares_the_pre_worst(self):
        from pin_caps import PIN_SIZE_CAP, compute_deny_reason

        big = self._pins_list(1, PIN_SIZE_CAP + 100)
        bigger = self._pins_list(1, PIN_SIZE_CAP + 200)
        assert compute_deny_reason(big, big, growth=0) is None
        assert f"exceeded: 'P0' is {PIN_SIZE_CAP + 200} chars." in (
            compute_deny_reason(big, bigger, growth=0) or "")
        assert compute_deny_reason(bigger + big, bigger, growth=0) is None
        assert f"exceeded: 'P0' is {PIN_SIZE_CAP + 100} chars." in (
            compute_deny_reason([], big, growth=0) or "")


# ---------------------------------------------------------------------------
# Whitespace a reader cannot see is never charged
# ---------------------------------------------------------------------------

def _pin_of(lines, newline="\n"):
    return newline.join(["<!-- pinned: 2026-01-01 -->", "### Big", *lines]) + newline


_LINES_1485 = ["w" * 98] * 14 + ["w" * 99]  # 14*98 + 99 + 14 breaks = 1,485


class TestTrailingWhitespaceIsNotCharged:

    def _verdict(self, before, after, growth=0):
        from pin_caps import compute_deny_reason
        from fixtures.pin_helpers import parse_pins

        return compute_deny_reason(parse_pins(before), parse_pins(after), growth=growth)

    def test_the_fixture_charges_1485(self):
        from fixtures.pin_helpers import parse_pins

        assert parse_pins(_pin_of(_LINES_1485))[0].body_chars == 1485

    def test_trailing_blanks_on_every_line_are_allowed(self):
        padded = [line + "  \t " for line in _LINES_1485]
        assert self._verdict(_pin_of(_LINES_1485), _pin_of(padded)) is None

    def test_a_crlf_rewrite_is_allowed(self):
        assert self._verdict(_pin_of(_LINES_1485), _pin_of(_LINES_1485, "\r\n")) is None

    def test_real_text_past_the_cap_is_denied_on_size(self):
        grown = _LINES_1485 + ["w" * 24]  # + 1 break + 24 = 1,510
        reason = self._verdict(_pin_of(_LINES_1485), _pin_of(grown))
        assert reason is not None and "1510 chars" in reason

    def test_trailing_spaces_are_never_charged_the_named_under_block(self):
        """Adversarial only, accepted with this rule: a pin may carry any amount
        of trailing whitespace without it counting toward the cap."""
        lines = ["w" * 99] * 14
        lines[3] += " " * 2000
        assert self._verdict(_pin_of(["w" * 99] * 14), _pin_of(lines)) is None


# ---------------------------------------------------------------------------
# Staleness marks, the archive block and the pin age
# ---------------------------------------------------------------------------

class TestTheOtherReaders:

    def test_a_fenced_heading_with_an_old_date_is_not_marked_stale(self):
        from staleness import apply_staleness_markings

        old = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
        body = (f"### Real (PR #1, merged {old})\nbody\n"
                f"{FENCE}\n### Example (PR #2, merged {old})\n{FENCE}\n")
        doc = parse(body)
        new, count, modified, _ = apply_staleness_markings(body, doc, 0, len(doc.lines) - 1)
        assert (count, modified) == (1, True)
        assert new.count("<!-- STALE:") == 1
        assert new.index("<!-- STALE:") < new.index(FENCE)

    def test_a_fenced_warning_shaped_line_is_neither_stripped_nor_recognised(self):
        from staleness import _has_budget_warning, _strip_budget_warnings

        line = "<!-- WARNING: Pinned context ~9 tokens (budget: 3). x -->\n"
        fenced = f"{FENCE}\n{line}{FENCE}\n"
        fenced_doc, line_doc = parse(fenced), parse(line)
        assert _strip_budget_warnings(fenced_doc, 0, 2) == 0
        assert _has_budget_warning(fenced_doc, 0, 2) is False
        assert _has_budget_warning(line_doc, 0, 0) is True
        assert _strip_budget_warnings(line_doc, 0, 0) == 1  # the control: the bare line is stripped

    def test_the_archive_block_keeps_a_fenced_heading_inside_its_pin(self):
        import archive_pin
        from fixtures.pin_helpers import parse_pins

        body = ("<!-- pinned: 2026-01-01 -->\n### A\nintro\n"
                f"{FENCE}\n### fenced\n{FENCE}\n\n"
                "<!-- pinned: 2026-02-02 -->\n### B\nbody\n")
        doc = parse(body)
        block = archive_pin.extract_pin_block(doc, 0, len(doc.lines) - 1, 0, parse_pins(body))
        assert block.startswith("<!-- pinned: 2026-01-01 -->\n### A")
        assert "### fenced" in block
        assert "2026-02-02" not in block

    def test_the_pin_age_comes_from_the_resolved_comment(self):
        import check_pin_caps
        now = datetime(2026, 5, 1, tzinfo=timezone.utc)
        age = check_pin_caps._pin_age_days
        assert age("<!-- pinned: 2026-04-21 -->", now=now) == 10
        assert age("<!--  PINNED:2026-04-21, pin-size-override: x -->", now=now) == 10
        assert age("<!-- pinned: 2026-01-01, reconfirmed: 2026-04-30 because x -->",
                   now=now) == 1
        assert age("<!-- pinned: unknown -->", now=now) is None
        assert age("<!-- pinned: unknown, noted 2026-04-21 -->", now=now) is None


# ---------------------------------------------------------------------------
# The finder stays off the hot hooks' import path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module", ["shared", "pin_caps_gate", "pin_caps", "staleness", "session_init"])
def test_importing_a_hot_module_does_not_load_the_finder(module):
    """`shared/__init__.py` imports `pin_caps`, so a module-level finder
    import there would load the parser in every PACT hook; the readers import
    it inside the functions that use it."""
    probe = (
        f"import sys; sys.path.insert(0, {str(HOOKS_DIR)!r}); import {module}; "
        "print('shared.claude_md_markers' in sys.modules)"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                            text=True, env=env, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"
