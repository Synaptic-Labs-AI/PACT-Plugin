"""
Tests for hooks/pin_staleness_gate.py — PreToolUse marker-gate for
CLAUDE.md Pinned Context edits under stale-pins-pending state.

Risk tier: CRITICAL (auth-adjacent — gate blocks user tool calls). All
I/O failure paths MUST fail-open (SACROSANCT: gate bugs never block).

Matrix: marker absence/present × CLAUDE.md path match/miss × teammate/team-lead
        × Edit/Write → 16 cells minimum, plus fail-open assertions.
"""

import json
import sys
from pathlib import Path

import pytest

from helpers import make_claude_md_with_pins, make_pin_entry  # noqa: E402


@pytest.fixture
def gate_env(tmp_path, monkeypatch, pact_context):
    """Assemble a minimal PreToolUse gate environment.

    Returns a callable that writes a CLAUDE.md, optionally writes a
    pin-staleness-pending marker, sets pact_context, and yields the paths
    needed to build tool_input payloads.
    """
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text(
        make_claude_md_with_pins([make_pin_entry(title="Pin", body_chars=4)]),
        encoding="utf-8",
    )

    session_dir = tmp_path / "session-dir"
    session_dir.mkdir()

    # Point pact_context at a writable session dir.
    pact_context(
        team_name="test-team",
        session_id="session-abc",
        project_dir=str(tmp_path),
    )

    # Patch get_session_dir to return our tmp path.
    import shared.pact_context as ctx_module
    monkeypatch.setattr(
        ctx_module, "get_session_dir", lambda: str(session_dir)
    )

    # Patch get_project_claude_md_path so _is_project_claude_md resolves
    # our tmp CLAUDE.md.
    import staleness
    monkeypatch.setattr(
        staleness, "get_project_claude_md_path", lambda: claude_md
    )

    def _setup(*, marker_present=True):
        from pin_staleness_gate import PIN_STALENESS_MARKER_NAME
        marker_path = session_dir / PIN_STALENESS_MARKER_NAME
        if marker_present and not marker_path.exists():
            marker_path.touch()
        elif not marker_present and marker_path.exists():
            marker_path.unlink()
        return {
            "claude_md": claude_md,
            "session_dir": session_dir,
            "marker_path": marker_path,
        }

    return _setup


def _call_gate(input_data):
    """Invoke _check_tool_allowed directly with a synthesized input_data.

    #878: the gate now keys lead-detection on is_lead (the harness-set
    agent_type), not the old empty-resolve_agent_name heuristic. Default to a
    LEAD frame (the unmarked case these DENY tests assume) unless the caller
    supplies an explicit agent_type (teammate/plain bypass tests).
    """
    from pin_staleness_gate import _check_tool_allowed
    if "agent_type" not in input_data:
        input_data = {**input_data, "agent_type": "pact-orchestrator"}
    return _check_tool_allowed(input_data)


# Matrix coverage note: the full matrix is (marker absent/present) × (path match/miss) ×
# (teammate/team-lead) × (Edit/Write) = 16 cells. Three cells are NOT exercised explicitly
# because they reduce to tested paths: (marker-absent × teammate × {Edit,Write,path-miss})
# all short-circuit at the same marker-check before any teammate/path logic runs —
# TestPinStalenessGate_MarkerAbsent already covers that short-circuit for team-lead callers,
# and the marker-absent return is agent-name-independent by construction.


class TestPinStalenessGate_ToolMatch:
    """Only Edit and Write are gated — other tools always pass."""

    @pytest.mark.parametrize("tool_name", ["Read", "Bash", "Glob", "Grep",
                                           "Agent", "NotebookEdit", ""])
    def test_non_gated_tools_pass(self, tool_name, gate_env):
        gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": tool_name,
            "tool_input": {"file_path": "whatever", "content": "whatever"},
        })
        assert result is None


class TestPinStalenessGate_MarkerAbsent:
    """Marker absent → always allow regardless of path/content."""

    def test_edit_on_claude_md_without_marker_allowed(self, gate_env):
        env = gate_env(marker_present=False)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "## Pinned Context",
                "new_string": "## Pinned Context\nmore",
            },
        })
        assert result is None

    def test_write_on_claude_md_without_marker_allowed(self, gate_env):
        env = gate_env(marker_present=False)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": "## Pinned Context\nbody",
            },
        })
        assert result is None


class TestPinStalenessGate_MarkerPresent:
    """Marker present × path match × ADD-shaped edit → DENY.

    Post-F1 remediation: only ADD-shaped edits (net-new `<!-- pinned:`
    comment) are gated. Archival (pin removal) and refactor (pin body
    rewrite) MUST be allowed so the user can resolve the stale-pins
    condition within the same session via /PACT:pin-memory.
    """

    def test_edit_adding_new_pin_denied(self, gate_env):
        """An Edit that adds a pin to the pinned section is denied.

        THE ANCHOR MUST BE PRESENT IN THE FIXTURE, and the assertion below
        holds it there. The gate compares the document BEFORE the edit against
        the document AFTER it, so an `old_string` the fixture does not carry
        makes the edit a no-op: the two documents agree, no pin is added, and
        the gate allows. Such an arm asserts nothing about an add and it goes
        green whatever the gate does. An earlier revision of this arm carried
        exactly that payload, correctly, because the gate then compared the two
        payload fragments and read no document at all.
        """
        env = gate_env(marker_present=True)
        current = env["claude_md"].read_text(encoding="utf-8")
        anchor = "### Pin\nxxxx\n"
        assert current.count(anchor) == 1, (
            "non-vacuity: the anchor must occur one time in the fixture, or "
            "the edit applies nowhere and this arm cannot observe an add"
        )
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": anchor,
                "new_string": anchor + "<!-- pinned: 2026-05-01 -->\n### Y\nbody\n",
            },
        })
        assert result is not None
        assert "Pinned Context" in result
        assert "stale pins" in result

    def test_edit_adding_pin_at_section_terminator_denied(self, gate_env):
        """The add anchored on the line that TERMINATES the pinned section.

        This anchor is the one a person reaches for to append a pin at the end
        of the section, and it is the position an offset-based locus test read
        as OUTSIDE the region. Measured on the project document: the same pin,
        added by two anchors one byte apart, gave opposite verdicts. This arm
        reddens if that test returns.
        """
        env = gate_env(marker_present=True)
        current = env["claude_md"].read_text(encoding="utf-8")
        anchor = "## Working Memory"
        assert current.count(anchor) == 1, (
            "non-vacuity: the anchor must occur one time in the fixture"
        )
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": anchor,
                "new_string": "<!-- pinned: 2026-05-01 -->\n### Y\nbody\n" + anchor,
            },
        })
        assert result is not None
        assert "stale pins" in result

    def test_edit_with_absent_anchor_allowed(self, gate_env):
        """An Edit whose anchor is absent writes nothing, so the gate allows.

        The payload carries a pin comment, which the retired fragment
        comparison read as an add. The document comparison reads what the edit
        WRITES: the anchor occurs zero times, the replacement changes nothing,
        the two documents are byte-identical, and no pin arrives.
        """
        env = gate_env(marker_present=True)
        current = env["claude_md"].read_text(encoding="utf-8")
        anchor = "some text"
        assert current.count(anchor) == 0, (
            "this arm needs an anchor the fixture does NOT carry"
        )
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": anchor,
                "new_string": "<!-- pinned: 2026-04-20 -->\n### X\nbody",
            },
        })
        assert result is None

    def test_write_increasing_pin_count_denied(self, gate_env):
        """Write replacement with MORE pin comments than current → deny.

        The replacement injects the new pin comment INSIDE the managed
        region (before `## Working Memory`) so the Arch-M3 bounding in
        `_count_pin_comments` (via extract_managed_region) observes the
        increase. Appending the pin AFTER `<!-- PACT_MANAGED_END -->`
        would be ignored by the bounded count — the gate would allow
        and this test would pass for the wrong reason (phantom-green).
        """
        env = gate_env(marker_present=True)
        current = env["claude_md"].read_text(encoding="utf-8")
        # current has exactly 1 pin (from make_claude_md_with_pins in fixture);
        # build a replacement with 2 pins INSIDE the managed region.
        new_pin = "<!-- pinned: 2026-04-20 -->\n### New Pin\nbody\n\n"
        replacement = current.replace(
            "## Working Memory\n",
            f"{new_pin}## Working Memory\n",
        )
        # Sanity: the replacement actually differs and the new pin is
        # inside the managed region.
        assert replacement != current
        from shared.claude_md_manager import extract_managed_region
        region_result = extract_managed_region(replacement)
        assert region_result is not None, (
            "phantom-green guard: factory must emit canonical markers"
        )
        region_text, _ = region_result
        assert region_text.count("<!-- pinned:") == 2
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": replacement,
            },
        })
        assert result is not None
        assert "stale pins" in result


