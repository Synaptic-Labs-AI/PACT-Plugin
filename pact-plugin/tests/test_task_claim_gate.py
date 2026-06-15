"""Comprehensive §11 both-teammateMode TEST matrix for task_claim_gate.py — the
STANDING MERGE GATE for #961 Cycle 2 (the teammate-side PreToolUse hook that
makes a teammate claim its pre-assigned, just-unblocked Task B `pending →
in_progress` before implementation work).

SUPERSEDES the smoke subset (test_task_claim_gate_smoke.py): this file is a
STRICT SUPERSET — every smoke scenario S1–S11 is present and strengthened (the
cross-reference is noted per test), plus the matrix rows the smoke subset did
not cover (T5, T8 exemption parity, T9 Bash spurious-flip defense, the T10
remainder, T11 non-vacuity, T13 unresolvable-blocker variants). Once this file
is green and confirmed a strict superset, the smoke file is deleted.

Matrix contract: architecture spec §11 (docs/architecture/961-task-claim-gate-
architecture.md), rows T1–T13. Keyed on the STRUCTURAL session-topology signal
(`session_id` vs `leadSessionId`), NEVER a mode flag.

Two disciplines run through the whole file (the §11 Test Data Needs caveat):
  • REGISTER ALL ACTORS in the team config (every task owner appears in
    `members[]` with its `agentType`) so a `count==0` / NO-OP never passes for
    the WRONG reason (under-registration). Exemption parity tests pair the
    exempt case with a POSITIVE CONTROL that fires in the SAME fixture.
  • NON-VACUITY (T11): each branch is neutered via monkeypatch and a SPECIFIC
    named scenario is asserted to INVERT (go from its correct outcome to the
    regression outcome) — removal AND inversion AND mode-branch AND ownership.
    The inversion probe (topology) is the calibration case: a guard that, when
    inverted, green-lights the very wrong-flip it exists to prevent.

The §12.3 platform-fidelity check (`test_T12_3_real_pretooluse_frames_platform_fidelity`)
asserts the role discriminators against THREE real redacted PreToolUse frames
captured live (Claude Code 2.1.177) and committed to `tests/fixtures/role_frames.py`:
agent_type presence on all three (tmux teammate / lead / in-process subagent), the
in-process `session_id == leadSessionId` collapse, and the agent_id present/absent
corroboration. The decision LOGIC (is_lead reads only `agent_type`) is exercised
throughout via synthesized frames; the captured fixture confirms the platform
actually stamps those fields (no remaining skip).
"""

import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

import task_claim_gate as gate  # noqa: E402
import role_frames  # noqa: E402  — committed real-frame fixtures (§12.3 fidelity)

TEAM = "test-team"
LEAD_QUALIFIED = "PACT:pact-orchestrator"      # is_lead True (qualified spelling)
LEAD_UNQUALIFIED = "pact-orchestrator"          # is_lead True (unqualified spelling)
DEVOPS = "pact-devops-engineer"                 # the acting teammate (name == agentType)
OTHER = "pact-database-engineer"                # a second teammate (not-owned cases)
SECRETARY = "secretary"                         # name; agentType pact-secretary (exempt)

LEAD_SID = "lead-session-0001"
TMUX_SID = "tmux-session-0002"
SECRETARY_SID = "tmux-session-secretary-0003"

_HOOK_PATH = Path(__file__).parent.parent / "hooks" / "task_claim_gate.py"
_HOOKS_DIR = Path(__file__).parent.parent / "hooks"

# Register ALL actors used anywhere in this file, each with its agentType, so no
# count/NO-OP assertion passes via under-registration (the §11 Test Data caveat).
_DEFAULT_MEMBERS = (
    {"name": DEVOPS, "agentType": DEVOPS},
    {"name": OTHER, "agentType": OTHER},
    {"name": SECRETARY, "agentType": "pact-secretary"},
)


# ─── ownership-neuter sentinel (T11): a name that == every owner string ───────
class _MatchAnyName(str):
    """A registry name whose `==` is True against any owner string. Injected via
    a monkeypatched `_split_name_team` to faithfully DROP the inline ownership
    match in `mine` without editing the source — proving the owner== guard is
    load-bearing (T11 ownership mutation)."""

    def __eq__(self, other):  # noqa: D105
        return True

    __hash__ = str.__hash__


# ─── seeding helpers (compatible with the superseded smoke file) ──────────────


