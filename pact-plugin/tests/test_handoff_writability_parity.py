"""
Location: pact-plugin/tests/test_handoff_writability_parity.py
Summary: C3a marker-claim WRITABILITY parity smoke test for the #917 fix.
Used by: the pact-plugin test suite (standing merge gate).

The #917 fix gates the OPTIMISTIC O_EXCL agent_handoff marker-claim on
canonical-journal writability: get_journal_path() must be non-empty BEFORE
already_emitted() (the marker test-and-set) runs, in BOTH emit paths —

  b1 = agent_handoff_emitter.main()                       (TaskCompleted)
  b2 = task_lifecycle_gate._emit_lead_side_agent_handoff  (lead TaskUpdate-completed)

Without the gate, a fire that resolves a team_name for the marker dir but
CANNOT write the canonical journal (a teammate process whose context is
unpersisted, #877) claims the shared O_EXCL marker and then loses the
append_event — a CLAIM-WITHOUT-WRITE that permanently suppresses the reliable
lead-side b2 emit (the #917 0/7 symptom).

LOAD-BEARING assertion (relational, mirroring the #898 eligibility-parity test):
a journal-UNWRITABLE fire DEFERS — it claims NO marker (no file under
.agent_handoff_emitted/) and writes NO event; a journal-WRITABLE fire still
claims the marker + writes EXACTLY ONCE.

WHY "no marker after an unwritable fire" PROVES the ordering: if a path lacked
the get_journal_path() gate, or placed it AFTER already_emitted(), the O_EXCL
marker file would be created before/without the writability check. Asserting
the marker file is ABSENT after an unwritable fire is therefore the behavioral
proof that the writability check runs BEFORE the marker claim on that path — and
it fails the instant a future edit drops the gate from EITHER path (the
#901/#887 divergence class this test guards). A source-ordering pin
(test_writability_check_precedes_marker_claim_in_source) backstops it.

SELF-CONTAINED: minimal frames + handoff are constructed inline; the REAL
marker mechanism (shared.agent_handoff_marker.already_emitted) runs against a
tmp HOME so the marker-file assertions are against the actual O_EXCL side-effect,
not a mock. append_event is spied so "writes" means "the journal write was
attempted exactly once" (the dedup semantic the marker exists for). This file
imports NO captured-frame fixture promotion — it stands alone.
"""
import inspect
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import agent_handoff_emitter as b1  # noqa: E402
import task_lifecycle_gate as tlg  # noqa: E402

TEAM = "pact-test"
LEAD = "PACT:pact-orchestrator"
TASK_ID = "917"
OWNER = "devops"
SUBJECT = "devops: ship the gate"

# Minimal valid handoff — inline (no fixtures import).
HANDOFF = {
    "produced": ["src/x"],
    "decisions": ["d"],
    "uncertainty": [],
    "integration": ["i"],
    "open_questions": [],
}


def _marker_files(home: Path) -> list[str]:
    """Names of marker files the REAL O_EXCL test-and-set created under the
    patched HOME, or [] if the marker dir was never created (the defer)."""
    marker_dir = home / ".claude" / "teams" / TEAM / ".agent_handoff_emitted"
    return sorted(p.name for p in marker_dir.iterdir()) if marker_dir.exists() else []


