"""
Adversarial test sweep for pin caps enforcement.

Probes explicit attack surfaces and edge cases the happy-path boundary
matrix does not cover:

- Path-traversal attempts at the gate's _is_project_claude_md check
- Sibling-prefix collision (e.g. CLAUDE.md.bak, ~CLAUDE.md)
- Regex case-insensitivity on STALE/pinned/override markers
- Catastrophic-backtracking pathological payloads (ReDoS)
- Override rationale boundaries at 120/121 with multibyte UTF-8
- Inline <!-- STALE: --> inside body mid-line (should still detect)
- Override comment without required pinned: prefix
- Empty Pinned Context body with manifest pins after the heading's own
  next-section-terminator

Risk tier: CRITICAL. These probe CVE-adjacent surfaces.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))


class TestPinCapsAdversarial_PathResolution:
    """Gate MUST compare resolved paths — attack via relative + symlinks."""

    def test_relative_file_path_does_not_match_absolute_claude_md(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Relative "CLAUDE.md" must NOT resolve to project CLAUDE.md
        unless CWD happens to match. Gate uses Path.resolve() — this test
        guards against accidental prefix-match variants."""
        from pin_staleness_gate import (
            _check_tool_allowed,
            PIN_STALENESS_MARKER_NAME,
        )
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project\n\n## Pinned Context\n\n", encoding="utf-8")
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / PIN_STALENESS_MARKER_NAME).touch()
        pact_context()
        import shared.pact_context as ctx_module
        monkeypatch.setattr(ctx_module, "get_session_dir", lambda: str(session_dir))
        import staleness
        monkeypatch.setattr(staleness, "get_project_claude_md_path", lambda: claude_md)
        # CWD is worktree root; "CLAUDE.md" there is NOT the tmp path.
        result = _check_tool_allowed({
            "tool_name": "Write",
            "tool_input": {"file_path": "CLAUDE.md", "content": "x"},
        })
        assert result is None

    def test_sibling_prefix_does_not_match(
        self, tmp_path, monkeypatch, pact_context
    ):
        """CLAUDE.md.bak sits next to CLAUDE.md but must not match."""
        from pin_staleness_gate import (
            _check_tool_allowed,
            PIN_STALENESS_MARKER_NAME,
        )
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# x", encoding="utf-8")
        sibling = tmp_path / "CLAUDE.md.bak"
        sibling.write_text("backup", encoding="utf-8")
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / PIN_STALENESS_MARKER_NAME).touch()
        pact_context()
        import shared.pact_context as ctx_module
        monkeypatch.setattr(ctx_module, "get_session_dir", lambda: str(session_dir))
        import staleness
        monkeypatch.setattr(staleness, "get_project_claude_md_path", lambda: claude_md)
        result = _check_tool_allowed({
            "tool_name": "Write",
            "tool_input": {"file_path": str(sibling), "content": "x"},
        })
        assert result is None

    def test_symlink_to_claude_md_resolves_to_same_match(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Symlink pointing at CLAUDE.md resolves to the same path — ADD-shape
        edit MUST still trigger deny (proves symlink resolution works).
        """
        from pin_staleness_gate import (
            _check_tool_allowed,
            PIN_STALENESS_MARKER_NAME,
        )
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project\n", encoding="utf-8")
        symlink = tmp_path / "symlinked.md"
        try:
            symlink.symlink_to(claude_md)
        except OSError:
            pytest.skip("Symlink not supported on this platform")
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        (session_dir / PIN_STALENESS_MARKER_NAME).touch()
        pact_context()
        import shared.pact_context as ctx_module
        monkeypatch.setattr(ctx_module, "get_session_dir", lambda: str(session_dir))
        import staleness
        monkeypatch.setattr(staleness, "get_project_claude_md_path", lambda: claude_md)
        # ADD-shape Write: new content has 1 pin comment; current file has 0.
        result = _check_tool_allowed({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(symlink),
                "content": "# Project\n<!-- pinned: 2026-04-20 -->\n### New\nbody\n",
            },
        })
        # Symlink resolves to same target → ADD-shape Write → must deny.
        assert result is not None
        assert "stale pins" in result


class TestPinCapsAdversarial_RegexCaseSensitivity:
    """STALE marker + pinned comment are IGNORECASE — lowercase variants match."""

    def test_lowercase_stale_marker_detected(self):
        from pin_caps import parse_pins
        content = (
            "### Pin\n<!-- stale: last relevant 2026-01-01 -->\nbody\n"
        )
        pins = parse_pins(content)
        assert pins[0].is_stale is True

    def test_mixed_case_pinned_comment_detected(self):
        from pin_caps import parse_pins
        content = (
            "<!-- Pinned: 2026-04-20 -->\n### Pin\nbody\n"
        )
        pins = parse_pins(content)
        assert pins[0].date_comment is not None

    def test_uppercase_override_keyword_detected(self):
        from pin_caps import parse_pins
        content = (
            "<!-- PINNED: 2026-04-20, PIN-SIZE-OVERRIDE: reason -->\n"
            "### Pin\nbody\n"
        )
        pins = parse_pins(content)
        assert pins[0].override_rationale == "reason"


class TestPinCapsAdversarial_RationaleUtf8Boundary:
    """OVERRIDE_RATIONALE_MAX is character-count; multibyte UTF-8 must not overflow."""

    def test_unicode_rationale_at_limit_accepted(self):
        from pin_caps import parse_pins
        # 120 Japanese chars = 120 Python code points but 360 UTF-8 bytes.
        # Python len() counts code points → accepted.
        rationale = "あ" * 120
        content = (
            f"<!-- pinned: 2026-04-20, pin-size-override: {rationale} -->\n"
            "### Entry\nBody.\n"
        )
        pins = parse_pins(content)
        assert pins[0].override_rationale == rationale

    def test_unicode_rationale_over_limit_rejected(self):
        from pin_caps import parse_pins
        rationale = "あ" * 121
        content = (
            f"<!-- pinned: 2026-04-20, pin-size-override: {rationale} -->\n"
            "### Entry\nBody.\n"
        )
        pins = parse_pins(content)
        assert pins[0].override_rationale is None


class TestPinCapsAdversarial_StalenessInline:
    """STALE marker embedded inline mid-body still detected."""

    def test_stale_marker_mid_body_detected(self):
        from pin_caps import parse_pins
        content = (
            "### Pin\n"
            "Some text before\n"
            "<!-- STALE: Last relevant 2026-01-01 -->\n"
            "Some text after\n"
        )
        pins = parse_pins(content)
        assert pins[0].is_stale is True

    def test_stale_marker_on_same_line_as_body_detected(self):
        from pin_caps import parse_pins
        content = (
            "### Pin\n"
            "body <!-- STALE: Last relevant 2026-01-01 --> more body\n"
        )
        pins = parse_pins(content)
        assert pins[0].is_stale is True


class TestPinCapsAdversarial_OverrideWithoutPinnedPrefix:
    """A bare override comment (no pinned: prefix) MUST not grant override."""

    def test_bare_override_comment_rejected(self):
        from pin_caps import parse_pins
        # No "pinned:" prefix — regex MUST NOT match.
        content = (
            "<!-- pin-size-override: looks valid -->\n"
            "### Entry\nbody\n"
        )
        pins = parse_pins(content)
        assert pins[0].override_rationale is None
        assert pins[0].date_comment is None


class TestPinCapsAdversarial_RegexSafety:
    """ReDoS defense — pathological inputs must terminate quickly."""

    def test_long_override_text_terminates(self):
        """Very long candidate lines must not cause catastrophic backtracking."""
        from pin_caps import parse_pins
        # 10k chars of non-'-->' — fullmatch must fail fast.
        junk = "x" * 10000
        content = (
            f"<!-- pinned: 2026-04-20, pin-size-override: {junk} "
            f"trailing --> but no close -->\n"
            "### Entry\nbody\n"
        )
        import time
        t0 = time.monotonic()
        pins = parse_pins(content)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"parse_pins took {elapsed}s on 10k chars"
        # Rationale should be rejected (either too long or not captured).
        assert pins[0].override_rationale is None

    def test_many_pins_terminate_quickly(self):
        from pin_caps import parse_pins
        # 1000 pins → parser must be linear.
        content = "\n".join(f"### Pin {i}\nbody\n" for i in range(1000))
        import time
        t0 = time.monotonic()
        pins = parse_pins(content)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"parse_pins took {elapsed}s on 1000 pins"
        assert len(pins) == 1000


class TestPinCapsAdversarial_EdgeCaseParsing:
    """Parsing edge cases the happy path does not cover."""

    def test_heading_at_very_end_without_trailing_newline(self):
        from pin_caps import parse_pins
        content = "### Entry"
        pins = parse_pins(content)
        assert len(pins) == 1
        assert pins[0].heading == "### Entry"
        assert pins[0].body == ""

    def test_multiple_blank_lines_before_heading(self):
        from pin_caps import parse_pins
        # Multiple blank lines between pinned comment and heading
        content = (
            "<!-- pinned: 2026-04-20 -->\n"
            "\n\n\n"
            "### Entry\nbody\n"
        )
        pins = parse_pins(content)
        # Walker skips blank lines → still detects date_comment
        assert pins[0].date_comment == "<!-- pinned: 2026-04-20 -->"

    def test_content_between_date_comment_and_heading_breaks_attachment(self):
        from pin_caps import parse_pins
        # Prose line intervenes → walker sees prose as immediate preceding
        # line and does NOT attach date_comment.
        content = (
            "<!-- pinned: 2026-04-20 -->\n"
            "some random prose\n"
            "### Entry\nbody\n"
        )
        pins = parse_pins(content)
        assert pins[0].date_comment is None

    def test_four_hash_heading_not_parsed_as_pin(self):
        """Only `### ` at start-of-line is a pin — `#### ` is not."""
        from pin_caps import parse_pins
        content = "#### Not a pin\nbody\n"
        pins = parse_pins(content)
        # Four-hash heading: _PIN_HEADING_RE is "^### " — matches inside
        # "#### " because "### " is a prefix of "#### " at position 1.
        # Actually `^### ` won't match at position 1 (line start only).
        # But regex `^### ` with MULTILINE DOES match `### ` starting at
        # any line start, regardless of what follows. So `#### ` starts
        # with `#### ` not `### `. First four chars are `####`; `### ` at
        # index 0 requires chars 0-3 to be `###[space]`. Here chars 0-3
        # are `####`. So NO match.
        assert pins == []

    def test_two_hash_heading_not_parsed_as_pin(self):
        from pin_caps import parse_pins
        content = "## Heading\nbody\n"
        pins = parse_pins(content)
        assert pins == []

    def test_only_whitespace_before_heading(self):
        from pin_caps import parse_pins
        content = "     \n### Pin\nbody\n"
        pins = parse_pins(content)
        assert len(pins) == 1
        assert pins[0].date_comment is None


class TestPinCapsAdversarial_CountCapWithOversizedPins:
    """Count-cap applies even when existing pins carry overrides."""

    def test_count_cap_respects_existing_override_pins(self):
        from pin_caps import Pin, check_add_allowed
        # 12 existing pins — some with override rationale. Count is 12.
        pins = [
            Pin(heading=f"### P{i}", body="x" * 2000,
                body_chars=2000, date_comment=None,
                override_rationale="reason" if i % 2 == 0 else None,
                is_stale=False)
            for i in range(12)
        ]
        result = check_add_allowed(pins, "new", new_has_override=False)
        assert result is not None
        assert result.kind == "count"

    def test_count_cap_respects_stale_pins(self):
        """Stale pins still occupy a slot — count cap applies."""
        from pin_caps import Pin, check_add_allowed
        pins = [
            Pin(heading=f"### P{i}", body="x", body_chars=1,
                date_comment=None, override_rationale=None,
                is_stale=True)
            for i in range(12)
        ]
        result = check_add_allowed(pins, "new", False)
        assert result is not None
        assert result.kind == "count"


class TestPinCapsAdversarial_SessionInitReentry:
    """check_pin_slot_status is called every SessionStart — MUST be side-effect-free."""

    def test_repeated_invocation_does_not_mutate_claude_md(
        self, tmp_path, monkeypatch
    ):
        from session_init import check_pin_slot_status
        claude_md = tmp_path / "CLAUDE.md"
        original_content = (
            "# Project\n\n## Pinned Context\n\n"
            "<!-- pinned: 2026-04-20 -->\n### Pin\nbody\n"
        )
        claude_md.write_text(original_content, encoding="utf-8")
        monkeypatch.setattr(
            "session_init._get_project_claude_md_path", lambda: claude_md
        )
        for _ in range(5):
            check_pin_slot_status()
        assert claude_md.read_text(encoding="utf-8") == original_content
