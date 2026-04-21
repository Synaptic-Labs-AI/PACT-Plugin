"""
Tests for hooks/pin_staleness_gate.py — PreToolUse marker-gate for
CLAUDE.md Pinned Context edits under stale-pins-pending state.

Risk tier: CRITICAL (auth-adjacent — gate blocks user tool calls). All
I/O failure paths MUST fail-open (SACROSANCT: gate bugs never block).

Matrix: marker absence/present × CLAUDE.md path match/miss × teammate/lead
        × Edit/Write → 16 cells minimum, plus fail-open assertions.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent))

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
    """Invoke _check_tool_allowed directly with a synthesized input_data."""
    from pin_staleness_gate import _check_tool_allowed
    return _check_tool_allowed(input_data)


# Matrix coverage note: the full matrix is (marker absent/present) × (path match/miss) ×
# (teammate/lead) × (Edit/Write) = 16 cells. Three cells are NOT exercised explicitly
# because they reduce to tested paths: (marker-absent × teammate × {Edit,Write,path-miss})
# all short-circuit at the same marker-check before any teammate/path logic runs —
# TestPinStalenessGate_MarkerAbsent already covers that short-circuit for lead callers,
# and the marker-absent return is agent-name-independent by construction.


class TestPinStalenessGate_ToolMatch:
    """Only Edit and Write are gated — other tools always pass."""

    @pytest.mark.parametrize("tool_name", ["Read", "Bash", "Glob", "Grep",
                                           "Task", "NotebookEdit", ""])
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
        """Net-new pin comment in new_string → ADD → deny."""
        env = gate_env(marker_present=True)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "some text",
                "new_string": "<!-- pinned: 2026-04-20 -->\n### X\nbody",
            },
        })
        assert result is not None
        assert "Pinned Context" in result
        assert "stale pins" in result

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
        env = gate_env(marker_present=True)
        import shared.pact_context as ctx_module
        monkeypatch.setattr(
            ctx_module, "resolve_agent_name",
            lambda _input_data: "backend-coder",
        )
        result = _call_gate({
            "tool_name": "Edit",
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
        monkeypatch.setattr(sys, "stdin", StringIO(json.dumps({
            "tool_name": "Write",
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

    Load-bearing coverage for the bounded-count defense at
    `pin_staleness_gate.py:120-127` (extract_managed_region import
    block). Before this defense, a `<!-- pinned:` token appearing in
    user-authored prose or a fenced code block OUTSIDE the managed
    region would inflate the gate's count and either:
      - falsely BLOCK a legitimate pin edit (add-shape), or
      - falsely ALLOW a net-new pin while an outside decoy was
        simultaneously archived (same full-text count, different
        structural reality).

    Counter-test-by-revert: commenting out the try-block at
    `pin_staleness_gate.py:120-127` (so `_count_pin_comments` always
    falls through to `text.count(...)`) MUST cause at least one test
    here to fail. Without that proof, the defense is phantom-green.
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
            "In-region ADD masked by outside decoy removal — Arch-M3 bounding "
            "bypassed. If you see this failure after reverting "
            "pin_staleness_gate.py lines 120-127, that is the counter-test "
            "proof the defense is load-bearing."
        )
        assert "stale pins" in result

    def test_edit_fragment_without_markers_uses_full_text_count(
        self, gate_env
    ):
        """Edit fragment (no markers) falls through to full-text count.

        Edit.old_string and Edit.new_string are typically raw fragments
        that do not carry the PACT_MANAGED_START/END markers — they are
        structurally INSIDE the managed region by virtue of the section
        being edited. `_count_pin_comments` must fall through to
        `text.count(...)` on these, otherwise a net-new pin added via
        Edit would be invisible (extract_managed_region returns None →
        bounded count = 0 for both old and new → no ADD → phantom-allow).

        This test pins the else-branch behavior of
        `_count_pin_comments`: when MANAGED_START_MARKER is absent,
        count the full input. Currently exercised via a direct
        `_count_pin_comments` assertion (no gate call needed — the
        Edit ADD path is covered by test_edit_adding_new_pin_denied).
        """
        import pin_staleness_gate
        gate_env(marker_present=True)
        old_fragment = "### Existing\nbody\n"
        new_fragment = "<!-- pinned: 2026-04-20 -->\n### New\nbody\n"
        # Neither fragment contains MANAGED_START_MARKER.
        from shared.claude_md_manager import MANAGED_START_MARKER
        assert MANAGED_START_MARKER not in old_fragment
        assert MANAGED_START_MARKER not in new_fragment
        # Fall-through to full-text count MUST return the literal
        # count of `<!-- pinned:` in the fragment.
        assert pin_staleness_gate._count_pin_comments(old_fragment) == 0
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
