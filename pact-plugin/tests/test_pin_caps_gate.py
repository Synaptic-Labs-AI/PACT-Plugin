"""
Smoke tests for hooks/pin_caps_gate.py — PreToolUse hook enforcing
pin count / size / override caps on Edit|Write of the project CLAUDE.md.
There is no embedded-pin check: a `### ` line in a pin body is a pin, and
the count axis counts it.

Risk tier: CRITICAL (hook can deny every Edit to CLAUDE.md). Full
matrix (count ladder, size ladder, teammate bypass cells, override
ladder, adversarial Edit fragments, counter-test-by-revert per
predicate) lives in Phase E (test-engineer-6 scope) per the cycle-8
CODE/TEST phase split.

Minimum coverage shipped in the code-phase commit:
  - happy-path ALLOW (under-cap Edit)
  - happy-path DENY (count cap — pre-clean, post-violation)
  - teammate bypass (agent_name non-empty → always allow)
  - fail-open on _check_tool_allowed exception (SACROSANCT)
  - Write-baseline fail-CLOSED when baseline read fails AND Write is
    over-cap
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from helpers import make_claude_md_with_pins, make_pin_entry  # noqa: E402

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "pin_caps_gate.py"


@pytest.fixture
def caps_gate_env(tmp_path, monkeypatch, pact_context):
    """Build a minimal pin_caps_gate test environment.

    Yields a `setup(pin_count=...)` callable that writes a CLAUDE.md with
    the requested number of pins and returns the tmp paths for building
    tool_input payloads.
    """
    claude_md = tmp_path / "CLAUDE.md"
    pact_context(
        team_name="test-team",
        session_id="session-xyz",
        project_dir=str(tmp_path),
    )

    # Point the lifted match_project_claude_md at our tmp CLAUDE.md via
    # staleness.get_project_claude_md_path (the lazy import inside
    # shared/claude_md_manager.match_project_claude_md).
    import staleness
    monkeypatch.setattr(
        staleness, "get_project_claude_md_path", lambda: claude_md
    )

    def _setup(pin_count: int = 1):
        entries = [
            make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(pin_count)
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
    # supplies an explicit agent_type (teammate/plain bypass tests).
    from pin_caps_gate import _check_tool_allowed
    if "agent_type" not in input_data:
        input_data = {**input_data, "agent_type": "pact-orchestrator"}
    return _check_tool_allowed(input_data)


class TestPinCapsGate_Smoke:
    """Minimal hook-primary cap enforcement smoke tests."""

    def test_edit_under_cap_allows(self, caps_gate_env):
        env = caps_gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "irrelevant",
                "new_string": "also irrelevant",
                "replace_all": False,
            },
        })
        assert result is None

    def test_write_at_cap_boundary_allows(self, caps_gate_env):
        """Post-state at cap (12/12) is NOT a violation under strict `>`."""
        env = caps_gate_env(pin_count=3)
        # Write a full CLAUDE.md with exactly 12 pins (at cap, not over).
        entries = [make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(12)]
        new_content = make_claude_md_with_pins(entries)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": new_content,
            },
        })
        assert result is None

    def test_write_over_count_cap_denies(self, caps_gate_env):
        """Post-state 13/12 from a clean baseline denies via net-worse."""
        env = caps_gate_env(pin_count=3)
        entries = [make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(13)]
        new_content = make_claude_md_with_pins(entries)
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": new_content,
            },
        })
        assert result is not None
        assert "Pin count cap" in result

    def test_write_with_heading_in_new_pin_body_counts_it_as_a_pin(self, caps_gate_env):
        """A `### ` line inside a new pin's body is itself a pin. The count
        axis counts it, so under the cap the Write is allowed: there is no
        separate embedded-pin refusal (it only ever refused faithful undated
        renames, swaps and moves)."""
        env = caps_gate_env(pin_count=2)
        # Build a Write content with 3 pins — pins 0 and 1 are clean,
        # pin 2's body embeds a `### Smuggled` heading.
        boundary = (
            "# PACT Framework and Managed Project Memory\n\n"
            "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->\n"
            "<!-- PACT_MEMORY_START -->\n"
            "## Pinned Context\n\n"
        )
        pins_body = (
            "<!-- pinned: 2026-04-22 -->\n"
            "### CleanPinA\nBody A.\n\n"
            "<!-- pinned: 2026-04-22 -->\n"
            "### CleanPinB\nBody B.\n\n"
            "<!-- pinned: 2026-04-22 -->\n"
            "### SmugglerPin\nintro text\n### Smuggled\nsmuggled body\n\n"
        )
        closing = (
            "## Working Memory\n"
            "<!-- PACT_MEMORY_END -->\n"
            "<!-- PACT_MANAGED_END -->\n"
        )
        new_content = boundary + pins_body + closing
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": new_content,
            },
        })
        assert result is None, f"4 pins after the Write is under the cap, got: {result!r}"

    def test_write_with_heading_in_mutated_pin_body_counts_it_as_a_pin(
        self, caps_gate_env
    ):
        """An existing pin's body gaining a `### ` line gains a pin; under the
        cap that is allowed, with no embedded-pin refusal."""
        env = caps_gate_env(pin_count=3)  # baseline has 3 pins with clean bodies
        # Build a Write content that keeps pin headings the same but mutates
        # the first pin's body to smuggle `### Smuggled`.
        boundary = (
            "# PACT Framework and Managed Project Memory\n\n"
            "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->\n"
            "<!-- PACT_MEMORY_START -->\n"
            "## Pinned Context\n\n"
        )
        # Pin0 has a MUTATED body now containing `### Smuggled`; Pin1/Pin2 unchanged
        pins_body = (
            "<!-- pinned: 2026-04-22 -->\n"
            "### Pin0\nmutated body\n### Smuggled\ninjected\n\n"
            "<!-- pinned: 2026-04-22 -->\n"
            "### Pin1\nxxxx\n\n"
            "<!-- pinned: 2026-04-22 -->\n"
            "### Pin2\nxxxx\n\n"
        )
        closing = (
            "## Working Memory\n"
            "<!-- PACT_MEMORY_END -->\n"
            "<!-- PACT_MANAGED_END -->\n"
        )
        new_content = boundary + pins_body + closing
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": new_content,
            },
        })
        assert result is None, f"4 pins after the Write is under the cap, got: {result!r}"

    def test_a_heading_gained_in_a_pin_body_at_the_cap_is_denied_on_count(self, caps_gate_env):
        """At 12 pins, a body gaining a prose `### ` line adds a pin past the
        cap: denied with the count reason, not an embedded-pin reason."""
        env = caps_gate_env(pin_count=12)
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "### Pin3\nxxxx",
                "new_string": "### Pin3\nxxxx\n### Smuggled\nmore",
                "replace_all": False,
            },
        })
        assert result is not None and "Pin count cap" in result

    def test_write_unchanged_preexisting_embedded_pin_allows(
        self, caps_gate_env
    ):
        """F7 negative counter: a Write that leaves an already-embedded
        `### ` pin body UNCHANGED must NOT deny. Pre-malformed state never
        denies (F1 livelock precedent); the identity-by-body-text check
        correctly excludes unchanged bodies from the new_body scan.

        Scenario: baseline CLAUDE.md has a pin whose body already contains
        `### PreExistingEmbedded` (manually crafted past the gate at some
        prior point). A subsequent Write that preserves this body must not
        deny on the pre-existing state.
        """
        env = caps_gate_env(pin_count=0)  # start with empty baseline
        # Hand-write both the baseline AND the Write content identically,
        # both containing the embedded-pin pin. The env writes a "clean"
        # baseline with `pin_count=0` pins, so overwrite it here.
        boundary = (
            "# PACT Framework and Managed Project Memory\n\n"
            "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->\n"
            "<!-- PACT_MEMORY_START -->\n"
            "## Pinned Context\n\n"
        )
        pins_body = (
            "<!-- pinned: 2026-04-22 -->\n"
            "### LegacyPin\nbody\n### PreExistingEmbedded\nextra\n\n"
        )
        closing = (
            "## Working Memory\n"
            "<!-- PACT_MEMORY_END -->\n"
            "<!-- PACT_MANAGED_END -->\n"
        )
        same_content = boundary + pins_body + closing
        env["claude_md"].write_text(same_content, encoding="utf-8")
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": same_content,  # identical -> no body change
            },
        })
        assert result is None, (
            f"F7 over-strict: Write that preserves pre-existing embedded-pin "
            f"content denied (expected allow). Got: {result!r}"
        )

    def test_non_claude_md_path_allows(self, caps_gate_env):
        env = caps_gate_env(pin_count=3)
        # Different file → gate short-circuits at the path match.
        other = env["claude_md"].parent / "other.md"
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(other),
                "content": "# not claude_md\n",
            },
        })
        assert result is None

    def test_non_gated_tool_passes(self, caps_gate_env):
        env = caps_gate_env(pin_count=3)
        result = _call_gate({
            "tool_name": "Read",
            "tool_input": {"file_path": str(env["claude_md"])},
        })
        assert result is None

    def test_teammate_bypass(self, caps_gate_env):
        """Teammate sessions (non-lead agent_type) bypass the gate.

        #878: lead-detection migrated to is_lead, which reads agent_type
        directly (no longer resolve_agent_name). A specialist agent_type is not
        a lead spelling, so the gate bypasses.
        """
        env = caps_gate_env(pin_count=3)
        entries = [
            make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(13)
        ]
        new_content = make_claude_md_with_pins(entries)
        result = _call_gate({
            "tool_name": "Write",
            "agent_type": "pact-backend-coder",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "content": new_content,
            },
        })
        assert result is None

    def test_edit_legitimate_new_pin_with_date_comment_allows(
        self, caps_gate_env
    ):
        """Guards against #529 regression (PR #530): pre-fix the Edit path
        fell through to naive `return new_string`, so every date-marked
        new-pin Edit denied via `DENY_REASON_EMBEDDED_PIN`. Post-fix the
        Edit path mirrors Write via the pre/post-pin diff: legitimate adds
        (carrying a `<!-- pinned: YYYY-MM-DD -->` marker) ALLOW; naked
        smuggles deny.

        An Edit that adds a legitimate new pin (date-comment marker +
        `### Title` + body, no embedded `### ` inside the body) must be
        ALLOWED. The Edit-path smuggle-detection must mirror the Write
        path and distinguish legitimate date-marked adds from naked-heading
        smuggles.

        This is the documented `/PACT:pin-memory` Add flow: Read CLAUDE.md,
        insert `<!-- pinned: YYYY-MM-DD -->` + `### Entry Title` + body via
        Edit, commit. Pre-fix this flow was structurally broken.
        """
        env = caps_gate_env(pin_count=3)  # baseline has 3 clean pins, well under 12
        new_pin_block = (
            "<!-- pinned: 2026-04-23 -->\n"
            "### LegitimateNewPin\n"
            "Legitimate body content.\n\n"
        )
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                # old_string must exist in baseline — "## Working Memory\n"
                # appears verbatim in make_claude_md_with_pins output.
                "old_string": "## Working Memory\n",
                "new_string": new_pin_block + "## Working Memory\n",
                "replace_all": False,
            },
        })
        assert result is None, (
            f"#529 regressed: Edit adding a legitimate date-marked pin "
            f"denied (expected allow). Got: {result!r}"
        )

    def test_edit_undated_new_pin_under_the_cap_allows(
        self, caps_gate_env
    ):
        """An Edit inserting a `### Title` with no `<!-- pinned: -->` comment
        adds a pin; under the cap it is allowed. The date comment decides
        nothing: the removed embedded-pin check refused exactly these."""
        env = caps_gate_env(pin_count=3)
        smuggled_block = (
            "### SmuggledNoDateMarker\n"
            "body without a preceding <!-- pinned: --> marker\n\n"
        )
        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(env["claude_md"]),
                "old_string": "## Working Memory\n",
                "new_string": smuggled_block + "## Working Memory\n",
                "replace_all": False,
            },
        })
        assert result is None, f"an undated pin added under the cap must be allowed, got: {result!r}"

    def test_undated_pin_renamed_or_swapped_at_13_allows(self, caps_gate_env):
        """At 13 pins an undated pin renamed, or one pin deleted and an
        undated one added, adds nothing: allowed (main refused both as an
        embedded pin)."""
        env = caps_gate_env(pin_count=13)
        text = env["claude_md"].read_text(encoding="utf-8")
        undated = text.replace("<!-- pinned: 2026-04-20 -->\n### Pin5\n", "### Pin5\n", 1)
        env["claude_md"].write_text(undated, encoding="utf-8")
        rename = _call_gate({
            "tool_name": "Edit",
            "tool_input": {"file_path": str(env["claude_md"]), "old_string": "### Pin5\n",
                           "new_string": "### Pin5 renamed\n", "replace_all": False},
        })
        swap = _call_gate({
            "tool_name": "Edit",
            "tool_input": {"file_path": str(env["claude_md"]),
                           "old_string": "<!-- pinned: 2026-04-20 -->\n### Pin7\nxxxx",
                           "new_string": "### Undated newcomer\nyyyy", "replace_all": False},
        })
        assert rename is None and swap is None, (rename, swap)


class TestPinCapsGate_FailOpen:
    """SACROSANCT: gate bugs never block (with Write-baseline exception)."""

    def test_main_catches_unexpected_exception(self, caps_gate_env, monkeypatch, capsys):
        """If the decision raises, main() fail-opens."""
        import pin_caps_gate
        monkeypatch.setattr(
            pin_caps_gate,
            "_gate",
            lambda _: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        stdin_payload = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": "/tmp/nonexistent", "old_string": "",
                           "new_string": ""},
        })
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(stdin_payload))
        with pytest.raises(SystemExit) as exc_info:
            pin_caps_gate.main()
        assert exc_info.value.code == 0
        assert json.loads(capsys.readouterr().out) == {"suppressOutput": True}

    def test_invalid_json_stdin_fails_open(self, monkeypatch):
        """Malformed stdin → fail-open with suppressOutput."""
        import pin_caps_gate
        monkeypatch.setattr(
            "sys.stdin", __import__("io").StringIO("not valid json")
        )
        with pytest.raises(SystemExit) as exc_info:
            pin_caps_gate.main()
        assert exc_info.value.code == 0


class TestPinCapsGate_WriteBaselineFailClosed:
    """The one refusing failure path (Sec N7): a Write with no readable
    baseline is compared with an empty file, so its own pins over the cap
    are refused with the count reason."""

    def test_write_over_cap_with_missing_baseline_denies(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Baseline CLAUDE.md doesn't exist on disk; Write payload is
        13/12. Compared with an empty file, the Write adds 13 pins."""
        claude_md = tmp_path / "CLAUDE.md"  # Deliberately NOT created.
        pact_context(team_name="t", session_id="s", project_dir=str(tmp_path))

        import staleness
        monkeypatch.setattr(
            staleness, "get_project_claude_md_path", lambda: claude_md
        )

        entries = [
            make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(13)
        ]
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(claude_md),
                "content": make_claude_md_with_pins(entries),
            },
        })
        assert result is not None
        assert "Pin count cap" in result

    def test_write_under_cap_with_missing_baseline_allows(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Same baseline-missing condition, but the Write content is
        under-cap → allow. Fail-CLOSED only fires on a concrete
        over-cap Write; a clean Write isn't punished for a missing file."""
        claude_md = tmp_path / "CLAUDE.md"  # Deliberately NOT created.
        pact_context(team_name="t", session_id="s", project_dir=str(tmp_path))

        import staleness
        monkeypatch.setattr(
            staleness, "get_project_claude_md_path", lambda: claude_md
        )

        entries = [
            make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(3)
        ]
        result = _call_gate({
            "tool_name": "Write",
            "tool_input": {
                "file_path": str(claude_md),
                "content": make_claude_md_with_pins(entries),
            },
        })
        assert result is None

    def test_edit_with_missing_baseline_fails_open(
        self, tmp_path, monkeypatch, pact_context
    ):
        """Edit (not Write) with baseline missing → fail-OPEN.
        Asymmetric rule applies only to Write."""
        claude_md = tmp_path / "CLAUDE.md"
        pact_context(team_name="t", session_id="s", project_dir=str(tmp_path))

        import staleness
        monkeypatch.setattr(
            staleness, "get_project_claude_md_path", lambda: claude_md
        )

        result = _call_gate({
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(claude_md),
                "old_string": "x",
                "new_string": "y",
                "replace_all": False,
            },
        })
        assert result is None