def _seed_config(tmp_path, *, lead_session_id=LEAD_SID, members=_DEFAULT_MEMBERS):
    teams_dir = tmp_path / ".claude" / "teams" / TEAM
    teams_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "name": TEAM,
        "leadSessionId": lead_session_id,
        "members": [{"id": f"a-{m['name']}", **m} for m in members],
    }
    (teams_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")


def _seed_task(tmp_path, task_id, **fields):
    tasks_dir = tmp_path / ".claude" / "tasks" / TEAM
    tasks_dir.mkdir(parents=True, exist_ok=True)
    payload = {"id": task_id, **fields}
    (tasks_dir / f"{task_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def _seed_raw_task_file(tmp_path, task_id, raw_text):
    """Write a RAW (possibly malformed) task JSON file — for fail-open / corrupt
    blocker tests."""
    tasks_dir = tmp_path / ".claude" / "tasks" / TEAM
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / f"{task_id}.json").write_text(raw_text, encoding="utf-8")


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
    """Seam: fixed registry resolve() value (the on-disk file format is unit-
    tested in test_session_registry; here it is a monkeypatch seam)."""
    monkeypatch.setattr(gate, "registry_resolve", lambda sid: value)


def _mock_registry_map(monkeypatch, mapping):
    """Seam: per-session_id registry resolve() (for multi-actor fixtures)."""
    monkeypatch.setattr(gate, "registry_resolve", lambda sid: mapping.get(sid))


@pytest.fixture(autouse=True)
def _home(monkeypatch, tmp_path):
    # Filesystem isolation: every get_claude_config_dir() read resolves under
    # tmp_path. delenv CLAUDE_CONFIG_DIR so a leaked env var cannot redirect the
    # config root away from tmp_path (which would silence assertions = wrong-
    # reason GREEN).
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


def _capture_main(payload, capsys):
    with patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
        with pytest.raises(SystemExit) as exc:
            gate.main()
    raw = exc.value.code if exc.value.code is not None else 0
    code = int(raw) if isinstance(raw, int) else 0
    out = capsys.readouterr().out.strip()
    return code, (json.loads(out) if out else None)


# =============================================================================
# T1 — BOTH-MODE MATRIX (the hard merge gate): keyed on session TOPOLOGY, not a
#      mode flag. The SAME stdin/tasks must branch ONLY on session_id vs
#      leadSessionId. (Supersedes smoke S2.)
# =============================================================================


def test_T1_in_process_leg_generic_advisory_never_flips(tmp_path, monkeypatch):
    """In-process (session_id == leadSessionId): identity collapses → GENERIC
    attribution-free advisory only when a claimable task exists (F3); NEVER a
    flip and NEVER a task id named."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    advisory = gate._evaluate(_payload(session_id=LEAD_SID, agent_type=DEVOPS))
    assert advisory == gate._GENERIC_CLAIM_NUDGE
    assert "#B" not in advisory                      # attribution-free
    assert _read_task(tmp_path, "B")["status"] == "pending"  # NEVER flips


def test_T1_tmux_leg_enforces_flip(tmp_path, monkeypatch):
    """Tmux (session_id != leadSessionId): distinct session_id disambiguates
    identity → enforce (M2 auto-flip of the single candidate)."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    advisory = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS))
    assert "Auto-claimed" in advisory and "#B" in advisory
    assert _read_task(tmp_path, "B")["status"] == "in_progress"


def test_T1_structural_keying_same_input_branches_on_topology_only(tmp_path, monkeypatch):
    """THE hard-gate assertion: identical agent_type + identical task set; the
    ONLY difference is session_id == leadSessionId vs != . The branch MUST be
    driven by that structural signal (in-process → generic/no-flip; tmux →
    flip), never by a mode flag (there is none)."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")

    # in-process leg
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    in_proc = gate._evaluate(_payload(session_id=LEAD_SID, agent_type=DEVOPS))
    assert in_proc == gate._GENERIC_CLAIM_NUDGE
    assert _read_task(tmp_path, "B")["status"] == "pending"

    # tmux leg — re-seed to the SAME starting state; flip ONLY the session_id
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    tmux = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS))
    assert "Auto-claimed" in tmux
    assert _read_task(tmp_path, "B")["status"] == "in_progress"


def test_T1_stale_lead_session_id_misclassification_bounded_to_own_task(
    tmp_path, monkeypatch, capsys
):
    """Topology robustness: a STALE `config.leadSessionId` (≠ the actual current
    shared session the in-process teammate runs in) makes the topology compare
    read `session_id != leadSessionId` → in_process=False → an IN-PROCESS teammate
    is MISCLASSIFIED as tmux and reaches the enforce path. The blast radius is
    BOUNDED by the registry-confident-ownership + single-candidate conjunction:
    the worst the gate can do is auto-claim the actor's OWN single pending task
    (coordination-only, benign `pending → in_progress`, no privilege crossing). It
    MUST NEVER flip another member's task, NEVER deny, and always fail-open.

    Non-vacuous: drop the ownership bound and Part 2 inverts (the not-owned task
    becomes the sole candidate and is wrongly flipped — cross-actor escalation)."""
    stale_lead_sid = "stale-lead-session-9999"
    current_shared_sid = "current-shared-session-0001"  # the real in-process session
    _seed_config(tmp_path, lead_session_id=stale_lead_sid)  # config is STALE
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    frame = _payload(session_id=current_shared_sid, agent_type=DEVOPS)

    # Part 1 — BENIGN bound + same-fixture positive control: the actor's OWN task
    # is the single candidate → auto-claim of its OWN pending task only. Proves the
    # gate is ACTIVE under the misclassification (not an inert no-op) and the flip
    # is benign (own task, coordination-only).
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    advisory = gate._evaluate(frame)
    assert "Auto-claimed" in advisory and "#B" in advisory
    assert _read_task(tmp_path, "B")["status"] == "in_progress"

    # Part 2 — NO cross-actor escalation (the bound): replace the own task with a
    # NOT-owned task as the SOLE candidate. The ownership filter keeps `mine` empty
    # → NO-OP; another member's task is never flipped even under the
    # misclassification. (Drop the ownership bound and X escalates → this inverts.)
    (tmp_path / ".claude" / "tasks" / TEAM / "B.json").unlink()
    _seed_task(tmp_path, "X", subject="db: migrate", owner=OTHER,
               status="pending", blockedBy=[])
    assert gate._evaluate(frame) is None
    assert _read_task(tmp_path, "X")["status"] == "pending"  # OTHER's task untouched

    # Part 3 — fail-open / never-deny under the misclassification: main() emits a
    # PreToolUse advisory + exit 0, never a permissionDecision.
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    code, out = _capture_main(frame, capsys)
    assert code == 0
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in json.dumps(out)


def test_T1_stale_lead_session_id_misclassification_multicandidate_and_unconfident_stay_advisory(
    tmp_path, monkeypatch
):
    """Companion bound: under the SAME stale-`leadSessionId` misclassification
    (in-process misread as tmux), the auto-flip is gated by the FULL conjunction,
    not ownership alone. (a) MORE THAN ONE owned-unblocked-pending candidate →
    advisory-LIST, never a flip (the gate must not guess which task the actor is
    working on). (b) registry-UNCONFIDENT identity → generic attribution-free
    advisory, never a typed flip. Both bounds hold even though the topology is
    misclassified — pinning the single-candidate AND registry-confident conjuncts
    that complement the ownership bound in the sibling test."""
    stale_lead_sid = "stale-lead-session-9999"
    current_shared_sid = "current-shared-session-0001"  # the real in-process session
    _seed_config(tmp_path, lead_session_id=stale_lead_sid)  # config is STALE

    # (a) confident identity + >1 owned candidate → advisory-LIST, NO flip.
    # (Break the single-candidate bound and one of these would wrongly flip.)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: a", owner=DEVOPS,
               status="pending", blockedBy=[])
    _seed_task(tmp_path, "C", subject="devops: b", owner=DEVOPS,
               status="pending", blockedBy=[])
    frame = _payload(session_id=current_shared_sid, agent_type=DEVOPS)
    advisory = gate._evaluate(frame)
    assert "Auto-claimed" not in advisory and "#B" in advisory and "#C" in advisory
    assert _read_task(tmp_path, "B")["status"] == "pending"
    assert _read_task(tmp_path, "C")["status"] == "pending"

    # (b) registry-UNCONFIDENT identity → generic advisory, NO typed flip.
    # (Break the registry-confident bound and a typed guess could flip.)
    _mock_registry(monkeypatch, None)
    advisory = gate._evaluate(
        _payload(session_id=current_shared_sid, agent_type=DEVOPS, team_name=TEAM)
    )
    assert advisory == gate._GENERIC_CLAIM_NUDGE
    assert _read_task(tmp_path, "B")["status"] == "pending"
    assert _read_task(tmp_path, "C")["status"] == "pending"


# =============================================================================
# T2 — TMUX positive: confident identity + exactly one owned-unblocked-pending
#      → M2 auto-flip; whole-json preserved + gate_writeback marker.
#      (Supersedes smoke S3.)
# =============================================================================


def test_T2_tmux_single_candidate_m2_autoflip_preserves_siblings(tmp_path, monkeypatch):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[], metadata={"variety": {"total": 11}})
    advisory = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS))
    assert "Auto-claimed" in advisory and "#B" in advisory
    task = _read_task(tmp_path, "B")
    assert task["status"] == "in_progress"               # TOP-LEVEL flip
    assert task["metadata"]["gate_writeback"] is True    # convention marker set
    assert task["owner"] == DEVOPS                        # sibling key preserved
    assert task["blockedBy"] == []                        # sibling key preserved
    assert task["metadata"]["variety"] == {"total": 11}  # nested sibling preserved


def test_T2_main_advisory_output_shape(tmp_path, monkeypatch, capsys):
    """main() emits hookSpecificOutput.additionalContext + hookEventName ==
    PreToolUse + exit 0; NEVER permissionDecision. (Supersedes smoke S9.)"""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    code, out = _capture_main(_payload(session_id=TMUX_SID, agent_type=DEVOPS), capsys)
    assert code == 0
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "#B" in out["hookSpecificOutput"]["additionalContext"]
    assert "permissionDecision" not in json.dumps(out)


# =============================================================================
# T3 — TMUX negatives / no-op (incl. multi-candidate → advisory-list, never
#      flip). (Supersedes smoke S4.)
# =============================================================================


def test_T3_tmux_multi_candidate_lists_never_flips(tmp_path, monkeypatch):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: a", owner=DEVOPS,
               status="pending", blockedBy=[])
    _seed_task(tmp_path, "C", subject="devops: b", owner=DEVOPS,
               status="pending", blockedBy=[])
    advisory = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS))
    assert "#B" in advisory and "#C" in advisory and "Auto-claimed" not in advisory
    assert _read_task(tmp_path, "B")["status"] == "pending"
    assert _read_task(tmp_path, "C")["status"] == "pending"


def test_T3_not_owned_task_no_op(tmp_path, monkeypatch):
    """A pending-unblocked task owned by ANOTHER teammate → never in `mine` →
    NO-OP. (Both actors registered; positive control in the non-vacuity probe.)"""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "X", subject="db: migrate", owner=OTHER,
               status="pending", blockedBy=[])
    assert gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS)) is None
    assert _read_task(tmp_path, "X")["status"] == "pending"


def test_T3_no_owned_tasks_no_op(tmp_path, monkeypatch):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    assert gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS)) is None


def test_T3_completed_owned_task_no_op(tmp_path, monkeypatch):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: done", owner=DEVOPS,
               status="completed", blockedBy=[])
    assert gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS)) is None


def test_T3_blocked_task_b_no_op(tmp_path, monkeypatch):
    """Task B blockedBy=[A], A still pending → blocked → NO-OP."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "A", subject="devops: TEACHBACK for x", owner=DEVOPS,
               status="pending")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=["A"])
    assert gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS)) is None
    assert _read_task(tmp_path, "B")["status"] == "pending"


