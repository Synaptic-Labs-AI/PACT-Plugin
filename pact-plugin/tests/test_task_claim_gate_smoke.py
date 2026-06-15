"""Smoke tests for task_claim_gate.py — PreToolUse hook (matcher="Edit|Write|Bash")
that nudges/auto-claims a teammate's pre-assigned, just-unblocked Task B
(#961 Cycle 2).

NOT comprehensive coverage — the both-teammateMode matrix + non-vacuity is
TEST-phase scope (the §11 matrix). These cases lock the load-bearing M1
decisions so a regression surfaces fast:

  S1. Module loads + lead frame → NO-OP (suppress), exercised through main().
  S2. In-process (session_id == leadSessionId) + a claimable owned task →
      GENERIC attribution-free nudge (no task id named).
  S3. Tmux + registry-confident + exactly one owned-unblocked-pending task →
      M2 auto-flip: status → in_progress on disk, gate_writeback marker set,
      sibling top-level keys preserved (whole-json write), auto-claimed note.
  S4. Tmux + registry-confident + TWO owned-unblocked-pending → list nudge,
      never a flip.
  S5. Tmux + registry MISS (unconfident identity) → GENERIC nudge, never a
      typed guess.
  S6. §7 unblocked predicate: Task B blockedBy=[A]; A completed → eligible;
      A pending → blocked → NO-OP. (The corrected predicate, not
      blockedBy-empty.)
  S7. F1 idempotency: an owned task already `in_progress` → NO-OP.
  S8. Fail-open: malformed stdin and a missing leadSessionId both → exit 0 /
      NO-OP, never a deny.
  S9. main() advisory output shape: hookSpecificOutput.additionalContext +
      hookEventName == "PreToolUse" + exit 0 (NOT permissionDecision).
  S10. _atomic_claim no-clobber: on-disk status already moved off `pending` →
       aborts, no write (the race re-validation guard).
  S11. M2 write failure → degrade to the single nudge (fail-open), no flip.
"""

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import task_claim_gate as gate  # noqa: E402

TEAM = "test-team"
LEAD = "PACT:pact-orchestrator"
TEAMMATE = "pact-devops-engineer"
LEAD_SID = "lead-session-0001"
TMUX_SID = "tmux-session-0002"


# ─── seeding helpers ─────────────────────────────────────────────────────────


def _seed_config(tmp_path, *, lead_session_id=LEAD_SID, member_names=(TEAMMATE,)):
    teams_dir = tmp_path / ".claude" / "teams" / TEAM
    teams_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "name": TEAM,
        "leadSessionId": lead_session_id,
        "members": [{"name": n, "id": f"a-{n}"} for n in member_names],
    }
    (teams_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")


def _seed_task(tmp_path, task_id, **fields):
    tasks_dir = tmp_path / ".claude" / "tasks" / TEAM
    tasks_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": task_id, **fields}
    (tasks_dir / f"{task_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def _read_task(tmp_path, task_id):
    p = tmp_path / ".claude" / "tasks" / TEAM / f"{task_id}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _payload(*, session_id, agent_type=None, tool_name="Edit", team_name=None):
    p = {"tool_name": tool_name, "session_id": session_id}
    if agent_type is not None:
        p["agent_type"] = agent_type
    if team_name is not None:
        p["team_name"] = team_name
    return p


def _mock_registry(monkeypatch, value):
    """Seam: registry resolve() value (file-format is unit-tested elsewhere)."""
    monkeypatch.setattr(gate, "registry_resolve", lambda sid: value)


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


def _capture_main(payload, capsys):
    with patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
        with pytest.raises(SystemExit) as exc:
            gate.main()
    raw = exc.value.code if exc.value.code is not None else 0
    code = int(raw) if isinstance(raw, int) else 0
    out = capsys.readouterr().out.strip()
    return code, (json.loads(out) if out else None)


# ─── S1: lead frame → NO-OP (through main) ───────────────────────────────────


def test_lead_frame_noop(tmp_path, monkeypatch, capsys):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{TEAMMATE}@{TEAM}")
    code, out = _capture_main(
        _payload(session_id=TMUX_SID, agent_type=LEAD), capsys
    )
    assert code == 0
    assert out == {"suppressOutput": True}


# ─── S2: in-process → generic attribution-free nudge ─────────────────────────


def test_in_process_generic_nudge(tmp_path, monkeypatch):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{TEAMMATE}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=TEAMMATE,
               status="pending", blockedBy=[])
    advisory = gate._evaluate(_payload(session_id=LEAD_SID, agent_type=TEAMMATE))
    assert advisory is not None
    assert advisory == gate._GENERIC_CLAIM_NUDGE
    assert "#B" not in advisory  # attribution-free: never names a task id


# ─── S3: tmux + confident + single candidate → M2 auto-flip + auto-claimed note ─


def test_tmux_single_candidate_m2_autoflip(tmp_path, monkeypatch):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{TEAMMATE}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=TEAMMATE,
               status="pending", blockedBy=[],
               metadata={"variety": {"total": 11}})
    advisory = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=TEAMMATE))
    assert advisory is not None
    assert "Auto-claimed" in advisory and "#B" in advisory
    task = _read_task(tmp_path, "B")
    # M2 flips the TOP-LEVEL status...
    assert task["status"] == "in_progress"
    # ...sets the (non-load-bearing) gate_writeback marker...
    assert task["metadata"]["gate_writeback"] is True
    # ...and preserves every sibling top-level key (whole-json write).
    assert task["owner"] == TEAMMATE
    assert task["blockedBy"] == []
    assert task["metadata"]["variety"] == {"total": 11}


