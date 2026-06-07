"""
Location: pact-plugin/tests/test_audit_summary_overwrite_906.py
Summary: COMPREHENSIVE #906 auditor-verdict overwrite-protection coverage —
         the codified-mirror gate (MIRROR / RECOVER) in task_lifecycle_gate.py.
         Sibling file to test_task_lifecycle_gate.py's 5 backend smoke tests
         (test_906_*); this file does NOT re-assert those — it adds the
         unit-level severity-ladder logic, always-preserve-regardless-of-
         direction, the auditor-TEAMMATE-process both-modes MIRROR (auditor
         TEST-FOCUS item b), idempotency, override-authority, and the
         recursion guard. Kept in a sibling file so the 115K primary gate-test
         file's tight fire-count assertions are not diluted (sibling-file
         convention for a focused matrix).
Used by: the pact-plugin test suite (standing merge gate).

The mechanism (one hook, is_lead-branched):
  MIRROR  (non-lead writes audit_summary)   → snapshot → audit_summary_authored
  RECOVER (lead overwrites a DIVERGENT       → advisory audit_summary_overwrite
           authored verdict)                   + route lead value → lead_close_note;
                                                authored PRESERVED (no clobbered read).
Preservation is UNCONDITIONAL (any direction); a destructive downgrade
(severity lowered, e.g. RED->GREEN) escalates the advisory WORDING only.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import task_lifecycle_gate as tlg  # noqa: E402

TEAM = "test-team"
GREEN = {"signal": "GREEN", "note": "clean"}
YELLOW = {"signal": "YELLOW", "findings": ["stale comment"], "scope": "x"}
RED = {"signal": "RED", "findings": ["sql injection"], "scope": "backend"}


def _seed(tmp_path: Path, task_id="1", **fields) -> None:
    d = tmp_path / ".claude" / "tasks" / TEAM
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{task_id}.json").write_text(json.dumps({"id": task_id, **fields}), encoding="utf-8")


def _read_back(tmp_path: Path, task_id="1") -> dict:
    p = tmp_path / ".claude" / "tasks" / TEAM / f"{task_id}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _update(agent_type, audit_summary, task_id="1", extra_meta=None):
    meta = {"audit_summary": audit_summary}
    if extra_meta:
        meta.update(extra_meta)
    return {
        "tool_name": "TaskUpdate",
        "agent_type": agent_type,
        "tool_input": {"taskId": task_id, "metadata": meta},
        "tool_response": {},
    }


def _has(advisories, rule="audit_summary_overwrite"):
    return any(r == rule for r, _ in advisories)


def _msg(advisories, rule="audit_summary_overwrite"):
    return next((m for r, m in advisories if r == rule), "")


# ===========================================================================
# 1. Severity-ladder unit logic (pure; the destructive-downgrade discriminator)
# ===========================================================================
class TestSignalRank:
    def test_known_signals_rank_ascending(self):
        assert tlg._audit_signal_rank(GREEN) == 0
        assert tlg._audit_signal_rank(YELLOW) == 1
        assert tlg._audit_signal_rank(RED) == 2

    def test_case_and_whitespace_insensitive(self):
        assert tlg._audit_signal_rank({"signal": " red "}) == 2
        assert tlg._audit_signal_rank({"signal": "Green"}) == 0

    def test_unrankable_shapes_return_none(self):
        assert tlg._audit_signal_rank(None) is None
        assert tlg._audit_signal_rank("RED") is None        # not a dict
        assert tlg._audit_signal_rank({}) is None            # no signal
        assert tlg._audit_signal_rank({"signal": 7}) is None  # non-str
        assert tlg._audit_signal_rank({"signal": "PURPLE"}) is None  # unknown


class TestDestructiveDowngrade:
    def test_downgrade_true_only_when_severity_lowered(self):
        assert tlg._is_destructive_audit_downgrade(RED, GREEN) is True
        assert tlg._is_destructive_audit_downgrade(RED, YELLOW) is True
        assert tlg._is_destructive_audit_downgrade(YELLOW, GREEN) is True

    def test_upgrade_and_lateral_are_not_downgrades(self):
        assert tlg._is_destructive_audit_downgrade(GREEN, RED) is False     # upgrade
        assert tlg._is_destructive_audit_downgrade(YELLOW, YELLOW) is False  # lateral
        assert tlg._is_destructive_audit_downgrade(GREEN, GREEN) is False

    def test_unknown_either_side_is_not_a_downgrade(self):
        # Cannot rank → no escalation (the advisory still fires, just without the
        # downgrade emphasis). Conservative: never falsely cry "destructive".
        assert tlg._is_destructive_audit_downgrade({"signal": "PURPLE"}, GREEN) is False
        assert tlg._is_destructive_audit_downgrade(RED, {"note": "no signal"}) is False


# ===========================================================================
# 2. RECOVER — always-preserve regardless of DIRECTION; override authority
# ===========================================================================
class TestRecoverPreservation:
    def test_upgrade_also_preserves_and_advises_without_downgrade_wording(
        self, tmp_path, monkeypatch, pact_context
    ):
        """GREEN(authored) -> RED(lead) is an UPGRADE, not a downgrade — it must
        STILL fire the advisory + preserve the authored verdict + route the lead
        value, but the wording must NOT say DESTRUCTIVE DOWNGRADE. Proves
        preservation is unconditional w.r.t. direction (the architect ruling)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="test-session")
        _seed(tmp_path, metadata={"audit_summary": GREEN, "audit_summary_authored": GREEN})
        adv = tlg.evaluate_lifecycle(_update("pact-orchestrator", RED))
        assert _has(adv), "an upgrade overwrite still fires the advisory"
        assert "DESTRUCTIVE DOWNGRADE" not in _msg(adv), "an upgrade is NOT a destructive downgrade"
        back = _read_back(tmp_path)
        assert back["metadata"]["audit_summary_authored"] == GREEN, "authored preserved on upgrade"
        assert back["metadata"]["lead_close_note"] == RED, "lead value routed to lead_close_note"

    def test_live_audit_summary_not_restored_override_authority_intact(
        self, tmp_path, monkeypatch, pact_context
    ):
        """The gate PRESERVES to the mirror but does NOT restore the live
        audit_summary to the authored value — the lead's override stands. Seed
        the platform-applied state (audit_summary already == lead value on disk)
        and confirm the gate leaves it as the lead value."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="test-session")
        # Platform write already landed: live audit_summary == lead's GREEN; the
        # auditor's RED is preserved only in the mirror.
        _seed(tmp_path, metadata={"audit_summary": GREEN, "audit_summary_authored": RED})
        tlg.evaluate_lifecycle(_update("pact-orchestrator", GREEN))
        back = _read_back(tmp_path)
        assert back["metadata"]["audit_summary"] == GREEN, (
            "live audit_summary is NOT reverted to authored — lead override authority intact"
        )
        assert back["metadata"]["audit_summary_authored"] == RED, "authored still preserved in the mirror"


# ===========================================================================
# 3. MIRROR — both-modes (auditor TEAMMATE process), idempotency, refresh
# ===========================================================================
class TestMirrorBothModes:
    @pytest.mark.parametrize("session_id", ["test-session", "teammate-session"],
                             ids=["in_process", "tmux"])
    def test_mirror_persists_from_auditor_teammate_in_both_topologies(
        self, tmp_path, monkeypatch, pact_context, session_id
    ):
        """Auditor TEST-FOCUS (b): the author-time MIRROR fires in the auditor's
        TEAMMATE (non-lead) process in BOTH topologies. Task JSON is team-dir-
        scoped → writable from a teammate process (unlike the journal, which
        self-drops there, #877). A fail-open author-time write would leave no
        RECOVER protection later, so this persistence is load-bearing."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id=session_id)
        _seed(tmp_path, metadata={"audit_summary": RED})
        adv = tlg.evaluate_lifecycle(_update("pact-auditor", RED))
        back = _read_back(tmp_path)
        assert back["metadata"]["audit_summary_authored"] == RED, (
            f"MIRROR must persist audit_summary_authored from the auditor teammate "
            f"process (topology={session_id})"
        )
        assert not _has(adv), "MIRROR is silent (no overwrite advisory on the auditor's own write)"

    def test_mirror_idempotent_skips_redundant_write(
        self, tmp_path, monkeypatch, pact_context
    ):
        """When the mirror already equals the incoming authored verdict, the
        MIRROR branch skips the FS write (idempotent). Spy the writeback to prove
        no redundant disk churn."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="test-session")
        _seed(tmp_path, metadata={"audit_summary": RED, "audit_summary_authored": RED})
        calls = []
        real = tlg._writeback_audit_recovery
        monkeypatch.setattr(tlg, "_writeback_audit_recovery",
                            lambda tid, upd: calls.append((tid, upd)) or real(tid, upd))
        tlg.evaluate_lifecycle(_update("pact-auditor", RED))
        assert calls == [], "MIRROR must skip the FS write when the mirror already matches"

    def test_mirror_refreshes_when_authored_changes(
        self, tmp_path, monkeypatch, pact_context
    ):
        """A non-lead re-write with a CHANGED verdict refreshes the mirror
        (latest-authored semantics)."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="test-session")
        _seed(tmp_path, metadata={"audit_summary": YELLOW, "audit_summary_authored": YELLOW})
        tlg.evaluate_lifecycle(_update("pact-auditor", RED))  # auditor escalates
        assert _read_back(tmp_path)["metadata"]["audit_summary_authored"] == RED, (
            "MIRROR refreshes to the latest authored verdict"
        )


# ===========================================================================
# 4. No-fire guards (false-positive avoidance) + recursion guard
# ===========================================================================
class TestNoFireGuards:
    def test_no_audit_summary_in_update_is_noop(self, tmp_path, monkeypatch, pact_context):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="test-session")
        _seed(tmp_path, metadata={"audit_summary": RED, "audit_summary_authored": RED})
        payload = {
            "tool_name": "TaskUpdate",
            "agent_type": "pact-orchestrator",
            "tool_input": {"taskId": "1", "metadata": {"status": "completed"}},  # no audit_summary
            "tool_response": {},
        }
        adv = tlg.evaluate_lifecycle(payload)
        assert not _has(adv), "a TaskUpdate without an audit_summary never fires the overwrite gate"

    def test_gate_writeback_replay_does_not_refire(self, tmp_path, monkeypatch, pact_context):
        """The gate_writeback recursion guard: a replayed metadata change carrying
        gate_writeback=true must short-circuit the whole gate (no advisory, no
        re-mirror) so the writeback can't recursively re-trigger itself."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="test-session")
        _seed(tmp_path, metadata={"audit_summary": RED, "audit_summary_authored": GREEN})
        calls = []
        monkeypatch.setattr(tlg, "_writeback_audit_recovery",
                            lambda tid, upd: calls.append((tid, upd)))
        adv = tlg.evaluate_lifecycle(
            _update("pact-orchestrator", RED, extra_meta={"gate_writeback": True})
        )
        assert not _has(adv), "gate_writeback replay must not re-fire the advisory"
        assert calls == [], "gate_writeback replay must not re-write"

    def test_non_taskupdate_tool_is_noop(self, tmp_path, monkeypatch, pact_context):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="test-session")
        _seed(tmp_path, metadata={"audit_summary": RED, "audit_summary_authored": GREEN})
        payload = {
            "tool_name": "TaskCreate",
            "agent_type": "pact-orchestrator",
            "tool_input": {"subject": "x", "metadata": {"audit_summary": GREEN}},
            "tool_response": {},
        }
        adv = tlg.evaluate_lifecycle(payload)
        assert not _has(adv), "the overwrite gate is TaskUpdate-scoped"


# An audit_summary whose signal is NOT on the GREEN<YELLOW<RED ladder (or absent)
# → _audit_signal_rank returns None → unrankable.
UNRANKABLE = {"signal": "PURPLE", "note": "off the GREEN<YELLOW<RED ladder"}


class TestUnknownRankRecover:
    """M5: a RECOVER where the prior OR incoming verdict is UNRANKABLE — the
    INTEGRATION the coverage review flagged. _is_destructive_audit_downgrade
    returns False when either side can't be ranked (cannot rank → no escalation),
    so the advisory STILL fires + preserves + routes lead_close_note, but WITHOUT
    the DESTRUCTIVE-DOWNGRADE wording. (The pure logic is pinned in
    TestDestructiveDowngrade; this exercises it through evaluate_lifecycle.)"""

    def test_unrankable_incoming_advises_without_downgrade_wording(
        self, tmp_path, monkeypatch, pact_context
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="test-session")
        _seed(tmp_path, metadata={"audit_summary": RED, "audit_summary_authored": RED})
        adv = tlg.evaluate_lifecycle(_update("pact-orchestrator", UNRANKABLE))
        assert _has(adv), "an overwrite of an authored verdict fires even when incoming is unrankable"
        assert "DESTRUCTIVE DOWNGRADE" not in _msg(adv), (
            "unrankable incoming → cannot rank → no downgrade escalation (the advisory "
            "still fires, just without the severity-lowered emphasis)"
        )
        back = _read_back(tmp_path)
        assert back["metadata"]["audit_summary_authored"] == RED, "authored verdict still PRESERVED"
        assert back["metadata"]["lead_close_note"] == UNRANKABLE, "lead value routed to lead_close_note"

    def test_unrankable_authored_advises_without_downgrade_wording(
        self, tmp_path, monkeypatch, pact_context
    ):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        pact_context(team_name=TEAM, session_id="test-session")
        _seed(tmp_path, metadata={"audit_summary": UNRANKABLE, "audit_summary_authored": UNRANKABLE})
        adv = tlg.evaluate_lifecycle(_update("pact-orchestrator", GREEN))
        assert _has(adv), "advisory fires (authored != incoming)"
        assert "DESTRUCTIVE DOWNGRADE" not in _msg(adv), (
            "unrankable authored → cannot rank → no downgrade escalation"
        )
        assert _read_back(tmp_path)["metadata"]["audit_summary_authored"] == UNRANKABLE, "preserved"