# =============================================================================
# T4 — LEAD frame → NO-OP (both spellings), no scan. (Supersedes smoke S1.)
# =============================================================================


@pytest.mark.parametrize("lead_spelling", [LEAD_QUALIFIED, LEAD_UNQUALIFIED])
def test_T4_lead_frame_no_op_both_spellings(tmp_path, monkeypatch, capsys, lead_spelling):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    # A claimable task EXISTS — proving the lead NO-OP is the is_lead early-exit,
    # not an empty scan. (Same fixture positive control: the tmux test flips it.)
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    code, out = _capture_main(_payload(session_id=TMUX_SID, agent_type=lead_spelling), capsys)
    assert code == 0
    assert out == {"suppressOutput": True}
    assert _read_task(tmp_path, "B")["status"] == "pending"  # lead never flips


# =============================================================================
# T5 — F2 multi-instance-of-type + registry MISS → advisory, NEVER a typed
#      guess (no resolve_agent_name type-strip fallback). (Supersedes smoke S5.)
# =============================================================================


def test_T5_registry_miss_generic_never_typed_guess(tmp_path, monkeypatch):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, None)  # resolve MISS → identity unconfident
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    advisory = gate._evaluate(
        _payload(session_id=TMUX_SID, agent_type=DEVOPS, team_name=TEAM)
    )
    assert advisory == gate._GENERIC_CLAIM_NUDGE
    assert "#B" not in advisory
    assert _read_task(tmp_path, "B")["status"] == "pending"  # never flips


def test_T5_multi_same_type_registry_miss_flips_nothing(tmp_path, monkeypatch):
    """Two same-agentType teammates' owned tasks present + registry miss → NO
    type-strip guess; NEITHER task flipped; generic advisory only."""
    members = (
        {"name": "te-1", "agentType": "pact-test-engineer"},
        {"name": "te-2", "agentType": "pact-test-engineer"},
    )
    _seed_config(tmp_path, members=members)
    _mock_registry(monkeypatch, None)  # cannot disambiguate the two te-* actors
    _seed_task(tmp_path, "B1", subject="te1: implement", owner="te-1",
               status="pending", blockedBy=[])
    _seed_task(tmp_path, "B2", subject="te2: implement", owner="te-2",
               status="pending", blockedBy=[])
    advisory = gate._evaluate(
        _payload(session_id=TMUX_SID, agent_type="pact-test-engineer", team_name=TEAM)
    )
    assert advisory == gate._GENERIC_CLAIM_NUDGE
    assert _read_task(tmp_path, "B1")["status"] == "pending"
    assert _read_task(tmp_path, "B2")["status"] == "pending"


# =============================================================================
# T6 — §7 unblocked predicate: completed blocker → eligible; open blocker →
#      blocked. NOT "blockedBy empty". (Supersedes smoke S6.)
# =============================================================================


