"""
Counter-test-by-revert for pin_caps_gate.py predicates.

For each of the four cap predicates (count, size, embedded_pin, override),
we temporarily REVERT the predicate's enforcement logic via monkeypatch,
run a target test, assert it would FAIL against the reverted source, then
restore. This proves each predicate-level test is load-bearing — not
exercising a proxy.

Per the staged-peer-fix phantom-green institutional memory, we do NOT
revert via git-checkout on the shared worktree. Reversions are in-
memory monkeypatches scoped to single tests. This avoids corrupting
the worktree for parallel readers and makes each counter-test trivially
reversible.

Cardinality pins (per-predicate failure count on revert):
  count      : 1 load-bearing target test → 1 fail on revert
  size       : 1 load-bearing target test → 1 fail on revert
  embedded   : 1 load-bearing target test → 1 fail on revert
  override   : 2 load-bearing target tests (len + empty) → 2 fail on revert

If any counter-test produces 0 fails, the target test is phantom-green
and must be rewritten.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

from helpers import make_claude_md_with_pins, make_pin_entry  # noqa: E402


@pytest.fixture
def gate_env(tmp_path, monkeypatch, pact_context):
    """Minimal gate test environment (parallel to test_pin_caps_gate.py)."""
    claude_md = tmp_path / "CLAUDE.md"
    pact_context(
        team_name="test-team",
        session_id="session-counter",
        project_dir=str(tmp_path),
    )

    import staleness
    monkeypatch.setattr(
        staleness, "get_project_claude_md_path", lambda: claude_md
    )

    def _setup(pin_count=3, body_chars=4):
        entries = [
            make_pin_entry(title=f"Pin{i}", body_chars=body_chars)
            for i in range(pin_count)
        ]
        claude_md.write_text(
            make_claude_md_with_pins(entries), encoding="utf-8"
        )
        return {"claude_md": claude_md}

    return _setup


def _call_gate(input_data):
    from pin_caps_gate import _check_tool_allowed
    return _check_tool_allowed(input_data)


def _build_claude_md(pin_count, pin_body_chars=4):
    entries = [
        make_pin_entry(title=f"Pin{i}", body_chars=pin_body_chars)
        for i in range(pin_count)
    ]
    return make_claude_md_with_pins(entries)


# ---------------------------------------------------------------------------
# COUNT predicate counter-test
# ---------------------------------------------------------------------------


class TestCounterRevert_CountPredicate:
    """Revert the count predicate: allow any count, deny never.

    Target test: Write of 13 pins against 3-pin baseline should DENY.
    Revert: monkeypatch `evaluate_full_state` to skip the count check.
    Expectation: target test FAILS on reverted source.
    """

    def test_target_test_passes_on_production_source(self, gate_env):
        """Baseline: target test passes against unmodified source."""
        env = gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": _build_claude_md(13),
            },
        })
        assert result is not None
        assert "Pin count cap" in result

    def test_counter_revert_count_predicate_causes_failure(
        self, gate_env, monkeypatch
    ):
        """Revert count check → target test SHOULD fail on reverted source.

        Monkeypatch `evaluate_full_state` to ignore count-cap entirely
        (only check size). If the test still PASSES, the count test is
        phantom-green.
        """
        import pin_caps

        original = pin_caps.evaluate_full_state

        def reverted_evaluate_full_state(pins):
            # Skip count check entirely — only size.
            for pin in pins:
                if (
                    pin.body_chars > pin_caps.PIN_SIZE_CAP
                    and not pin_caps.has_size_override(pin)
                ):
                    return pin_caps.CapViolation(
                        kind="size",
                        detail=f"pin '{pin.heading}' body is {pin.body_chars} chars",
                        offending_pin_chars=pin.body_chars,
                        current_count=len(pins),
                    )
            return None

        # Patch in the module where compute_deny_reason uses it.
        monkeypatch.setattr(
            pin_caps, "evaluate_full_state", reverted_evaluate_full_state
        )

        env = gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": _build_claude_md(13),
            },
        })
        # On reverted source, 13/12 is NOT flagged → result is None.
        # This confirms the count-cap target test above is load-bearing.
        assert result is None, (
            f"count predicate counter-revert should NOT deny 13/12, got: {result!r}. "
            "If this test fails, the count-cap check has a second enforcement "
            "site we haven't reverted — investigate for dual-patch phantom-green."
        )
        # Restore — not strictly needed (monkeypatch reverts) but explicit.
        monkeypatch.setattr(pin_caps, "evaluate_full_state", original)


# ---------------------------------------------------------------------------
# SIZE predicate counter-test
# ---------------------------------------------------------------------------


class TestCounterRevert_SizePredicate:
    """Revert the size predicate → target test FAILS."""

    def test_target_test_passes_on_production_source(self, gate_env):
        """Baseline: a 1501-char pin over a 100-char baseline DENIES."""
        env = gate_env(pin_count=0)
        env["claude_md"].write_text(
            _build_claude_md(1, pin_body_chars=100), encoding="utf-8"
        )
        old_string = "x" * 100
        new_string = "x" * 1501
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": old_string,
                "new_string": new_string,
                "replace_all": False,
            },
        })
        assert result is not None
        assert "cap" in result.lower()

    def test_counter_revert_size_predicate_causes_failure(
        self, gate_env, monkeypatch
    ):
        """Revert size check → target test SHOULD fail."""
        import pin_caps

        def reverted_evaluate_full_state(pins):
            # Skip size check — only count.
            count = len(pins)
            if count > pin_caps.PIN_COUNT_CAP:
                return pin_caps.CapViolation(
                    kind="count",
                    detail=f"post-edit pin count {count} exceeds cap",
                    offending_pin_chars=None,
                    current_count=count,
                )
            return None

        monkeypatch.setattr(
            pin_caps, "evaluate_full_state", reverted_evaluate_full_state
        )

        env = gate_env(pin_count=0)
        env["claude_md"].write_text(
            _build_claude_md(1, pin_body_chars=100), encoding="utf-8"
        )
        old_string = "x" * 100
        new_string = "x" * 1501
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": old_string,
                "new_string": new_string,
                "replace_all": False,
            },
        })
        assert result is None, (
            f"size predicate counter-revert should NOT deny 1501/1500, got: {result!r}"
        )


# ---------------------------------------------------------------------------
# EMBEDDED_PIN predicate counter-test
# ---------------------------------------------------------------------------


class TestCounterRevert_EmbeddedPinPredicate:
    """Revert the embedded_pin predicate → target test FAILS."""

    def test_target_test_passes_on_production_source(self, gate_env):
        """Baseline: Edit new_string with `### ` heading DENIES."""
        env = gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "Pin0",
                "new_string": (
                    "<!-- pinned: 2026-04-20 -->\n"
                    "### Sneaky Embedded Pin\nbody"
                ),
                "replace_all": False,
            },
        })
        assert result is not None
        assert "embedded pin" in result.lower()

    def test_counter_revert_embedded_pin_causes_failure(
        self, gate_env, monkeypatch
    ):
        """Revert the embedded-pin short-circuit in compute_deny_reason.

        The embedded-pin check fires BEFORE evaluate_full_state. The fix
        in compute_deny_reason is the `if new_body and parse_pins(new_body)`
        branch. Revert by swapping `pin_caps_gate.compute_deny_reason`
        (the NAME bound in pin_caps_gate at import time — patching
        `pin_caps.compute_deny_reason` would not take effect because
        `from pin_caps import compute_deny_reason` freezes the reference).
        """
        import pin_caps
        import pin_caps_gate
        from pin_caps import evaluate_full_state

        def reverted_compute_deny_reason(pre_pins, post_pins, new_body):
            # Skip embedded-pin check.
            post_violation = evaluate_full_state(post_pins)
            if post_violation is None:
                return None
            pre_violation = evaluate_full_state(pre_pins)
            if pre_violation is None:
                return pin_caps._render_deny_reason(post_violation)
            # Conservative: allow if both bad, skip worse-than logic for
            # the revert — we just want to confirm embedded-pin is the
            # test's load-bearer, not net-worse.
            return None

        monkeypatch.setattr(
            pin_caps_gate, "compute_deny_reason", reverted_compute_deny_reason
        )

        env = gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "Pin0",
                "new_string": (
                    "<!-- pinned: 2026-04-20 -->\n"
                    "### Sneaky Embedded Pin\nbody"
                ),
                "replace_all": False,
            },
        })
        # With embedded-pin revert, an Edit that substitutes "Pin0" with
        # a new pin block causes the post-state to still be 3 pins
        # (Pin0→SneakyEmbedded, Pin1, Pin2) — the OLD PinN count remains
        # 3 because the replacement is 1-for-1. So there's no count
        # violation either. Therefore result is None → PASS on revert.
        assert result is None, (
            f"embedded-pin predicate counter-revert should NOT deny, got: {result!r}"
        )


# ---------------------------------------------------------------------------
# OVERRIDE validation counter-test (len + empty)
# ---------------------------------------------------------------------------


class TestCounterRevert_OverridePredicate:
    """Revert the override rationale validation → target tests FAIL.

    Cardinality: 2 load-bearing tests (length cap + empty rationale).
    """

    def test_target_test_length_cap_denies(self, gate_env):
        """Baseline: 121-char rationale DENIES."""
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

    def test_target_test_empty_rationale_denies(self, gate_env):
        """Baseline: empty rationale DENIES."""
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

    def test_counter_revert_override_validation_causes_failure(
        self, gate_env, monkeypatch
    ):
        """Revert `_validate_override_rationale` to accept everything.

        Both length-cap and empty-rationale tests should then NOT DENY
        on the override path. The override-validation predicate is the
        sole enforcer; reverting it proves the target tests are load-
        bearing.
        """
        import pin_caps_gate

        monkeypatch.setattr(
            pin_caps_gate,
            "_validate_override_rationale",
            lambda rationale: None,  # Accept all rationales unconditionally.
        )

        env = gate_env(pin_count=3)

        # Length-cap target test under revert → should NOT deny via override.
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
        # The override-validation short-circuit is gone; now the gate
        # proceeds to cap evaluation. The Edit-simulated post-state
        # depends on whether the new_string is applied to a matching
        # old_string. "Pin0" appears in the pin heading "### Pin0" — so
        # the replacement succeeds. The post-state still has 3 pins
        # (Pin0 replaced by the override-comment text block + ### headings
        # disrupted), so no count violation. Override was invalid but
        # validation is reverted, so no invalid-override deny. Expected:
        # result is None.
        assert result is None, (
            f"override-validation counter-revert (length) should NOT deny, got: {result!r}"
        )

        # Empty-rationale target test under revert → should NOT deny.
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "Pin0",
                "new_string": "<!-- pinned: 2026-04-20, pin-size-override:  -->",
                "replace_all": False,
            },
        })
        assert result is None, (
            f"override-validation counter-revert (empty) should NOT deny, got: {result!r}"
        )


# ---------------------------------------------------------------------------
# NET-WORSE predicate counter-test (invariant #2 — strict `>` not `>=`)
# ---------------------------------------------------------------------------


class TestCounterRevert_NetWorsePredicate:
    """Revert net-worse logic → target test (pre-malformed livelock) FAILS.

    Pre-existing 13 pins with Edit that touches only body chars (no count
    change) should ALLOW on production source (net-worse=False). If we
    revert compute_deny_reason to use absolute post-state (not net-worse),
    the same Edit DENIES (livelock).
    """

    def test_target_test_passes_on_production_source(self, gate_env):
        """Baseline: 13-pin state with body-only Edit ALLOWS."""
        env = gate_env(pin_count=13, body_chars=50)
        # Edit just body chars — no pin count change.
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "xxxxx",  # Appears in every pin's body.
                "new_string": "yyyyy",
                "replace_all": True,
            },
        })
        assert result is None, (
            f"13-pin state with body-only Edit should ALLOW (net-worse), got: {result!r}"
        )

    def test_counter_revert_net_worse_causes_failure(
        self, gate_env, monkeypatch
    ):
        """Revert compute_deny_reason to use absolute post-state.

        When the net-worse predicate is removed, a pre-existing 13-pin
        state DENIES every Edit until curator prunes — this is the
        F1 livelock the net-worse predicate was introduced to prevent.
        Patch via `pin_caps_gate.compute_deny_reason` (bound name at
        gate module import time).
        """
        import pin_caps
        import pin_caps_gate

        def reverted_compute_deny_reason(pre_pins, post_pins, new_body):
            # Absolute post-state check — no net-worse.
            if new_body and pin_caps.parse_pins(new_body):
                return pin_caps.DENY_REASON_EMBEDDED_PIN
            post_violation = pin_caps.evaluate_full_state(post_pins)
            if post_violation is None:
                return None
            return pin_caps._render_deny_reason(post_violation)

        monkeypatch.setattr(
            pin_caps_gate, "compute_deny_reason", reverted_compute_deny_reason
        )

        env = gate_env(pin_count=13, body_chars=50)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "xxxxx",
                "new_string": "yyyyy",
                "replace_all": True,
            },
        })
        # Under revert, a 13-pin body-Edit DENIES (pre-malformed livelock).
        assert result is not None, (
            "net-worse counter-revert should DENY the body-only Edit "
            "(13 pins over cap) — if this passes, the net-worse predicate "
            "test is phantom-green."
        )
        assert "Pin count cap" in result


# ---------------------------------------------------------------------------
# ASYMMETRIC FAIL-CLOSED counter-test (invariant #3 — Write baseline)
# ---------------------------------------------------------------------------


class TestCounterRevert_WriteBaselineFailClosed:
    """Revert asymmetric fail-CLOSED → target test FAILS (Write fail-opens
    instead of failing closed on missing baseline + over-cap content).
    """

    def test_target_test_passes_on_production_source(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Baseline: Write 13/12 against missing baseline DENIES."""
        claude_md = tmp_path / "CLAUDE.md"  # Not created.
        pact_context(
            team_name="test-team",
            session_id="session-closed",
            project_dir=str(tmp_path),
        )

        import staleness
        monkeypatch.setattr(
            staleness, "get_project_claude_md_path", lambda: claude_md
        )

        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(claude_md),
                "content": _build_claude_md(13),
            },
        })
        assert result is not None
        assert "Refusing Write" in result

    def test_counter_revert_fail_closed_causes_failure(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Revert the asymmetric branch in _check_tool_allowed.

        Actually, the asymmetric logic is wired via
        `_evaluate_write_as_fresh_start` — revert that to always return
        None (unconditionally allow). The Write should then pass.
        """
        import pin_caps_gate

        monkeypatch.setattr(
            pin_caps_gate,
            "_evaluate_write_as_fresh_start",
            lambda tool_input: None,  # Always allow.
        )

        claude_md = tmp_path / "CLAUDE.md"
        pact_context(
            team_name="test-team",
            session_id="session-closed-revert",
            project_dir=str(tmp_path),
        )

        import staleness
        monkeypatch.setattr(
            staleness, "get_project_claude_md_path", lambda: claude_md
        )

        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(claude_md),
                "content": _build_claude_md(13),
            },
        })
        assert result is None, (
            f"fail-CLOSED counter-revert should ALLOW (fail-open), got: {result!r}"
        )