class TestPinStalenessGate_PathMiss:
    """Marker present but file_path does NOT match project CLAUDE.md → allow."""

    def test_edit_on_unrelated_file_allowed(self, gate_env, tmp_path):
        gate_env(marker_present=True)
        other = tmp_path / "README.md"
        other.write_text("readme", encoding="utf-8")
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(other),
                "old_string": "## Pinned Context",
                "new_string": "## Pinned Context\nnope",
            },
        })
        assert result is None

    def test_edit_with_empty_file_path_allowed(self, gate_env):
        gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": "",
                "old_string": "a",
                "new_string": "b",
            },
        })
        assert result is None

    def test_edit_with_missing_file_path_allowed(self, gate_env):
        gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {"old_string": "a", "new_string": "b"},
        })
        assert result is None


class TestPinStalenessGate_NonTouchingEdit:
    """Marker present, path match, but edit does NOT touch pinned section → allow."""

    def test_edit_elsewhere_in_claude_md_allowed(self, gate_env):
        env = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "## Working Memory",
                "new_string": "## Working Memory\nnew",
            },
        })
        assert result is None


class TestPinStalenessGate_TeammateBypass:
    """Teammates bypass the gate (worktree scope — no CLAUDE.md in worktrees)."""

    def test_teammate_edit_on_claude_md_allowed(self, gate_env, monkeypatch):
        """#878: a non-lead agent_type bypasses the gate. The gate keys on
        is_lead (agent_type), not resolve_agent_name."""
        env = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "agent_type": "pact-backend-coder",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "## Pinned Context",
                "new_string": "## Pinned Context\nteammate edit",
            },
        })
        assert result is None


class TestPinStalenessGate_FailOpen:
    """SACROSANCT: any exception in gate logic → allow (fail-open)."""

    def test_session_dir_none_allows(self, gate_env, monkeypatch):
        gate_env(marker_present=True)
        import shared.pact_context as ctx_module
        monkeypatch.setattr(ctx_module, "get_session_dir", lambda: None)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {"file_path": "foo", "content": "bar"},
        })
        assert result is None

    def test_tool_input_not_dict_allowed(self, gate_env):
        gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": "malformed-string-not-dict",
        })
        assert result is None

    def test_claude_md_resolution_none_allows(self, gate_env, monkeypatch):
        env = gate_env(marker_present=True)
        import staleness
        monkeypatch.setattr(
            staleness, "get_project_claude_md_path", lambda: None
        )
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "## Pinned Context",
                "new_string": "## Pinned Context\n",
            },
        })
        assert result is None

    def test_main_malformed_stdin_suppresses_output(self, monkeypatch, capsys):
        """Malformed stdin → exit 0 with {"suppressOutput": true}."""
        from io import StringIO
        import pin_staleness_gate
        monkeypatch.setattr(sys, "stdin", StringIO("not-json"))
        with pytest.raises(SystemExit) as exc_info:
            pin_staleness_gate.main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out.strip()
        assert json.loads(out) == {"suppressOutput": True}

    def test_main_internal_exception_suppresses_output(
        self, gate_env, monkeypatch, capsys
    ):
        """Exception inside _check_tool_allowed → exit 0 fail-open."""
        from io import StringIO
        import pin_staleness_gate
        gate_env(marker_present=True)
        monkeypatch.setattr(
            pin_staleness_gate, "_check_tool_allowed",
            lambda _x: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(sys, "stdin", StringIO(json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": "x", "old_string": "a", "new_string": "b"},
        })))
        with pytest.raises(SystemExit) as exc_info:
            pin_staleness_gate.main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out.strip()
        assert json.loads(out) == {"suppressOutput": True}