def test_T6_completed_blocker_eligible(tmp_path, monkeypatch):
    """blockedBy=[A] but A completed → B unblocked (the platform RETAINS the
    completed-blocker id). Eligible → M2 flips B."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "A", subject="devops: TEACHBACK for x", owner=DEVOPS,
               status="completed")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=["A"])
    advisory = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS))
    assert advisory is not None and "#B" in advisory
    assert _read_task(tmp_path, "B")["status"] == "in_progress"


def test_T6_open_blocker_blocked_no_op(tmp_path, monkeypatch):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "A", subject="devops: TEACHBACK for x", owner=DEVOPS,
               status="in_progress")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=["A"])
    assert gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS)) is None
    assert _read_task(tmp_path, "B")["status"] == "pending"


# =============================================================================
# T7 — F1 idempotency: already in_progress → NO-OP (not re-nagged/re-flipped).
#      (Supersedes smoke S7.)
# =============================================================================


def test_T7_idempotency_already_in_progress_no_op(tmp_path, monkeypatch):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="in_progress", blockedBy=[])
    assert gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS)) is None


# =============================================================================
# T8 — EXEMPTION PARITY: secretary-owned (agentType) / signal task / own
#      teachback Task-A → EXCLUDED from `mine`. Each paired with a POSITIVE
#      CONTROL firing in the SAME fixture (defeats wrong-reason GREEN).
# =============================================================================


def test_T8_signal_task_excluded_positive_control_fires(tmp_path, monkeypatch):
    """An owned signal task (completion_type==signal, type==blocker) is EXCLUDED;
    a sibling normal owned task IS flipped. The flip of the normal task proves
    the exclusion (else `mine` would be 2 candidates → advisory-list, no flip)."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "SIG", subject="devops: signal", owner=DEVOPS,
               status="pending", blockedBy=[],
               metadata={"completion_type": "signal", "type": "blocker"})
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])  # positive control
    advisory = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS))
    assert "Auto-claimed" in advisory and "#B" in advisory  # control fired
    assert _read_task(tmp_path, "B")["status"] == "in_progress"
    assert _read_task(tmp_path, "SIG")["status"] == "pending"  # exempt → untouched


def test_T8_own_teachback_task_a_excluded_positive_control_fires(tmp_path, monkeypatch):
    """An owned teachback Task-A (is_teachback_subject) is EXCLUDED; the sibling
    normal task fires. Single-candidate flip proves the Task-A exclusion."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "A", subject="pact-devops-engineer: TEACHBACK for the matrix",
               owner=DEVOPS, status="pending", blockedBy=[])
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])  # positive control
    advisory = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS))
    assert "Auto-claimed" in advisory and "#B" in advisory
    assert _read_task(tmp_path, "B")["status"] == "in_progress"
    assert _read_task(tmp_path, "A")["status"] == "pending"  # teachback Task-A untouched


def test_T8_secretary_owned_task_excluded_cross_actor_positive_control(tmp_path, monkeypatch):
    """A secretary actor's own pending task is EXCLUDED (owner agentType
    pact-secretary → is_self_complete_exempt surface 1) → NO-OP. POSITIVE
    CONTROL in the SAME fixture: a devops actor with an identical pending task
    DOES flip — proving the config/registry wiring is sound and the secretary
    NO-OP is the exemption, not under-registration."""
    _seed_config(tmp_path)
    _mock_registry_map(monkeypatch, {
        SECRETARY_SID: f"{SECRETARY}@{TEAM}",
        TMUX_SID: f"{DEVOPS}@{TEAM}",
    })
    _seed_task(tmp_path, "SECTASK", subject="secretary: harvest", owner=SECRETARY,
               status="pending", blockedBy=[])
    # secretary actor → exempt → NO-OP, no flip
    assert gate._evaluate(_payload(session_id=SECRETARY_SID, agent_type="pact-secretary")) is None
    assert _read_task(tmp_path, "SECTASK")["status"] == "pending"

    # positive control (same fixture): devops actor + own task → flips
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    advisory = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS))
    assert "Auto-claimed" in advisory and "#B" in advisory
    assert _read_task(tmp_path, "B")["status"] == "in_progress"


# =============================================================================
# T9 — Bash read-only spurious-flip defense: a tmux Bash frame with no owned-
#      unblocked-pending task → NO-OP (the filter neutralizes the Bash
#      false-positive). Positive control: with a claimable task, Bash DOES nudge
#      (impl-work is exactly Edit|Write|Bash).
# =============================================================================


def test_T9_bash_frame_no_owned_task_no_op(tmp_path, monkeypatch):
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    # only a NOT-owned task exists → the owned-unblocked-pending filter → NO-OP
    _seed_task(tmp_path, "X", subject="db: migrate", owner=OTHER,
               status="pending", blockedBy=[])
    assert gate._evaluate(
        _payload(session_id=TMUX_SID, agent_type=DEVOPS, tool_name="Bash")
    ) is None
    assert _read_task(tmp_path, "X")["status"] == "pending"


def test_T9_bash_frame_with_owned_task_does_enforce(tmp_path, monkeypatch):
    """Positive control: a Bash frame is real impl-work — with an owned candidate
    it enforces (so the no-op above is the filter, not a Bash exclusion)."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    advisory = gate._evaluate(
        _payload(session_id=TMUX_SID, agent_type=DEVOPS, tool_name="Bash")
    )
    assert "Auto-claimed" in advisory and "#B" in advisory
    assert _read_task(tmp_path, "B")["status"] == "in_progress"


# =============================================================================
# T10 — FAIL-OPEN: every degraded input → exit 0, never deny, no traceback.
#       (Supersedes smoke S8 + adds over-cap, corrupt registry, malformed task
#       JSON, module-load failure, and a real-subprocess crashpath.)
# =============================================================================


def test_T10_malformed_stdin_failopen(capsys):
    with patch.object(sys, "stdin", io.StringIO("{not json")):
        with pytest.raises(SystemExit) as exc:
            gate.main()
    assert (exc.value.code or 0) == 0
    assert json.loads(capsys.readouterr().out.strip()) == {"suppressOutput": True}


def test_T10_over_cap_stdin_failopen(capsys):
    """An over-cap frame truncates mid-read → JSONDecodeError → suppress+exit 0."""
    huge = '{"tool_name":"Edit","session_id":"x","pad":"' + ("a" * (gate._STDIN_READ_MAX + 16))
    with patch.object(sys, "stdin", io.StringIO(huge)):
        with pytest.raises(SystemExit) as exc:
            gate.main()
    assert (exc.value.code or 0) == 0
    assert json.loads(capsys.readouterr().out.strip()) == {"suppressOutput": True}


def test_T10_non_dict_stdin_failopen(capsys):
    with patch.object(sys, "stdin", io.StringIO("[1, 2, 3]")):
        with pytest.raises(SystemExit) as exc:
            gate.main()
    assert (exc.value.code or 0) == 0
    assert json.loads(capsys.readouterr().out.strip()) == {"suppressOutput": True}


def test_T10_module_load_failure_failopen(monkeypatch, capsys):
    """Simulate a cross-package import failure: _IMPORTS_OK False → suppress."""
    monkeypatch.setattr(gate, "_IMPORTS_OK", False)
    with patch.object(sys, "stdin", io.StringIO(json.dumps(_payload(session_id="x")))):
        with pytest.raises(SystemExit) as exc:
            gate.main()
    assert (exc.value.code or 0) == 0
    assert json.loads(capsys.readouterr().out.strip()) == {"suppressOutput": True}


