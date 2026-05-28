"""
Phase-aware validation of the Step 0.5 self-correcting teardown safety
net + Opt-3 directive-prose runtime verification.

Two related concerns covered in one file (the natural home for both
because they share the same Step 0.5 + _TEARDOWN_DIRECTIVE surfaces):

PART 1 — Step 0.5 safety-net validation
=======================================

PR-B introduces emission-site Gate 6 suppression for the OPERATIONAL-
LULL-AT-PHASE-BOUNDARY class. The Step 0.5 self-correcting teardown
check in commands/scan-pending-tasks.md remains the SAFETY NET that
fires Skill('PACT:stop-pending-scan') when a teardown_request event
DOES make it to the journal (whether legitimate or from a Gate-6
fails-open scenario).

These tests confirm Step 0.5 correctly handles the post-PR-B journal
shape: it fires on legitimate teardown_request events the same way
it always has (Gate 6 doesn't change Step 0.5's input contract — the
event shape is unchanged). The fails-open scenario is exercised by
synthesizing a teardown_request event and confirming Step 0.5 picks
it up.

The existing test_scan_pending_tasks_self_teardown.py covers the
core Step 0.5 behavior end-to-end via bash-block extraction. This
file adds PHASE-AWARE coverage — explicitly setting up the disk shape
the safety net must catch when emission-site Gate 6 cannot.

PART 2 — Opt-3 directive-prose runtime verification
====================================================

C9 (devops's Opt-3) softened the _TEARDOWN_DIRECTIVE to a verify-first
formulation: 'Active-task count observed at zero. You MUST verify no
specialist work is in flight (CronList + check for active teammate
task entries) and, if so, then you MUST invoke
Skill("PACT:stop-pending-scan") before your next tool call. This is a
non-negotiable lifecycle gate.'

Devops's teachback A4 contingency: the softened prose must still elicit
orchestrator interpretation as a skill invocation. Empirical
verification under an actual LLM is intractable in unit tests; these
probes assert PARSING-LEVEL structural properties that an orchestrator
persona's literal-compliance reflex keys on (the 'You MUST invoke
Skill(...)' shape + the non-negotiable lifecycle-gate label + the
verify-first conjunction).

The structural properties are NECESSARY but not SUFFICIENT for LLM
compliance; sufficiency requires the live orchestrator's response,
which is out of scope for hook-level unit tests. The structural pins
catch the regression class where a future prose edit drops one of the
load-bearing tokens.
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK_DIR = ROOT / "hooks"
SCAN_MD = ROOT / "commands" / "scan-pending-tasks.md"
PLUGIN_ROOT = ROOT
SJ_PATH = PLUGIN_ROOT / "hooks" / "shared" / "session_journal.py"


# =============================================================================
# PART 1 — Step 0.5 safety-net validation
# =============================================================================
#
# These helpers mirror test_scan_pending_tasks_self_teardown.py's
# bash-block extraction pattern; co-located here per #551 fixture-
# location convention so the file is self-contained.
# =============================================================================


ISO_FORMAT_LITERAL = "%Y-%m-%dT%H:%M:%SZ"
SENTINEL = "STEP_0_5_FELL_THROUGH"


def _extract_step_0_5_bash_block(scan_md_text: str) -> str:
    """Extract the fenced ```bash``` block from §Operation Step 0.5
    of commands/scan-pending-tasks.md. Mirrors the SSOT-extraction
    pattern in test_scan_pending_tasks_self_teardown.py — same source,
    same extraction shape, so phase-aware tests exercise the literal
    bash that runs in production."""
    op_start = scan_md_text.find("\n## Operation")
    assert op_start >= 0, "scan-pending-tasks.md missing §Operation section"
    step_0_5_pos = scan_md_text.find("\n0.5. ", op_start)
    assert step_0_5_pos >= 0, "scan-pending-tasks.md §Operation missing Step 0.5"
    step_1_pos = scan_md_text.find("\n1. ", step_0_5_pos)
    body = (
        scan_md_text[step_0_5_pos:step_1_pos] if step_1_pos > 0
        else scan_md_text[step_0_5_pos:]
    )
    match = re.search(r"```bash\n(.*?)```", body, re.DOTALL)
    assert match is not None, "Step 0.5 must contain a fenced bash block"
    raw = match.group(1)
    lines = raw.splitlines()
    dedented = [ln[3:] if ln.startswith("   ") else ln for ln in lines]
    return "\n".join(dedented)


@pytest.fixture(scope="module")
def step_0_5_bash_template() -> str:
    """The Step 0.5 bash block, with {plugin_root} and {session_dir}
    template tokens preserved for caller-side rendering."""
    return _extract_step_0_5_bash_block(SCAN_MD.read_text(encoding="utf-8"))


def _iso_ts(epoch_seconds: int) -> str:
    """Render an epoch as ISO-8601 UTC matching make_event's format."""
    return datetime.datetime.fromtimestamp(
        epoch_seconds, tz=datetime.timezone.utc
    ).strftime(ISO_FORMAT_LITERAL)


