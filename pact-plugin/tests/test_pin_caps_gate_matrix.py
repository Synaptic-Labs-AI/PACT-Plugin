"""
Phase E exhaustive cap-check matrix for hooks/pin_caps_gate.py.

Risk tier: CRITICAL. Matrix covers the full PreToolUse gate decision
surface on top of the smoke tests in test_pin_caps_gate.py. Class names
use scope-suffix (TestPinCapsGate_Matrix_*) to dodge pytest shadow-class
collisions with test_pin_caps_gate.py's TestPinCapsGate_*.

Matrix axes:
  tool:      Edit | Write
  violation: under-cap | at-cap | over-cap-count | over-cap-size |
             embedded-pin | invalid-override
  baseline:  fresh (existing CLAUDE.md with N < cap pins)
             missing (no CLAUDE.md on disk)
             corrupt (CLAUDE.md exists but no Pinned Context section)
  frame:     team-lead
             teammate (gated like the lead; a count denial asks the
             team-lead instead of naming the pin command)

Full 2 * 6 * 3 * 2 = 72 logical cells. Not every combination produces a
distinct outcome. Parameterization collapses duplicates while preserving meaningful
discrimination. Total parameterized cases: ~100.

Invariants enforced:
  #1 symmetric oracle (parse_pins on both sides)
  #2 net-worse strict `>`
  #3 Write-baseline fail-CLOSED asymmetric exception
  #4 failure_log observability on fail-open bypass paths
  #5 no twin-copy drift (parser/hook share parse_pins, not regex clones)
  #6 override validation ONLY in hook primary path
  #7 str.replace Edit-simulation byte-identical
  #8 full-replacement emulation (Write is full file, not fragment)
"""

import json

import pytest

from helpers import make_claude_md_with_pins, make_pin_entry, point_resolver_at  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixture: gate test environment with tunable baseline state.
# ---------------------------------------------------------------------------


@pytest.fixture
def gate_env(tmp_path, monkeypatch, pact_context):
    """Build a pin_caps_gate test env.

    Returns a `setup(pin_count=N, baseline='fresh'|'missing'|'corrupt')`
    callable. Baseline controls the state of the on-disk CLAUDE.md before
    the gate fires.
    """
    claude_md = tmp_path / "CLAUDE.md"
    pact_context(
        team_name="test-team",
        session_id="session-matrix",
        project_dir=str(tmp_path),
    )

    point_resolver_at(monkeypatch, tmp_path)

    def _setup(pin_count=3, baseline="fresh"):
        if baseline == "missing":
            # Leave CLAUDE.md off disk.
            if claude_md.exists():
                claude_md.unlink()
        elif baseline == "corrupt":
            # File exists but has no Pinned Context section — parser
            # returns None → hook treats as empty baseline.
            claude_md.write_text(
                "# PACT Framework and Managed Project Memory\n"
                "\n"
                "Some prose but no managed-region markers.\n",
                encoding="utf-8",
            )
        elif baseline == "unreadable":
            # File exists with content but unreadable (permission 0).
            entries = [
                make_pin_entry(title=f"Pin{i}", body_chars=4)
                for i in range(pin_count)
            ]
            claude_md.write_text(
                make_claude_md_with_pins(entries), encoding="utf-8"
            )
            claude_md.chmod(0o000)
        else:
            entries = [
                make_pin_entry(title=f"Pin{i}", body_chars=4)
                for i in range(pin_count)
            ]
            claude_md.write_text(
                make_claude_md_with_pins(entries), encoding="utf-8"
            )
        return {"claude_md": claude_md, "tmp_path": tmp_path}

    yield _setup

    # Restore perms so tmp_path teardown can clean up.
    if claude_md.exists():
        try:
            claude_md.chmod(0o644)
        except OSError:
            pass