def _drive_b1(*, writable: bool, tmp_path: Path, monkeypatch, append_calls: list, handoff=HANDOFF) -> None:
    """Fire b1 (agent_handoff_emitter.main) for one handoff-bearing completed
    TaskCompleted frame. `writable` controls the in-hook get_journal_path()
    return ('' = unwritable). `handoff` is the metadata.handoff value (default
    a valid dict; pass a non-dict to exercise the M1 type gate). The real
    already_emitted runs under the patched HOME; append_event is spied."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    journal = str(tmp_path / "session-journal.jsonl") if writable else ""
    task_data = {"status": "completed", "owner": OWNER, "metadata": {"handoff": handoff}}
    stdin = {
        "task_id": TASK_ID,
        "task_subject": SUBJECT,
        "teammate_name": OWNER,
        "team_name": TEAM,
    }
    # Patch the symbol bound in the HOOK module's namespace (b1 does
    # `from shared.session_journal import ... get_journal_path`), NOT
    # session_journal.get_journal_path — else the gate reads the real
    # resolution while this stub is ignored (a vacuous pass).
    with patch.object(b1, "get_journal_path", return_value=journal), \
         patch.object(b1, "read_task_json", return_value=task_data), \
         patch.object(b1, "append_event", side_effect=lambda e: append_calls.append(e) or True), \
         patch("sys.stdin", io.StringIO(json.dumps(stdin))):
        with pytest.raises(SystemExit):
            b1.main()


def _drive_b2(*, writable: bool, tmp_path: Path, monkeypatch, pact_context, append_calls: list, handoff=HANDOFF) -> None:
    """Fire b2 (_emit_lead_side_agent_handoff, reached via evaluate_lifecycle
    with a LEAD frame so is_lead is True). Same writability control + append
    spy; `handoff` is the metadata.handoff value (default a valid dict; pass a
    non-dict to exercise the M1 type gate). The real already_emitted runs under
    the patched HOME."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    pact_context(team_name=TEAM, session_id="s1")
    journal = str(tmp_path / "session-journal.jsonl") if writable else ""
    monkeypatch.setattr(tlg, "get_journal_path", lambda: journal)
    monkeypatch.setattr(tlg, "append_event", lambda e: append_calls.append(e) or True)
    task = {"id": TASK_ID, "subject": SUBJECT, "owner": OWNER, "metadata": {"handoff": handoff}}
    tlg.evaluate_lifecycle({
        "agent_type": LEAD,
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": TASK_ID, "status": "completed"},
        "tool_response": {"task": task},
    })


# Relational driver table: both emit paths exercised by the SAME assertions.
# A param is (label, driver) where driver(writable, tmp_path, monkeypatch,
# pact_context, append_calls) fires that path once.
def _b1_driver(writable, tmp_path, monkeypatch, pact_context, append_calls, handoff=HANDOFF):
    _drive_b1(writable=writable, tmp_path=tmp_path, monkeypatch=monkeypatch,
              append_calls=append_calls, handoff=handoff)


def _b2_driver(writable, tmp_path, monkeypatch, pact_context, append_calls, handoff=HANDOFF):
    _drive_b2(writable=writable, tmp_path=tmp_path, monkeypatch=monkeypatch,
              pact_context=pact_context, append_calls=append_calls, handoff=handoff)


_PATHS = [("b1", _b1_driver), ("b2", _b2_driver)]


class TestWritabilityGateParity:
    """Both emit paths must DEFER (claim no marker, write no event) on an
    unwritable journal, and EMIT exactly once on a writable journal."""

    @pytest.mark.parametrize("label,driver", _PATHS)
    def test_unwritable_fire_defers_no_marker_no_event(
        self, label, driver, tmp_path, monkeypatch, pact_context
    ):
        """get_journal_path()=='' → the fire DEFERS: NO marker file is claimed
        (the gate runs BEFORE already_emitted) and NO event is written. This is
        the #917 anti-poison property. NON-VACUOUS: reverting the gate makes the
        marker get claimed (file appears) and this fails."""
        calls: list = []
        driver(False, tmp_path, monkeypatch, pact_context, calls)
        assert _marker_files(tmp_path) == [], (
            f"{label}: an UNWRITABLE fire must claim NO marker — a marker file "
            "means the writability gate is missing or runs AFTER already_emitted "
            "(claim-without-write poison, #917)."
        )
        assert calls == [], f"{label}: an unwritable fire must write no event."

    @pytest.mark.parametrize("label,driver", _PATHS)
    def test_writable_fire_claims_marker_and_emits_once(
        self, label, driver, tmp_path, monkeypatch, pact_context
    ):
        """Positive control: get_journal_path() resolvable → the fire claims
        exactly ONE marker and writes exactly ONE event. Proves the defer above
        is the writability gate, not 'always suppress'."""
        calls: list = []
        driver(True, tmp_path, monkeypatch, pact_context, calls)
        assert len(_marker_files(tmp_path)) == 1, (
            f"{label}: a WRITABLE fire must claim exactly one marker."
        )
        assert len(calls) == 1, f"{label}: a writable fire must emit exactly once."

    @pytest.mark.parametrize("label,driver", _PATHS)
    def test_writable_refire_dedups_exactly_once(
        self, label, driver, tmp_path, monkeypatch, pact_context
    ):
        """Exactly-once preserved: two writable fires for the same
        (team, task_id, occupant) → still ONE marker + ONE event (the optimistic
        O_EXCL test-and-set is unchanged; the gate only adds a precondition)."""
        calls: list = []
        driver(True, tmp_path, monkeypatch, pact_context, calls)
        driver(True, tmp_path, monkeypatch, pact_context, calls)
        assert len(_marker_files(tmp_path)) == 1, (
            f"{label}: a re-fire must not create a second marker."
        )
        assert len(calls) == 1, (
            f"{label}: a re-fire for the same occupant must dedup to one event "
            "(exactly-once must survive the new writability precondition)."
        )


