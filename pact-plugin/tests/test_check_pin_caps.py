"""
Tests for scripts/check_pin_caps.py — CLI JSON-contract enforcement,
fail-open behavior, and exit-code semantics.

Risk tier: CRITICAL (enforcement CLI; /PACT:pin-memory depends on JSON
contract).
"""

import io
import json
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

from helpers import make_claude_md_with_pins, make_pin_entry  # noqa: E402


@pytest.fixture
def patched_claude_md(tmp_path, monkeypatch):
    """Write a CLAUDE.md and patch get_project_claude_md_path resolution."""
    def _write(content):
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(content, encoding="utf-8")
        # check_pin_caps.py binds the symbol at import — patch there.
        # Only the consumer-side patch is load-bearing; prior staleness-side
        # patch was decorative and risked dead-patch status if staleness.py
        # refactors its resolution callsite.
        import check_pin_caps
        monkeypatch.setattr(
            check_pin_caps, "get_project_claude_md_path", lambda: claude_md
        )
        return claude_md
    return _write


def _run_cli(argv):
    """Invoke check_pin_caps.main and capture stdout + return code."""
    import check_pin_caps
    buf = io.StringIO()
    with patch.object(sys, "stdout", buf):
        rc = check_pin_caps.main(argv)
    out = buf.getvalue().strip()
    payload = json.loads(out) if out else None
    return rc, payload


def _make_pinned_content(n_pins=0, pin_body_chars=100, stale_indices=()):
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


class TestCheckPinCapsCli_StatusQuery:
    """--status emits current slot state without checking any add."""

    def test_status_zero_pins(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(0))
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert payload["violation"] is None
        assert "0/12" in payload["slot_status"]
        assert payload["evictable_pins"] == []

    def test_status_with_pins(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(3, pin_body_chars=200))
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert "3/12" in payload["slot_status"]
        assert len(payload["evictable_pins"]) == 3
        # evictable_pins shape
        p0 = payload["evictable_pins"][0]
        assert p0["index"] == 0
        assert p0["heading"] == "Pin 0"  # "### " stripped
        assert p0["chars"] == 200
        assert p0["stale"] is False
        assert p0["override"] is False


class TestCheckPinCapsCli_AddAllowed:
    """Happy-path: --new-body fits under both caps → exit 0 + allowed=true."""

    def test_new_body_under_caps_allowed(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(3))
        rc, payload = _run_cli(["--new-body", "short new pin"])
        assert rc == 0
        assert payload["allowed"] is True
        assert payload["violation"] is None


class TestCheckPinCapsCli_CountRefusal:
    """At 12 pins, any add is refused with exit 1 and kind=count."""

    def test_full_slots_count_refused(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(12, pin_body_chars=50))
        rc, payload = _run_cli(["--new-body", "small"])
        assert rc == 1
        assert payload["allowed"] is False
        assert payload["violation"]["kind"] == "count"
        assert payload["violation"]["current_count"] == 12
        assert "(FULL)" in payload["slot_status"]

    def test_full_slots_with_override_still_count_refused(self, patched_claude_md):
        """Override is size-only bypass; does not relax count cap."""
        patched_claude_md(_make_pinned_content(12, pin_body_chars=50))
        rc, payload = _run_cli(["--new-body", "small", "--has-override"])
        assert rc == 1
        assert payload["violation"]["kind"] == "count"


