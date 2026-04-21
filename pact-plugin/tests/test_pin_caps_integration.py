"""
Integration tests for pin caps — cross-module boundary verification.

Covers:
- staleness.check_pinned_block_signal: end-to-end CLAUDE.md → CapViolation
- session_init.check_pin_slot_status: Tier-0 additionalContext line
- session_init.check_pin_stale_block_directive: marker lifecycle + directive
- pin-memory.md prose contract: two-step AskUserQuestion grammar
- Property-test: parse_pins stale detection agrees with
  detect_stale_entries walker on shared fixtures
- Boundary-agreement: live CLAUDE.md:68 override line round-trips

Risk tier: CRITICAL.
"""

import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

from helpers import make_claude_md_with_pins, make_pin_entry  # noqa: E402


def _build_pinned_claude_md(n_pins=0, pin_body_chars=100, stale_indices=()):
    """Thin wrapper around helpers.py factories preserving legacy signature."""
    entries = [
        make_pin_entry(
            title=f"Pin {i}",
            body_chars=pin_body_chars,
            stale_date="2026-01-01" if i in stale_indices else None,
        )
        for i in range(n_pins)
    ]
    return make_claude_md_with_pins(entries)


class TestCheckPinnedBlockSignal_EndToEnd:
    """staleness.check_pinned_block_signal on real CLAUDE.md content."""

    def test_no_stale_pins_returns_none(self, tmp_path):
        from staleness import check_pinned_block_signal
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(_build_pinned_claude_md(3), encoding="utf-8")
        assert check_pinned_block_signal(claude_md) is None

    def test_one_stale_pin_returns_none_below_threshold(self, tmp_path):
        from staleness import check_pinned_block_signal
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(3, stale_indices={0}), encoding="utf-8"
        )
        assert check_pinned_block_signal(claude_md) is None

    def test_two_stale_pins_returns_violation(self, tmp_path):
        from staleness import check_pinned_block_signal
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(3, stale_indices={0, 1}), encoding="utf-8"
        )
        result = check_pinned_block_signal(claude_md)
        assert result is not None
        assert result.kind == "stale"

    def test_three_stale_pins_returns_violation(self, tmp_path):
        from staleness import check_pinned_block_signal
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(3, stale_indices={0, 1, 2}),
            encoding="utf-8",
        )
        result = check_pinned_block_signal(claude_md)
        assert result is not None

    def test_missing_claude_md_fails_open(self, tmp_path):
        from staleness import check_pinned_block_signal
        nonexistent = tmp_path / "does-not-exist.md"
        # read_text raises OSError → fail-open (None)
        assert check_pinned_block_signal(nonexistent) is None

    def test_no_pinned_section_fails_open(self, tmp_path):
        from staleness import check_pinned_block_signal
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n\n## Working Memory\n\nNo pins.\n", encoding="utf-8"
        )
        assert check_pinned_block_signal(claude_md) is None

    def test_parse_exception_fails_open(self, tmp_path, monkeypatch):
        """parse_pins raising does NOT propagate — block signal returns None."""
        from staleness import check_pinned_block_signal
        import staleness as staleness_mod
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(2, stale_indices={0, 1}), encoding="utf-8"
        )

        def _boom(_content):
            raise RuntimeError("parse blew up")

        monkeypatch.setattr(staleness_mod, "parse_pins", _boom)
        assert check_pinned_block_signal(claude_md) is None