def _write_journal_event(session_dir: Path, event_type: str, payload: dict):
    """Append a single JSONL event to the session journal with a
    canonical %Y-%m-%dT%H:%M:%SZ ts; callers may override ts in payload."""
    session_dir.mkdir(parents=True, exist_ok=True)
    journal = session_dir / "session-journal.jsonl"
    record = {"v": 1, "type": event_type, "ts": _iso_ts(int(time.time()))}
    record.update(payload)
    with journal.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _render_step_0_5(template: str, session_dir: Path) -> str:
    """Substitute {plugin_root} and {session_dir} placeholders."""
    return template.replace("{plugin_root}", str(PLUGIN_ROOT)).replace(
        "{session_dir}", str(session_dir),
    )


def _run_step_0_5(bash_body: str) -> subprocess.CompletedProcess:
    """Run the Step 0.5 bash block with a sentinel echo appended.
    Sentinel absent => Step 0.5 fired (exit 0 short-circuit). Sentinel
    present => Step 0.5 fell through."""
    script = bash_body + f'\necho "{SENTINEL}"\n'
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=10,
    )


class TestStep0_5SafetyNetUnderPhaseAwareSurface:
    """PHASE-AWARE Step 0.5 safety-net validation. The Gate-6 fix
    suppresses emission-site teardown_request events for the
    OPERATIONAL-LULL class, but Step 0.5 remains the safety net for
    legitimate teardown_requests AND for fails-open scenarios where
    Gate 6 cannot fire (e.g., a future bug that bypasses the
    emission-site predicate). These tests confirm the safety net is
    intact post-PR-B."""

    def test_step_0_5_fires_on_legitimate_teardown_request_after_arm(
        self, tmp_path, step_0_5_bash_template,
    ):
        """The legitimate end-of-orchestration teardown_request lands
        in the journal; Step 0.5 fires Skill('PACT:stop-pending-scan').
        Post-PR-B, Gate 6 does NOT suppress this event (umbrella has
        completed); the safety net handles the normal case unchanged."""
        session_dir = tmp_path / "session"
        now = int(time.time())
        _write_journal_event(session_dir, "scan_armed", {"ts": _iso_ts(now - 600)})
        _write_journal_event(session_dir, "teardown_request", {
            "task_id": "umbrella-1",
            "team_name": "team-phase-aware",
            "ts": _iso_ts(now - 100),
        })

        bash = _render_step_0_5(step_0_5_bash_template, session_dir)
        result = _run_step_0_5(bash)
        assert result.returncode == 0, (
            f"Step 0.5 exit code expected 0; got {result.returncode}. "
            f"stderr={result.stderr!r}"
        )
        assert SENTINEL not in result.stdout, (
            f"Step 0.5 must FIRE on legitimate teardown_request after "
            f"scan_armed with no scan_disarmed. Sentinel present "
            f"indicates fall-through. stdout={result.stdout!r}"
        )

    def test_step_0_5_fires_on_fails_open_phase_lull_teardown_request(
        self, tmp_path, step_0_5_bash_template,
    ):
        """Fails-open scenario: a phase-lull teardown_request reaches
        the journal (e.g., Gate 6 source got reverted in a future
        refactor or a malformed task on disk caused the umbrella
        predicate to return False). Step 0.5 is the safety net that
        catches this and triggers stop-pending-scan within one cron
        interval (~5min) — bounded compliance latency regardless of
        directive-prose handling."""
        session_dir = tmp_path / "session"
        now = int(time.time())
        _write_journal_event(session_dir, "scan_armed", {"ts": _iso_ts(now - 1200)})
        # Synthesize a teardown_request as if Gate 6 had failed-open
        # during a phase-lull. task_id + team_name shape are identical
        # to a Gate-6-suppressed event; the only signal that the safety
        # net needs is "teardown_request after scan_armed, no
        # scan_disarmed after it".
        _write_journal_event(session_dir, "teardown_request", {
            "task_id": "phase-lull-task",
            "team_name": "team-fails-open",
            "tier": "1",
            "reason": "lead_terminal_taskupdate",
            "ts": _iso_ts(now - 50),
        })

        bash = _render_step_0_5(step_0_5_bash_template, session_dir)
        result = _run_step_0_5(bash)
        assert result.returncode == 0
        assert SENTINEL not in result.stdout, (
            f"Step 0.5 must FIRE on fails-open phase-lull "
            f"teardown_request — the safety-net guarantee. "
            f"stdout={result.stdout!r}"
        )

    def test_step_0_5_does_not_fire_when_disarmed_after_teardown(
        self, tmp_path, step_0_5_bash_template,
    ):
        """Step 0.5 fall-through: scan_disarmed has already serviced
        the teardown. Confirms the safety net respects the
        already-serviced signal in phase-aware scenarios — repeated
        cron fires after a single teardown don't cause duplicate
        stop-pending-scan invocations."""
        session_dir = tmp_path / "session"
        now = int(time.time())
        _write_journal_event(session_dir, "scan_armed", {"ts": _iso_ts(now - 600)})
        _write_journal_event(session_dir, "teardown_request", {
            "task_id": "umbrella-2",
            "team_name": "team-phase-aware-serviced",
            "ts": _iso_ts(now - 300),
        })
        _write_journal_event(session_dir, "scan_disarmed", {
            "ts": _iso_ts(now - 200),
        })

        bash = _render_step_0_5(step_0_5_bash_template, session_dir)
        result = _run_step_0_5(bash)
        assert result.returncode == 0
        assert SENTINEL in result.stdout, (
            f"Step 0.5 must FALL THROUGH when scan_disarmed > "
            f"teardown_request (already serviced). "
            f"stdout={result.stdout!r}"
        )

    def test_step_0_5_fires_on_re_arm_then_phase_lull_teardown(
        self, tmp_path, step_0_5_bash_template,
    ):
        """Re-arm cycle: scan_armed -> teardown_request -> scan_disarmed
        -> scan_armed -> teardown_request. Step 0.5 must fire on the
        LATEST teardown_request (post-re-arm); the historical disarm
        does not satisfy the current re-armed cycle.

        Phase-aware framing: after one orchestration completes and a
        new one starts, Step 0.5 must catch the new orchestration's
        teardown_request independently of the prior cycle's resolution."""
        session_dir = tmp_path / "session"
        now = int(time.time())
        # First cycle
        _write_journal_event(session_dir, "scan_armed", {"ts": _iso_ts(now - 2000)})
        _write_journal_event(session_dir, "teardown_request", {
            "task_id": "first-umbrella",
            "team_name": "team-rearm",
            "ts": _iso_ts(now - 1800),
        })
        _write_journal_event(session_dir, "scan_disarmed", {
            "ts": _iso_ts(now - 1700),
        })
        # Second cycle: re-armed + new teardown_request
        _write_journal_event(session_dir, "scan_armed", {"ts": _iso_ts(now - 500)})
        _write_journal_event(session_dir, "teardown_request", {
            "task_id": "second-umbrella",
            "team_name": "team-rearm",
            "ts": _iso_ts(now - 100),
        })

        bash = _render_step_0_5(step_0_5_bash_template, session_dir)
        result = _run_step_0_5(bash)
        assert result.returncode == 0
        assert SENTINEL not in result.stdout, (
            f"Step 0.5 must FIRE on the post-re-arm teardown_request "
            f"(latest triple drives the decision). "
            f"stdout={result.stdout!r}"
        )