class TestCheckPinCapsCli_SizeRefusal:
    """Oversize bodies refused unless --has-override."""

    def test_oversize_refused_without_override(self, patched_claude_md):
        # Need at least one existing pin so the Pinned Context section is
        # non-empty; empty sections fail-open (unknown state).
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1600
        rc, payload = _run_cli(["--new-body", body])
        assert rc == 1
        assert payload["violation"]["kind"] == "size"
        assert payload["violation"]["offending_pin_chars"] == 1600

    def test_oversize_allowed_with_override(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1600
        rc, payload = _run_cli(["--new-body", body, "--has-override"])
        assert rc == 0
        assert payload["allowed"] is True

    def test_exactly_at_cap_without_override_allowed(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(1, pin_body_chars=50))
        body = "x" * 1500  # exactly at cap → > 1500 predicate is false
        rc, payload = _run_cli(["--new-body", body])
        assert rc == 0
        assert payload["allowed"] is True


class TestCheckPinCapsCli_FailOpen:
    """Resolution / read / parse failures yield allowed=true + exit 0."""

    def test_no_claude_md_fails_open_on_add(self, monkeypatch):
        import staleness
        import check_pin_caps
        monkeypatch.setattr(staleness, "get_project_claude_md_path", lambda: None)
        monkeypatch.setattr(
            check_pin_caps, "get_project_claude_md_path", lambda: None
        )
        rc, payload = _run_cli(["--new-body", "anything"])
        assert rc == 0
        assert payload["allowed"] is True
        assert "unknown" in payload["slot_status"]

    def test_unreadable_file_fails_open_on_add(
        self, patched_claude_md, monkeypatch
    ):
        claude_md = patched_claude_md(_make_pinned_content(3))
        import check_pin_caps

        def _raise(*a, **k):
            raise IOError("simulated read failure")

        monkeypatch.setattr(Path, "read_text", _raise)
        rc, payload = _run_cli(["--new-body", "anything"])
        assert rc == 0
        assert payload["allowed"] is True

    def test_no_pinned_section_fails_open_on_add(self, patched_claude_md):
        """CLAUDE.md exists but has no ## Pinned Context — allow."""
        patched_claude_md("# Project\n\n## Working Memory\n\n")
        rc, payload = _run_cli(["--new-body", "anything"])
        assert rc == 0
        assert payload["allowed"] is True

    def test_empty_pinned_section_fails_open_on_add(self, patched_claude_md):
        """## Pinned Context heading present but body empty — treated
        as unknown state; fail-open per _resolve_pins contract."""
        patched_claude_md(_make_pinned_content(0))  # heading + blank
        rc, payload = _run_cli(["--new-body", "x" * 2000])
        assert rc == 0
        assert payload["allowed"] is True
        assert "unknown" in payload["slot_status"]

    def test_parse_exception_fails_open_on_add(
        self, patched_claude_md, monkeypatch
    ):
        patched_claude_md(_make_pinned_content(3))
        import check_pin_caps

        def _boom(_pinned_content):
            raise RuntimeError("parse blew up")

        monkeypatch.setattr(check_pin_caps, "parse_pins", _boom)
        rc, payload = _run_cli(["--new-body", "anything"])
        assert rc == 0
        assert payload["allowed"] is True

    def test_status_with_no_claude_md_still_emits(self, monkeypatch):
        import staleness
        import check_pin_caps
        monkeypatch.setattr(staleness, "get_project_claude_md_path", lambda: None)
        monkeypatch.setattr(
            check_pin_caps, "get_project_claude_md_path", lambda: None
        )
        # --status bypasses the fail-open short-circuit; emits empty-pin view.
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True


class TestCheckPinCapsCli_EvictablePins:
    """evictable_pins surface — ordering, stale/override flags, heading strip."""

    def test_evictable_includes_stale_flag(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(3, stale_indices={1}))
        rc, payload = _run_cli(["--status"])
        flags = [p["stale"] for p in payload["evictable_pins"]]
        assert flags == [False, True, False]

    def test_evictable_includes_override_flag(self, patched_claude_md):
        content = make_claude_md_with_pins([
            make_pin_entry(
                title="Override Pin",
                body_chars=4,
                date="2026-04-11",
                override_rationale="load-bearing verbatim form",
            ),
            make_pin_entry(title="Plain Pin", body_chars=4),
        ])
        patched_claude_md(content)
        rc, payload = _run_cli(["--status"])
        overrides = [p["override"] for p in payload["evictable_pins"]]
        assert overrides == [True, False]

    def test_evictable_heading_has_prefix_stripped(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(2))
        rc, payload = _run_cli(["--status"])
        for entry in payload["evictable_pins"]:
            assert not entry["heading"].startswith("### ")


class TestCheckPinCapsCli_NeverExit2:
    """Exit code 2 is reserved and MUST NEVER be used by this CLI."""

    def test_exit_never_2_on_add_allowed(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(0))
        rc, _ = _run_cli(["--new-body", "short"])
        assert rc != 2

    def test_exit_never_2_on_add_refused(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(12))
        rc, _ = _run_cli(["--new-body", "any"])
        assert rc != 2

    def test_exit_never_2_on_fail_open(self, monkeypatch):
        import staleness
        import check_pin_caps
        monkeypatch.setattr(staleness, "get_project_claude_md_path", lambda: None)
        monkeypatch.setattr(
            check_pin_caps, "get_project_claude_md_path", lambda: None
        )
        rc, _ = _run_cli(["--new-body", "any"])
        assert rc != 2
