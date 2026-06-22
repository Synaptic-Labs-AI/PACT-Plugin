"""
Stale-team/store-mismatch self-diagnosis at dispatch_gate deny sites.

Cheap-win (PR 1 of the restart/persistence cluster): when a Claude Code
restart/fork leaves PACT's persisted team_name/session_id stale, this gate
resolves an orphaned task store while Task* tools write the live one, so every
pact-* spawn is denied with a MISLEADING message ("no Task assigned…" /
"team_name unavailable") that never names the real cause. This change surfaces
the EXISTING shared.stale_session.detect_stale_session_block detection at the
two restart-symptom deny sites (rule ⑥ team_name_unavailable, rule ⑧
no_task_assigned), MESSAGE-ONLY.

What this file pins:
  - BOTH-MODES MATRIX (in-process session_id==leadSessionId AND tmux
    session_id!=leadSessionId): on a stale-team mismatch the augmented
    self-diagnosis appears at BOTH deny sites; on no mismatch the ORIGINAL
    message is preserved verbatim. The augmentation is mode-agnostic, so the
    matrix is an INVARIANCE proof (same outcome both modes).
  - NON-VACUITY via PAIRED ENABLE/DISABLE (mismatch-present vs mismatch-absent
    inputs), not git-revert: the augmentation marker is present iff a mismatch
    is detected. A test would FAIL if the augmentation were removed.
  - DECISION UNCHANGED: the gate still DENYs (exit 2) on exactly the same
    inputs; only the message text changes.
  - DEFENSIVE never-raises: if the detector raises, the deny still returns the
    ORIGINAL message and no exception escapes.

Reuses the in-process harness from test_dispatch_gate.py (_run_main / _full_setup
/ _seed_team) so the both-modes setup matches the rest of the gate's suite.
"""

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from test_dispatch_gate import (  # noqa: E402 — sibling harness reuse
    _make_input,
    _run_main,
    _full_setup,
    _seed_team,
    _TEAM,
    _NAME,
)

# A marker substring unique to the augmentation — asserting on it proves the
# net-new self-diagnosis text is present without coupling to exact wording.
_AUGMENT_MARKER = "STALE-TEAM/STORE MISMATCH"
_REALIGN_MARKER = "pact-session-context.json"

# session_id values for the both-modes matrix.
_LIVE_SESSION_ID = "test-session"          # the stdin session_id _make_input uses
_RECORDED_STALE_ID = "0000dead-beef-4000-8000-000000000000"  # != live → stale
_RECORDED_HEALTHY_ID = _LIVE_SESSION_ID    # == live → healthy (no mismatch)


