"""
Counter-test-by-revert for pin_caps_gate.py predicates.

For each refusal the gate makes (count growth, size, invalid override, and
the unreadable-baseline Write) and for the growth rule that keeps an
over-cap file editable, we temporarily REVERT the enforcement via
monkeypatch, run a target test, assert it would FAIL against the reverted
source, then restore. This proves each predicate-level test is load-bearing
— not exercising a proxy.

Per the staged-peer-fix phantom-green institutional memory, we do NOT
revert via git-checkout on the shared worktree. Reversions are in-
memory monkeypatches scoped to single tests. This avoids corrupting
the worktree for parallel readers and makes each counter-test trivially
reversible.

Cardinality pins (per-predicate failure count on revert):
  count       : 1 load-bearing target test → 1 fail on revert
  size        : 1 load-bearing target test → 1 fail on revert
  body heading: 1 load-bearing target test → 1 fail on revert
  override    : 2 load-bearing target tests (len + empty) → 2 fail on revert

If any counter-test produces 0 fails, the target test is phantom-green
and must be rewritten.
"""


import pytest

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
    # #878: the gate now keys lead-detection on is_lead (the harness-set
    # agent_type), not the old empty-resolve_agent_name heuristic. Default to a
    # LEAD frame (the unmarked case these DENY tests assume) unless the caller
    # supplies an explicit agent_type.
    from pin_caps_gate import _check_tool_allowed
    if "agent_type" not in input_data:
        input_data = {**input_data, "agent_type": "pact-orchestrator"}
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


def _no_growth(*args, **kwargs):
    """A pin-growth rule that never counts growth."""
    return 0