class TestPinStalenessGate_MainDenyPath:
    """Main emits permissionDecision=deny + exit 2 on positive detection."""

    def test_main_denies_write_increasing_pin_count(
        self, gate_env, monkeypatch, capsys
    ):
        from io import StringIO
        import pin_staleness_gate
        env = gate_env(marker_present=True)
        current = env["claude_md"].read_text(encoding="utf-8")
        # Write adds a net-new pin comment INSIDE the managed region →
        # ADD shape under Arch-M3 bounding → deny. Appending outside the
        # managed region would be ignored by the bounded count (see
        # test_write_increasing_pin_count_denied rationale).
        new_pin = "<!-- pinned: 2026-04-20 -->\n### New Pin\nbody\n\n"
        replacement = current.replace(
            "## Working Memory\n",
            f"{new_pin}## Working Memory\n",
        )
        assert replacement != current
        # #878: lead frame (agent_type) so the is_lead-gated DENY path fires.
        monkeypatch.setattr(sys, "stdin", StringIO(json.dumps({
            "tool_name": "Write",
            "agent_type": "pact-orchestrator",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": replacement,
            },
        })))
        with pytest.raises(SystemExit) as exc_info:
            pin_staleness_gate.main()
        assert exc_info.value.code == 2
        out = capsys.readouterr().out.strip()
        payload = json.loads(out)
        hso = payload["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "deny"
        assert "stale pins" in hso["permissionDecisionReason"]


class TestPinStalenessGate_Archival:
    """Regression: marker armed + /PACT:pin-memory archival edit → ALLOW.

    Reviewer-security F1 (#492 Cycle 1): same-session marker livelock.
    The original _edit_touches_pinned_section did a substring check for
    `<!-- pinned:` in combined old/new; ANY archival edit (whose
    old_string contains the substring because a pin is being removed)
    matched and was denied. The user could never resolve the stale-pins
    condition within the session. Fix: gate only ADD-shaped edits
    (new pin count > old pin count).
    """

    def test_archival_edit_allowed(self, gate_env):
        """old_string has a pin comment; new_string does not → archive → allow."""
        env = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": (
                    "<!-- pinned: 2026-01-01 -->\n### Stale\nold body\n"
                ),
                "new_string": "",
            },
        })
        assert result is None, (
            "Archival edits must not be blocked — user needs this path "
            "to resolve stale-pins condition within the same session "
            "(F1 livelock fix)."
        )

    def test_archival_edit_single_pin_removal_allowed(self, gate_env):
        """Strict pin count decrease → archive → allow."""
        env = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": (
                    "<!-- pinned: 2026-01-01 -->\n### A\nbody\n"
                    "<!-- pinned: 2026-02-01 -->\n### B\nbody\n"
                ),
                "new_string": (
                    "<!-- pinned: 2026-02-01 -->\n### B\nbody\n"
                ),
            },
        })
        assert result is None

    def test_refactor_edit_unchanged_pin_count_allowed(self, gate_env):
        """Pin body rewrite without count change → refactor → allow."""
        env = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": (
                    "<!-- pinned: 2026-04-20 -->\n### X\nold body\n"
                ),
                "new_string": (
                    "<!-- pinned: 2026-04-20 -->\n### X\nnew body\n"
                ),
            },
        })
        assert result is None

    def test_boundary_marker_touch_without_pin_add_allowed(self, gate_env):
        """Touching PACT_MEMORY_START without adding a pin → allow.

        The old substring matcher denied any edit that mentioned the
        memory boundary marker, which would block migrations and
        restructuring. Under the ADD-only contract, this is a refactor.
        """
        env = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "<!-- PACT_MEMORY_START -->",
                "new_string": "<!-- PACT_MEMORY_START -->\nextra",
            },
        })
        assert result is None

    def test_stale_marker_injection_refactor_allowed(self, gate_env):
        """SessionStart staleness.apply_staleness_markings-shaped edit → allow.

        staleness.py inserts <!-- STALE: ... --> markers into existing
        pins. This is a refactor: pin count unchanged. MUST not be
        blocked or the hook self-deadlocks on its own detection pass.
        """
        env = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": (
                    "<!-- pinned: 2026-01-01 -->\n### A\nbody\n"
                ),
                "new_string": (
                    "<!-- pinned: 2026-01-01 -->\n"
                    "<!-- STALE: Last relevant 2026-01-01 -->\n"
                    "### A\nbody\n"
                ),
            },
        })
        assert result is None

    def test_write_archival_via_shorter_content_allowed(self, gate_env):
        """Write replacement with FEWER pin comments than current → allow."""
        env = gate_env(marker_present=True)
        # Fixture CLAUDE.md has 1 pin; replacement has 0.
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": "# Header\n\n## Pinned Context\n\n## Working Memory\n",
            },
        })
        assert result is None

    def test_write_refactor_same_pin_count_allowed(self, gate_env):
        """Write replacement with SAME pin count → refactor → allow."""
        env = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": (
                    "# Header\n\n## Pinned Context\n\n"
                    "<!-- pinned: 2026-04-20 -->\n### Rewritten\nnew body\n\n"
                    "## Working Memory\n"
                ),
            },
        })
        assert result is None

    def test_write_fails_open_on_unreadable_current(
        self, gate_env, monkeypatch
    ):
        """If current CLAUDE.md cannot be read → fail-open (allow).

        The Write-shape path depends on reading the current file to diff
        pin counts. Any read error MUST return allow per SACROSANCT gate
        invariant — not deny-by-default.
        """
        env = gate_env(marker_present=True)

        # Monkey-patch Path.read_text to raise IOError specifically for
        # the project CLAUDE.md. Identity-scoped so unrelated reads
        # (tmp paths, marker file) aren't affected.
        original_read_text = Path.read_text
        target = env["claude_md"].resolve()

        def _raising_read_text(self, *args, **kwargs):
            if self.resolve() == target:
                raise IOError("simulated unreadable CLAUDE.md")
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _raising_read_text)

        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": (
                    "## Pinned Context\n\n"
                    "<!-- pinned: 2026-04-20 -->\n### A\nbody\n"
                    "<!-- pinned: 2026-04-20 -->\n### B\nbody\n"
                ),
            },
        })
        assert result is None


