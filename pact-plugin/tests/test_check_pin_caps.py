"""
Tests for scripts/check_pin_caps.py — ADVISORY CLI (cycle-8 demotion).

Risk tier: CRITICAL on the advisory-shape contract (/PACT:prune-memory
depends on the JSON shape and on the fail-open exit-code invariant).
The CLI no longer enforces caps — enforcement lives in
hooks/pin_caps_gate.py. Tests here cover the retained surface:
  * --status and --list-evictable emit the same JSON payload shape
  * evictable_pins serialization (stale / override / heading strip)
  * fail-open paths never exit non-zero
  * SACROSANCT: never exit 2 under any resolved-state or fail-open path

Retired-flag tests (--new-body, --body-from-stdin, --has-override,
--override-rationale, stdin-empty refusal, in-band override rationale
validation) were deleted in the cycle-8 CLI-demote commit. Hook-side
enforcement is exercised by tests/test_pin_caps_gate.py (smoke) plus
Phase E exhaustive matrix coverage.

Historical reference: pre-cycle-8 test file carried ~600 LOC of
add-path / stdin / override tests. Those tests intentionally exercised
the CLI's enforcement responsibilities, which no longer exist. Keeping
them would produce phantom-green coverage against a CLI path no
curator ever reaches post-demotion (hook denies first).
"""

import io
import json
import sys
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
        import check_pin_caps
        monkeypatch.setattr(
            check_pin_caps, "get_project_claude_md_path", lambda: claude_md
        )
        return claude_md
    return _write


def _run_cli(argv) -> tuple[int, dict]:
    """Invoke check_pin_caps.main and capture stdout + return code.

    Contract: the demoted CLI always emits a JSON payload (advisory
    or fail-open both write to stdout) — an empty stdout would be a
    CLI bug. We assert on that invariant so downstream tests can
    subscript the payload dict without Optional-narrowing noise.
    """
    import check_pin_caps
    buf = io.StringIO()
    with patch.object(sys, "stdout", buf):
        rc = check_pin_caps.main(argv)
    out = buf.getvalue().strip()
    assert out, "CLI produced no stdout — advisory payload is always required"
    payload = json.loads(out)
    assert isinstance(payload, dict), (
        f"CLI payload must be a JSON object, got {type(payload).__name__}"
    )
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


class TestCheckPinCaps_Advisory_StatusQuery:
    """--status emits current slot state. Post-demotion, this is also the
    default (no-flag) behavior — the CLI has no add-time flags to select."""

    def test_status_zero_pins(self, patched_claude_md):
        """Empty Pinned Context body routes through fail-open in _resolve_pins
        (parsed is None → reason='no pinned section'). --status surfaces
        the fail-open signal rather than a fake 0/12 slot state."""
        patched_claude_md(_make_pinned_content(0))
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert payload["violation"] is None
        assert "unknown" in payload["slot_status"]
        assert "proceeding" in payload["slot_status"]
        assert payload["evictable_pins"] == []

    def test_status_with_pins(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(3, pin_body_chars=200))
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert "3/12" in payload["slot_status"]
        assert len(payload["evictable_pins"]) == 3
        p0 = payload["evictable_pins"][0]
        assert p0["index"] == 0
        assert p0["heading"] == "Pin 0"  # "### " stripped
        assert p0["chars"] == 200
        assert p0["stale"] is False
        assert p0["override"] is False

    def test_default_argv_equivalent_to_status(self, patched_claude_md):
        """No flags → same payload as --status. Post-demotion the CLI has
        no argv-selected behaviors; advisory is the only mode."""
        patched_claude_md(_make_pinned_content(3))
        rc_default, payload_default = _run_cli([])
        rc_status, payload_status = _run_cli(["--status"])
        assert rc_default == rc_status == 0
        assert payload_default == payload_status

    def test_list_evictable_equivalent_to_status(self, patched_claude_md):
        """--list-evictable is an alias for --status; same payload shape."""
        patched_claude_md(_make_pinned_content(3))
        rc_list, payload_list = _run_cli(["--list-evictable"])
        rc_status, payload_status = _run_cli(["--status"])
        assert rc_list == rc_status == 0
        assert payload_list == payload_status


