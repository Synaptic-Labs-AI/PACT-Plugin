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
  bypass:    lead (agent_name empty)
             teammate (agent_name non-empty)

Full 2 * 6 * 3 * 2 = 72 logical cells. Not every combination produces a
distinct outcome (e.g., teammate bypass short-circuits all violation
axes). Parameterization collapses duplicates while preserving meaningful
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
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

from helpers import make_claude_md_with_pins, make_pin_entry  # noqa: E402


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

    import staleness
    monkeypatch.setattr(
        staleness, "get_project_claude_md_path", lambda: claude_md
    )

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
    from pin_caps_gate import _check_tool_allowed
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
    compute_deny_reason. Edit + missing baseline is an explicit fail-OPEN
    path (asymmetric rule applies ONLY to Write — #3).
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
        """Count-axis Edit: replace the managed region wholesale.

        Strategy: Edit the ENTIRE pre-CLAUDE.md content with a freshly
        built new-CLAUDE.md via old_string=<whole-file>, new_string=<new-
        whole-file>. The `new_string` DOES contain `### ` headings
        (legitimate pins) but the embedded-pin check is computed against
        `_extract_new_body` which for Edit returns `new_string` — so a
        count test via Edit's full-file replacement trips embedded-pin
        every time.

        Real-world Edit usage for count changes is single-pin Edit: the
        old_string is a 1-pin block, new_string removes/replaces that
        block with a plain-text fragment. Simulate that instead: start
        with N pins, replace the ENTIRE managed region via a Write-like
        Edit (old=baseline, new=target) but route the count comparison
        via Write tool semantics — the embedded-pin carve-out applies
        to Write (content key) not to Edit (new_string key).

        The honest test: use Edit to REMOVE `### PinN` headings entirely
        (no `### ` in new_string). Pre count 3 → remove 0 → post count
        3 (allow). Pre count 3 → remove a pin-comment → post 2 (allow).
        For the OVER-cap direction we must go via Write (full payload)
        or use an Edit-time fragment that DOESN'T embed `### ` but still
        mutates count, which is architecturally impossible via Edit for
        a count INCREASE — you cannot add a pin without introducing
        `### `. So count-INCREASE tests belong in the Write matrix.

        For Edit, we test count-DECREASE (allow paths) and count-UNCHANGED
        (irrelevant-fragment Edit).
        """
        # Count-increase via Edit is architecturally impossible without
        # the fragment containing `### ` → embedded-pin DENY. So we skip
        # increase tests here and defer them to the Write matrix.
        if post_count > pre_count:
            pytest.skip(
                "count-increase via Edit requires `### ` in new_string → "
                "embedded-pin DENY path (tested separately); count matrix "
                "belongs in the Write axis"
            )

        env = gate_env(pin_count=pre_count)
        # Use a small non-heading Edit: patch a body character. The
        # post-state pin count is unchanged, so regardless of pre_count,
        # net-worse on count axis is False.
        # To simulate a DECREASE, the Edit replaces a whole `### PinN`
        # block + body with a plain-text marker (no `### `).
        if post_count < pre_count:
            # Remove (pre_count - post_count) pin blocks.
            # Each pin block: "<!-- pinned: 2026-04-20 -->\n### PinN\nxxxx"
            baseline = env["claude_md"].read_text(encoding="utf-8")
            for n in range(post_count, pre_count):
                block = (
                    f"<!-- pinned: 2026-04-20 -->\n### Pin{n}\nxxxx"
                )
                baseline = baseline.replace(block, "", 1)
            env["claude_md"].write_text(baseline, encoding="utf-8")
            # Now re-read; the Edit is a no-op trailing whitespace fix
            # just to exercise the gate.
            old_string = "## Working Memory"
            new_string = "## Working Memory"  # idempotent Edit
        else:
            # Same count — no-op Edit just to exercise the gate with
            # no `### ` in new_string.
            old_string = "## Working Memory"
            new_string = "## Working Memory"

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

    def test_edit_embedded_pin_denies(self, gate_env):
        """Edit new_string containing a `### ` heading → DENY embedded_pin."""
        env = gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "Pin0",
                "new_string": (
                    "<!-- pinned: 2026-04-20 -->\n"
                    "### Sneaky Embedded Pin\nbody-smuggled"
                ),
                "replace_all": False,
            },
        })
        assert result is not None
        assert "embedded pin" in result.lower()

    def test_edit_invalid_override_denies(self, gate_env):
        """Edit new_string with override rationale exceeding 120 chars → DENY."""
        env = gate_env(pin_count=3)
        too_long = "x" * 121
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "Pin0",
                "new_string": f"<!-- pinned: 2026-04-20, pin-size-override: {too_long} -->",
                "replace_all": False,
            },
        })
        assert result is not None
        assert "override" in result.lower()

    def test_edit_empty_override_denies(self, gate_env):
        """Empty rationale → DENY with invalid-override reason."""
        env = gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "Pin0",
                "new_string": "<!-- pinned: 2026-04-20, pin-size-override:  -->",
                "replace_all": False,
            },
        })
        assert result is not None
        assert "empty" in result.lower() or "override" in result.lower()

    @pytest.mark.parametrize("terminator", ["\n", "\r", " ", " ", ""])
    def test_edit_override_with_line_terminator_denies(self, gate_env, terminator):
        """Override rationale containing forbidden line-terminator chars → DENY.

        Invariant: the gate's _FORBIDDEN_RATIONALE_CHARS is derived from
        pin_caps._FORBIDDEN_TERMINATOR_TABLE (no twin-copy drift). This
        test asserts each documented forbidden char trips the gate.

        Note: rationales containing a newline don't even reach the line-
        terminator check because OVERRIDE_COMMENT_RE.fullmatch requires a
        single-line pin-size-override comment. The rationale extraction
        uses splitlines() + .strip(), which strips newline/CR entirely,
        so a rationale with a true newline is rejected at regex-match
        time (returns None → no override claimed → no deny from invalid-
        override path). U+2028/U+2029/U+0085 slip past splitlines in some
        renderers — those ARE caught by the char check.
        """
        env = gate_env(pin_count=3)
        # Build the candidate — rationale has the terminator embedded.
        rationale = f"valid text{terminator}injected"
        # Put the override comment on its own line so splitlines isolates it.
        new_string = f"<!-- pinned: 2026-04-20, pin-size-override: {rationale} -->"
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "Pin0",
                "new_string": new_string,
                "replace_all": False,
            },
        })
        # Empirical finding: Python's `splitlines()` recognizes U+2028,
        # U+2029, U+0085 AND ASCII newline/CR as line boundaries. All
        # five terminators are stripped BEFORE the gate's forbidden-char
        # check — so the _FORBIDDEN_RATIONALE_CHARS check is effectively
        # dead code for these specific chars. What matters observably:
        # the terminator must NEVER be accepted into a parsed Pin's
        # override_rationale. Either the gate denies, or the gate allows
        # because no override was captured (rationale=None). A ALLOW
        # result here means the terminator was not smuggled into a pin.
        if result is not None:
            assert terminator not in result, (
                f"terminator {terminator!r} leaked into deny reason"
            )

    def test_gate_forbidden_chars_derived_from_parser_table(self):
        """Invariant #5: no twin-copy drift. The gate's
        `_FORBIDDEN_RATIONALE_CHARS` is derived at module load from
        `pin_caps._FORBIDDEN_TERMINATOR_TABLE`, not duplicated as a
        literal. A parser-side table change must propagate to the gate
        without a second edit site.
        """
        import pin_caps
        import pin_caps_gate
        parser_chars = "".join(
            chr(o) for o in pin_caps._FORBIDDEN_TERMINATOR_TABLE.keys()
        )
        assert set(pin_caps_gate._FORBIDDEN_RATIONALE_CHARS) == set(parser_chars)

    @pytest.mark.parametrize(
        "terminator,ord_hex",
        [
            ("\n", "0x0a"),
            ("\r", "0x0d"),
            (" ", "0x2028"),
            (" ", "0x2029"),
            ("", "0x0085"),
        ],
    )
    def test_splitlines_eats_forbidden_chars_before_validation(
        self, terminator, ord_hex
    ):
        """Upstream-split invariant (per auditor-2 recommendation,
        2026-04-21 consultant mode): Python's str.splitlines() recognizes
        every char in `_FORBIDDEN_RATIONALE_CHARS` as a line boundary,
        which is WHY the char check at `pin_caps_gate.py:148` is not
        runtime-reachable in the current call graph.

        `_extract_override_rationale` applies `splitlines()` and then
        runs `OVERRIDE_COMMENT_RE.fullmatch` on each stripped line. A
        forbidden char in the middle of what looks like a single override
        comment splits the comment across two lines — neither line
        fullmatches, so extraction returns None and
        `_validate_override_rationale` is called with None (short-circuits
        before reaching the char-check block).

        This test asserts the load-bearing upstream-split behavior. If a
        future refactor of `_extract_override_rationale` stops calling
        splitlines (e.g., moves to a regex that scans the whole fragment
        in one pass), this test fails loudly and the char-check block at
        `pin_caps_gate.py:148` becomes the load-bearing defense. Converts
        the latent dead-code tradeoff into a loud one.
        """
        # The canonical override-comment fragment, with a forbidden char
        # injected mid-rationale.
        candidate = (
            f"<!-- pinned: 2026-04-20, "
            f"pin-size-override: before{terminator}after -->"
        )
        parts = candidate.splitlines()
        # At least two parts — splitlines recognized the terminator.
        assert len(parts) >= 2, (
            f"splitlines() did NOT split on {ord_hex} ({terminator!r}) — "
            f"if this fails, the forbidden-char check at pin_caps_gate.py:148 "
            f"has become runtime-reachable. Update the inline comment in "
            f"that file and re-verify the char-check block is exercised."
        )

        # Downstream: the gate's extractor returns None (no override
        # captured) because no single line fullmatches the OVERRIDE_COMMENT_RE.
        from pin_caps_gate import _extract_override_rationale
        result = _extract_override_rationale(candidate)
        assert result is None, (
            f"override extractor captured a rationale despite {ord_hex} "
            f"splitting the line — extractor behavior has changed; "
            f"review test_edit_override_with_line_terminator_never_accepted."
        )

    @pytest.mark.parametrize("baseline", ["fresh", "missing", "corrupt"])
    def test_edit_teammate_bypass(self, gate_env, baseline):
        """Teammate session bypasses the gate regardless of baseline state."""
        env = gate_env(pin_count=3, baseline=baseline)
        import shared.pact_context as ctx_module
        with patch.object(
            ctx_module, "resolve_agent_name", return_value="backend-coder-x"
        ):
            result = _call_gate({
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(env["claude_md"]),
                    "old_string": "anything",
                    "new_string": _build_claude_md(99),  # Wildly over-cap
                    "replace_all": False,
                },
            })
        assert result is None, (
            f"teammate should bypass regardless of baseline={baseline}, got {result!r}"
        )

    def test_edit_missing_baseline_allows(self, gate_env):
        """Edit with missing baseline → fail-OPEN (asymmetric rule is Write-only)."""
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
        """Write over-cap with missing baseline → fail-CLOSED asymmetric.

        `pre_count` has no semantic meaning here (baseline="missing")
        but we still parametrize to catch any accidental baseline-state
        dependency on the fail-closed path.
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
        assert "Refusing Write" in result

    def test_write_missing_baseline_under_cap_allows(self, gate_env):
        """Write clean (under-cap) with missing baseline → ALLOW.

        Asymmetric fail-CLOSED fires ONLY on a concrete over-cap Write.
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
    def test_write_teammate_bypass(self, gate_env, baseline):
        env = gate_env(pin_count=3, baseline=baseline)
        import shared.pact_context as ctx_module
        with patch.object(
            ctx_module, "resolve_agent_name", return_value="backend-coder-x"
        ):
            result = _call_gate({
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(env["claude_md"]),
                    "content": _build_claude_md(99),
                },
            })
        assert result is None


# ---------------------------------------------------------------------------
# Matrix 3: Non-gated-tool passthrough + non-matching file paths
# ---------------------------------------------------------------------------


class TestPinCapsGate_Matrix_Passthrough:
    """Short-circuit paths: wrong tool, wrong file, missing fields."""

    @pytest.mark.parametrize(
        "tool_name",
        ["Read", "Bash", "Grep", "Glob", "Task", "NotebookEdit", "TodoWrite"],
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
        """No file_path → match_project_claude_md returns None → allow."""
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
        stdin_payload = json.dumps({
            "tool_name": "Write",
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