def test_T10_no_lead_session_id_failopen(tmp_path, monkeypatch):
    """config without leadSessionId → topology undeterminable → NO-OP."""
    teams_dir = tmp_path / ".claude" / "teams" / TEAM
    teams_dir.mkdir(parents=True, exist_ok=True)
    (teams_dir / "config.json").write_text(json.dumps({"name": TEAM}), encoding="utf-8")
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    assert gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS)) is None


def test_T10_corrupt_registry_failopen(tmp_path, monkeypatch, capsys):
    """registry_resolve raising → caught by main()'s catch-all → suppress+exit 0
    (never deny)."""
    _seed_config(tmp_path)

    def _boom(_sid):
        raise RuntimeError("corrupt registry")

    monkeypatch.setattr(gate, "registry_resolve", _boom)
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    code, out = _capture_main(_payload(session_id=TMUX_SID, agent_type=DEVOPS), capsys)
    assert code == 0
    assert out == {"suppressOutput": True}


def test_T10_malformed_task_json_skipped_no_crash(tmp_path, monkeypatch):
    """A corrupt task JSON file is skipped by iter_team_task_jsons → the gate
    does not crash; the remaining valid owned task still resolves."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_raw_task_file(tmp_path, "BROKEN", "{ this is : not json ]")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    advisory = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS))
    assert advisory is not None and "#B" in advisory  # no crash; valid task seen
    assert _read_task(tmp_path, "B")["status"] == "in_progress"


def test_T10_subprocess_malformed_stdin_exits_zero_no_traceback(tmp_path):
    """Real crashpath: run the hook file as the platform does (subprocess) with
    malformed stdin → returncode 0, suppress on stdout, NO traceback on stderr.
    (Env CLAUDE_CONFIG_DIR → tmp so the subprocess never touches real state; the
    malformed-stdin path exits before any config read anyway.)"""
    env = {
        "PATH": __import__("os").environ.get("PATH", ""),
        "PYTHONPATH": str(_HOOKS_DIR),
        "CLAUDE_CONFIG_DIR": str(tmp_path / ".claude"),
    }
    proc = subprocess.run(
        [sys.executable, str(_HOOK_PATH)],
        input="{not json",
        text=True, capture_output=True, env=env, timeout=30,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout.strip()) == {"suppressOutput": True}
    assert "Traceback" not in proc.stderr


# =============================================================================
# T11 — NON-VACUITY (the real safety): neuter each branch via monkeypatch and
#       assert a SPECIFIC named scenario INVERTS. Removal AND inversion AND
#       mode-branch AND ownership AND the §7 predicate AND exemption. Each probe
#       FAILS-LOUD by design — if a future refactor makes the gate vacuous, the
#       paired correct-behavior test stops being load-bearing and one of these
#       inverts.
# =============================================================================


def test_T11_removal_is_lead_guard_inverts_lead_noop(tmp_path, monkeypatch):
    """REMOVAL: neuter the is_lead early-exit (→ False). A LEAD frame, which is
    a NO-OP normally (T4), now falls through to the in-process branch and emits
    a generic advisory. Proves the is_lead guard is load-bearing."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    lead_frame = _payload(session_id=LEAD_SID, agent_type=LEAD_QUALIFIED)
    assert gate._evaluate(lead_frame) is None                 # intact: NO-OP
    monkeypatch.setattr(gate.pact_context, "is_lead", lambda _stdin: False)
    assert gate._evaluate(lead_frame) is not None             # neutered: INVERTS


def test_T11_inversion_topology_compare_inverts_in_process_no_flip(tmp_path, monkeypatch):
    """INVERSION (the calibration case): the topology compare green-lights the
    wrong-flip it exists to prevent if inverted. An IN-PROCESS frame must NEVER
    flip (identity collapsed). Neuter by making _read_lead_session_id return a
    value != the frame's session_id → the gate computes in_process=False → treats
    the in-process frame as tmux → WRONG-FLIP. Proves the in-process no-flip test
    is load-bearing against the inversion."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    in_process_frame = _payload(session_id=LEAD_SID, agent_type=DEVOPS)
    # intact: in-process → generic advisory, NEVER flips
    assert gate._evaluate(in_process_frame) == gate._GENERIC_CLAIM_NUDGE
    assert _read_task(tmp_path, "B")["status"] == "pending"
    # neuter: lead_session_id read returns a DIFFERENT id → in_process=False
    monkeypatch.setattr(gate, "_read_lead_session_id", lambda team, teams_dir=None: "NOT-" + LEAD_SID)
    advisory = gate._evaluate(in_process_frame)
    assert "Auto-claimed" in advisory                          # INVERTS to a wrong-flip
    assert _read_task(tmp_path, "B")["status"] == "in_progress"


def test_T11_mode_branch_drop_status_guard_inverts_idempotency(tmp_path, monkeypatch):
    """MODE-BRANCH / status guard: drop the `status == pending` scan guard
    (simulate by making the scan see an in_progress task as pending). The F1
    idempotency NO-OP (T7) inverts to an advisory (the task enters `mine`;
    _atomic_claim's disk re-read still aborts the actual flip, so no overwrite —
    but the gate re-nags, which is the regression)."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="in_progress", blockedBy=[])
    frame = _payload(session_id=TMUX_SID, agent_type=DEVOPS)
    assert gate._evaluate(frame) is None                       # intact: NO-OP

    real_iter = gate.iter_team_task_jsons

    def _neutered_iter(team_name):
        for t in real_iter(team_name):
            t = dict(t)
            if t.get("status") == "in_progress":
                t["status"] = "pending"                        # scan sees it claimable
            yield t

    monkeypatch.setattr(gate, "iter_team_task_jsons", _neutered_iter)
    advisory = gate._evaluate(frame)
    assert advisory is not None and "#B" in advisory           # INVERTS (re-nag)
    # _atomic_claim's disk re-read still guards the WRITE (no overwrite):
    assert _read_task(tmp_path, "B")["status"] == "in_progress"