def _call_gate(input_data):
    # #878: the gate now keys lead-detection on is_lead (the harness-set
    # agent_type), not the old empty-resolve_agent_name heuristic. Default to a
    # LEAD frame (the unmarked case these DENY tests assume) unless the caller
    # supplies an explicit agent_type (the teammate tests).
    from pin_caps_gate import _check_tool_allowed
    if "agent_type" not in input_data:
        input_data = {**input_data, "agent_type": "pact-orchestrator"}
    return _check_tool_allowed(input_data)


def _build_claude_md(pin_count, pin_body_chars=4, with_override=False):
    entries = []
    for i in range(pin_count):
        if with_override and i == 0:
            entries.append(
                make_pin_entry(
                    title=f"Pin{i}",
                    body_chars=pin_body_chars,
                    override_rationale="verbatim load-bearing — do not split",
                )
            )
        else:
            entries.append(make_pin_entry(title=f"Pin{i}", body_chars=pin_body_chars))
    return make_claude_md_with_pins(entries)


# ---------------------------------------------------------------------------
# Matrix 1: Edit × violation × baseline × bypass
# ---------------------------------------------------------------------------


class TestPinCapsGate_Matrix_Edit:
    """Edit-tool cap checks across violation × baseline × bypass.

    The Edit path goes: baseline read → parse → simulate via str.replace →
    compute_deny_reason. An Edit of a file that does not exist replaces
    nothing, so it allows.
    """

    @pytest.mark.parametrize(
        "pre_count,post_count,expected_allow",
        [
            (3, 3, True),     # under-cap → under-cap
            (3, 11, True),    # under-cap → just-under-cap
            (3, 12, True),    # under-cap → at-cap (strict `>` allows 12)
            (3, 13, False),   # under-cap → over-cap-count (net-worse)
            (3, 20, False),   # under-cap → wildly-over (net-worse)
            (13, 13, True),   # pre bad, post same count — NOT net-worse
            (13, 14, False),  # pre bad, post worse count — net-worse
            (14, 13, True),   # pre worse than post — allow (improved)
        ],
    )
    def test_edit_count_axis(self, gate_env, pre_count, post_count, expected_allow):
        """Count-axis Edit, each case a real change. Pins are added as undated
        `### ` lines before `## Working Memory`: rule U counts a heading as a pin
        whatever its comment, so an undated add is ordinary growth, allowed up
        to the cap and denied past it. A decrease removes the last pin blocks;
        an unchanged count edits one pin's body."""
        env = gate_env(pin_count=pre_count)
        baseline = env["claude_md"].read_text(encoding="utf-8")
        if post_count > pre_count:
            added = "".join(f"### Added{i}\nbody\n\n" for i in range(post_count - pre_count))
            old_string, new_string = "## Working Memory", added + "## Working Memory"
        elif post_count < pre_count:
            blocks = [f"<!-- pinned: 2026-04-20 -->\n### Pin{n}\nxxxx" for n in range(post_count, pre_count)]
            old_string, new_string = "\n\n" + "\n\n".join(blocks), ""
        else:
            old_string, new_string = "### Pin0\nxxxx", "### Pin0\nxxxy"
        assert old_string in baseline, (pre_count, post_count)

        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": old_string,
                "new_string": new_string,
                "replace_all": False,
            },
        })
        if expected_allow:
            assert result is None, (
                f"pre={pre_count} post={post_count} should ALLOW, got: {result!r}"
            )
        else:
            assert result is not None, f"pre={pre_count} post={post_count} should DENY"
            assert "Pin count cap" in result

    @pytest.mark.parametrize(
        "pre_body,post_body,expected_allow",
        [
            (100, 100, True),    # under-cap → under-cap
            (100, 1500, True),   # under-cap → at-cap (boundary)
            (100, 1501, False),  # under-cap → just-over (net-worse)
            (1501, 1501, True),  # pre over-cap, post same — not net-worse
            (1501, 1700, False), # pre over-cap, post worse — net-worse
            (1700, 1501, True),  # pre worst, post less-worst — allow (improved)
        ],
    )
    def test_edit_size_axis(self, gate_env, pre_body, post_body, expected_allow):
        """Size-axis Edit: patch the pin BODY without touching `### `.

        old_string / new_string contain only body characters ('x' padding)
        — no `### ` heading. This dodges the embedded-pin short-circuit
        that would fire on a full-file new_string.
        """
        env = gate_env(pin_count=0)
        env["claude_md"].write_text(
            _build_claude_md(1, pin_body_chars=pre_body), encoding="utf-8"
        )
        old_string = "x" * pre_body
        new_string = "x" * post_body
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": old_string,
                "new_string": new_string,
                "replace_all": False,
            },
        })
        if expected_allow:
            assert result is None, f"pre={pre_body} post={post_body} should ALLOW, got {result!r}"
        else:
            assert result is not None, f"pre={pre_body} post={post_body} should DENY"
            assert "cap" in result.lower()

    def test_edit_undated_heading_is_counted_as_a_pin(self, gate_env):
        """An undated `### ` heading added by an Edit is a pin, counted by the
        count axis: at 3 pins plus one it is under the cap and allowed. There
        is no embedded-pin refusal keyed on the missing date comment."""
        env = gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "## Working Memory\n",
                "new_string": (
                    "### SmuggledNoDateMarker\n"
                    "body-smuggled\n\n"
                    "## Working Memory\n"
                ),
                "replace_all": False,
            },
        })
        assert result is None, f"an undated pin under the cap must be allowed, got: {result!r}"

    def test_edit_invalid_override_denies(self, gate_env):
        """An override row whose rationale exceeds 120 chars, on a pin the
        Edit changes → DENY with the invalid-override reason."""
        env = gate_env(pin_count=3)
        too_long = "x" * 121
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "<!-- pinned: 2026-04-20 -->\n### Pin0",
                "new_string": f"<!-- pinned: 2026-04-20, pin-size-override: {too_long} -->\n### Pin0",
                "replace_all": False,
            },
        })
        assert result is not None
        assert "override" in result.lower()

    def test_override_text_inside_a_heading_is_not_an_override(self, gate_env):
        """Override text written into a heading line is not an override row:
        nothing is granted, so there is nothing to refuse (main refused it by
        scanning the raw fragment)."""
        env = gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "Pin0",
                "new_string": "<!-- pinned: 2026-04-20, pin-size-override: {} -->".format("x" * 121),
                "replace_all": False,
            },
        })
        assert result is None, result

    def test_edit_empty_override_denies(self, gate_env):
        """An override row with a blank rationale on a pin the Edit changes →
        DENY with the invalid-override reason."""
        env = gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "<!-- pinned: 2026-04-20 -->\n### Pin0",
                "new_string": "<!-- pinned: 2026-04-20, pin-size-override:   -->\n### Pin0",
                "replace_all": False,
            },
        })
        assert result is not None
        assert "empty" in result.lower() or "override" in result.lower()

    def test_a_fenced_example_of_the_override_syntax_is_not_an_override(self, gate_env):
        """A new pin documenting the override syntax inside a fence, with an
        empty rationale, is allowed: fenced lines are not override rows."""
        env = gate_env(pin_count=3)
        example = (
            "<!-- pinned: 2026-04-21 -->\n### Override syntax\n"
            "```\n<!-- pinned: 2026-04-20, pin-size-override:   -->\n```\n\n"
        )
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "## Working Memory\n",
                "new_string": example + "## Working Memory\n",
                "replace_all": False,
            },
        })
        assert result is None, result

    def test_an_unchanged_pins_old_invalid_override_is_not_rechecked(self, gate_env):
        """A Write that leaves a pin and its invalid override untouched is not
        refused for that override: only pins the change adds or edits are
        checked."""
        env = gate_env(pin_count=3)
        bad = env["claude_md"].read_text(encoding="utf-8").replace(
            "<!-- pinned: 2026-04-20 -->\n### Pin2", "<!-- pinned: 2026-04-20, pin-size-override:   -->\n### Pin2", 1)
        env["claude_md"].write_text(bad, encoding="utf-8")
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {"file_path": str(env["claude_md"]), "content": bad.replace("### Pin0", "### Pin0 renamed")},
        })
        assert result is None, result

    def test_an_override_shaped_row_inside_a_body_is_not_an_override(self, gate_env):
        """Only the row the parser attributes to a pin as its comment is read as
        its override. The same text further down an edited pin's body is body
        text, so an empty rationale there is not refused."""
        env = gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "### Pin1\nxxxx",
                "new_string": "### Pin1\nxxxx\n<!-- pinned: 2026-04-20, pin-size-override:   -->",
                "replace_all": False,
            },
        })
        assert result is None, result

    @pytest.mark.parametrize("terminator, ord_hex", [
        ("\n", "0x0a"), ("\r", "0x0d"), ("\u2028", "0x2028"), ("\u2029", "0x2029"),
        ("\x85", "0x0085"), ("\x0b", "0x0b"), ("\x0c", "0x0c"), ("\x1c", "0x1c"),
    ])
    def test_a_terminator_in_a_rationale_is_not_an_override(self, gate_env, terminator, ord_hex):
        """A comment row broken by a line terminator, or holding a character
        `str.splitlines` breaks at, is not attributed to the pin, so it is not
        an override: the gate does not refuse it as an invalid one, and a small
        pin under it is allowed. The second pin is used because there the
        unattributed row sits among the first pin's rows, where the gate used to
        read it as an override and refuse a rationale holding U+2028, U+2029 or
        U+0085 as invalid."""
        env = gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "<!-- pinned: 2026-04-20 -->\n### Pin1",
                "new_string": (f"<!-- pinned: 2026-04-20, pin-size-override: valid text{terminator}injected -->\n"
                               "### Pin1"),
                "replace_all": False,
            },
        })
        assert result is None, (ord_hex, result)

    @pytest.mark.parametrize(
        "terminator,ord_hex",
        [
            ("\n", "0x0a"),
            ("\r", "0x0d"),
            ("\u2028", "0x2028"),
            ("\u2029", "0x2029"),
            ("\x85", "0x0085"),
        ],
    )
    def test_a_terminator_in_a_rationale_never_passes_as_an_override(
        self, gate_env, terminator, ord_hex
    ):
        """A forbidden line terminator inside an override rationale never
        unlocks the size cap. The parser ends a row only at \\r and \\n, and
        attributes no comment row holding U+2028, U+2029 or U+0085, so no
        override is granted: a pin grown past 1,500 characters under such a
        comment is refused on size."""
        env = gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "<!-- pinned: 2026-04-20 -->\n### Pin0\nxxxx",
                "new_string": (f"<!-- pinned: 2026-04-20, pin-size-override: before{terminator}after -->\n"
                               f"### Pin0\n" + "y" * 1600),
                "replace_all": False,
            },
        })
        assert result is not None, f"{ord_hex}: a smuggled terminator unlocked the size cap"
        assert "chars (cap: 1500)" in result, (ord_hex, result)

    @pytest.mark.parametrize(
        "terminator,ord_hex",
        [
            (chr(0x2028), "0x2028"),
            (chr(0x2029), "0x2029"),
            (chr(0x0085), "0x0085"),
            ("\r", "0x0d"),
        ],
    )
    def test_oracle_symmetry_terminator_smuggling(self, terminator, ord_hex):
        """The parser never grants an override whose rationale holds a
        terminator, and the gate never lets one through as valid: a smuggled
        terminator cannot unlock the size cap on either side."""
        from pin_caps import parse_pins
        from pin_caps_gate import gate_decision

        candidate = (
            f"<!-- pinned: 2026-04-20, "
            f"pin-size-override: smuggled{terminator}rationale -->"
        )
        parsed = parse_pins(f"{candidate}\n### TargetPin\nbody text here\n")
        assert all(pin.override_rationale is None for pin in parsed), (ord_hex, parsed)
        before = _build_claude_md(3)
        after = before.replace("<!-- pinned: 2026-04-20 -->\n### Pin0", f"{candidate}\n### Pin0", 1)
        decision = gate_decision(before, "Write", {"content": after})
        assert decision.cause != "override", decision

    @pytest.mark.parametrize("baseline, gated", [("fresh", True), ("missing", False), ("corrupt", False)])
    def test_edit_teammate_is_gated(self, gate_env, baseline, gated):
        """A teammate's Edit is gated like the lead's. Adding ten pins to the
        3-pin file is refused with the ask-the-team-lead text; with no file, or
        a file with no Working Memory heading, the Edit replaces nothing and is
        allowed, as it is for the lead."""
        env = gate_env(pin_count=3, baseline=baseline)
        added = "".join(f"<!-- pinned: 2026-04-21 -->\n### Added{i}\nbody\n\n" for i in range(10))
        result = _call_gate({
            "tool_name": "Edit",
            "agent_type": "pact-backend-coder",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "## Working Memory",
                "new_string": added + "## Working Memory",
                "replace_all": False,
            },
        })
        if gated:
            assert result is not None and result.endswith("Do not change CLAUDE.md yourself, by any route; tell the team-lead."), result
        else:
            assert result is None, (baseline, result)

    def test_edit_missing_baseline_allows(self, gate_env):
        """Edit with no file before → nothing to replace → ALLOW."""
        env = gate_env(pin_count=0, baseline="missing")
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "foo",
                "new_string": "bar",
                "replace_all": False,
            },
        })
        assert result is None

    def test_edit_corrupt_baseline_allows(self, gate_env):
        """Edit with corrupt baseline (no managed region) → fail-OPEN."""
        env = gate_env(baseline="corrupt")
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "prose",
                "new_string": "replacement",
                "replace_all": False,
            },
        })
        # Baseline parses to 0 pins (no Pinned Context section); Edit
        # simulates post-edit that also has no managed region → 0 pins.
        # Net-worse check: 0 vs 0 → not worse → allow.
        assert result is None