# ---------------------------------------------------------------------------
# The real hook, run as a subprocess on a real PreToolUse frame
# ---------------------------------------------------------------------------


def _pins(n, bodies=None, extra=""):
    entries = [make_pin_entry(title=f"Pin{i}", body_chars=4) for i in range(n)]
    for i, body in (bodies or {}).items():
        entries[i] = f"<!-- pinned: 2026-04-20 -->\n### Pin{i}\n{body}"
    return make_claude_md_with_pins(entries) + extra


SNIPPET = "xxxx\n```markdown\n### step one\n### step two\n```"
OPEN = "xxxx\n```bash\necho hi"
CLOSED = "xxxx\n```bash\necho hi\n```"
LONG = "y" * 1600
OVERRIDE_FENCED = "xxxx\n```\n<!-- pinned: 2026-04-20, pin-size-override:   -->\n```"


def _edit(old, new, replace_all=False):
    return "Edit", {"old_string": old, "new_string": new, "replace_all": replace_all}


def _write(content):
    return "Write", {"content": content}


# (name, text before or "unreadable", tool and input, expected outcome). None as
# the outcome builds the change from the file and adds one pin to it.
REAL_HOOK_ROWS = [
    ("count: a rename at 13 pins", _pins(13), _edit("### Pin5\n", "### Pin5 renamed\n"), "allow"),
    ("count: a pin added at 12", _pins(12), _edit("### Pin11\nxxxx", "### Pin11\nxxxx\n\n<!-- pinned: 2026-04-21 -->\n### New\nbody"), "deny"),
    ("snippet: a fenced snippet holding ### lines added at 13", _pins(13), _edit("### Pin9\nxxxx", "### Pin9\n" + SNIPPET), "allow"),
    ("snippet: a snippet line renamed and a pin added", _pins(12, {3: SNIPPET}),
     _edit("### step one\n", "### step 1\n"), None),
    ("reveal: closing an unclosed fence that hid pins", _pins(13, {6: OPEN}), _write(_pins(13, {6: CLOSED})), "allow"),
    ("reveal: closing an unclosed fence and adding a pin", _pins(13, {6: OPEN}),
     _write(_pins(13, {6: CLOSED}, extra="")), None),
    ("size: an oversize pin left as it is beside a rename", _pins(12, {4: LONG}), _edit("### Pin2\n", "### Pin2 renamed\n"), "allow"),
    ("size: a pin grown past the size cap", _pins(12), _edit("### Pin4\nxxxx", "### Pin4\n" + LONG), "deny"),
    ("override: a fenced example of the override syntax", _pins(3), _edit("### Pin1\nxxxx", "### Pin1\n" + OVERRIDE_FENCED), "allow"),
    ("override: an invalid override row on an edited pin", _pins(3),
     _edit("<!-- pinned: 2026-04-20 -->\n### Pin0", "<!-- pinned: 2026-04-20, pin-size-override:   -->\n### Pin0"), "deny"),
    ("unreadable file: a Write with 12 pins", "unreadable", _write(_pins(12)), "allow"),
    ("unreadable file: a Write with 13 pins", "unreadable", _write(_pins(13)), "deny"),
    ("unreadable file: an Edit", "unreadable", _edit("### Pin1\n", "### Pin1 renamed\n"), "advisory"),
    ("not located: an unclosed fence above the Pinned section", _pins(13),
     _write("# notes\n\n```\nunclosed\n\n" + _pins(20)), "advisory"),
]