class TestPinStalenessGate_DecoyBypass:
    """Arch-M3 managed-region bounding of `_count_pin_comments`.

    Load-bearing coverage for the bounded-count defense, which lives in
    `_counts_show_an_add`: it asks `extract_managed_region` for a slice and
    counts inside it when the two sides agree that the markers are present.

    CITED BY SYMBOL RATHER THAN BY LINE NUMBER, and that is deliberate. This
    docstring named `pin_staleness_gate.py:128-138` and that range now holds
    the fail-closed import wrapper, while `extract_managed_region` is imported
    function-local inside `_counts_show_an_add`. A line number moves whenever
    somebody edits above it, so it goes stale on an unrelated change and sends
    the next reader to the wrong mechanism. A symbol moves only on a rename.

    Before this defense, a `<!-- pinned:` token appearing in
    user-authored prose or a fenced code block OUTSIDE the managed
    region would inflate the gate's count and either:
      - falsely BLOCK a legitimate pin edit (add-shape), or
      - falsely ALLOW a net-new pin while an outside decoy was
        simultaneously archived (same full-text count, different
        structural reality).

    Counter-test-by-revert, STATED AS AN ABLATION OF A NAMED BRANCH so it
    survives an edit: make `_counts_show_an_add` skip its managed-region
    branch, so the two sides fall through to the whole-text slice. That MUST
    cause at least one test here to fail. Without that proof, the defense is
    phantom-green.
    """

    def test_decoy_outside_region_does_not_inflate_count(self, gate_env):
        """Write with same in-region pin count + new OUTSIDE decoy → allow.

        Current on-disk file: 1 pin inside the managed region, 0 decoys
        outside. Write payload: 1 pin inside the managed region, 1
        decoy `<!-- pinned:` in user-authored prose AFTER
        MANAGED_END_MARKER.

        Arch-M3 bounded count: both current and new see exactly 1 pin
        → no ADD → allow. Reverted (full-text) count: current=1,
        new=2 → ADD → deny.

        If this test fails after reverting the bounding, the defense
        is load-bearing.
        """
        from shared.claude_md_manager import MANAGED_END_MARKER
        env = gate_env(marker_present=True)
        current = env["claude_md"].read_text(encoding="utf-8")
        # Inject the decoy AFTER the managed-region end marker — this
        # lives in user-authored prose territory where outside-region
        # tokens must be ignored by the gate.
        assert MANAGED_END_MARKER in current
        decoy_outside = (
            "\n## User Notes\n\n"
            "Here is some prose explaining what `<!-- pinned: 2020-01-01 -->` "
            "used to mean in the legacy format.\n"
        )
        replacement = current + decoy_outside
        # Sanity: the decoy is structurally outside the managed region.
        end_idx = replacement.find(MANAGED_END_MARKER)
        decoy_idx = replacement.rfind("<!-- pinned:")
        assert decoy_idx > end_idx, (
            "decoy must be after MANAGED_END_MARKER for this test to exercise "
            "the outside-region code path"
        )
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": replacement,
            },
        })
        assert result is None, (
            "Outside-region decoy inflated count — Arch-M3 bounding bypassed. "
            "If you see this failure after reverting pin_staleness_gate.py "
            "lines 120-127, that is the counter-test proof the defense is "
            "load-bearing."
        )

    def test_add_inside_while_removing_decoy_outside_still_denies(
        self, gate_env
    ):
        """In-region ADD while outside decoy is removed → must DENY.

        Symmetry probe: the full-text count is unchanged (1 → 1), but
        the structural count inside the managed region goes 1 → 2.
        Arch-M3 bounding detects the real ADD; the reverted full-text
        count would see net-zero and allow (false-allow).

        Current: 1 in-region pin + 1 outside decoy (total=2).
        New:     2 in-region pins + 0 outside decoys (total=2).
        """
        from shared.claude_md_manager import MANAGED_END_MARKER
        env = gate_env(marker_present=True)
        original = env["claude_md"].read_text(encoding="utf-8")
        assert MANAGED_END_MARKER in original
        # Seed the current file with an outside-region decoy.
        seeded = original + (
            "\n## User Notes\n\n"
            "Legacy reference: `<!-- pinned: 2020-01-01 -->` in prose.\n"
        )
        env["claude_md"].write_text(seeded, encoding="utf-8")
        # Build replacement: add a second pin INSIDE the managed
        # region, and drop the outside decoy entirely.
        new_pin = "<!-- pinned: 2026-04-20 -->\n### New Pin\nbody\n\n"
        replacement = original.replace(
            "## Working Memory\n",
            f"{new_pin}## Working Memory\n",
        )
        # Sanity: full-text counts unchanged across seeded vs replacement.
        assert seeded.count("<!-- pinned:") == replacement.count(
            "<!-- pinned:"
        ), "full-text count must match so the revert sees no ADD"
        # Sanity: bounded counts differ (1 → 2).
        from shared.claude_md_manager import extract_managed_region
        seeded_region_text, _ = extract_managed_region(seeded)
        replacement_region_text, _ = extract_managed_region(replacement)
        assert seeded_region_text.count("<!-- pinned:") == 1
        assert replacement_region_text.count("<!-- pinned:") == 2
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": replacement,
            },
        })
        assert result is not None, (
            "In-region ADD masked by outside decoy removal. The managed-region "
            "bounding was bypassed. If you see this failure after ablating the "
            "managed-region branch of `_counts_show_an_add`, that is the "
            "counter-test proof the defense is load-bearing."
        )
        assert "stale pins" in result

    def test_edit_fragment_without_markers_uses_full_text_count(
        self, gate_env
    ):
        """Edit fragment (no markers) falls through to full-text count.

        Edit.old_string and Edit.new_string are typically raw fragments
        that do not carry the PACT_MANAGED_START/END markers — they are
        structurally INSIDE the managed region by virtue of the section
        being edited. `_count_pin_comments` must fall through to full-text
        parse_pins on these, otherwise a net-new pin added via Edit would
        be invisible (extract_managed_region returns None → bounded count
        fails → else-branch must cover it).

        Post-symmetric-oracle: fragment counts now use
        `len(parse_pins(text))` directly. Since parse_pins treats bare
        `### Heading` as a Pin, `### Existing\\nbody\\n` parses as 1
        pin (not 0). The gate still denies net-new adds because the
        OLD fragment and NEW fragment both parse consistently: a fragment
        with ONLY a bare `### Existing` counts 1; a fragment with a
        `<!-- pinned: -->\\n### New` ALSO counts 1 (comment + heading
        form one Pin). Adding a net-new heading anywhere raises the
        count symmetrically.
        """
        import pin_staleness_gate
        gate_env(marker_present=True)
        old_fragment = "### Existing\nbody\n"
        new_fragment = "<!-- pinned: 2026-04-20 -->\n### New\nbody\n"
        # Neither fragment contains MANAGED_START_MARKER.
        from shared.claude_md_manager import MANAGED_START_MARKER
        assert MANAGED_START_MARKER not in old_fragment
        assert MANAGED_START_MARKER not in new_fragment
        # Fall-through to full-text parse_pins MUST return the parse_pins
        # count on the fragment. Both fragments parse as 1 pin: a bare
        # `### Heading` and a `<!-- pinned: -->\n### Heading` are both
        # valid Pin shapes under parse_pins (this is the symmetric-oracle
        # property — BareHeadingBypass and WhitespaceVariant tests below
        # exercise the cross-fragment delta that matters for the gate).
        assert pin_staleness_gate._count_pin_comments(old_fragment) == 1
        assert pin_staleness_gate._count_pin_comments(new_fragment) == 1


