"""
End-to-end integration tests for /PACT:prune-memory — mock curator
round-trip through the advisory CLI and the pin_caps_gate hook.

Risk tier: HIGH. These tests exercise the full evict-retry loop that
curators will run when the count cap fires:
  1. Curator attempts to add a 13th pin → pin_caps_gate DENIES (count cap).
  2. Curator runs `check_pin_caps --status` → JSON lists 12 evictable pins.
  3. Curator simulates prune-memory.md's Step 3: Edit removing one pin.
     → pin_caps_gate ALLOWS (post < pre → net-worse=False).
  4. Curator retries the original add → pin_caps_gate ALLOWS (under cap now).

Also includes prose-contract tests for commands/prune-memory.md — the
markdown spec is executable insofar as downstream LLM curators must be
able to parse its pagination rules, label formats, and evict algorithm.
"""

import io
import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent))

from helpers import make_claude_md_with_pins, make_pin_entry  # noqa: E402


@pytest.fixture
def curator_env(tmp_path, monkeypatch, pact_context):
    """Build a full environment wired for the curator round-trip.

    Returns dict with:
        claude_md    — path to on-disk CLAUDE.md
        call_gate    — function(tool_input dict) → deny reason or None
        call_cli     — function(argv list) → (rc, payload dict)
        reset_state  — function(n_pins) → rewrite baseline to N pins
    """
    claude_md = tmp_path / "CLAUDE.md"
    pact_context(
        team_name="test-team",
        session_id="session-curator",
        project_dir=str(tmp_path),
    )

    # Wire BOTH the hook's path resolver and the CLI's path resolver to
    # our tmp CLAUDE.md. IMPORTANT: import `check_pin_caps` FIRST — it
    # calls `_load_hook_module("staleness")` which REPLACES
    # `sys.modules['staleness']` with a fresh module instance. If we
    # monkeypatch staleness before check_pin_caps is imported, the patch
    # lives on the old module object and the CLI's reload silently
    # overwrites it. Patching AFTER ensures both the hook's staleness
    # reference (resolved at hook-call time) AND the CLI's local
    # get_project_claude_md_path symbol point at the same tmp path.
    import check_pin_caps  # triggers _load_hook_module side effects first
    import staleness
    monkeypatch.setattr(
        staleness, "get_project_claude_md_path", lambda: claude_md
    )
    monkeypatch.setattr(
        check_pin_caps, "get_project_claude_md_path", lambda: claude_md
    )

    def _reset_state(n_pins, body_chars=4):
        entries = [
            make_pin_entry(title=f"Pin{i}", body_chars=body_chars)
            for i in range(n_pins)
        ]
        claude_md.write_text(
            make_claude_md_with_pins(entries), encoding="utf-8"
        )

    def _call_gate(tool_name, tool_input):
        from pin_caps_gate import _check_tool_allowed
        return _check_tool_allowed({
            "tool_name": tool_name,
            "tool_input": tool_input,
        })

    def _call_cli(argv):
        buf = io.StringIO()
        with patch.object(sys, "stdout", buf):
            rc = check_pin_caps.main(argv)
        return rc, json.loads(buf.getvalue().strip())

    return {
        "claude_md": claude_md,
        "call_gate": _call_gate,
        "call_cli": _call_cli,
        "reset_state": _reset_state,
    }


def _build_full_content(pin_count, body_chars=4):
    entries = [
        make_pin_entry(title=f"Pin{i}", body_chars=body_chars)
        for i in range(pin_count)
    ]
    return make_claude_md_with_pins(entries)


# ---------------------------------------------------------------------------
# Mock curator round-trip
# ---------------------------------------------------------------------------