def test_T11_ownership_drop_inverts_not_owned_no_op(tmp_path, monkeypatch):
    """OWNERSHIP: drop the inline `owner == confident_name` match (via a name
    whose __eq__ is always True). A NOT-owned task (T3), a NO-OP normally, now
    gets flipped — a wrong-flip of another teammate's task. Load-bearing."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "X", subject="db: migrate", owner=OTHER,
               status="pending", blockedBy=[])
    frame = _payload(session_id=TMUX_SID, agent_type=DEVOPS)
    assert gate._evaluate(frame) is None                       # intact: NO-OP
    assert _read_task(tmp_path, "X")["status"] == "pending"
    # neuter: confident_name matches ANY owner → ownership filter dropped
    monkeypatch.setattr(gate, "_split_name_team", lambda resolved: (_MatchAnyName("x"), TEAM))
    advisory = gate._evaluate(frame)
    assert advisory is not None and "#X" in advisory           # INVERTS (wrong-flip)
    assert _read_task(tmp_path, "X")["status"] == "in_progress"


def test_T11_swap_unblocked_for_blockedby_empty_inverts_section7(tmp_path, monkeypatch):
    """§7 PREDICATE: swap `_is_unblocked` for the naive `blockedBy empty`. A
    just-unblocked Task B (blockedBy=[A], A completed) — eligible under the
    corrected predicate (T6) — is misread as BLOCKED (blockedBy non-empty) → the
    gate no-ops on its OWN target scenario. Inverts T6's flip to a NO-OP."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "A", subject="devops: TEACHBACK for x", owner=DEVOPS,
               status="completed")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=["A"])
    frame = _payload(session_id=TMUX_SID, agent_type=DEVOPS)
    # intact: A completed → B unblocked → flips
    assert gate._evaluate(frame) is not None
    assert _read_task(tmp_path, "B")["status"] == "in_progress"
    # re-seed + neuter: blockedBy-empty swap → B (blockedBy=[A]) misread as blocked
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=["A"])
    monkeypatch.setattr(gate, "_is_unblocked", lambda task, by_id: not (task.get("blockedBy") or []))
    assert gate._evaluate(frame) is None                       # INVERTS to NO-OP
    assert _read_task(tmp_path, "B")["status"] == "pending"


def test_T11_drop_exemption_inverts_signal_task_parity(tmp_path, monkeypatch):
    """EXEMPTION: drop is_self_complete_exempt + is_teachback_subject. The signal
    task (T8) — excluded normally — now enters `mine` alongside the normal task →
    2 candidates → advisory-list (no flip). The T8 single-candidate flip inverts
    (the positive control's auto-flip becomes a multi-list)."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "SIG", subject="devops: signal", owner=DEVOPS,
               status="pending", blockedBy=[],
               metadata={"completion_type": "signal", "type": "blocker"})
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    frame = _payload(session_id=TMUX_SID, agent_type=DEVOPS)
    # intact: SIG exempt → single candidate B → auto-flip
    assert "Auto-claimed" in gate._evaluate(frame)
    assert _read_task(tmp_path, "B")["status"] == "in_progress"
    # re-seed + neuter both exemption predicates
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    monkeypatch.setattr(gate, "is_self_complete_exempt", lambda *a, **k: False)
    monkeypatch.setattr(gate, "is_teachback_subject", lambda _s: False)
    advisory = gate._evaluate(frame)
    assert "Auto-claimed" not in advisory                      # INVERTS: now multi-list
    assert "#SIG" in advisory and "#B" in advisory
    assert _read_task(tmp_path, "B")["status"] == "pending"


# =============================================================================
# T12 — M2 no-clobber re-validation + write-failure fallback.
#       (Supersedes smoke S10 + S11.)
# =============================================================================


def test_T12_atomic_claim_no_clobber_on_nonpending(tmp_path):
    """The scan saw `pending`; the on-disk status moved before the write.
    _atomic_claim re-reads, sees non-pending, aborts — no overwrite."""
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="in_progress", blockedBy=[])
    assert gate._atomic_claim("B", TEAM) is False
    assert _read_task(tmp_path, "B")["status"] == "in_progress"  # untouched


def test_T12_write_failure_degrades_to_single_nudge(tmp_path, monkeypatch):
    """M2 write failure (_atomic_claim False) → degrade to the single nudge
    (fail-open), no flip."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    monkeypatch.setattr(gate, "_atomic_claim", lambda tid, team: False)
    advisory = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS))
    assert advisory is not None and "#B" in advisory and "Auto-claimed" not in advisory
    assert _read_task(tmp_path, "B")["status"] == "pending"  # no flip


# =============================================================================
# T13 — Unresolvable-blocker permissive choice (§7) — lead-mandated variants.
# =============================================================================


def test_T13a_deleted_blocker_treated_unblocked(tmp_path, monkeypatch):
    """(a) Task B blockedBy=[X], X deleted/file-absent (no matching task) →
    treated UNBLOCKED (permissive correct) → eligible → flips."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=["X-DELETED"])  # X has no task file
    advisory = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS))
    assert advisory is not None and "#B" in advisory
    assert _read_task(tmp_path, "B")["status"] == "in_progress"


def test_T13b_corrupt_blocker_json_no_wrong_flip(tmp_path, monkeypatch):
    """(b) Blocker X is a corrupt/unparseable task file → skipped by the iterator
    → X absent from by_id → permissive-unblocked. The permissive path must never
    produce a WRONG flip: here B is legitimately the actor's single owned task,
    so it flips (correct). The guard is that a corrupt blocker does not CRASH and
    does not flip anything OTHER than the actor's single owned candidate."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_raw_task_file(tmp_path, "X", "{ corrupt blocker ]")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=["X"])
    advisory = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS))
    assert advisory is not None and "#B" in advisory          # no crash; bounded
    assert _read_task(tmp_path, "B")["status"] == "in_progress"


def test_T13c_permissive_unblock_multi_candidate_blocks_flip(tmp_path, monkeypatch):
    """(c) Permissive unblock + >1 candidate → the single-candidate conjunction
    bounds the wrong-flip: NO flip, advisory-list only. Both B and C carry a
    deleted blocker (permissive-unblocked) but the multi-candidate guard holds."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    _seed_task(tmp_path, "B", subject="devops: a", owner=DEVOPS,
               status="pending", blockedBy=["GONE"])
    _seed_task(tmp_path, "C", subject="devops: b", owner=DEVOPS,
               status="pending", blockedBy=["GONE"])
    advisory = gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS))
    assert "Auto-claimed" not in advisory and "#B" in advisory and "#C" in advisory
    assert _read_task(tmp_path, "B")["status"] == "pending"
    assert _read_task(tmp_path, "C")["status"] == "pending"


def test_T13c_permissive_unblock_identity_unconfident_blocks_flip(tmp_path, monkeypatch):
    """(c) Permissive unblock + identity UNCONFIDENT (registry miss) → the
    registry-confident conjunction bounds the wrong-flip: generic advisory, NO
    flip, even though the deleted blocker makes B permissive-unblocked."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, None)  # identity unconfident
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=["GONE"], team_name=TEAM)
    advisory = gate._evaluate(
        _payload(session_id=TMUX_SID, agent_type=DEVOPS, team_name=TEAM)
    )
    assert advisory == gate._GENERIC_CLAIM_NUDGE
    assert _read_task(tmp_path, "B")["status"] == "pending"