class TestCheckPinSlotStatus_SessionInit:
    """session_init.check_pin_slot_status emits Tier-0 additionalContext line."""

    def test_returns_status_string_with_pins(self, tmp_path, monkeypatch):
        from session_init import check_pin_slot_status
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(3, pin_body_chars=200), encoding="utf-8"
        )
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )
        result = check_pin_slot_status()
        assert result is not None
        assert "3/12" in result

    def test_returns_zero_status_when_no_pinned_section(
        self, tmp_path, monkeypatch
    ):
        """Missing pinned section → surface 0-used so orchestrator sees headroom."""
        from session_init import check_pin_slot_status
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "# Project\n\n## Working Memory\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )
        result = check_pin_slot_status()
        assert result == "Pin slots: 0/12 used"

    def test_returns_none_when_no_claude_md(self, monkeypatch):
        from session_init import check_pin_slot_status
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: None
        )
        assert check_pin_slot_status() is None

    def test_returns_none_on_read_error(self, tmp_path, monkeypatch):
        from session_init import check_pin_slot_status
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(2), encoding="utf-8"
        )
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )

        def _raise(*a, **k):
            raise IOError("simulated")

        monkeypatch.setattr(Path, "read_text", _raise)
        assert check_pin_slot_status() is None

    def test_idempotent_on_repeated_invocation(self, tmp_path, monkeypatch):
        """P0: SessionStart fires repeatedly; output MUST NOT drift."""
        from session_init import check_pin_slot_status
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(5, pin_body_chars=300), encoding="utf-8"
        )
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )
        results = [check_pin_slot_status() for _ in range(3)]
        assert results[0] == results[1] == results[2]

    def test_returns_none_on_parse_exception(self, tmp_path, monkeypatch):
        from session_init import check_pin_slot_status
        import session_init as si
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(2), encoding="utf-8"
        )
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )

        def _boom(_pinned):
            raise RuntimeError("nope")

        monkeypatch.setattr(si, "parse_pins", _boom)
        assert check_pin_slot_status() is None


class TestCheckPinStaleBlockDirective_MarkerLifecycle:
    """session_init.check_pin_stale_block_directive — marker arm/clear cycle.

    The directive returns a hard-rule MUST string on positive detection
    AND writes a session-scoped marker so pin_staleness_gate (PreToolUse)
    can block later Edit/Write. When detection goes negative, the marker
    MUST be cleared so the gate does not persist stale arming.
    """

    def test_positive_detection_emits_directive_and_arms_marker(
        self, tmp_path, monkeypatch, pact_context
    ):
        from session_init import check_pin_stale_block_directive
        from pin_staleness_gate import PIN_STALENESS_MARKER_NAME
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(3, stale_indices={0, 1}), encoding="utf-8"
        )
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )
        pact_context()
        import shared.pact_context as ctx_module
        monkeypatch.setattr(
            ctx_module, "get_session_dir", lambda: str(session_dir)
        )

        result = check_pin_stale_block_directive()
        assert result is not None
        assert "MUST" in result
        assert "/PACT:pin-memory" in result
        assert (session_dir / PIN_STALENESS_MARKER_NAME).exists()

    def test_negative_detection_clears_marker(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Resolved state MUST unwind the marker so the gate disarms."""
        from session_init import check_pin_stale_block_directive
        from pin_staleness_gate import PIN_STALENESS_MARKER_NAME
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(3, stale_indices=()), encoding="utf-8"
        )
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        # Pre-seed marker — simulating a prior armed state.
        (session_dir / PIN_STALENESS_MARKER_NAME).touch()
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )
        pact_context()
        import shared.pact_context as ctx_module
        monkeypatch.setattr(
            ctx_module, "get_session_dir", lambda: str(session_dir)
        )

        result = check_pin_stale_block_directive()
        assert result is None
        assert not (session_dir / PIN_STALENESS_MARKER_NAME).exists()

    def test_positive_detection_idempotent_marker_arming(
        self, tmp_path, monkeypatch, pact_context
    ):
        """P0: double-invocation does not error; marker stays single file."""
        from session_init import check_pin_stale_block_directive
        from pin_staleness_gate import PIN_STALENESS_MARKER_NAME
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(3, stale_indices={0, 1}), encoding="utf-8"
        )
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )
        pact_context()
        import shared.pact_context as ctx_module
        monkeypatch.setattr(
            ctx_module, "get_session_dir", lambda: str(session_dir)
        )

        r1 = check_pin_stale_block_directive()
        r2 = check_pin_stale_block_directive()
        assert r1 == r2
        assert (session_dir / PIN_STALENESS_MARKER_NAME).exists()

    def test_no_session_dir_still_returns_directive_on_detection(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Marker management is best-effort; directive must still fire."""
        from session_init import check_pin_stale_block_directive
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            _build_pinned_claude_md(3, stale_indices={0, 1}), encoding="utf-8"
        )
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )
        pact_context()
        import shared.pact_context as ctx_module
        monkeypatch.setattr(ctx_module, "get_session_dir", lambda: None)

        result = check_pin_stale_block_directive()
        assert result is not None
        assert "MUST" in result


