"""
Location: pact-plugin/tests/test_handoff_unclaim_twin_917.py
Summary: #917 R1 (compensating-unclaim) F3-TWIN coverage for the LEAD-side b2
         emit (task_lifecycle_gate._emit_lead_side_agent_handoff), under BOTH
         topologies. The b1 (agent_handoff_emitter) unclaim is already pinned by
         test_emitter_idempotency.py::test_journal_write_failure_unclaims_marker_
         so_a_retry_can_reemit — but that twin had NO b2 counterpart and no
         topology axis. R1 is a declared F3 twin across b1+b2, so a b2-only
         regression (marker left claimed on a lead-side write-failure) would
         silently re-open the claim-without-write poison on the lead path.
Used by: the pact-plugin test suite (standing both-modes merge gate).

NON-VACUITY (documented for the verifier): neuter the b2 R1 unclaim by
source-only-reverting the `if not written: unclaim(...)` rollback in
task_lifecycle_gate._emit_lead_side_agent_handoff — run in an ISOLATED throwaway
worktree (`git worktree add --detach /tmp/verify917 HEAD`), NEVER the shared
tree. Expected cardinality: the marker persists after the forced False return,
so the retry hits already_emitted()==True and the second-emit assertion FAILS
(net: the marker-persists assertion + the re-emit assertion flip RED). Restore
via `git worktree remove --force`.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import task_lifecycle_gate as tlg  # noqa: E402
from shared.agent_handoff_marker import occupant_hash  # noqa: E402
from fixtures.emitter import VALID_HANDOFF  # noqa: E402

TEAM = "pact-test"
TASK_ID = "b2-unclaim-probe"
OWNER = "probe-agent"
SUBJECT = "lead-side write fails"


def _marker(home: Path) -> Path:
    occ = occupant_hash(OWNER, SUBJECT)
    return home / ".claude" / "teams" / TEAM / ".agent_handoff_emitted" / f"{TASK_ID}-{occ}"


class TestB2CompensatingUnclaimTwin:
    """The lead-side b2 emit must compensating-unclaim on a write-failure, in
    parity with b1. Topology-parametrized: the lead's journal is writable in
    BOTH in-process and tmux (it is the lead's own session), so the unclaim is
    topology-invariant — that invariance IS the assertion (a regression would
    not depend on mode)."""

    @pytest.mark.parametrize(
        "session_id", ["lead-session", "alt-lead-session"], ids=["in_process", "tmux"]
    )
    def test_b2_write_failure_unclaims_then_retry_reemits(
        self, tmp_path, monkeypatch, pact_context, session_id
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        # Persisted lead context → get_journal_path() resolves (writable), so b2
        # passes the #917 writability gate and reaches the claim+append+unclaim —
        # isolating the RESIDUAL case the gate does NOT cover (a writable-path
        # append that nonetheless returns False).
        pact_context(team_name=TEAM, session_id=session_id)
        meta = {"handoff": VALID_HANDOFF}

        # --- first fire: append_event returns False AFTER the marker is claimed ---
        calls = {"n": 0}
        monkeypatch.setattr(tlg, "append_event", lambda e: calls.__setitem__("n", calls["n"] + 1) or False)
        tlg._emit_lead_side_agent_handoff(TEAM, TASK_ID, OWNER, SUBJECT, meta)
        assert calls["n"] == 1, "the write path IS attempted (append called once)"
        assert not _marker(tmp_path).exists(), (
            "#917 R1 b2 TWIN: a failed lead-side write must UNCLAIM the marker "
            "(parity with b1) — else the lead path permanently suppresses every "
            "later fire for this key (claim-without-write poison)."
        )

        # --- retry: append succeeds → re-emits (proves the unclaim restored it) ---
        monkeypatch.setattr(tlg, "append_event", lambda e: calls.__setitem__("n", calls["n"] + 1) or True)
        tlg._emit_lead_side_agent_handoff(TEAM, TASK_ID, OWNER, SUBJECT, meta)
        assert calls["n"] == 2, "after the unclaim, already_emitted()==False → the write is RETRIED"
        assert _marker(tmp_path).exists(), "the successful retry re-claims and persists the marker"

    @pytest.mark.parametrize(
        "session_id", ["lead-session", "alt-lead-session"], ids=["in_process", "tmux"]
    )
    def test_b2_successful_write_keeps_marker_no_unclaim(
        self, tmp_path, monkeypatch, pact_context, session_id
    ):
        """Positive control: a SUCCESSFUL b2 write claims and KEEPS the marker
        (the unclaim fires only on failure) — and dedups a second fire to one
        event. Proves the unclaim above is failure-gated, not unconditional."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        pact_context(team_name=TEAM, session_id=session_id)
        meta = {"handoff": VALID_HANDOFF}

        calls = {"n": 0}
        monkeypatch.setattr(tlg, "append_event", lambda e: calls.__setitem__("n", calls["n"] + 1) or True)
        tlg._emit_lead_side_agent_handoff(TEAM, TASK_ID, OWNER, SUBJECT, meta)
        assert calls["n"] == 1 and _marker(tmp_path).exists(), "a successful write keeps the marker"
        # second fire: already_emitted()==True → suppressed (no double-emit, no unclaim)
        tlg._emit_lead_side_agent_handoff(TEAM, TASK_ID, OWNER, SUBJECT, meta)
        assert calls["n"] == 1, "the claimed marker dedups the second fire to exactly one event"
        assert _marker(tmp_path).exists(), "a successful claim is NOT unclaimed on a dedup'd re-fire"