class TestCounterRevert_CountPredicate:
    """Revert the count predicate: growth is never counted.

    Target test: Write of 13 pins against 3-pin baseline should DENY.
    Revert: monkeypatch `pin_growth.pin_growth` to report no growth.
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
        """Revert growth counting → 13/12 is no longer refused."""
        from shared import pin_growth

        monkeypatch.setattr(pin_growth, "pin_growth", _no_growth)
        env = gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": _build_claude_md(13),
            },
        })
        assert result is None, (
            f"count predicate counter-revert should NOT deny 13/12, got: {result!r}. "
            "If this test fails, the count cap has a second enforcement site."
        )


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
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "x" * 100,
                "new_string": "x" * 1501,
                "replace_all": False,
            },
        })
        assert result is not None
        assert "cap" in result.lower()

    def test_counter_revert_size_predicate_causes_failure(
        self, gate_env, monkeypatch
    ):
        """Revert the size cap (no body is ever over it) → no refusal."""
        from shared import pin_growth

        # Patch the pin_caps the decision calls, not `import pin_caps`: a
        # script loaded by file path (check_pin_caps) puts a fresh pin_caps in
        # sys.modules, while shared.pin_growth keeps the one it bound at import.
        monkeypatch.setitem(
            pin_growth.compute_deny_reason.__globals__, "PIN_SIZE_CAP", 10 ** 9
        )
        env = gate_env(pin_count=0)
        env["claude_md"].write_text(
            _build_claude_md(1, pin_body_chars=100), encoding="utf-8"
        )
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "x" * 100,
                "new_string": "x" * 1501,
                "replace_all": False,
            },
        })
        assert result is None, (
            f"size predicate counter-revert should NOT deny 1501/1500, got: {result!r}"
        )


# ---------------------------------------------------------------------------
# A heading in a pin body is a pin (no embedded-pin check)
# ---------------------------------------------------------------------------


class TestCounterRevert_BodyHeadingCountedAsPin:
    """A prose `### ` line smuggled into a pin body is a pin, refused by the
    count axis at the cap. There is no separate embedded-pin check."""

    def test_target_test_passes_on_production_source(self, gate_env):
        """Baseline: at 12 pins, a body gaining `### ` DENIES on count."""
        env = gate_env(pin_count=12)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "### Pin3\nxxxx",
                "new_string": "### Pin3\nxxxx\n### Sneaky Embedded Pin\nbody",
                "replace_all": False,
            },
        })
        assert result is not None
        assert "Pin count cap" in result

    def test_counter_revert_body_heading_causes_failure(self, gate_env, monkeypatch):
        """With growth uncounted, the smuggled heading is no longer refused:
        the count axis is its only enforcement."""
        from shared import pin_growth

        monkeypatch.setattr(pin_growth, "pin_growth", _no_growth)
        env = gate_env(pin_count=12)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "### Pin3\nxxxx",
                "new_string": "### Pin3\nxxxx\n### Sneaky Embedded Pin\nbody",
                "replace_all": False,
            },
        })
        assert result is None, f"body-heading counter-revert should NOT deny, got: {result!r}"

    def test_a_dated_pin_swapped_in_under_the_cap_is_allowed(self, gate_env):
        """The removed embedded-pin check refused this: Pin0's title replaced
        by a dated pin block, 3 pins plus one, under the cap → ALLOW."""
        env = gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "Pin0",
                "new_string": "<!-- pinned: 2026-04-20 -->\n### Sneaky Embedded Pin\nbody",
                "replace_all": False,
            },
        })
        assert result is None, result


# ---------------------------------------------------------------------------
# OVERRIDE validation counter-test (len + empty)
# ---------------------------------------------------------------------------


def _override_edit(env, rationale):
    return {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(env["claude_md"]),
            "old_string": "<!-- pinned: 2026-04-20 -->\n### Pin0",
            "new_string": f"<!-- pinned: 2026-04-20, pin-size-override: {rationale} -->\n### Pin0",
            "replace_all": False,
        },
    }


class TestCounterRevert_OverridePredicate:
    """Revert the override rationale validation → target tests FAIL.

    Cardinality: 2 load-bearing tests (length cap + empty rationale).
    """

    def test_target_test_length_cap_denies(self, gate_env):
        """Baseline: a 121-char rationale on a changed pin DENIES."""
        env = gate_env(pin_count=3)
        result = _call_gate(_override_edit(env, "x" * 121))
        assert result is not None
        assert "override" in result.lower()

    def test_target_test_empty_rationale_denies(self, gate_env):
        """Baseline: a blank rationale on a changed pin DENIES."""
        env = gate_env(pin_count=3)
        result = _call_gate(_override_edit(env, "  "))
        assert result is not None
        assert "empty" in result.lower() or "override" in result.lower()

    def test_counter_revert_override_validation_causes_failure(
        self, gate_env, monkeypatch
    ):
        """Revert `_validate_override_rationale` to accept everything: both
        target Edits are then allowed (3 small pins, nothing else to refuse)."""
        import pin_caps_gate

        monkeypatch.setattr(pin_caps_gate, "_validate_override_rationale", lambda rationale: None)
        env = gate_env(pin_count=3)
        result = _call_gate(_override_edit(env, "x" * 121))
        assert result is None, (
            f"override-validation counter-revert (length) should NOT deny, got: {result!r}"
        )
        result = _call_gate(_override_edit(env, "  "))
        assert result is None, (
            f"override-validation counter-revert (empty) should NOT deny, got: {result!r}"
        )


# ---------------------------------------------------------------------------
# GROWTH predicate counter-test (an over-cap file stays editable)
# ---------------------------------------------------------------------------


class TestCounterRevert_GrowthPredicate:
    """Revert growth to the absolute post count → target test (pre-malformed
    livelock) FAILS.

    Pre-existing 13 pins with an Edit that touches only body chars should
    ALLOW (no pin added). If growth is replaced by the post count, the same
    Edit DENIES (livelock).
    """

    def test_target_test_passes_on_production_source(self, gate_env):
        """Baseline: 13-pin state with body-only Edit ALLOWS."""
        env = gate_env(pin_count=13, body_chars=50)
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
            f"13-pin state with body-only Edit should ALLOW (no growth), got: {result!r}"
        )

    def test_counter_revert_growth_causes_failure(self, gate_env, monkeypatch):
        """Growth replaced by every pin after the change → the body-only Edit
        on a 13-pin file DENIES (the livelock growth prevents)."""
        from shared import pin_growth

        monkeypatch.setattr(pin_growth, "pin_growth", lambda before, after, **kwargs: 13)
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
        assert result is not None, (
            "growth counter-revert should DENY the body-only Edit (13 pins over "
            "cap) — if this passes, the growth test is phantom-green."
        )
        assert "Pin count cap" in result


# ---------------------------------------------------------------------------
# UNREADABLE-BASELINE WRITE counter-test (the one refusing failure path)
# ---------------------------------------------------------------------------


class TestCounterRevert_WriteBaselineFailClosed:
    """Revert the unreadable-baseline Write refusal → target test FAILS
    (the Write would fail open on a missing baseline + over-cap content)."""

    def test_target_test_passes_on_production_source(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Baseline: Write 13/12 against missing baseline DENIES."""
        claude_md = tmp_path / "CLAUDE.md"  # Not created.
        pact_context(team_name="test-team", session_id="session-closed", project_dir=str(tmp_path))

        import staleness
        monkeypatch.setattr(staleness, "get_project_claude_md_path", lambda: claude_md)

        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {"file_path": str(claude_md), "content": _build_claude_md(13)},
        })
        assert result is not None
        assert "Pin count cap" in result

    def test_counter_revert_fail_closed_causes_failure(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Revert `_unreadable_decision` to allow unconditionally → the Write passes."""
        import pin_caps_gate

        monkeypatch.setattr(pin_caps_gate, "_unreadable_decision", lambda *args: None)
        claude_md = tmp_path / "CLAUDE.md"
        pact_context(team_name="test-team", session_id="session-closed-revert", project_dir=str(tmp_path))

        import staleness
        monkeypatch.setattr(staleness, "get_project_claude_md_path", lambda: claude_md)

        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {"file_path": str(claude_md), "content": _build_claude_md(13)},
        })
        assert result is None, (
            f"fail-CLOSED counter-revert should ALLOW (fail-open), got: {result!r}"
        )