# ---------------------------------------------------------------------------
# Matrix 2: Write × violation × baseline × bypass
# ---------------------------------------------------------------------------


class TestPinCapsGate_Matrix_Write:
    """Write-tool cap checks. Write is full-file replacement so embedded-pin
    is NOT applicable (legitimate CLAUDE.md contains `### ` by construction
    — hook skips embedded-pin check on Write per _extract_new_body)."""

    @pytest.mark.parametrize(
        "pre_count,post_count,expected_allow",
        [
            (3, 3, True),
            (3, 12, True),    # at-cap allowed
            (3, 13, False),   # over-cap denied
            (3, 14, False),
            (13, 13, True),   # pre bad, post same — not net-worse
            (13, 14, False),  # net-worse
            (14, 13, True),   # improvement
        ],
    )
    def test_write_count_axis(self, gate_env, pre_count, post_count, expected_allow):
        env = gate_env(pin_count=pre_count)
        new_content = _build_claude_md(post_count)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": new_content,
            },
        })
        if expected_allow:
            assert result is None, f"pre={pre_count} post={post_count} should ALLOW"
        else:
            assert result is not None
            assert "Pin count cap" in result

    @pytest.mark.parametrize(
        "pre_body,post_body,expected_allow",
        [
            (100, 100, True),
            (100, 1500, True),
            (100, 1501, False),
            (1501, 1501, True),
            (1501, 1700, False),
            (1700, 1501, True),
        ],
    )
    def test_write_size_axis(self, gate_env, pre_body, post_body, expected_allow):
        env = gate_env(pin_count=0)
        env["claude_md"].write_text(
            _build_claude_md(1, pin_body_chars=pre_body), encoding="utf-8"
        )
        new_content = _build_claude_md(1, pin_body_chars=post_body)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": new_content,
            },
        })
        if expected_allow:
            assert result is None
        else:
            assert result is not None
            assert "cap" in result.lower()

    def test_write_embedded_pin_in_content_is_not_flagged(self, gate_env):
        """Invariant #8: Write's full payload contains `### ` headings by
        construction — the embedded-pin check must be SKIPPED on Write,
        or every legitimate Write denies. Only net-worse count catches
        inflation via Write."""
        env = gate_env(pin_count=3)
        # Write a legit 11-pin CLAUDE.md — every pin has `### Heading`.
        new_content = _build_claude_md(11)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": new_content,
            },
        })
        assert result is None

    def test_write_at_cap_boundary_allows(self, gate_env):
        """12/12 is at the cap, not over — invariant #2 (strict `>`)."""
        env = gate_env(pin_count=3)
        new_content = _build_claude_md(12)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": new_content,
            },
        })
        assert result is None

    @pytest.mark.parametrize("pre_count", [0, 3, 11])
    def test_write_missing_baseline_over_cap_denies(
        self, gate_env, pre_count
    ):
        """Write over-cap with no file before (a first Write) → compared with
        an empty file, refused on count.

        `pre_count` has no semantic meaning here (baseline="missing")
        but we still parametrize to catch any accidental baseline-state
        dependency on the first-Write path.
        """
        env = gate_env(pin_count=pre_count, baseline="missing")
        new_content = _build_claude_md(13)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": new_content,
            },
        })
        assert result is not None
        assert "Pin count cap" in result

    def test_write_missing_baseline_under_cap_allows(self, gate_env):
        """Write clean (under-cap) with no file before → ALLOW.

        A first Write is refused only when its own pins are over the cap.
        """
        env = gate_env(baseline="missing")
        new_content = _build_claude_md(3)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": new_content,
            },
        })
        assert result is None

    def test_write_corrupt_baseline_over_cap_denies(self, gate_env):
        """Corrupt baseline (no managed region) + over-cap Write.

        The baseline READ succeeds (file exists), but _parse_pinned_section
        returns None. pre_pins = []. Post-state computed normally. Since
        post > cap and pre was empty, net-worse → deny with standard
        count-cap reason (NOT the fail-CLOSED reason — baseline WAS
        readable, just had no pins).
        """
        env = gate_env(baseline="corrupt")
        new_content = _build_claude_md(13)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": new_content,
            },
        })
        assert result is not None
        assert "Pin count cap" in result

    def test_write_invalid_override_in_content_denies(self, gate_env):
        """Override validation on Write content payload."""
        env = gate_env(pin_count=3)
        too_long = "x" * 121
        # Build a valid CLAUDE.md shell but stuff an invalid override in.
        malformed = _build_claude_md(1).replace(
            "<!-- pinned: 2026-04-20 -->",
            f"<!-- pinned: 2026-04-20, pin-size-override: {too_long} -->",
            1,
        )
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": malformed,
            },
        })
        assert result is not None
        assert "override" in result.lower()

    @pytest.mark.parametrize("baseline", ["fresh", "missing", "corrupt"])
    def test_write_teammate_is_gated(self, gate_env, baseline):
        """A teammate's 99-pin Write is refused whatever the file before, with
        the ask-the-team-lead text in place of the pin command."""
        env = gate_env(pin_count=3, baseline=baseline)
        result = _call_gate({
            "tool_name": "Write",
            "agent_type": "pact-backend-coder",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": _build_claude_md(99),
            },
        })
        assert result is not None and result.endswith("Do not change CLAUDE.md yourself, by any route; tell the team-lead."), result
        assert "/PACT:" not in result