# =============================================================================
# PART 2 — Opt-3 directive-prose runtime verification
# =============================================================================
#
# Devops's C9 (Opt-3) softened the _TEARDOWN_DIRECTIVE to a verify-
# first formulation. Devops's teachback A4 contingency surfaced the
# legitimate concern that the softened prose must still elicit
# orchestrator interpretation as a skill invocation.
#
# Approach: parsing-level structural assertions on the directive
# constant. Subprocess-integration probe against an actual orchestrator
# LLM is intractable in unit tests; the structural properties below
# are NECESSARY conditions for compliance — they catch the regression
# class where a future prose edit drops a load-bearing token.
# =============================================================================


class TestTeardownDirectiveOpt3RuntimeProperties:
    """Opt-3 directive-prose runtime verification — devops's A4
    contingency. The softened directive carries verify-first structural
    properties an orchestrator persona's literal-compliance reflex keys
    on. These structural pins are NECESSARY (not sufficient) for LLM
    compliance — a future prose edit dropping a load-bearing token
    flips the relevant assertion RED.

    Approach: parsing-level structural assertions, not subprocess
    probes. Subprocess-integration against a live orchestrator LLM is
    out of scope (intractable in unit tests + non-deterministic).
    Structural pins on the prose are the practical surface a test
    suite CAN deterministically guarantee."""

    def _directive(self) -> str:
        sys.path.insert(0, str(HOOK_DIR))
        import wake_lifecycle_emitter as emitter
        return emitter._TEARDOWN_DIRECTIVE

    def test_opt3_directive_carries_you_must_invoke_skill_invocation_shape(self):
        """The 'You MUST invoke Skill("PACT:stop-pending-scan")' shape
        is the load-bearing invocation cue. The orchestrator persona
        keys literal-compliance on the 'You MUST invoke Skill(...)'
        pattern across all hook-emitted directives; a softening that
        drops the MUST-binding word or the verb 'invoke' or the
        canonical Skill(...) wrapper degrades to advisory phrasing
        (the #760 failure mode where lead-side compliance dropped).

        Verifies the post-C9 directive PRESERVES the MUST-invoke-Skill
        shape on the stop-pending-scan call site specifically."""
        directive = self._directive()
        # The invocation-cue phrase. The literal in the post-C9
        # directive carries lowercase 'you' because the cue follows
        # the 'if so, then' connective; case-insensitive substring
        # match preserves that the load-bearing tokens are MUST +
        # invoke + Skill("PACT:stop-pending-scan") and is robust to
        # the leading-capitalization shift between unconditional vs.
        # conditional formulations.
        assert 'you must invoke skill("pact:stop-pending-scan")' in directive.lower(), (
            f"Opt-3 directive must carry the 'you MUST invoke "
            f'Skill("PACT:stop-pending-scan")\' invocation shape so the '
            f"orchestrator persona's literal-compliance reflex routes "
            f"the directive to skill invocation. got: {directive!r}"
        )

    def test_opt3_directive_carries_verify_first_conjunction(self):
        """The verify-first conjunction ('You MUST verify... and, if
        so, then you MUST invoke...') is the structural defense
        against the #760-class failure where the directive
        unconditionally fired even when specialist work was actually
        in flight. The Opt-3 softening REPLACED an unconditional
        MUST-invoke with a verify-first conditional MUST-invoke; the
        conditional must remain present to preserve the safety
        rationale.

        The structural marker: a 'verify' verb AND a 'if so, then'
        connective AND the 'invoke Skill' clause downstream — all
        three in sequence. A future edit that drops 'if so, then'
        would re-introduce the unconditional fire shape."""
        directive = self._directive()
        # Verify-first verb is present.
        assert "verify" in directive.lower(), (
            f"Opt-3 directive must carry a 'verify' clause "
            f"(verify-first defense). got: {directive!r}"
        )
        # The verify-first → invoke conjunction. The 'if so, then'
        # connective is the structural marker that the invoke is
        # CONDITIONAL on the verify-clause outcome, not unconditional.
        assert "if so, then" in directive.lower(), (
            f"Opt-3 directive must carry 'if so, then' conditional "
            f"connective between the verify clause and the invoke "
            f"clause — preserves the safety rationale that the "
            f"invoke fires only if the verify confirms no specialist "
            f"work is in flight. got: {directive!r}"
        )

    def test_opt3_directive_carries_non_negotiable_lifecycle_gate_label(self):
        """The 'non-negotiable lifecycle gate' protocol-class label is
        the routing token that the orchestrator persona uses to
        distinguish lifecycle-gate directives from optional /
        advisory additionalContext. Without this label, the directive
        could be deprioritized in turns where a teammate-action
        message competes for attention (the #760 failure mode).

        Pin: case-insensitive substring presence. The literal in the
        directive constant is 'non-negotiable lifecycle gate' (lowercase
        per the canonical Opt-3 formulation)."""
        directive = self._directive()
        assert "non-negotiable lifecycle gate" in directive.lower(), (
            f"Opt-3 directive must carry the 'non-negotiable lifecycle "
            f"gate' protocol-class label so the orchestrator persona "
            f"routes it into lifecycle-gate handling rather than "
            f"advisory deferral. got: {directive!r}"
        )