class TestPinStalenessGate_CaseInsensitivity:
    """`_count_pin_comments` must match pin-comment markers case-insensitively.

    Asymmetry guard: `pin_caps.OVERRIDE_COMMENT_RE` and the sibling
    pin-comment regexes in pin_caps.py use `re.IGNORECASE`, so
    `parse_pins` treats `<!-- PINNED:`, `<!-- Pinned:`, and
    `<!-- pInNeD:` as valid pin comments. A case-sensitive
    `.count("<!-- pinned:")` in the gate under-counts against what
    parse_pins produces, letting a user slip past the gate with an
    upper-case marker while the cap check still sees the pin.

    Counter-test-by-revert: reverting the case-insensitive count in
    `_count_pin_comments` (line 125 / 128) to `text.count("<!-- pinned:")`
    causes these tests to fail because mixed-case markers are not
    matched.
    """

    def test_count_pin_comments_matches_uppercase_marker(self):
        """`<!-- PINNED:` in a fragment → counted as 1."""
        import pin_staleness_gate
        fragment = "<!-- PINNED: 2026-04-20 -->\n### X\nbody\n"
        assert pin_staleness_gate._count_pin_comments(fragment) == 1

    def test_count_pin_comments_matches_titlecase_marker(self):
        """`<!-- Pinned:` in a fragment → counted as 1."""
        import pin_staleness_gate
        fragment = "<!-- Pinned: 2026-04-20 -->\n### X\nbody\n"
        assert pin_staleness_gate._count_pin_comments(fragment) == 1

    def test_count_pin_comments_matches_mixed_case_marker(self):
        """`<!-- pInNeD:` (alternating case) → counted as 1."""
        import pin_staleness_gate
        fragment = "<!-- pInNeD: 2026-04-20 -->\n### X\nbody\n"
        assert pin_staleness_gate._count_pin_comments(fragment) == 1

    def test_count_pin_comments_sums_mixed_case_markers(self):
        """Lowercase + uppercase + mixed in one text → counted as 3."""
        import pin_staleness_gate
        fragment = (
            "<!-- pinned: 2026-01-01 -->\n### A\n"
            "<!-- PINNED: 2026-02-01 -->\n### B\n"
            "<!-- pInNeD: 2026-03-01 -->\n### C\n"
        )
        assert pin_staleness_gate._count_pin_comments(fragment) == 3

    def test_gate_denies_write_adding_uppercase_pin(self, gate_env):
        """End-to-end: Write adding an uppercase `<!-- PINNED:` → deny.

        With the case-sensitive bug, the gate's bounded count would see
        current=1 and new=1 (upper-case pin invisible) → no ADD → allow.
        With the case-insensitive fix, the gate sees current=1 and
        new=2 → ADD → deny. This probes the pin_caps ↔ gate asymmetry
        through the real decision path.
        """
        env = gate_env(marker_present=True)
        current = env["claude_md"].read_text(encoding="utf-8")
        new_pin = "<!-- PINNED: 2026-04-20 -->\n### Loud Pin\nbody\n\n"
        replacement = current.replace(
            "## Working Memory\n",
            f"{new_pin}## Working Memory\n",
        )
        assert replacement != current
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": replacement,
            },
        })
        assert result is not None, (
            "Upper-case `<!-- PINNED:` slipped past the gate — the "
            "case-sensitive `.count(\"<!-- pinned:\")` under-counts "
            "vs parse_pins (which is IGNORECASE). Fix in "
            "pin_staleness_gate.py:_count_pin_comments."
        )
        assert "stale pins" in result


class TestPinStalenessGate_BareHeadingBypass:
    """Symmetric-oracle defense: bare `### Heading`
    adds with no preceding date comment MUST DENY.

    Before that fix, `_count_pin_comments` used a regex substring count
    of `<!-- pinned:` tokens. This was asymmetric with `parse_pins`,
    which recognizes a bare `### Heading` (no preceding date comment)
    as a Pin. An adversarial ADD of a level-3 heading-only pin landed
    as a parse_pins-visible pin on reload — yet the gate saw zero
    `<!-- pinned:` tokens and let the ADD through while the cap-check
    CLI simultaneously counted the pin against the 12-pin quota.

    Fix: `_count_pin_comments` now calls `parse_pins` directly, so the
    gate and the cap-check share one oracle. ADDing a bare `### Heading`
    raises the parse_pins count by 1 → gate denies.

    Counter-test-by-revert: reverting the symmetric-oracle fix (restoring the regex
    substring count) MUST cause these tests to FAIL. A revert that
    leaves them passing is phantom-green coverage.
    """

    def test_edit_adding_bare_heading_without_comment_denied(self, gate_env):
        """Edit: old_string has no pin comment, new_string adds bare `### Smuggled`
        with body → gate DENIES (parse_pins sees +1 pin).

        This is the load-bearing bypass: a curator with the
        stale-pins-pending marker armed could previously smuggle a
        net-new pin past the gate by skipping the date comment line.
        """
        env = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "## Working Memory\n",
                "new_string": "### Smuggled Pin\nsmuggled body\n\n## Working Memory\n",
            },
        })
        assert result is not None, (
            "Bare `### Heading` ADD slipped past the gate — the "
            "pre-symmetric-oracle regex substring count missed bare "
            "headings. If you see this failure after reverting "
            "the symmetric-oracle fix (restoring the substring count), "
            "that is counter-test proof the symmetric-oracle defense "
            "is load-bearing."
        )
        assert "stale pins" in result

    def test_write_adding_bare_heading_without_comment_denied(self, gate_env):
        """Write: full-file replacement adds a bare `### Smuggled` in the
        managed region → gate DENIES (parse_pins sees +1 pin).

        Write-path twin of the Edit case above. Exercises the same
        asymmetry via the Write-shape branch of `_is_add_shaped_edit`.
        """
        env = gate_env(marker_present=True)
        current = env["claude_md"].read_text(encoding="utf-8")
        # Inject a bare heading inside the managed region.
        bare_pin = "### Smuggled Pin\nsmuggled body\n\n"
        replacement = current.replace(
            "## Working Memory\n",
            f"{bare_pin}## Working Memory\n",
        )
        assert replacement != current
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": replacement,
            },
        })
        assert result is not None, (
            "Write-path bare-heading ADD bypassed the gate. "
            "Revert-counter-test on the symmetric-oracle fix must fail."
        )
        assert "stale pins" in result

    def test_count_pin_comments_counts_bare_heading_as_pin(self):
        """Direct oracle assertion: `### Heading\\nbody` counts as 1 pin.

        Parses the new oracle behavior in isolation — independent of the
        gate decision path. If this assertion fails, the test above will
        fail too (cause vs. effect); this test isolates the cause.
        """
        import pin_staleness_gate
        fragment = "### Smuggled\nbody\n"
        assert pin_staleness_gate._count_pin_comments(fragment) == 1, (
            "parse_pins treats a bare `### Heading` as a Pin; "
            "_count_pin_comments must agree (symmetric oracle). Under "
            "the pre-symmetric-oracle regex substring count, this returned 0."
        )