def _write_project_claude_md(monkeypatch, tmp_path, recorded_session_id):
    """Create a project CLAUDE.md with a '- Resume:' line carrying
    ``recorded_session_id`` and point CLAUDE_PROJECT_DIR at it, so
    detect_stale_session_block can compare recorded-vs-live.
    """
    project_dir = tmp_path / "claudemd_project"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "CLAUDE.md").write_text(
        "# Project\n\n## Current Session\n"
        f"- Resume: `claude --resume {recorded_session_id}`\n"
        f"- Team: `{_TEAM}`\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    return project_dir


def _seed_team_with_lead(home, team_name, lead_session_id, members=(), tasks=()):
    """_seed_team variant that also stamps leadSessionId on config.json — the
    STRUCTURAL mode discriminator (in-process: lead==live; tmux: lead!=live).
    The augmentation does not key on this, so it is recorded to make the
    both-modes matrix faithful, not to drive behavior.
    """
    _seed_team(home, team_name=team_name, members=members, tasks=tasks)
    cfg = home / ".claude" / "teams" / team_name / "config.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["leadSessionId"] = lead_session_id
    cfg.write_text(json.dumps(data), encoding="utf-8")


# Mode matrix: (mode_label, leadSessionId). in-process == live id; tmux != live.
_MODES = [
    ("in_process", _LIVE_SESSION_ID),
    ("tmux", "ffff9999-1111-4000-8000-000000000000"),
]


# =============================================================================
# Rule ⑧ — no_task_assigned deny site
# =============================================================================


@pytest.mark.parametrize("mode_label,lead_id", _MODES)
def test_no_task_assigned_deny_augmented_on_stale_mismatch(
    mode_label, lead_id, tmp_path, monkeypatch, capsys
):
    """ENABLE leg: a stale recorded-vs-live session_id mismatch augments the
    rule-⑧ 'no Task assigned' deny with the self-diagnosis, in BOTH modes."""
    # Seed a team with a task owned by SOMEONE ELSE so has_task_assigned(name)
    # is False → rule ⑧ DENY fires for _NAME.
    plugin_root = _full_setup(
        monkeypatch, tmp_path, tasks=(("someone-else", "pending"),)
    )
    _seed_team_with_lead(
        tmp_path, _TEAM, lead_id, tasks=(("someone-else", "pending"),)
    )
    _write_project_claude_md(monkeypatch, tmp_path, _RECORDED_STALE_ID)

    code, out = _run_main(_make_input(), capsys)

    assert code == 2, f"[{mode_label}] decision must still be DENY (exit 2)"
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "no Task assigned" in reason, f"[{mode_label}] original deny preserved"
    assert _AUGMENT_MARKER in reason, f"[{mode_label}] self-diagnosis augmented"
    assert _REALIGN_MARKER in reason, f"[{mode_label}] re-align steps present"


@pytest.mark.parametrize("mode_label,lead_id", _MODES)
def test_no_task_assigned_deny_unaugmented_when_no_mismatch(
    mode_label, lead_id, tmp_path, monkeypatch, capsys
):
    """DISABLE leg: when recorded==live (healthy), the rule-⑧ deny keeps its
    ORIGINAL message verbatim — no augmentation. Paired with the ENABLE test
    above this is the non-vacuity proof (marker present IFF mismatch)."""
    _full_setup(monkeypatch, tmp_path, tasks=(("someone-else", "pending"),))
    _seed_team_with_lead(
        tmp_path, _TEAM, lead_id, tasks=(("someone-else", "pending"),)
    )
    _write_project_claude_md(monkeypatch, tmp_path, _RECORDED_HEALTHY_ID)

    code, out = _run_main(_make_input(), capsys)

    assert code == 2, f"[{mode_label}] decision unchanged (DENY)"
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "no Task assigned" in reason
    assert _AUGMENT_MARKER not in reason, f"[{mode_label}] NOT augmented (healthy)"
    assert _REALIGN_MARKER not in reason


# =============================================================================
# Rule ⑥ — team_name_unavailable deny site
# =============================================================================


def _setup_empty_team_name(monkeypatch, tmp_path):
    """Force rule ⑥ (team_name_unavailable) by writing an EMPTY context
    team_name — get_team_name() short-circuits empty → DENY fail-closed."""
    plugin_root = _full_setup(monkeypatch, tmp_path)
    # Overwrite the context file with an empty team_name (the fail-closed
    # signal) while keeping plugin_root resolvable so we reach rule ⑥.
    import shared.pact_context as ctx_module
    ctx_path = tmp_path / "pact-session-context.json"
    ctx_path.write_text(
        json.dumps({
            "team_name": "",
            "session_id": _LIVE_SESSION_ID,
            "project_dir": str(tmp_path / "project"),
            "plugin_root": str(plugin_root),
            "started_at": "2026-01-01T00:00:00Z",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(ctx_module, "_context_path", ctx_path)
    monkeypatch.setattr(ctx_module, "_cache", None)
    monkeypatch.setattr(ctx_module, "_aligned_cache", None)
    return plugin_root


@pytest.mark.parametrize("mode_label,lead_id", _MODES)
def test_team_name_unavailable_deny_augmented_on_stale_mismatch(
    mode_label, lead_id, tmp_path, monkeypatch, capsys
):
    """ENABLE leg: the rule-⑥ team_name_unavailable deny is also augmented on a
    stale mismatch, in BOTH modes."""
    _setup_empty_team_name(monkeypatch, tmp_path)
    _seed_team_with_lead(tmp_path, _TEAM, lead_id)
    _write_project_claude_md(monkeypatch, tmp_path, _RECORDED_STALE_ID)

    code, out = _run_main(_make_input(), capsys)

    assert code == 2, f"[{mode_label}] DENY"
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "team_name is unavailable" in reason or "team_name" in reason
    assert _AUGMENT_MARKER in reason, f"[{mode_label}] self-diagnosis augmented"


@pytest.mark.parametrize("mode_label,lead_id", _MODES)
def test_team_name_unavailable_deny_unaugmented_when_no_mismatch(
    mode_label, lead_id, tmp_path, monkeypatch, capsys
):
    """DISABLE leg: healthy recorded==live → rule ⑥ keeps original message."""
    _setup_empty_team_name(monkeypatch, tmp_path)
    _seed_team_with_lead(tmp_path, _TEAM, lead_id)
    _write_project_claude_md(monkeypatch, tmp_path, _RECORDED_HEALTHY_ID)

    code, out = _run_main(_make_input(), capsys)

    assert code == 2
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert _AUGMENT_MARKER not in reason, f"[{mode_label}] NOT augmented (healthy)"


# =============================================================================
# Non-symptom deny rules are NOT augmented (avoid misdirecting recovery)
# =============================================================================


def test_non_symptom_deny_not_augmented_even_under_mismatch(
    tmp_path, monkeypatch, capsys
):
    """A name-validation deny (rule ④, not a restart symptom) must NOT receive
    the stale-team note even when a CLAUDE.md mismatch exists — it would
    misdirect recovery."""
    _full_setup(monkeypatch, tmp_path)
    _write_project_claude_md(monkeypatch, tmp_path, _RECORDED_STALE_ID)

    # An invalid name triggers rule ④ (name_invalid_regex), which is NOT in
    # _STALE_DIAGNOSABLE_RULES.
    code, out = _run_main(_make_input(name="Bad Name!"), capsys)

    assert code == 2
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert _AUGMENT_MARKER not in reason
    assert _REALIGN_MARKER not in reason


# =============================================================================
# Defensive never-raises: detector exception → original message, no escape
# =============================================================================


@pytest.mark.parametrize("mode_label,lead_id", _MODES)
def test_detector_exception_falls_back_to_original_message(
    mode_label, lead_id, tmp_path, monkeypatch, capsys
):
    """If detect_stale_session_block raises, the deny still returns the ORIGINAL
    message and the gate still exits 2 — no exception escapes dispatch."""
    _full_setup(monkeypatch, tmp_path, tasks=(("someone-else", "pending"),))
    _seed_team_with_lead(
        tmp_path, _TEAM, lead_id, tasks=(("someone-else", "pending"),)
    )
    _write_project_claude_md(monkeypatch, tmp_path, _RECORDED_STALE_ID)

    import dispatch_gate

    def _boom(_input_data):
        raise RuntimeError("detector blew up")

    # Patch the name the augmentation helper resolves (module global imported
    # into dispatch_gate), the same seam the helper's never-raises wrap guards.
    monkeypatch.setattr(dispatch_gate, "detect_stale_session_block", _boom)

    code, out = _run_main(_make_input(), capsys)

    assert code == 2, f"[{mode_label}] deny still fires despite detector raise"
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "no Task assigned" in reason, f"[{mode_label}] original message preserved"
    assert _AUGMENT_MARKER not in reason, f"[{mode_label}] no partial augmentation"


# =============================================================================
# Helper-direct unit coverage (clean seam the lead's Q1 ruling created)
# =============================================================================


def test_augment_helper_passes_through_non_diagnosable_rule():
    import dispatch_gate
    out = dispatch_gate._augment_deny_with_stale_diagnosis(
        "name_required", "ORIGINAL", {"session_id": "x"}
    )
    assert out == "ORIGINAL"


def test_augment_helper_passes_through_non_dict_input():
    import dispatch_gate
    out = dispatch_gate._augment_deny_with_stale_diagnosis(
        "no_task_assigned", "ORIGINAL", None
    )
    assert out == "ORIGINAL"


def test_augment_helper_never_raises_on_detector_error(monkeypatch):
    import dispatch_gate
    monkeypatch.setattr(
        dispatch_gate,
        "detect_stale_session_block",
        lambda _d: (_ for _ in ()).throw(ValueError("boom")),
    )
    out = dispatch_gate._augment_deny_with_stale_diagnosis(
        "no_task_assigned", "ORIGINAL", {"session_id": "x"}
    )
    assert out == "ORIGINAL"


def test_augment_helper_appends_on_detected_mismatch(monkeypatch):
    import dispatch_gate
    monkeypatch.setattr(
        dispatch_gate,
        "detect_stale_session_block",
        lambda _d: "\n\nWARNING — stale session block: ...",
    )
    out = dispatch_gate._augment_deny_with_stale_diagnosis(
        "team_name_unavailable", "ORIGINAL", {"session_id": "x"}
    )
    assert out.startswith("ORIGINAL")
    assert _AUGMENT_MARKER in out
    assert _REALIGN_MARKER in out


# =============================================================================
# Both-modes INVARIANCE pinned DIRECTLY: the in-process and tmux legs must
# produce the SAME deny reason for the same input. The augmentation is
# mode-agnostic (leadSessionId is read nowhere in the detection / rule-⑥/⑧
# path), so both legs run the same code today — this assertion turns that
# documented design property into a guard: a future change that made the
# augmentation mode-DEPENDENT (keying on leadSessionId) would diverge the two
# reason strings and fail here. Without it the matrix only proves "each leg
# augments," not "the two legs agree."
# =============================================================================


def _augmented_reason_for_lead_id(lead_id, tmp_path, monkeypatch, capsys):
    """Run the rule-⑧ stale-mismatch deny under a given leadSessionId and
    return the deny reason string."""
    _full_setup(monkeypatch, tmp_path, tasks=(("someone-else", "pending"),))
    _seed_team_with_lead(
        tmp_path, _TEAM, lead_id, tasks=(("someone-else", "pending"),)
    )
    _write_project_claude_md(monkeypatch, tmp_path, _RECORDED_STALE_ID)
    code, out = _run_main(_make_input(), capsys)
    assert code == 2
    return out["hookSpecificOutput"]["permissionDecisionReason"]


def test_both_modes_produce_identical_reason_string(
    tmp_path, monkeypatch, capsys
):
    """INVARIANCE: the augmented deny reason is byte-identical across the
    in-process (lead==live) and tmux (lead!=live) topologies for the same
    input. Pins mode-independence directly — a mode-dependent regression
    would diverge these and fail.

    Each leg gets its own tmp_path subdir + monkeypatch context via the
    helper so the two runs do not contaminate each other (fresh HOME,
    fresh pact_context cache reset inside _full_setup/_setup_session).
    """
    in_process_lead = _MODES[0][1]   # == _LIVE_SESSION_ID
    tmux_lead = _MODES[1][1]         # != live
    assert in_process_lead != tmux_lead, "matrix legs must differ structurally"

    reason_in_process = _augmented_reason_for_lead_id(
        in_process_lead, tmp_path / "leg_in_process", monkeypatch, capsys
    )
    reason_tmux = _augmented_reason_for_lead_id(
        tmux_lead, tmp_path / "leg_tmux", monkeypatch, capsys
    )

    assert reason_in_process == reason_tmux, (
        "augmentation must be mode-INVARIANT: the in-process and tmux legs "
        "produced different deny reasons, implying the augmentation now keys "
        "on leadSessionId (the forbidden mode discriminator)"
    )
    # And it is the augmented (not the plain) reason that is invariant.
    assert _AUGMENT_MARKER in reason_in_process


# =============================================================================
# GATE-SITE graceful-degradation integration tests (drive main() end-to-end,
# not just the detector unit): on a degraded detection input the ORIGINAL
# deny message stands verbatim — no augmentation, decision unchanged.
# =============================================================================


def test_gate_site_claude_md_absent_falls_back_to_original_message(
    tmp_path, monkeypatch, capsys
):
    """M2: when the project CLAUDE.md is ABSENT (the worktree/gitignored case
    the PR leans on), the detector returns None → the rule-⑧ deny keeps its
    ORIGINAL message verbatim, end-to-end through dispatch_gate.main().

    Distinct from the healthy-recorded==live disable leg: there NO file is
    written at all, and CLAUDE_PROJECT_DIR points at a dir with no CLAUDE.md
    (neither .claude/CLAUDE.md nor ./CLAUDE.md), exercising the
    `content is None` branch rather than the recorded==actual branch.
    """
    _full_setup(monkeypatch, tmp_path, tasks=(("someone-else", "pending"),))
    _seed_team_with_lead(
        tmp_path, _TEAM, _LIVE_SESSION_ID, tasks=(("someone-else", "pending"),)
    )
    # Point CLAUDE_PROJECT_DIR at an empty dir — NO CLAUDE.md on either path.
    empty_project = tmp_path / "no_claude_md_project"
    empty_project.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(empty_project))
    assert not (empty_project / "CLAUDE.md").exists()
    assert not (empty_project / ".claude" / "CLAUDE.md").exists()

    code, out = _run_main(_make_input(), capsys)

    assert code == 2, "decision unchanged (DENY) when CLAUDE.md absent"
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "no Task assigned" in reason, "original deny preserved"
    assert _AUGMENT_MARKER not in reason, "no augmentation without a CLAUDE.md to compare"
    assert _REALIGN_MARKER not in reason


def test_gate_site_unset_project_dir_falls_back_to_original_message(
    tmp_path, monkeypatch, capsys
):
    """M2 sibling: CLAUDE_PROJECT_DIR UNSET → detector returns None (cannot
    locate CLAUDE.md) → original deny message, end-to-end."""
    _full_setup(monkeypatch, tmp_path, tasks=(("someone-else", "pending"),))
    _seed_team_with_lead(
        tmp_path, _TEAM, _LIVE_SESSION_ID, tasks=(("someone-else", "pending"),)
    )
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

    code, out = _run_main(_make_input(), capsys)

    assert code == 2
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "no Task assigned" in reason
    assert _AUGMENT_MARKER not in reason


@pytest.mark.parametrize(
    "bad_session_id",
    ["", "   ", "unknown-abcd1234", "good\nbad"],
    ids=["empty", "whitespace_only", "unknown_sentinel", "control_char"],
)
def test_gate_site_bad_session_id_falls_back_to_original_message(
    bad_session_id, tmp_path, monkeypatch, capsys
):
    """M3: an invalid/sentinel/control-char stdin session_id makes the detector
    return None (per _is_unknown_or_missing_session — an unvalidated id must
    never be interpolated into the warning) → the rule-⑧ deny keeps its
    ORIGINAL message, even though a stale-recorded CLAUDE.md is present.

    The CLAUDE.md records a DIFFERENT id, so were the bad id naively compared
    it would 'mismatch' and wrongly augment; the predicate gate must suppress
    that. Drives main() end-to-end with a hand-built input carrying the bad
    session_id (the shared _make_input hardcodes a valid one).
    """
    _full_setup(monkeypatch, tmp_path, tasks=(("someone-else", "pending"),))
    _seed_team_with_lead(
        tmp_path, _TEAM, _LIVE_SESSION_ID, tasks=(("someone-else", "pending"),)
    )
    _write_project_claude_md(monkeypatch, tmp_path, _RECORDED_STALE_ID)

    bad_input = _make_input()
    bad_input["session_id"] = bad_session_id

    code, out = _run_main(bad_input, capsys)

    assert code == 2, "decision unchanged (DENY) under a bad session_id"
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "no Task assigned" in reason, "original deny preserved"
    assert _AUGMENT_MARKER not in reason, (
        "a bad/sentinel session_id must NOT be compared or interpolated → "
        "no augmentation"
    )


# =============================================================================
# Bounded deny-message length (SEC): a pathologically long recorded/actual
# session_id must NOT be reflected verbatim into the deny message. Written
# tolerantly of the exact cap locus (devops owns the hook-side cap) — asserts
# the full long id is NOT present AND the augmented region is length-bounded.
# =============================================================================


def test_deny_message_bounds_oversized_recorded_session_id(
    tmp_path, monkeypatch, capsys
):
    """SEC: when the CLAUDE.md Resume line records an absurdly long session_id,
    the augmented deny message must not echo it in full (a cap bounds the
    reflected id). Robust to the exact cap value/locus: asserts the raw
    300-char id does not appear verbatim, while the augmentation still fires
    (a real mismatch is detected).

    The recorded id is hex-shaped (matches _RESUME_LINE_RE's [0-9a-f-]+) so the
    detector genuinely sees a mismatch vs the live 'test-session'.
    """
    oversized = "a" * 300  # hex-class, far exceeds any reasonable id length
    _full_setup(monkeypatch, tmp_path, tasks=(("someone-else", "pending"),))
    _seed_team_with_lead(
        tmp_path, _TEAM, _LIVE_SESSION_ID, tasks=(("someone-else", "pending"),)
    )
    _write_project_claude_md(monkeypatch, tmp_path, oversized)

    code, out = _run_main(_make_input(), capsys)

    assert code == 2
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    # Augmentation fired (a genuine mismatch was detected)...
    assert _AUGMENT_MARKER in reason, "oversized id is still a real mismatch"
    # ...but the raw oversized id is NOT reflected verbatim — it is capped.
    assert oversized not in reason, (
        "the full oversized recorded session_id must not be echoed into the "
        "deny message (it must be length-capped)"
    )
    # Defense-in-depth, tolerant of the exact cap value: devops's stated cap is
    # 64, so a contiguous run far above it must not survive. Allow generous
    # slack (100) so this is not brittle to the precise locus while still
    # failing on a verbatim 300-char echo.
    assert "a" * 100 not in reason, "no ~100-char raw-id run should survive the cap"