# =============================================================================
# T14 — fail-safe / relevance-guard / path-traversal hardening. Folds in the
#       secondary-path coverage gaps surfaced in review: the relevance-guard
#       negative case, the session_id / team_name fail-safes, the F2 identity
#       split, the _atomic_claim path-traversal + symlink + write-failure guards,
#       and a full-path real-subprocess run. Each is non-vacuous where a function
#       seam exists; the _atomic_claim cases lock the security-probed behavior in
#       as a standing regression test.
# =============================================================================


def test_relevance_guard_negative_in_process_no_candidate_no_op(tmp_path, monkeypatch):
    """In-process + NO claimable candidate → the relevance-guard returns False →
    NO-OP (the generic nudge must NOT fire on every in-process Edit/Write/Bash
    when nothing is claimable). Non-vacuous: neuter the guard to always-True and
    the same frame inverts to the generic nudge."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, f"{DEVOPS}@{TEAM}")
    # only a COMPLETED owned task exists → nothing claimable
    _seed_task(tmp_path, "B", subject="devops: done", owner=DEVOPS,
               status="completed", blockedBy=[])
    frame = _payload(session_id=LEAD_SID, agent_type=DEVOPS)
    assert gate._evaluate(frame) is None                          # intact: NO-OP
    monkeypatch.setattr(gate, "_any_unclaimed_claim_candidate", lambda *a, **k: True)
    assert gate._evaluate(frame) == gate._GENERIC_CLAIM_NUDGE     # neutered: INVERTS


def test_relevance_guard_negative_unconfident_tmux_no_candidate_no_op(tmp_path, monkeypatch):
    """Tmux + registry-UNCONFIDENT + NO claimable candidate → relevance-guard
    False → NO-OP. Non-vacuous: neuter the guard to always-True → generic nudge."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, None)  # identity unconfident
    _seed_task(tmp_path, "B", subject="devops: done", owner=DEVOPS,
               status="completed", blockedBy=[])
    frame = _payload(session_id=TMUX_SID, agent_type=DEVOPS, team_name=TEAM)
    assert gate._evaluate(frame) is None                          # intact: NO-OP
    monkeypatch.setattr(gate, "_any_unclaimed_claim_candidate", lambda *a, **k: True)
    assert gate._evaluate(frame) == gate._GENERIC_CLAIM_NUDGE     # neutered: INVERTS


def test_missing_or_empty_session_id_fail_safe_no_op(tmp_path, monkeypatch):
    """A frame with empty / missing session_id → fail-safe NO-OP (no identity or
    topology can be resolved). Same-fixture positive control: a valid session_id
    flips B → proves the no-op is the session_id guard, not an inert fixture."""
    _seed_config(tmp_path)
    _mock_registry_map(monkeypatch, {TMUX_SID: f"{DEVOPS}@{TEAM}"})  # only TMUX_SID resolves
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    # empty session_id → NO-OP
    assert gate._evaluate(_payload(session_id="", agent_type=DEVOPS, team_name=TEAM)) is None
    # missing session_id key → NO-OP
    assert gate._evaluate({"tool_name": "Edit", "agent_type": DEVOPS, "team_name": TEAM}) is None
    assert _read_task(tmp_path, "B")["status"] == "pending"
    # positive control: a valid session_id DOES act (flip) in the SAME fixture
    assert "Auto-claimed" in gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS))
    assert _read_task(tmp_path, "B")["status"] == "in_progress"


def test_unresolvable_team_name_fail_safe_no_op(tmp_path, monkeypatch):
    """Registry miss (no @team half) + no context team + no stdin team_name →
    team_name unresolvable → fail-safe NO-OP. Same-fixture positive control: with
    a stdin team_name the gate proceeds (generic advisory) → proves the no-op is
    the team_name guard."""
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, None)  # no @team half
    monkeypatch.setattr(gate.pact_context, "get_team_name", lambda: "")  # no context team
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    # no stdin team_name → unresolvable → NO-OP
    assert gate._evaluate(_payload(session_id=TMUX_SID, agent_type=DEVOPS)) is None
    # positive control: stdin team_name resolves → gate proceeds (unconfident → generic)
    assert gate._evaluate(
        _payload(session_id=TMUX_SID, agent_type=DEVOPS, team_name=TEAM)
    ) == gate._GENERIC_CLAIM_NUDGE
    assert _read_task(tmp_path, "B")["status"] == "pending"


def test_split_name_team_malformed_values_are_unconfident(tmp_path, monkeypatch):
    """The identity split (F2 gate): any value lacking a non-empty name AND a
    non-empty team → (None, None) = UNCONFIDENT; a '@'-less registry value must
    never yield a typed owner. Pure-function assertions + an integration check
    that the unconfident path falls back to the generic (attribution-free)
    advisory, never a typed flip."""
    assert gate._split_name_team("malformed-no-at-sign") == (None, None)
    assert gate._split_name_team("name@") == (None, None)        # empty team
    assert gate._split_name_team("@team") == (None, None)        # empty name
    assert gate._split_name_team(123) == (None, None)            # non-string
    assert gate._split_name_team(None) == (None, None)
    assert gate._split_name_team("name@team") == ("name", "team")  # positive control
    # integration: a '@'-less registry value → unconfident → generic, never typed
    _seed_config(tmp_path)
    _mock_registry(monkeypatch, "no-at-sign-here")
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    advisory = gate._evaluate(
        _payload(session_id=TMUX_SID, agent_type=DEVOPS, team_name=TEAM)
    )
    assert advisory == gate._GENERIC_CLAIM_NUDGE
    assert "#B" not in advisory
    assert _read_task(tmp_path, "B")["status"] == "pending"      # never a typed flip


def test_atomic_claim_hostile_task_id_and_team_name_return_false(tmp_path):
    """_atomic_claim contract lock (locks in the security-probed behavior): a
    hostile task_id / team_name → False, no flip. Defense-in-depth:
    sanitize_path_component + the team_name regex in _atomic_claim, mirrored by
    read_task_json's own traversal guards."""
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])
    assert gate._atomic_claim("..", TEAM) is False               # sanitizes to empty → abort
    assert gate._atomic_claim("../../etc/passwd", TEAM) is False  # → "etcpasswd", not found
    assert gate._atomic_claim("B", "../evil") is False           # team_name regex reject
    assert gate._atomic_claim("B", "team/../../etc") is False    # team_name regex reject
    assert _read_task(tmp_path, "B")["status"] == "pending"      # legit task untouched