class TestPinStalenessGate_WhitespaceVariant:
    """Symmetric-oracle defense: whitespace-tolerant
    pin markers (`<!--  pinned:` with double-space, tabs, leading spaces)
    MUST count toward the gate as parse_pins counts them.

    Before that fix, `_count_pin_comments` used a literal substring
    count of `<!-- pinned:` (case-insensitive via regex flag, but with
    EXACTLY one space before `pinned:`). parse_pins tolerates
    `<!--\\s*pinned:` — two spaces, a tab, any whitespace run. A
    curator smuggling a pin with `<!--  pinned: 2026-04-20 -->` (double
    space) landed as a parse_pins-visible pin on reload but was invisible
    to the substring-count gate.

    Fix: _count_pin_comments delegates to parse_pins, which uses the
    whitespace-tolerant regex. Gate + cap-check now agree.

    Counter-test-by-revert: reverting the symmetric-oracle fix MUST cause these tests
    to FAIL. If they pass after a revert, the defense is phantom-green.
    """

    def test_edit_adding_double_space_marker_denied(self, gate_env):
        """Edit: adding `<!--  pinned:` (double space) → DENIES.

        parse_pins matches `<!--\\s*pinned:` → 1 new pin. Old substring
        count of `<!-- pinned:` (single space) → 0 new pins → bypass.
        """
        env = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "## Working Memory\n",
                "new_string": (
                    "<!--  pinned: 2026-04-20 -->\n"  # double-space bypass
                    "### Smuggled Pin\nbody\n\n"
                    "## Working Memory\n"
                ),
            },
        })
        assert result is not None, (
            "Double-space `<!--  pinned:` ADD slipped past the gate — "
            "the pre-symmetric-oracle substring count required exactly "
            "one space. If you see this failure after reverting "
            "the symmetric-oracle fix, the defense is load-bearing."
        )
        assert "stale pins" in result

    def test_write_adding_double_space_marker_denied(self, gate_env):
        """Write: adding `<!--  pinned:` in the managed region → DENIES."""
        env = gate_env(marker_present=True)
        current = env["claude_md"].read_text(encoding="utf-8")
        # Double-space marker — bypass under old oracle.
        new_pin = (
            "<!--  pinned: 2026-04-20 -->\n"
            "### Smuggled\nbody\n\n"
        )
        replacement = current.replace(
            "## Working Memory\n",
            f"{new_pin}## Working Memory\n",
        )
        assert replacement != current
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": replacement,
            },
        })
        assert result is not None, (
            "Double-space `<!--  pinned:` ADD via Write bypassed the "
            "gate. Revert-counter-test on the symmetric-oracle fix must fail here."
        )
        assert "stale pins" in result

    def test_count_pin_comments_counts_whitespace_variants(self):
        """Direct oracle assertion: whitespace-variant markers count as pins.

        Isolates the cause from the effect: if parse_pins tolerates
        `<!--  pinned:`, `<!--\\tpinned:` and so on, the gate must see
        the same count. Independent of the gate decision path.
        """
        import pin_staleness_gate
        # Double-space preceding `pinned:`
        double_space = "<!--  pinned: 2026-04-20 -->\n### X\nbody\n"
        assert pin_staleness_gate._count_pin_comments(double_space) == 1, (
            "Double-space `<!--  pinned:` did not count. "
            "Pre-symmetric-oracle substring count required exactly one space."
        )
        # Tab after `<!--`
        tab_sep = "<!--\tpinned: 2026-04-20 -->\n### Y\nbody\n"
        assert pin_staleness_gate._count_pin_comments(tab_sep) == 1, (
            "Tab-separated `<!--\\tpinned:` did not count."
        )
        # No space at all (parse_pins \s* permits zero whitespace too)
        no_space = "<!--pinned: 2026-04-20 -->\n### Z\nbody\n"
        assert pin_staleness_gate._count_pin_comments(no_space) == 1, (
            "Zero-space `<!--pinned:` did not count."
        )


class TestPinStalenessGate_FailOpenIsReported:
    """The three broad catches must EMIT when they fail open.

    WHY THESE ARMS EXIST, and it is one level up from the usual reason. The
    emit turns a silent catch into a loud one, so a defect in this gate stops
    presenting as total permissiveness with no signal. THE EMIT ITSELF WAS
    UNARMED: a later edit could delete it and no arm reddened, which is the
    same class of silent loss that the emit exists to catch.

    THE OBJECT DRIVEN IS IN PROCESS FOR ALL THREE, and the capture is `capsys`.
    A subprocess drive would carry the shipped entry point, and it cannot
    inject a defect without a mutated tree, so the injection point decides the
    object here. State the object beside the result: `_count_pin_comments` for
    the count site, `_is_add_shaped_edit` for the shape site, and `main` for
    the decision site.

    EACH ARM ASSERTS THE VERDICT AS WELL AS THE MESSAGE. An arm that reads only
    stderr passes a change that reports correctly and refuses wrongly.

    EACH ARM CARRIES A DISTINCT STAGE NAME, so an arm cannot pass on the emit
    of another site. That is what makes the three separable rather than three
    readings of one.
    """

    def test_healthy_path_emits_nothing(self, gate_env, capsys):
        """THE CONTROL. With no defect present, stderr stays EMPTY.

        Without this arm the three below cannot show that the DEFECT produced
        the message. A gate that emitted on each call would satisfy them all.
        """
        env = gate_env(marker_present=True)
        import pin_staleness_gate
        current = env["claude_md"].read_text(encoding="utf-8")
        verdict = pin_staleness_gate._is_add_shaped_edit(
            {"file_path": str(env["claude_md"]),
             "old_string": "### Pin\nxxxx",
             "new_string": "### Pin\nyyyy"},
            env["claude_md"],
            "Edit",
        )
        assert verdict is False, "the reword must stay allowed"
        assert current  # the fixture carried content
        captured = capsys.readouterr()
        assert captured.err == "", (
            "the healthy path emitted on stderr, so a message below proves "
            f"nothing about a defect: {captured.err!r}"
        )

    def test_count_site_reports_when_the_oracle_raises(self, monkeypatch, capsys):
        """SITE 1, object driven = `_count_pin_comments`, fail-open value 0."""
        import pin_staleness_gate

        def _raise(_text):
            raise RuntimeError("rv2test count defect")

        monkeypatch.setattr(pin_staleness_gate, "parse_pins", _raise)
        result = pin_staleness_gate._count_pin_comments("<!-- pinned: 2026-04-20 -->\n### X\nb")
        assert result == 0, "the count site must keep its fail-open value"
        err = capsys.readouterr().err
        assert "pin count" in err, (
            "the pin-count catch failed open in SILENCE. Restore the "
            f"_report_fail_open call at that catch. stderr was: {err!r}"
        )
        assert "RuntimeError" in err and "rv2test count defect" in err

    def test_shape_site_reports_when_the_simulation_raises(
        self, gate_env, monkeypatch, capsys
    ):
        """SITE 2, object driven = `_is_add_shaped_edit`, fail-open value False.

        The injected error is a NameError because that is the shape of the
        regression this emit was built for: a rename left a return that named
        variables no longer present, and the catch returned the quiet value for
        each payload with nothing reported.
        """
        env = gate_env(marker_present=True)
        import pin_staleness_gate

        def _raise(*_args, **_kwargs):
            raise NameError("rv2test shape defect")

        monkeypatch.setattr(
            pin_staleness_gate, "_simulate_post_edit_document", _raise
        )
        verdict = pin_staleness_gate._is_add_shaped_edit(
            {"file_path": str(env["claude_md"]),
             "old_string": "### Pin\nxxxx",
             "new_string": "### Pin\nxxxx<!-- pinned: 2026-05-01 -->\n### Y\nb"},
            env["claude_md"],
            "Edit",
        )
        assert verdict is False, "the shape site must keep its fail-open value"
        err = capsys.readouterr().err
        assert "add-shape detection" in err, (
            "the add-shape catch failed open in SILENCE. This is the catch "
            "that swallowed a NameError and killed the whole Edit path. "
            f"stderr was: {err!r}"
        )
        assert "NameError" in err and "rv2test shape defect" in err

    def test_decision_site_reports_when_the_gate_raises(
        self, monkeypatch, capsys
    ):
        """SITE 3, object driven = `main`, fail-open value exit code 0."""
        import io

        import pin_staleness_gate

        def _raise(_input_data):
            raise RuntimeError("rv2test decision defect")

        monkeypatch.setattr(pin_staleness_gate, "_check_tool_allowed", _raise)
        monkeypatch.setattr(
            sys, "stdin", io.StringIO(json.dumps({"tool_name": "Edit"}))
        )
        with pytest.raises(SystemExit) as exit_info:
            pin_staleness_gate.main()
        assert exit_info.value.code == 0, (
            "the decision site must keep its fail-open exit code"
        )
        captured = capsys.readouterr()
        assert "gate decision" in captured.err, (
            "the outermost catch failed open in SILENCE. Restore the "
            f"_report_fail_open call in main. stderr was: {captured.err!r}"
        )
        assert "RuntimeError" in captured.err
        assert "suppressOutput" in captured.out, (
            "the hook protocol line must stay on stdout"
        )