class TestMockCuratorRoundTrip:
    """Exercises the full prune-memory workflow end-to-end.

    The curator is simulated in-test — no LLM in the loop, just the
    mechanical steps the curator would execute. Confirms all four
    components interlock correctly: gate DENY, CLI --status, gate ALLOW
    on prune, gate ALLOW on retry.
    """

    def test_over_cap_write_denied(self, curator_env):
        """Step 1: Write attempt that pushes count over the cap is DENIED."""
        curator_env["reset_state"](n_pins=12)
        # 13-pin payload against 12-pin baseline.
        result = curator_env["call_gate"](
            "Write",
            {
                "file_path": str(curator_env["claude_md"]),
                "content": _build_full_content(13),
            },
        )
        assert result is not None
        assert "Pin count cap" in result

    def test_cli_status_lists_all_evictable_pins(self, curator_env):
        """Step 2: CLI --status returns the evictable-pin list."""
        curator_env["reset_state"](n_pins=12)
        rc, payload = curator_env["call_cli"](["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert len(payload["evictable_pins"]) == 12
        # Each entry has the documented shape.
        for entry in payload["evictable_pins"]:
            assert set(entry.keys()) == {
                "index", "heading", "chars", "stale", "override"
            }
            assert isinstance(entry["index"], int)
            assert isinstance(entry["heading"], str)
            assert not entry["heading"].startswith("### ")  # Stripped per CLI.

    def test_prune_edit_allowed_when_count_decreases(self, curator_env):
        """Step 3: Edit removing one pin block is ALLOWED.

        Strategy: remove the `<!-- pinned: ... -->\n### Pin11\nxxxx\n\n`
        fragment via Edit. Post-state has 11 pins (down from 12) — net-
        worse=False → allow. Embedded-pin short-circuit is also False
        because new_string is empty (no `### ` in the replacement).
        """
        curator_env["reset_state"](n_pins=12)
        # Build the exact block to remove. Matches make_pin_entry output
        # and make_pinned_section joiner ("\n\n").
        block_to_remove = (
            "<!-- pinned: 2026-04-20 -->\n"
            "### Pin11\n"
            "xxxx\n"
            "\n"
        )
        result = curator_env["call_gate"](
            "Edit",
            {
                "file_path": str(curator_env["claude_md"]),
                "old_string": block_to_remove,
                "new_string": "",
                "replace_all": False,
            },
        )
        assert result is None, (
            f"prune Edit should ALLOW (net-worse=False on count decrease), "
            f"got: {result!r}"
        )

    def test_retry_add_allowed_after_prune(self, curator_env):
        """Step 4: After prune (11 pins on disk), adding a 12th succeeds."""
        # Simulate post-prune state: 11 pins on disk.
        curator_env["reset_state"](n_pins=11)
        # Curator attempts the add via Write of 12 pins.
        result = curator_env["call_gate"](
            "Write",
            {
                "file_path": str(curator_env["claude_md"]),
                "content": _build_full_content(12),
            },
        )
        assert result is None

    def test_full_round_trip_sequence(self, curator_env):
        """Sequence: all 4 steps in order, sharing state via on-disk CLAUDE.md.

        This is the load-bearing integration test — each step's output
        is the next step's input. A regression in ANY component breaks
        the sequence at a discoverable boundary.
        """
        # Start with 12 pins (at cap).
        curator_env["reset_state"](n_pins=12)

        # Step 1: 13-pin add is denied.
        deny = curator_env["call_gate"](
            "Write",
            {
                "file_path": str(curator_env["claude_md"]),
                "content": _build_full_content(13),
            },
        )
        assert deny is not None and "Pin count cap" in deny

        # Step 2: CLI enumerates 12 evictable pins.
        rc, payload = curator_env["call_cli"](["--status"])
        assert rc == 0
        assert len(payload["evictable_pins"]) == 12
        # Curator picks index 11 (stale pins would be preferred in
        # real flow; here all pins are fresh, so pick last).
        picked_heading = payload["evictable_pins"][11]["heading"]
        assert picked_heading == "Pin11"

        # Step 3: Curator edits CLAUDE.md to remove Pin11.
        # The Edit must be applied to the on-disk file so step 4 sees the
        # new state. The gate APPROVES the edit, so we apply it ourselves
        # to simulate what the platform would do post-approval.
        block = (
            "<!-- pinned: 2026-04-20 -->\n"
            "### Pin11\n"
            "xxxx\n"
            "\n"
        )
        gate_approval = curator_env["call_gate"](
            "Edit",
            {
                "file_path": str(curator_env["claude_md"]),
                "old_string": block,
                "new_string": "",
                "replace_all": False,
            },
        )
        assert gate_approval is None
        # Apply the edit out-of-band (simulating Claude Code's tool apply).
        current = curator_env["claude_md"].read_text(encoding="utf-8")
        curator_env["claude_md"].write_text(
            current.replace(block, "", 1), encoding="utf-8"
        )

        # Step 4: Retry add — now under cap, ALLOWS.
        retry = curator_env["call_gate"](
            "Write",
            {
                "file_path": str(curator_env["claude_md"]),
                "content": _build_full_content(12),
            },
        )
        assert retry is None


class TestMockCuratorRoundTrip_StaleFirstPreference:
    """prune-memory.md specifies stale pins should be evictable-flagged
    so the curator can prefer them. CLI returns stale booleans; curator
    logic (modeled in-test) picks stale first.
    """

    def test_cli_flags_stale_pins(self, curator_env):
        """CLI --status surfaces `stale: true` for pins with STALE marker."""
        # Build 3 pins with the middle one marked stale.
        entries = [
            make_pin_entry(title="Fresh0", body_chars=4),
            make_pin_entry(title="Stale1", body_chars=4, stale_date="2025-01-01"),
            make_pin_entry(title="Fresh2", body_chars=4),
        ]
        curator_env["claude_md"].write_text(
            make_claude_md_with_pins(entries), encoding="utf-8"
        )
        rc, payload = curator_env["call_cli"](["--status"])
        assert rc == 0
        evictable = payload["evictable_pins"]
        assert len(evictable) == 3
        stale_flags = {e["heading"]: e["stale"] for e in evictable}
        assert stale_flags == {
            "Fresh0": False,
            "Stale1": True,
            "Fresh2": False,
        }

    def test_cli_flags_override_pins(self, curator_env):
        """CLI --status surfaces `override: true` for override-carrying pins."""
        entries = [
            make_pin_entry(title="Plain", body_chars=4),
            make_pin_entry(
                title="Overridden",
                body_chars=4,
                override_rationale="load-bearing verbatim dispatch form",
            ),
        ]
        curator_env["claude_md"].write_text(
            make_claude_md_with_pins(entries), encoding="utf-8"
        )
        rc, payload = curator_env["call_cli"](["--status"])
        assert rc == 0
        override_flags = {e["heading"]: e["override"] for e in payload["evictable_pins"]}
        assert override_flags == {"Plain": False, "Overridden": True}


# ---------------------------------------------------------------------------
# prune-memory.md prose contract tests
# ---------------------------------------------------------------------------


class TestPruneMemoryProseContract:
    """The prune-memory.md command file is the curator's executable spec.
    Downstream LLM readers parse pagination rules, label shapes, and the
    evict algorithm from it. These tests pin load-bearing prose elements
    so documentation drift that would break the curator is caught at
    test-time.
    """

    @pytest.fixture
    def prune_md(self):
        path = (
            Path(__file__).parent.parent / "commands" / "prune-memory.md"
        )
        return path.read_text(encoding="utf-8")

    def test_has_four_step_process(self, prune_md):
        """Spec is structured as four steps. A step deletion would
        silently amputate the workflow."""
        step_headings = re.findall(r"^### Step \d+", prune_md, re.MULTILINE)
        assert len(step_headings) == 4, (
            f"prune-memory.md should have exactly 4 steps, found "
            f"{len(step_headings)}: {step_headings}"
        )

    def test_references_advisory_cli(self, prune_md):
        """Step 1 must point curators at check_pin_caps --status."""
        assert "check_pin_caps.py" in prune_md
        assert "--status" in prune_md

    def test_declares_json_contract_shape(self, prune_md):
        """Spec must document the JSON shape curators consume."""
        # All required fields of the evictable-pin list entry.
        for field in ("index", "heading", "chars", "stale", "override"):
            assert field in prune_md, (
                f"prune-memory.md missing evictable_pins field {field!r}"
            )

    def test_declares_pagination_rule(self, prune_md):
        """Pagination (3-per-page + Show more, last page + Cancel) must
        survive as a grammar-observable contract."""
        assert "Show more" in prune_md
        assert "Cancel" in prune_md
        # "3 candidate pins per `AskUserQuestion`" — presence check.
        assert "3" in prune_md  # Weak; stronger check below.
        assert re.search(
            r"(?:≤|<=)\s*3\s*evictable", prune_md
        ) or re.search(
            r"3\s*pins\s*\+\s*.Show more.", prune_md
        ) or re.search(
            r"3\s*candidate\s*pins", prune_md
        ), "Pagination rule (3-per-page) not grammar-detectable in prune-memory.md"

    def test_declares_stale_first_preference(self, prune_md):
        """Spec recommends STALE pins as preferred evict candidates."""
        assert (
            re.search(r"[Ss]tale", prune_md) is not None
            and re.search(r"[Pp]refer", prune_md) is not None
        )

    def test_declares_one_pin_per_invocation(self, prune_md):
        """Safety invariant: never evicts more than one pin per run.

        This is load-bearing: multi-evict would bypass the per-evict
        auditable checkpoint the spec promises.
        """
        assert "NEVER evicts more than one pin" in prune_md

    def test_declares_net_worse_interaction(self, prune_md):
        """Spec must flag that the hook ALLOWS prune Edits (pin count
        strictly decreases → net-worse=False). Curators who don't
        understand this will mistakenly re-invoke check_pin_caps after
        the prune."""
        # Either "ALLOW" mention or "strictly decreases" or "net-worse"
        assert (
            "ALLOW" in prune_md
            or "strictly" in prune_md
            or "net-worse" in prune_md
            or "strictly better" in prune_md
        )

    def test_references_pin_caps_gate_hook(self, prune_md):
        """Cross-reference to the authoritative enforcer."""
        assert "pin_caps_gate" in prune_md

    def test_fail_open_unknown_state_handling(self, prune_md):
        """Spec must instruct curators to STOP on 'unknown (...)' CLI state
        rather than proceed with a bad pick."""
        assert "unknown" in prune_md
        assert re.search(r"do NOT|stop|Stop", prune_md)


class TestPinMemoryProseContract:
    """Sibling prose-contract tests for pin-memory.md. It was rewritten
    to pin-add-only in cycle-8; the Refusal-flow section and cross-refs
    to prune-memory are load-bearing."""

    @pytest.fixture
    def pin_md(self):
        path = (
            Path(__file__).parent.parent / "commands" / "pin-memory.md"
        )
        return path.read_text(encoding="utf-8")

    def test_declares_hook_enforcement(self, pin_md):
        """The spec must tell curators the hook is authoritative — no
        CLI check needed before add."""
        assert "pin_caps_gate" in pin_md
        # "authoritative" or "denied by hook" phrasing.
        assert "authoritative" in pin_md or "denied" in pin_md.lower()

    def test_declares_four_cap_kinds(self, pin_md):
        """All four refusal reasons must be documented so curators
        recognize them when the hook DENIES."""
        # Count cap
        assert "count cap" in pin_md.lower() or "12/12" in pin_md
        # Size cap
        assert "1500" in pin_md
        # Embedded pin
        assert "embedded" in pin_md.lower() or "`### `" in pin_md
        # Override malformed
        assert "rationale" in pin_md.lower()

    def test_cross_refs_prune_memory(self, pin_md):
        """Pin-memory.md must point curators at prune-memory.md when the
        count cap fires."""
        assert "/PACT:prune-memory" in pin_md

    def test_override_rationale_constraints(self, pin_md):
        """Rationale MUST be ≤120 chars, single line — the spec is the
        contract curators follow."""
        assert "120" in pin_md
        assert "single" in pin_md.lower() or "single-line" in pin_md.lower()