# ─── S4: tmux + confident + multiple candidates → list nudge, never flip ─────


def test_tmux_multi_candidate_list_no_flip(tmp_path, monkeypatch):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{TEAMMATE}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: a", owner=TEAMMATE,
               status="pending", blockedBy=[])
    _seed_task(tmp_path, "C", subject="devops: b", owner=TEAMMATE,
               status="pending", blockedBy=[])
    advisory = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=TEAMMATE))
    assert advisory is not None
    assert "#B" in advisory and "#C" in advisory
    assert _read_task(tmp_path, "B")["status"] == "pending"
    assert _read_task(tmp_path, "C")["status"] == "pending"


# ─── S5: tmux + registry MISS → generic nudge, never a typed guess ───────────


def test_tmux_registry_miss_generic_nudge(tmp_path, monkeypatch):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, None)  # resolve miss → identity unconfident
    _seed_task(tmp_path, "B", subject="devops: implement", owner=TEAMMATE,
               status="pending", blockedBy=[])
    advisory = gate._evaluate(
        _payload(session_id=TMUX_SID, agent_type=TEAMMATE, team_name=TEAM)
    )
    assert advisory == gate._GENERIC_CLAIM_NUDGE
    assert "#B" not in advisory


# ─── S6: §7 unblocked predicate — completed blocker eligible, open blocker not ─


def test_unblocked_predicate_completed_vs_open_blocker(tmp_path, monkeypatch):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{TEAMMATE}@{TEAM}")
    _seed_task(tmp_path, "A", subject="devops: TEACHBACK for x", owner=TEAMMATE,
               status="completed")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=TEAMMATE,
               status="pending", blockedBy=["A"])
    # A completed → B unblocked → eligible (M2 auto-claims; note names #B).
    advisory = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=TEAMMATE))
    assert advisory is not None and "#B" in advisory
    assert _read_task(tmp_path, "B")["status"] == "in_progress"

    # Open blocker → blocked → NO-OP. Re-seed BOTH (the first call flipped B), so
    # the second assertion fails for the RIGHT reason (B blocked, not B already
    # in_progress).
    _seed_task(tmp_path, "A", subject="devops: TEACHBACK for x", owner=TEAMMATE,
               status="pending")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=TEAMMATE,
               status="pending", blockedBy=["A"])
    assert gate._evaluate(_payload(session_id=TMUX_SID, agent_type=TEAMMATE)) is None
    assert _read_task(tmp_path, "B")["status"] == "pending"  # NO-OP, no flip


# ─── S7: F1 idempotency — already in_progress → NO-OP ────────────────────────


def test_idempotency_already_in_progress(tmp_path, monkeypatch):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{TEAMMATE}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=TEAMMATE,
               status="in_progress", blockedBy=[])
    assert gate._evaluate(_payload(session_id=TMUX_SID, agent_type=TEAMMATE)) is None


# ─── S8: fail-open — malformed stdin + missing leadSessionId ─────────────────


def test_failopen_malformed_stdin(tmp_path, capsys):
    with patch.object(sys, "stdin", io.StringIO("{not json")):
        with pytest.raises(SystemExit) as exc:
            gate.main()
    assert (exc.value.code or 0) == 0
    assert json.loads(capsys.readouterr().out.strip()) == {"suppressOutput": True}


def test_failopen_no_lead_session_id(tmp_path, monkeypatch):
    # config without leadSessionId → topology undeterminable → NO-OP.
    teams_dir = tmp_path / ".claude" / "teams" / TEAM
    teams_dir.mkdir(parents=True, exist_ok=True)
    (teams_dir / "config.json").write_text(json.dumps({"name": TEAM}), encoding="utf-8")
    _mock_registry(monkeypatch, f"{TEAMMATE}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=TEAMMATE,
               status="pending", blockedBy=[])
    assert gate._evaluate(_payload(session_id=TMUX_SID, agent_type=TEAMMATE)) is None


# ─── S9: main() advisory output shape ────────────────────────────────────────


def test_main_advisory_output_shape(tmp_path, monkeypatch, capsys):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{TEAMMATE}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=TEAMMATE,
               status="pending", blockedBy=[])
    code, out = _capture_main(
        _payload(session_id=TMUX_SID, agent_type=TEAMMATE), capsys
    )
    assert code == 0
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "#B" in out["hookSpecificOutput"]["additionalContext"]
    assert "permissionDecision" not in json.dumps(out)


# ─── S10: _atomic_claim no-clobber — on-disk status already moved off pending ─


def test_atomic_claim_no_clobber_on_nonpending(tmp_path):
    # Simulate the race: the scan saw `pending`, but the on-disk status moved
    # before the write. _atomic_claim re-reads, sees non-pending, aborts — no
    # overwrite.
    _seed_task(tmp_path, "B", subject="devops: implement", owner=TEAMMATE,
               status="in_progress", blockedBy=[])
    assert gate._atomic_claim("B", TEAM) is False
    assert _read_task(tmp_path, "B")["status"] == "in_progress"  # untouched


# ─── S11: M2 write failure → degrade to single nudge (fail-open), no flip ────


def test_write_failure_falls_back_to_single_nudge(tmp_path, monkeypatch):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{TEAMMATE}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=TEAMMATE,
               status="pending", blockedBy=[])
    monkeypatch.setattr(gate, "_atomic_claim", lambda tid, team: False)
    advisory = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=TEAMMATE))
    assert advisory is not None
    assert "#B" in advisory and "Auto-claimed" not in advisory
    assert _read_task(tmp_path, "B")["status"] == "pending"  # no flip