class TestPinStalenessGate_SimulationEditEdges:
    """THE EDIT EDGES OF `_simulate_post_edit_document`.

    It reads the shared simulation, `shared.edit_simulation.simulate`:
      `replace_all` TRUE  -> replace each occurrence.
      `replace_all` FALSE -> replace the first occurrence.
      an EMPTY `old_string` on a file that holds text -> return the PRE-state,
        because the tool refuses that edit. (On a blank file it creates or
        fills the file; see `TestPinStalenessGate_SharedSimulation`.)

    WHY THESE ARMS READ THE SIMULATION DIRECTLY RATHER THAN THE VERDICT.
    Driven through the whole decision, an empty-`old_string` arm can pass
    because the insertion landed OUTSIDE the counted slice rather than because
    the guard refused it. The reviewer that found this gap paid for that
    lesson on its own first probe. A slice bound and a payload guard are two
    mechanisms, and an arm that cannot say which one answered is holding
    neither. The simulation is the unit that owns these three edges, so it is
    the unit these arms drive.
    """

    def test_an_empty_old_string_on_a_file_with_text_returns_the_pre_state(self):
        """The edit the tool refuses, held by identity against the input.

        The Edit tool refuses an empty `old_string` on a file that holds text,
        so the file is unchanged and the caller compares pre against pre.
        `str.replace` with an empty needle would instead put the replacement
        BETWEEN each character, a document the tool never produces.

        NON-VACUITY: identity with `current` is a strong oracle here, because
        the mutated form produces a document that is longer than the input by
        one copy of `new_string` for each character position. There is no
        quiet answer that satisfies this by accident.
        """
        import pin_staleness_gate

        current = "# P\n\n## Pinned Context\n\n<!-- pinned: 2026-01-01 -->\n"
        simulated = pin_staleness_gate._simulate_post_edit_document(
            {"old_string": "", "new_string": "<!-- pinned: 2026-02-02 -->\n"},
            current,
            "Edit",
        )

        assert simulated == current, (
            "AN EMPTY `old_string` DID NOT RETURN THE PRE-STATE. "
            "`str.replace` with an empty needle interleaves the replacement "
            "between every character, so the gate would then compare the "
            "pre-state against a document the tool cannot produce. The tool "
            "refuses this edit on a file that holds text, so the gate must "
            "judge the file unchanged.\n"
            f"  length in: {len(current)}   length out: {len(simulated or '')}"
        )

    def test_the_replace_all_flag_selects_between_each_and_the_first(self):
        """The flag is READ, proven by the two answers it selects between.

        ONE CALL CANNOT HOLD THIS EDGE. An arm that asserts only the
        `replace_all=True` output passes when the flag is ignored and the
        document happens to carry one occurrence. So the fixture carries TWO
        occurrences and the arm asserts that the two flag values give
        DIFFERENT documents, then pins each one.
        """
        import pin_staleness_gate

        current = "MARK\nMARK\n"
        payload = {"old_string": "MARK", "new_string": "DONE"}

        each = pin_staleness_gate._simulate_post_edit_document(
            {**payload, "replace_all": True}, current, "Edit")
        first = pin_staleness_gate._simulate_post_edit_document(
            {**payload, "replace_all": False}, current, "Edit")

        assert each != first, (
            "THE `replace_all` FLAG SELECTED NOTHING. The two flag values "
            "produced the identical document on a fixture carrying two "
            "occurrences, so the flag is ignored and the simulation no longer "
            "models what the Edit tool will do"
        )
        assert each == "DONE\nDONE\n", (
            f"`replace_all` TRUE must replace each occurrence, and it gave "
            f"{each!r}"
        )
        assert first == "DONE\nMARK\n", (
            f"`replace_all` FALSE must replace the first occurrence only, and "
            f"it gave {first!r}"
        )

    def test_the_caller_reads_the_simulated_document_and_not_the_payload(
        self, tmp_path
    ):
        """THE LEG THE TWO ARMS ABOVE CANNOT REACH.

        Those two drive `_simulate_post_edit_document` DIRECTLY, which takes
        the slice bound out of the circuit and is why they hold the guard
        rather than the bound. THE COST OF THAT CHOICE, NAMED BY THE REVIEWER
        THAT FOUND THE ORIGINAL GAP: neither arm can see whether
        `_is_add_shaped_edit` CALLS the simulation at all. A change that
        bypasses the call, or that counts the payload rather than the
        simulated document, leaves the two of them green.

        This arm closes that leg WITHOUT giving back the slice-bound exposure,
        because the document is chosen so the bound cannot answer for the
        guard.

        THE DOCUMENT MATTERS AND HERE IS WHY. It carries NO `## Pinned
        Context` heading and NO managed markers, so the counted slice is the
        WHOLE TEXT and an insertion at position 0 falls INSIDE it. On a
        document that HAS a pinned heading, the empty `old_string` inserts
        above the pinned span, the bound excludes it, and shipped and mutated
        agree. That shape reports a no-op and hides a true gap. It cost the
        reviewer one probe, and it is recorded here so the next author does
        not reach for it.
        """
        import pin_staleness_gate

        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project Memory\n\n## Working Memory\n\n")

        verdict = pin_staleness_gate._is_add_shaped_edit(
            {"old_string": "",
             "new_string": "<!-- pinned: 2026-02-02 -->\n### B\ny\n"},
            claude_md,
            "Edit",
        )

        assert verdict is False, (
            "AN EDIT THE TOOL REFUSES READ AS AN ADD. An empty `old_string` "
            "on a file that holds text leaves it unchanged, so the gate "
            "compares pre against pre and must stay quiet. A True here means "
            "the caller counted something other than the simulated document"
        )