# ---------------------------------------------------------------------------
# Matrix 3: Non-gated-tool passthrough + non-matching file paths
# ---------------------------------------------------------------------------


class TestPinCapsGate_Matrix_Passthrough:
    """Short-circuit paths: wrong tool, wrong file, missing fields."""

    @pytest.mark.parametrize(
        "tool_name",
        ["Read", "Bash", "Grep", "Glob", "Agent", "NotebookEdit", "TodoWrite"],
    )
    def test_non_gated_tools_allow(self, gate_env, tool_name):
        env = gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": tool_name,
            "tool_input": {"file_path": str(env["claude_md"])},
        })
        assert result is None

    def test_edit_non_claude_md_path_allows(self, gate_env):
        env = gate_env(pin_count=3)
        other = env["tmp_path"] / "notes.md"
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(other),
                "old_string": "anything",
                "new_string": _build_claude_md(99),
                "replace_all": False,
            },
        })
        assert result is None

    def test_write_non_claude_md_path_allows(self, gate_env):
        env = gate_env(pin_count=3)
        other = env["tmp_path"] / "notes.md"
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(other),
                "content": _build_claude_md(99),
            },
        })
        assert result is None

    def test_empty_tool_input_allows(self, gate_env):
        """Malformed tool_input (non-dict) → short-circuit allow."""
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": "not a dict",
        })
        assert result is None

    def test_missing_file_path_allows(self, gate_env):
        """No file_path → gate_target returns None → allow."""
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "old_string": "foo",
                "new_string": "bar",
            },
        })
        assert result is None