def _frame(claude_md, tool, tool_input):
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "session-real-hook",
        "agent_type": "pact-orchestrator",
        "cwd": str(claude_md.parent),
        "tool_name": tool,
        "tool_input": {"file_path": str(claude_md), **tool_input},
    }


def _run_hook(project, frame, hook=None):
    env = {**os.environ, "HOME": str(project / "home"), "CLAUDE_PROJECT_DIR": str(project)}
    (project / "home").mkdir(exist_ok=True)
    return subprocess.run([sys.executable, str(hook or HOOK)], input=json.dumps(frame), capture_output=True,
                          text=True, env=env, cwd=project, timeout=120)


def _outcome(result):
    if result.returncode == 2:
        out = json.loads(result.stdout)["hookSpecificOutput"]
        assert out["permissionDecision"] == "deny" and out["permissionDecisionReason"], out
        return "deny"
    assert result.returncode == 0, (result.returncode, result.stderr)
    out = json.loads(result.stdout)
    if out == {"suppressOutput": True}:
        return "allow"
    specific = out["hookSpecificOutput"]
    assert "permissionDecision" not in specific and specific["additionalContext"], out
    return "advisory"


class TestPinCapsGate_RealHook:
    """One allowed and one denied change per family, through the shipped
    pin_caps_gate.py run as a subprocess on a real PreToolUse frame, with the
    project directory and HOME in tmp_path."""

    @pytest.mark.parametrize("name, before, call, expected", REAL_HOOK_ROWS, ids=[r[0] for r in REAL_HOOK_ROWS])
    def test_real_hook(self, tmp_path, name, before, call, expected):
        claude_md = tmp_path / "CLAUDE.md"
        if before == "unreadable":
            if os.geteuid() == 0:
                pytest.skip("root reads a mode-000 file, so EACCES cannot be produced")
            claude_md.write_text(_pins(3), encoding="utf-8")
            claude_md.chmod(0o000)
        else:
            claude_md.write_text(before, encoding="utf-8")
        tool, tool_input = call
        if expected is None:  # the rows whose change is built from the file: the edit adds one pin too
            expected = "deny"
            if tool == "Edit":
                text = claude_md.read_text(encoding="utf-8").replace(tool_input["old_string"], tool_input["new_string"], 1)
            else:
                text = tool_input["content"]
            tool, tool_input = _write(text.replace("## Working Memory", "<!-- pinned: 2026-04-21 -->\n### New\nbody\n\n## Working Memory", 1))
        try:
            assert _outcome(_run_hook(tmp_path, _frame(claude_md, tool, tool_input))) == expected
        finally:
            claude_md.chmod(0o644)

    def test_a_module_load_failure_allows_and_says_so(self, tmp_path):
        """A gate that cannot import its modules allows the call (exit 0) and
        says on stdout and stderr that pin caps are not being checked."""
        lonely = tmp_path / "lonely"
        lonely.mkdir()
        hook = lonely / "pin_caps_gate.py"
        shutil.copy(HOOK, hook)  # no shared/ beside it, so its imports fail
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(_pins(12), encoding="utf-8")
        result = _run_hook(tmp_path, _frame(claude_md, *_write(_pins(14))), hook=hook)
        assert result.returncode == 0, result
        message = json.loads(result.stdout)["systemMessage"]
        assert "not checking pin caps" in message and "ModuleNotFoundError" in message
        assert "not checking pin caps" in result.stderr