# M1: truthy-but-non-dict handoff values that must DEFER. The journal schema
# requires handoff to be a dict; without the isinstance gate any of these would
# pass a bare presence check, claim the O_EXCL marker, then fail append_event's
# schema validation — an orphaned/poisoned marker.
_NON_DICT_HANDOFFS = [
    ("string", "a handoff string"),
    ("list", ["produced", "decisions"]),
    ("int", 42),
    ("bool", True),
]


class TestHandoffTypeGateParity:
    """M1: a truthy-but-NON-DICT metadata.handoff must DEFER on BOTH paths —
    claim NO marker, write NO event — exactly like the unwritable case. The
    journal is WRITABLE here, so the ONLY defer cause under test is the handoff
    TYPE (isolated from the #917 writability gate). The valid-dict positive
    control is TestWritabilityGateParity::test_writable_fire_claims_marker_and_
    emits_once, so this is not 'always suppress'. NON-VACUOUS: dropping the
    isinstance(dict) guard lets the marker get claimed (file appears) and these
    fail."""

    @pytest.mark.parametrize("label,driver", _PATHS)
    @pytest.mark.parametrize("kind,handoff", _NON_DICT_HANDOFFS)
    def test_non_dict_handoff_defers_no_marker_no_event(
        self, label, driver, kind, handoff, tmp_path, monkeypatch, pact_context
    ):
        calls: list = []
        driver(True, tmp_path, monkeypatch, pact_context, calls, handoff=handoff)
        assert _marker_files(tmp_path) == [], (
            f"{label}: a non-dict handoff ({kind}) must claim NO marker — a "
            "marker means the isinstance(dict) type gate is missing or runs "
            "AFTER already_emitted (orphaned-marker poison, M1)."
        )
        assert calls == [], (
            f"{label}: a non-dict handoff ({kind}) must write no event."
        )


def test_writability_check_precedes_marker_claim_in_source():
    """Source-ordering pin (relational): BOTH call sites must perform the
    get_journal_path() writability check BEFORE the already_emitted() marker
    claim in their source. Backstops the behavioral defer-test against a
    refactor that keeps both symbols but reorders them. Fails if a future edit
    drops the gate from either path.

    Targets the executable CALL STATEMENTS (`if not get_journal_path():` and
    `if already_emitted(`) rather than the bare symbol names — both functions
    mention already_emitted in incidental earlier comments (e.g. the path-
    sanitization note), so a bare `index("already_emitted")` would match the
    comment, not the call. The two emit paths use the identical gate
    statement, which is what makes this a true relational parity pin."""
    GATE_CALL = "if not get_journal_path():"
    MARKER_CALL = "if already_emitted("
    for fn, name in (
        (b1.main, "agent_handoff_emitter.main (b1)"),
        (tlg._emit_lead_side_agent_handoff, "task_lifecycle_gate._emit_lead_side_agent_handoff (b2)"),
    ):
        src = inspect.getsource(fn)
        assert GATE_CALL in src, (
            f"{name} lost its `{GATE_CALL}` writability gate (#917)."
        )
        assert MARKER_CALL in src, (
            f"{name} no longer calls already_emitted() — test may be stale."
        )
        assert src.index(GATE_CALL) < src.index(MARKER_CALL), (
            f"{name}: the get_journal_path() writability check must precede the "
            "already_emitted() marker claim — the gate must run BEFORE the "
            "O_EXCL test-and-set so a non-writable fire defers without claiming "
            "(#917)."
        )