class TestCheckPinCaps_Advisory_FailOpen:
    """Resolution / read / parse failures yield allowed=true + exit 0.

    Post-demotion every code path is advisory; fail-open is the universal
    shape. Degradation reason surfaces in slot_status so consumers see
    'unknown (...)' instead of a fake zero-state.
    """

    def test_no_claude_md_fails_open(self, monkeypatch):
        import staleness
        import check_pin_caps
        monkeypatch.setattr(
            staleness, "get_project_claude_md_path", lambda: None
        )
        monkeypatch.setattr(
            check_pin_caps, "get_project_claude_md_path", lambda: None
        )
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert "unknown" in payload["slot_status"]
        assert "claude.md not found" in payload["slot_status"]

    def test_unreadable_file_fails_open(self, patched_claude_md, monkeypatch):
        patched_claude_md(_make_pinned_content(3))

        def _raise(*a, **k):
            raise IOError("simulated read failure")

        monkeypatch.setattr(Path, "read_text", _raise)
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert "unknown" in payload["slot_status"]

    def test_no_pinned_section_fails_open(self, patched_claude_md):
        """CLAUDE.md exists but has no ## Pinned Context."""
        patched_claude_md("# Project\n\n## Working Memory\n\n")
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert "no pinned section" in payload["slot_status"]

    def test_parse_exception_fails_open(self, patched_claude_md, monkeypatch):
        patched_claude_md(_make_pinned_content(3))
        import check_pin_caps

        def _boom(_pinned_content):
            raise RuntimeError("parse blew up")

        monkeypatch.setattr(check_pin_caps, "parse_pins", _boom)
        rc, payload = _run_cli(["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert "parse error" in payload["slot_status"]


class TestCheckPinCaps_Advisory_EvictablePins:
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


class TestCheckPinCaps_Advisory_NeverExit2:
    """SACROSANCT: exit code 2 is reserved; advisory CLI MUST never emit it.

    argparse `--help` uses SystemExit(0); argparse validation uses
    SystemExit(2) — both are argparse-internal and re-raised. Our CLI's
    own logic MUST never introduce a new exit-2 path (no enforcement
    failures, no fail-open fault converted to 2, no argparse-error from
    a retired flag).
    """

    def test_exit_never_2_on_status(self, patched_claude_md):
        patched_claude_md(_make_pinned_content(3))
        rc, _ = _run_cli(["--status"])
        assert rc != 2

    def test_exit_never_2_on_fail_open(self, monkeypatch):
        import staleness
        import check_pin_caps
        monkeypatch.setattr(
            staleness, "get_project_claude_md_path", lambda: None
        )
        monkeypatch.setattr(
            check_pin_caps, "get_project_claude_md_path", lambda: None
        )
        rc, _ = _run_cli(["--status"])
        assert rc != 2

    def test_retired_flags_do_not_exit_2(self, patched_claude_md):
        """Retired cycle-7 flags (--new-body, --body-from-stdin,
        --has-override, --override-rationale) must not crash the CLI with
        argparse-exit-2 now that they're removed. The demoted CLI uses
        `parse_known_args` so unknown flags are silently ignored — stale
        /PACT:pin-memory callers won't DoS themselves while the pin-memory
        command is being rewritten in a sibling commit."""
        patched_claude_md(_make_pinned_content(3))
        for retired in [
            ["--new-body", "anything"],
            ["--body-from-stdin"],
            ["--has-override"],
            ["--override-rationale", "anything"],
        ]:
            rc, payload = _run_cli(retired)
            assert rc != 2, (
                f"retired flag {retired[0]} must not crash the CLI with "
                f"argparse-exit-2 — got rc={rc}, payload={payload}"
            )


class TestParsePins_MultilineRationale:
    """Parser-side defense against multi-line override rationales.

    This test covers hooks/pin_caps.py:parse_pins (not the CLI). Pre-
    cycle-8 it lived in a CLI-focused test class; moved here post-CLI-
    demotion because the CLI no longer owns rationale validation.
    Hook-side rationale validation (identical semantics) is in
    tests/test_pin_caps_gate.py.
    """

    def test_parser_rejects_multiline_rationale_cleanly(self):
        """parse_pins on a CLAUDE.md fragment with a multi-line override:
          (a) must not raise (fail-open by construction)
          (b) must NOT capture the broken rationale as a valid override
              (silent downgrade to no-override is the documented behavior)
        """
        import pin_caps
        pinned_content = (
            "<!-- pinned: 2026-04-21, pin-size-override: split\n"
            "across-lines -->\n"
            "### Broken Pin\n"
            "body content\n"
        )
        pins = pin_caps.parse_pins(pinned_content)
        assert len(pins) == 1
        assert pins[0].override_rationale is None, (
            "Multi-line rationale was captured as a valid override — "
            "parse_pins must not accept split-line rationale forms. "
            f"Got: {pins[0].override_rationale!r}"
        )
