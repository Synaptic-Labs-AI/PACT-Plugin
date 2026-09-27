"""
End-to-end integration tests for /PACT:prune-memory — mock curator
round-trip through the advisory CLI and the pin_caps_gate hook.

Risk tier: HIGH. These tests exercise the full evict-retry loop that
curators will run when the count cap fires:
  1. Curator attempts to add a 13th pin → pin_caps_gate DENIES (count cap).
  2. Curator runs `check_pin_caps --status` → JSON lists 12 evictable pins.
  3. Curator simulates prune-memory.md's Step 4: Edit removing one pin.
     (Step 3, the archive-and-verify gate that must pass before that Edit
     is emitted, is exercised separately — this round-trip covers the
     gate/CLI interlock, not the archival path.)
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
        staleness, "get_project_claude_md_path", lambda errors=None: claude_md
    )
    monkeypatch.setattr(
        check_pin_caps, "get_project_claude_md_path", lambda errors=None: claude_md
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
        # #878: the gate keys lead-detection on is_lead (top-level agent_type).
        # These integration assertions exercise the lead CLAUDE.md-edit path.
        from pin_caps_gate import _check_tool_allowed
        return _check_tool_allowed({
            "tool_name": tool_name,
            "agent_type": "pact-orchestrator",
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
        """Step 2: CLI --status returns the evictable-pin list.

        The key set is asserted as an EXACT equality and the contract has
        GROWN: `age_days` and `overdue` joined the original five when the
        additive age signal landed, because the eviction ordering needs a
        signal that actually fires (the marker-based `stale` flag does not
        fire on the canonical pin format).

        Kept as `==` rather than relaxed to a subset check when it grew.
        prune-memory.md instructs curators to parse these entries BY NAME,
        so a field that silently vanishes breaks the command just as badly
        as one that silently appears, and a subset check can only see the
        second. Widening the expected set records the new contract;
        loosening the operator would have discarded the property.
        """
        curator_env["reset_state"](n_pins=12)
        rc, payload = curator_env["call_cli"](["--status"])
        assert rc == 0
        assert payload["allowed"] is True
        assert len(payload["evictable_pins"]) == 12
        # Each entry has the documented shape. Kept as an EXACT set rather
        # than a subset check: prune-memory.md tells curators to parse these
        # entries by name, so an unannounced field addition or rename in the
        # CLI contract should fail here rather than surface as a curator
        # reading a key that is no longer there. `age_days` / `overdue` are
        # the additive age signal the eviction ordering keys on.
        for entry in payload["evictable_pins"]:
            assert set(entry.keys()) == {
                "index", "heading", "chars", "stale", "override",
                "age_days", "overdue",
            }
            assert isinstance(entry["index"], int)
            assert isinstance(entry["heading"], str)
            assert not entry["heading"].startswith("### ")  # Stripped per CLI.
            # Dated fixture pins, so both age fields are populated here. The
            # unknown-age third state (both null) is covered where the age
            # computation itself is tested; what matters for the prune flow
            # is that `overdue` is never a bare False standing in for null.
            assert isinstance(entry["age_days"], int)
            assert isinstance(entry["overdue"], bool)

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

    def test_has_all_required_steps(self, prune_md):
        """Spec is a named step sequence, and a step deletion would
        silently amputate the workflow.

        This guard's STATED purpose has always been deletion-detection.
        It previously spelled that as `len(step_headings) == 4`, which
        is both stricter and weaker than the intent: stricter because it
        rejects a justified ADDITION, and weaker because a count cannot
        say WHICH step vanished — swapping two steps for two others
        passes it. Pinning the steps BY NAME is strictly better
        deletion-detection, so the rewrite is a correction to the
        assertion rather than a relaxation of the guard.

        The floor permits justified addition — an archive-and-verify
        step was added when eviction became archive-gated — while any
        rename or removal of a named step still fails.
        """
        headings = re.findall(r"^### Step \d+ — (.+)$", prune_md, re.MULTILINE)
        assert headings, (
            "no '### Step N — ' headings found in prune-memory.md — this "
            "guard cannot verify a sequence it cannot locate. Re-aim the "
            "pattern rather than deleting this line. NOTE this is a "
            "DIAGNOSTIC, not a vacuity guard: measured, an empty match set "
            "already fails every assertion below (the bijection finds no "
            "match for the first required name, and the floor fails at 0 "
            "of 5). It exists so the failure names the real cause — a "
            "broken pattern — instead of surfacing as a confusing report "
            "that every required step is missing."
        )
        # BIJECTION, not `any(required in h for h in headings)`. Matching
        # each required name against ANY heading lets ONE heading satisfy
        # TWO requirements, so folding a step into a sibling's title --
        # "### Step 3 — Archive the selected pin and Report" while renaming
        # step 5 -- DELETES a step and still passes, with the >= 5 floor met
        # by whatever filler remains. Measured: `any()` passes that
        # mutation, this does not. Consuming each match makes "every
        # required step exists as its OWN heading" the actual assertion,
        # which is what the docstring above has always claimed.
        remaining = list(headings)
        for required in ("Read the evictable-pin list", "Ask the curator",
                         "Archive the selected pin", "Remove the selected pin",
                         "Report"):
            match = next((h for h in remaining if required in h), None)
            assert match is not None, (
                f"prune-memory.md missing required step {required!r} as a "
                f"distinct heading; unconsumed headings: {remaining} "
                f"(all headings: {headings})"
            )
            remaining.remove(match)
        assert len(headings) >= 5, (
            f"prune-memory.md should have at least 5 steps, found "
            f"{len(headings)}: {headings}"
        )

    def test_decision_prompt_frames_removal_as_demotion(self, prune_md):
        """The AskUserQuestion prompt is the DECISION SURFACE — the one
        sentence the curator reads at the moment they choose to give up a
        pin — so it is where the demotion framing has to hold. Prose
        elsewhere in the file explaining that demotion preserves content
        does not reach a curator who reads only the prompt.

        SCOPED TO THE PROMPT TEXT, deliberately. A file-wide ban on
        'evict' is impossible and would be wrong: `evictable_pins` is the
        CLI's field name, and 'eviction' is the accurate technical term
        in the instructions addressed to the executing agent. The word is
        only misleading where it describes the CONSEQUENCE to the curator,
        which is this one line.

        The negative arm is what makes this non-vacuous: a positive-only
        check passes on a prompt reading 'Demote or evict?'.
        """
        prompts = re.findall(r'^\s*question:\s*"([^"]*)"', prune_md, re.MULTILINE)
        # Guard the guard: if the AskUserQuestion block is ever restructured
        # so no prompt is extractable, this test must fail loudly rather
        # than pass over an empty list.
        assert prompts, (
            "no AskUserQuestion `question:` line found in prune-memory.md — "
            "this guard cannot verify a decision surface it cannot locate"
        )
        for prompt in prompts:
            assert re.search(r"demote", prompt, re.IGNORECASE), (
                f"decision prompt {prompt!r} does not frame the action as "
                "demotion; AC-B3 requires the curator-facing wording to say "
                "the content is preserved, not removed"
            )
            for banned in ("evict", "delete"):
                assert banned not in prompt.lower(), (
                    f"decision prompt {prompt!r} contains {banned!r}; the "
                    "prompt describes the consequence to the curator and "
                    "must not imply the pin is discarded"
                )

    def test_step_4_consumes_the_handle_and_derives_no_boundary(self, prune_md):
        """Step 4 removes an emitted string; it must NOT restate the span rule.

        The span rule lived in BOTH `archive_pin.py` and this prose, and the
        two diverged: the prose kept an end-boundary the script had already
        corrected, so the destroy range was a superset of the archive range
        and the excess was a RETAINED pin's date comment. The fix was to
        delete the second copy, not to correct it — a corrected copy is still
        a copy, and the copy is the drift mechanism.

        So after the deletion, any reappearance of span-derivation vocabulary
        in Step 4 IS the defect returning, which is why the negative arm is
        the point of this test.

        POSITIVE AND NEGATIVE ARMS ARE COUPLED HERE DELIBERATELY — DO NOT
        SPLIT THEM. A bare `not in` assertion passes trivially over an empty
        string: delete Step 4, restructure the file, or let the section
        extraction miss, and it goes green forever. A negative assertion has
        no natural non-vacuity arm, because absence is exactly what it seeks
        — it cannot distinguish "the bad thing is gone" from "nothing is
        here". The positive arm is what establishes the surface exists, so
        the negative one means something. Split across two tests, the
        positive reads as trivially true, someone deletes it, and the
        negative silently becomes decoration.
        """
        section = re.search(
            r"^### Step 4 — .*?(?=^### Step \d+ — )", prune_md,
            re.MULTILINE | re.DOTALL)
        assert section, (
            "Step 4 section not found in prune-memory.md — the extraction is "
            "broken or the step was removed. Re-aim rather than deleting: "
            "without this, the negative assertions below pass over an empty "
            "string and this guard silently protects nothing."
        )
        body = section.group(0)

        # POSITIVE: the step consumes the emitted handle at the resolved path.
        for required in ("delete_string", "claude_md_path"):
            assert required in body, (
                f"Step 4 no longer references {required!r}. It must remove the "
                "string the verdict emitted, at the path the verdict reports — "
                "not a boundary it works out for itself."
            )

        # NEGATIVE: and it derives no boundary of its own.
        for banned in ("span start", "date comment line", "next `### ` heading",
                       "preserving surrounding blank lines"):
            assert banned not in body, (
                f"Step 4 restates the span rule ({banned!r}). That is a SECOND "
                "copy of a rule the archiver already owns, and the two diverged "
                "once already — the prose kept an end-boundary the script had "
                "corrected, destroying a retained pin's date comment. Remove "
                "the restatement; the emitted handle is the boundary."
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

    def test_json_example_honors_the_three_state_age_contract(self, prune_md):
        """The RULE and the EXAMPLE are two independent claims and can
        disagree — this pins the example, because the example is the one
        that gets copied.

        prune-memory.md states that `age_days: null` is a third state and
        is NOT `overdue: false`; `check_pin_caps.py` emits
        `None if age_days is None else ...`, so the pairing the example
        showed is one the code never produces. Found by `coder-prose`, and
        verified by OBSERVATION rather than by reading the ternary: a
        `--status` run over a CLAUDE.md with one dated and one undated pin
        returned `age_days=2397, overdue=True` and `age_days=None,
        overdue=None`. The dated pin is the positive control — an all-null
        result would have meant a broken harness rather than a finding.

        WHY THE EXAMPLE AND NOT THE RULE. The failure mode is a curator
        COPYING the example, so the misleading artifact is the example
        itself; adjacent prose is more likely to be skimmed than a JSON
        block is to be mis-transcribed. A curator reasoning from
        `overdue: false` treats an undated pin as not-overdue, silently
        exempting the pin most likely to have sat longest — which is the
        failure the third state exists to prevent.
        """
        pairs = re.findall(
            r'"age_days":\s*(null|\d+),\s*"overdue":\s*(null|true|false)',
            prune_md,
        )
        assert pairs, (
            "no age_days/overdue pair found in prune-memory.md's JSON "
            "example — this guard cannot check an example it cannot locate"
        )
        for age, overdue in pairs:
            if age == "null":
                assert overdue == "null", (
                    f'example pairs "age_days": null with "overdue": '
                    f"{overdue} — the code emits null; an unknown age is "
                    "not the same as not-overdue"
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