class TestPinMemoryCommand_Grammar:
    """pin-memory.md contract assertions (cycle-8 demoted scope).

    Post-cycle-8, pin-memory.md is a thin pin-add guide. Cap enforcement
    lives in hooks/pin_caps_gate.py; interactive eviction lives in
    /PACT:prune-memory. The bulky two-step AskUserQuestion eviction flow
    and the shell-scaffolding heredoc+nonce surface are GONE. Tests here
    cover the residual informational surface: caps mentioned, refusal
    flow enumerated, hook-as-enforcer called out, cross-reference to
    prune-memory present.

    DELETED in cycle-8 commit 6 alongside the pin-memory.md rewrite:
    - test_documents_check_cli_invocation (CLI no longer invoked)
    - test_documents_step_a_three_options / test_documents_step_b_pagination_cap
      (eviction moved to /PACT:prune-memory)
    - test_documents_size_refusal_three_options (size refusal is now a
      hook deny-reason + plain-text remediation, not an AskUserQuestion)
    - test_heredoc_opener_is_always_quoted_in_code_fences (no heredoc)
    - test_heredoc_nonce_uses_python3_secrets_not_openssl (no nonce)
    """

    @pytest.fixture(scope="class")
    def pin_memory_content(self):
        path = (
            Path(__file__).parent.parent / "commands" / "pin-memory.md"
        )
        return path.read_text(encoding="utf-8")

    def test_documents_caps(self, pin_memory_content):
        """Cap numbers remain informational for curator awareness."""
        assert "12 pins maximum" in pin_memory_content
        assert "1500 characters" in pin_memory_content
        # Cycle-8: the hook is the authoritative enforcer. Command text
        # MUST direct curators away from manual bypass attempts.
        assert "MUST NOT bypass" in pin_memory_content

    def test_documents_hook_as_enforcer(self, pin_memory_content):
        """pin-memory.md must name the hook so curators know where denies
        come from (they appear as PreToolUse permissionDecision: deny,
        not as a CLI exit code)."""
        assert "pin_caps_gate" in pin_memory_content

    def test_documents_refusal_flow(self, pin_memory_content):
        """Hook deny-reasons are enumerated so curators see the exact
        actionable next step without leaving the command text."""
        assert "Pin count cap reached" in pin_memory_content
        assert "New pin body is" in pin_memory_content
        assert "Embedded pin structure" in pin_memory_content
        assert "Override rationale malformed" in pin_memory_content

    def test_documents_rationale_120_char_limit(self, pin_memory_content):
        """The 120-char rationale cap remains informational so curators
        self-limit before the hook denies."""
        assert "120 chars" in pin_memory_content

    def test_documents_override_grammar_example(self, pin_memory_content):
        """Exact override comment form (live CLAUDE.md:68) is preserved
        as a verbatim example. parse_pins round-trips this exact line;
        drift would break the live CLAUDE.md round-trip (see
        TestLiveClaudeMdOverrideLine_RoundTrip below)."""
        assert (
            "pin-size-override: verbatim dispatch form is load-bearing "
            "for LLM readers"
        ) in pin_memory_content

    def test_references_prune_memory_for_eviction(self, pin_memory_content):
        """Cap-count refusal must direct the curator to /PACT:prune-memory
        for interactive eviction — the command that owns that flow
        post-demotion."""
        assert "/PACT:prune-memory" in pin_memory_content

    def test_no_heredoc_scaffolding(self, pin_memory_content):
        """Regression guard: the shell-scaffolding heredoc+nonce surface
        MUST NOT reappear in pin-memory.md. Cycle-8 eliminated it by
        construction — any re-introduction (perhaps by a well-meaning
        future commit adding a "before add" validation step) would
        reopen the shell-injection surface cycle-7 hardened against.
        """
        # No heredoc markers.
        assert "<<'" not in pin_memory_content
        assert '<<"' not in pin_memory_content
        # No bash fences at all (pin-memory.md became a plain-text guide).
        assert "```bash" not in pin_memory_content
        # No CLI invocation of check_pin_caps — that's now hook-only.
        # (The CLI still exists as /PACT:prune-memory's backing, but
        # pin-memory.md does not invoke it.)
        assert "check_pin_caps.py" not in pin_memory_content
        # No retired flags.
        for flag in [
            "--new-body",
            "--body-from-stdin",
            "--has-override",
            "--override-rationale",
        ]:
            assert flag not in pin_memory_content, (
                f"Retired cycle-7 flag {flag} reappeared in pin-memory.md; "
                "cap enforcement is now hook-authoritative and the CLI is "
                "advisory-only. Remove the reference."
            )