class TestPinStalenessGate_SharedSimulation:
    """The staleness gate judges the document the cap gate judges.

    `_simulate_post_edit_document` reads `shared.edit_simulation.simulate`,
    so each Edit shape the cap gate decides as the tool applies it (an empty
    `old_string` on a blank file, a curly quote matched by its straight form,
    `replace_all` through that match) reaches this gate as the same document.
    The equality rows hold the shared call; the verdict rows, with the marker
    present, hold the add the gate refuses and the refused edit it allows. A
    fill of a blank file is allowed: see `TestPinStalenessGate_BlankFile`.
    """

    PIN = "<!-- pinned: 2026-02-02 -->\n### B\ny\n"
    CURLY = "# P\n\n## Pinned Context\n\n<!-- pinned: 2026-01-01 -->\n### A\nsay “hi”\n"

    @pytest.mark.parametrize("current, tool_input", [
        ("", {"old_string": "", "new_string": PIN}),
        ("\n  \n", {"old_string": "", "new_string": PIN}),
        (CURLY, {"old_string": "", "new_string": PIN}),
        (CURLY, {"old_string": "say \"hi\"", "new_string": "say \"hi\"\n\n" + PIN}),
        (CURLY, {"old_string": "say “hi”", "new_string": "say \"hi\"\n\n" + PIN}),
        (CURLY.replace("“hi”", "\"hi\""),
         {"old_string": "say “hi”", "new_string": "say \"hi\"\n\n" + PIN}),
        (CURLY + "say “hi”\n",
         {"old_string": "say \"hi\"", "new_string": "x", "replace_all": True}),
    ], ids=["create", "fill whitespace", "empty old_string on text", "straight on curly",
            "exact curly", "curly on straight", "replace_all through the fold"])
    def test_the_gate_reads_the_shared_simulation(self, current, tool_input):
        import pin_staleness_gate
        from shared.edit_simulation import simulate

        expected = simulate(current, "Edit", tool_input)
        assert expected != current or tool_input["old_string"] == ""
        assert pin_staleness_gate._simulate_post_edit_document(tool_input, current, "Edit") == expected

    def test_an_empty_old_string_on_a_file_with_text_is_allowed(self, gate_env):
        paths = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {"file_path": str(paths["claude_md"]), "old_string": "", "new_string": self.PIN},
        })
        assert result is None

    @pytest.mark.parametrize("file_text, old_string", [
        (CURLY, "say \"hi\""),
        (CURLY, "say “hi”"),
        (CURLY.replace("“hi”", "\"hi\""), "say “hi”"),
    ], ids=["straight on curly", "exact curly", "curly on straight"])
    def test_a_pin_added_through_a_quote_match_is_an_add(self, gate_env, file_text, old_string):
        paths = gate_env(marker_present=True)
        paths["claude_md"].write_text(file_text, encoding="utf-8")
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {"file_path": str(paths["claude_md"]), "old_string": old_string,
                           "new_string": "say \"hi\"\n\n" + self.PIN},
        })
        assert result is not None


class TestPinStalenessGate_BlankFile:
    """A file that is blank before the change holds no stale pins, so the
    marker does not refuse a change to it, by Edit or by Write. A file that
    holds pins is still refused on an add while the marker is set."""

    PIN = "<!-- pinned: 2026-02-02 -->\n### B\ny\n"

    BLANKS = ["", "\n  \t\n", "\ufeff", "\ufeff\n", "\u00a0"]
    BLANK_IDS = ["empty", "whitespace only", "byte-order mark", "byte-order mark and newline",
                 "no-break space"]

    @pytest.mark.parametrize("blank", BLANKS, ids=BLANK_IDS)
    def test_an_edit_that_fills_a_blank_file_is_allowed(self, gate_env, blank):
        paths = gate_env(marker_present=True)
        paths["claude_md"].write_text(blank, encoding="utf-8")
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {"file_path": str(paths["claude_md"]), "old_string": "", "new_string": self.PIN},
        })
        assert result is None

    @pytest.mark.parametrize("blank", BLANKS, ids=BLANK_IDS)
    def test_a_write_of_pins_to_a_blank_file_is_allowed(self, gate_env, blank):
        paths = gate_env(marker_present=True)
        paths["claude_md"].write_text(blank, encoding="utf-8")
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {"file_path": str(paths["claude_md"]), "content": self.PIN},
        })
        assert result is None

    def test_a_write_of_pins_to_a_next_line_only_file_is_refused(self, gate_env):
        """U+0085 is not blank to the Edit tool, so the file is not blank."""
        paths = gate_env(marker_present=True)
        paths["claude_md"].write_text("\x85", encoding="utf-8")
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {"file_path": str(paths["claude_md"]), "content": self.PIN},
        })
        assert result is not None

    def test_an_add_to_a_file_that_holds_pins_is_still_refused(self, gate_env):
        paths = gate_env(marker_present=True)
        current = paths["claude_md"].read_text(encoding="utf-8")
        assert current.strip()
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {"file_path": str(paths["claude_md"]),
                           "content": current.replace("## Working Memory", self.PIN + "\n## Working Memory")},
        })
        assert result is not None