def test_atomic_claim_refuses_write_through_symlinked_team_dir(tmp_path):
    """Symlink-anchoring WRITE guard: tasks/{team} is a SYMLINK escaping the base.
    read_task_json reads the task from the bare base (it skips the escaping
    symlink), but the WRITE target tasks/{team}/B.json would resolve OUTSIDE the
    anchored base. _atomic_claim's resolve()+relative_to containment check refuses
    → False, nothing written outside. Non-vacuous: remove the containment check
    and os.replace lands the write at outside/B.json."""
    base = tmp_path / ".claude" / "tasks"
    base.mkdir(parents=True, exist_ok=True)
    # B readable at the BARE BASE so read_task_json returns it (status pending)
    (base / "B.json").write_text(
        json.dumps({"id": "B", "owner": DEVOPS, "status": "pending", "blockedBy": []}),
        encoding="utf-8",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (base / "EVIL").symlink_to(outside, target_is_directory=True)
    assert gate._atomic_claim("B", "EVIL") is False
    assert json.loads((base / "B.json").read_text())["status"] == "pending"  # not flipped
    assert not (outside / "B.json").exists()                     # nothing written outside


def test_atomic_claim_write_failure_returns_false_and_cleans_tmp(tmp_path, monkeypatch):
    """A real OSError during the atomic write (os.replace raises) → _atomic_claim
    returns False, does NOT flip, and cleans up the .tmp (no leaked temp file).
    Non-vacuous: removing the except would propagate the OSError (the direct call
    would raise instead of returning False)."""
    _seed_task(tmp_path, "B", subject="devops: implement", owner=DEVOPS,
               status="pending", blockedBy=[])

    def _boom(src, dst):
        raise OSError("simulated disk-full")

    monkeypatch.setattr(gate.os, "replace", _boom)
    assert gate._atomic_claim("B", TEAM) is False
    assert _read_task(tmp_path, "B")["status"] == "pending"      # not flipped
    tasks_dir = tmp_path / ".claude" / "tasks" / TEAM
    assert not list(tasks_dir.glob(".*.json.tmp"))              # tmp cleaned up


def test_subprocess_full_evaluate_path_exits_zero_no_traceback(tmp_path):
    """Real subprocess (as the platform runs the hook) through the FULL _evaluate
    path — valid frame + real config + a claimable task — exits 0 with a
    well-formed advisory and NO traceback. Complements the malformed-stdin
    subprocess test (which exits before any config read)."""
    claude = tmp_path / ".claude"
    teams_dir = claude / "teams" / TEAM
    teams_dir.mkdir(parents=True, exist_ok=True)
    (teams_dir / "config.json").write_text(
        json.dumps({"name": TEAM, "leadSessionId": LEAD_SID,
                    "members": [{"id": "a-d", "name": DEVOPS, "agentType": DEVOPS}]}),
        encoding="utf-8",
    )
    tasks_dir = claude / "tasks" / TEAM
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "B.json").write_text(
        json.dumps({"id": "B", "subject": "devops: implement", "owner": DEVOPS,
                    "status": "pending", "blockedBy": []}),
        encoding="utf-8",
    )
    env = {
        "PATH": __import__("os").environ.get("PATH", ""),
        "PYTHONPATH": str(_HOOKS_DIR),
        "CLAUDE_CONFIG_DIR": str(claude),
    }
    # tmux topology (session_id != leadSessionId). No registry entry exists in a
    # fresh subprocess → identity unconfident → generic advisory (B claimable).
    frame = {"tool_name": "Edit", "session_id": TMUX_SID,
             "agent_type": DEVOPS, "team_name": TEAM}
    proc = subprocess.run(
        [sys.executable, str(_HOOK_PATH)],
        input=json.dumps(frame),
        text=True, capture_output=True, env=env, timeout=30,
    )
    assert proc.returncode == 0
    assert "Traceback" not in proc.stderr
    out = json.loads(proc.stdout.strip())
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert gate._NUDGE_PREFIX in out["hookSpecificOutput"]["additionalContext"]


# =============================================================================
# §12.3 — PLATFORM-FIDELITY against REAL captured PreToolUse frames (un-skipped:
#         the committed fixture landed). Asserts the role discriminators the gate
#         depends on hold on three real redacted frames (Claude Code 2.1.177).
# =============================================================================


def test_T12_3_real_pretooluse_frames_platform_fidelity():
    """The platform stamps the role discriminators the gate relies on. Three REAL
    redacted PreToolUse frames (committed in tests/fixtures/role_frames.py) confirm:
      (1) agent_type is PRESENT on ALL three frames — the field is_lead() and the
          topology resolution read;
      (2) the in-process subagent's session_id EQUALS the lead's leadSessionId —
          the session_id==leadSessionId collapse that routes the in-process branch
          (previously M0-INFERRED, now CAPTURED);
      (3) the tmux teammate's session_id DIFFERS from the lead's — the tmux topology;
      (4) agent_id is PRESENT on the in-process subagent and ABSENT on the tmux +
          lead frames — captured corroboration (is_lead deliberately does NOT read
          agent_id; this records the captured shape).
    Finally, the captured frames are fed through the gate's REAL is_lead predicate
    to confirm they drive the correct role decision."""
    tmux = role_frames.captured_pretooluse_teammate_tmux()
    lead = role_frames.captured_pretooluse_lead_inprocess()
    subagent = role_frames.captured_pretooluse_teammate_inprocess_subagent()

    # all three are real PreToolUse frames
    for frame in (tmux, lead, subagent):
        assert frame["hook_event_name"] == "PreToolUse"

    # (1) agent_type PRESENT (non-empty string) on all three captured frames
    for frame in (tmux, lead, subagent):
        assert isinstance(frame.get("agent_type"), str) and frame["agent_type"]
    assert tmux["agent_type"] == "pact-test-engineer"        # non-lead spelling
    assert lead["agent_type"] == "PACT:pact-orchestrator"    # qualified lead spelling
    assert subagent["agent_type"] == "general-purpose"

    # (2) in-process collapse: subagent session_id == the lead's leadSessionId
    assert subagent["session_id"] == lead["session_id"]
    # (3) tmux topology: a DISTINCT session_id
    assert tmux["session_id"] != lead["session_id"]

    # (4) agent_id PRESENT on the in-process subagent, ABSENT on tmux + lead
    assert subagent.get("agent_id")
    assert "agent_id" not in tmux
    assert "agent_id" not in lead

    # the captured frames drive the gate's REAL role predicate correctly:
    # is_lead reads ONLY agent_type → lead True, both teammates False.
    assert gate.pact_context.is_lead(lead) is True
    assert gate.pact_context.is_lead(tmux) is False
    assert gate.pact_context.is_lead(subagent) is False