# ---------------------------------------------------------------------------
# Matrix 4: main() stdin integration — JSON payload, exit codes
# ---------------------------------------------------------------------------


class TestPinCapsGate_Matrix_Main:
    """End-to-end main() behavior: stdin → exit code + stdout JSON."""

    def test_allow_emits_suppress_output_exit_0(self, gate_env, monkeypatch, capsys):
        env = gate_env(pin_count=3)
        stdin_payload = json.dumps({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "irrelevant",
                "new_string": "also irrelevant",
                "replace_all": False,
            },
        })
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_payload))
        import pin_caps_gate
        with pytest.raises(SystemExit) as exc_info:
            pin_caps_gate.main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert '"suppressOutput": true' in captured.out

    def test_deny_emits_permission_decision_exit_2(
        self, gate_env, monkeypatch, capsys
    ):
        env = gate_env(pin_count=3)
        new_content = _build_claude_md(13)
        # #878: lead frame (agent_type) so the is_lead-gated DENY path fires.
        stdin_payload = json.dumps({
            "tool_name": "Write",
            "agent_type": "pact-orchestrator",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": new_content,
            },
        })
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_payload))
        import pin_caps_gate
        with pytest.raises(SystemExit) as exc_info:
            pin_caps_gate.main()
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert "Pin count cap" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_empty_stdin_fails_open(self, monkeypatch):
        """Empty stdin → JSON decode error → fail-open exit 0."""
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        import pin_caps_gate
        with pytest.raises(SystemExit) as exc_info:
            pin_caps_gate.main()
        assert exc_info.value.code == 0

    def test_non_json_stdin_fails_open(self, monkeypatch):
        """Random bytes on stdin → JSON decode error → fail-open."""
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO("garbage not json"))
        import pin_caps_gate
        with pytest.raises(SystemExit) as exc_info:
            pin_caps_gate.main()
        assert exc_info.value.code == 0

    def test_missing_tool_name_allows(self, gate_env, monkeypatch):
        """Valid JSON without tool_name → gate short-circuits (not in GATED_TOOLS)."""
        env = gate_env(pin_count=3)
        stdin_payload = json.dumps({
            "tool_input": {"file_path": str(env["claude_md"])},
        })
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin_payload))
        import pin_caps_gate
        with pytest.raises(SystemExit) as exc_info:
            pin_caps_gate.main()
        assert exc_info.value.code == 0