class TestParsePinsVsDetectStaleEntries_Agreement:
    """Property-test: parse_pins.is_stale agrees with detect_stale_entries
    on shared fixtures.

    Mechanical rule: detect_stale_entries uses a regex walker to find pins
    containing date-matched staleness; parse_pins uses STALE marker
    presence. For pins ALREADY carrying `<!-- STALE: Last relevant ... -->`
    markers, both parsers must agree the entry is stale — otherwise the
    twin-parsing architecture diverges (audit sub-YELLOW).
    """

    @pytest.mark.parametrize("n_pins,stale_indices", [
        (0, set()),
        (1, set()),
        (1, {0}),
        (3, set()),
        (3, {0}),
        (3, {0, 1}),
        (3, {0, 1, 2}),
        (5, {2}),
    ])
    def test_stale_marker_detection_agrees(self, n_pins, stale_indices):
        from pin_caps import parse_pins
        from helpers import make_pin_entry, make_pinned_section
        # Build content with explicit STALE markers — detect_stale_entries
        # skips already-marked entries, so our axis of comparison is
        # "pin_caps.is_stale == True iff STALE marker present".
        entries = [
            make_pin_entry(
                title=f"Pin {i}",
                body_chars=4,
                stale_date="2026-01-01" if i in stale_indices else None,
            )
            for i in range(n_pins)
        ]
        content = make_pinned_section(entries) if entries else ""
        pins = parse_pins(content)
        actual_stale = {i for i, p in enumerate(pins) if p.is_stale}
        assert actual_stale == stale_indices, (
            f"parse_pins stale set {actual_stale} disagrees with "
            f"expected {stale_indices}"
        )
        # Cross-parser agreement: detect_stale_entries skips entries already
        # carrying a STALE marker, so on marked fixtures it MUST return the
        # empty list — both parsers agree "marked entries are finalized
        # stale, no further flagging needed."
        from staleness import detect_stale_entries
        assert len(detect_stale_entries(content)) == 0, (
            "detect_stale_entries flagged marker-carrying entries; it "
            "should skip them to avoid double-marking"
        )


class TestLiveClaudeMdOverrideLine_RoundTrip:
    """The override line on live CLAUDE.md:68 must round-trip through
    parse_pins unchanged. Regression guard against regex drift."""

    LIVE_LINE = (
        "<!-- pinned: 2026-04-11, pin-size-override: "
        "verbatim dispatch form is load-bearing for LLM readers -->"
    )
    LIVE_RATIONALE = "verbatim dispatch form is load-bearing for LLM readers"

    def test_round_trip_preserves_rationale(self):
        from pin_caps import parse_pins
        content = f"{self.LIVE_LINE}\n### Canonical Task Form\nbody\n"
        pins = parse_pins(content)
        assert len(pins) == 1
        assert pins[0].override_rationale == self.LIVE_RATIONALE
        assert pins[0].date_comment == self.LIVE_LINE

    def test_round_trip_inside_multi_pin_context(self):
        from pin_caps import parse_pins
        content = (
            "<!-- pinned: 2026-04-01 -->\n"
            "### Other Pin\n"
            "body a\n\n"
            f"{self.LIVE_LINE}\n"
            "### Canonical Task Form\n"
            "body b\n"
        )
        pins = parse_pins(content)
        assert len(pins) == 2
        assert pins[0].override_rationale is None
        assert pins[1].override_rationale == self.LIVE_RATIONALE
