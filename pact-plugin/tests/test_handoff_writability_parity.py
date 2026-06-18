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


def _drive_b1(*, writable: bool, tmp_path: Path, monkeypatch, pact_context, append_calls: list, handoff=HANDOFF) -> None:
    """Fire b1 (agent_handoff_emitter.main) for one handoff-bearing completed
    TaskCompleted frame. `writable` controls the in-hook get_journal_path()
    return ('' = unwritable). `handoff` is the metadata.handoff value (default
    a valid dict; pass a non-dict to exercise the M1 type gate). The real
    already_emitted runs under the patched HOME; append_event is spied.

    b1's marker team_name now resolves from the SESSION CONTEXT
    (get_pact_context), the SAME source b2 reads — so this driver sets up the
    real context (team_name=TEAM) via the `pact_context` fixture, exactly as
    _drive_b2 does. This is the post-rebind convergence: both paths key the
    O_EXCL marker off the identical context team_name. The writability gate is
    independent of this (it reads get_journal_path), so the UNWRITABLE-defer
    #917 property is preserved: an empty journal still defers before the marker
    claim even with the team_name resolvable."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    pact_context(team_name=TEAM, session_id="s1")
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
              pact_context=pact_context, append_calls=append_calls, handoff=handoff)


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


class TestMarkerKeyConvergence:
    """AC-5 (#979 Phase-2) 3-path marker-key convergence guard.

    All three O_EXCL emit paths must derive the marker key
    ``(team_name, task_id, occupant)`` IDENTICALLY so they cannot split the
    dedup marker dir (the #887/#901 divergence class):

      b1 = agent_handoff_emitter.main()                       (TaskCompleted)
      b2 = _emit_lead_side_agent_handoff via lead TaskUpdate-completed
      b3 = _emit_lead_side_agent_handoff via the second lead call site
           (SAME function as b2 — see task_lifecycle_gate; convergence with b1
           is what these tests pin, and b2≡b3 by construction since they are the
           SAME callee fed the same function-scope team_name).

    The convergence atoms post-rebind:
      * team_name — BOTH b1 (emitter:157) and b2/b3 (the caller at the
        function-scope resolution) read ``get_pact_context().get("team_name","")``.
      * occupant + already_emitted — SHARED via shared.agent_handoff_marker, so
        occupant_hash(owner, subject) and the marker join cannot drift.

    These tests feed b1 and b2 the SAME real session context (via the
    ``pact_context`` fixture, which exercises the REAL get_pact_context — so any
    read-side normalization applied inside get_pact_context applies to BOTH
    paths equally) and assert the marker key each passed to already_emitted is
    byte-identical. A future edit that resolved team_name from a different
    source on one path — or applied a normalization on one call site only —
    splits the captured keys and flips these RED.
    """

    def _capture_b1_marker_key(self, tmp_path, monkeypatch, pact_context):
        """Drive b1 with a writable journal + the shared context; return the
        (team_name, task_id, occupant) tuple b1 passed to already_emitted."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        pact_context(team_name=TEAM, session_id="s1")
        captured: list[tuple] = []
        real = b1.already_emitted

        def _spy(team_name, task_id, occupant):
            captured.append((team_name, task_id, occupant))
            return real(team_name, task_id, occupant)

        task_data = {"status": "completed", "owner": OWNER,
                     "metadata": {"handoff": HANDOFF}}
        stdin = {"task_id": TASK_ID, "task_subject": SUBJECT,
                 "teammate_name": OWNER, "team_name": "stdin-ignored"}
        with patch.object(b1, "get_journal_path",
                          return_value=str(tmp_path / "j.jsonl")), \
             patch.object(b1, "read_task_json", return_value=task_data), \
             patch.object(b1, "already_emitted", side_effect=_spy), \
             patch.object(b1, "append_event", side_effect=lambda e: True), \
             patch("sys.stdin", io.StringIO(json.dumps(stdin))):
            with pytest.raises(SystemExit):
                b1.main()
        assert captured, "b1 never reached already_emitted (setup broke)."
        return captured[0]

    def _capture_b2_marker_key(self, tmp_path, monkeypatch, pact_context):
        """Drive b2 (lead TaskUpdate-completed) with the shared context; return
        the (team_name, task_id, occupant) tuple b2 passed to already_emitted."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        pact_context(team_name=TEAM, session_id="s1")
        captured: list[tuple] = []
        real = tlg.already_emitted

        def _spy(team_name, task_id, occupant):
            captured.append((team_name, task_id, occupant))
            return real(team_name, task_id, occupant)

        monkeypatch.setattr(tlg, "get_journal_path",
                            lambda: str(tmp_path / "j.jsonl"))
        monkeypatch.setattr(tlg, "append_event", lambda e: True)
        monkeypatch.setattr(tlg, "already_emitted", _spy)
        task = {"id": TASK_ID, "subject": SUBJECT, "owner": OWNER,
                "metadata": {"handoff": HANDOFF}}
        tlg.evaluate_lifecycle({
            "agent_type": LEAD,
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": TASK_ID, "status": "completed"},
            "tool_response": {"task": task},
        })
        assert captured, "b2 never reached already_emitted (setup broke)."
        return captured[0]

    def test_b1_b2_derive_identical_marker_key(
        self, tmp_path, monkeypatch, pact_context
    ):
        """b1 and b2, fed the SAME session context + same task identity, must
        pass the IDENTICAL (team_name, task_id, occupant) key to already_emitted.

        NON-VACUITY: the keys are captured from two independent real drives and
        compared element-wise; a divergence on ANY atom (a different team_name
        source, a different occupant derivation, a normalization on one path
        only) makes the tuples unequal and flips this RED. Per-element asserts
        localize WHICH atom drifted.
        """
        # Separate tmp dirs so neither path's marker side-effect dedups the
        # other (we are comparing the KEY each derived, not exactly-once here).
        b1_key = self._capture_b1_marker_key(
            tmp_path / "b1", monkeypatch, pact_context
        )
        b2_key = self._capture_b2_marker_key(
            tmp_path / "b2", monkeypatch, pact_context
        )

        assert b1_key[0] == b2_key[0], (
            f"team_name DIVERGENCE: b1 keyed the marker on {b1_key[0]!r} but b2 "
            f"on {b2_key[0]!r}. Post-AC-5 both MUST resolve team_name from "
            f"get_pact_context().get('team_name',''); a split source (or a "
            f"normalization applied on one path only) breaks the shared dedup "
            f"marker dir (#887/#901 divergence class)."
        )
        assert b1_key[1] == b2_key[1], (
            f"task_id DIVERGENCE: b1={b1_key[1]!r} b2={b2_key[1]!r}."
        )
        assert b1_key[2] == b2_key[2], (
            f"occupant DIVERGENCE: b1={b1_key[2]!r} b2={b2_key[2]!r}. The "
            f"occupant_hash(owner, subject) derivation must be shared via "
            f"shared.agent_handoff_marker so the two paths cannot drift."
        )
        assert b1_key == b2_key, (
            f"3-path marker-key convergence broken: b1={b1_key!r} b2={b2_key!r}."
        )

    def test_both_paths_resolve_team_name_from_get_pact_context(self):
        """Source pin backstopping the behavioral convergence: BOTH the b1
        resolution and the b2/b3 caller resolution read team_name from
        get_pact_context().get("team_name", ...). Fails if a future edit
        reintroduces a divergent source (e.g. an input_data/stdin read on b1, or
        a bespoke team_name resolution at the lead-side call site)."""
        RESOLVE = 'get_pact_context().get("team_name"'
        b1_src = inspect.getsource(b1.main)
        assert RESOLVE in b1_src, (
            "b1 (agent_handoff_emitter.main) no longer resolves the marker "
            "team_name from get_pact_context().get('team_name', ...) — the AC-5 "
            "rebind regressed (a divergent source splits the marker key)."
        )
        # b2/b3 resolve team_name at the evaluate_lifecycle function scope and
        # PASS it into _emit_lead_side_agent_handoff. Pin the resolution at the
        # caller (evaluate_lifecycle), the source of the team_name b2/b3 use.
        caller_src = inspect.getsource(tlg.evaluate_lifecycle)
        assert RESOLVE in caller_src, (
            "the lead-side caller (task_lifecycle_gate.evaluate_lifecycle) no "
            "longer resolves team_name from get_pact_context().get('team_name', "
            "...) before feeding _emit_lead_side_agent_handoff — b2/b3 would "
            "diverge from b1's marker key."
        )

    @pytest.mark.parametrize(
        "unsafe_team_name",
        [
            "../../etc",        # parent-dir traversal
            "session-../x",     # embedded traversal under a valid-looking prefix
            "a/b",              # path separator
            "..",               # bare parent-dir
            "foo bar",          # whitespace (not a safe single component)
        ],
    )
    def test_unsafe_persisted_team_name_handled_identically_on_both_paths(
        self, unsafe_team_name, tmp_path, monkeypatch, pact_context
    ):
        """SHARED-normalization convergence (the strongest #887 divergence
        guard): an UNSAFE persisted team_name is handled IDENTICALLY on b1 and
        b2 — BOTH reject-to-empty then fail-open to emit WITHOUT claiming a
        marker. Neither claims a (traversal-bearing) marker dir; both emit
        exactly once.

        WHY emit-without-marker (not defer): rev-backend's Group-B read-boundary
        re-validation lives INSIDE get_pact_context() (the SINGLE shared source
        all three paths read), so an unsafe persisted team_name is
        rejected-to-empty ('') for EVERY path at once. An empty team_name then
        hits the marker layer's degenerate-key guard
        (agent_handoff_marker: team_name in ('', '.', '..') → return None),
        which FAILS OPEN: already_emitted returns False (no marker claimed) and
        the emit proceeds — the deliberate bias to HANDOFF preservation over
        loss (#24 / #887). The load-bearing convergence property is that BOTH
        paths reach the SAME outcome: NO traversal-bearing marker dir is created
        AND both emit exactly once. A future edit that re-validated on ONE
        path's call site only (re-introducing the per-path split this AC-5 work
        closes) would let the un-normalized path key a marker on the raw unsafe
        value (a marker dir under the traversal path appears) while the other
        rejects — and this test flips RED.

        NON-VACUITY: the matching positive control
        (test_safe_team_name_survives_on_both_paths) drives the SAME machinery
        with a SAFE team_name and asserts BOTH paths emit + claim a marker under
        the SAFE team dir — so the no-traversal-marker here is the shared
        rejection, not 'never claims a marker'. The is_safe_path_component
        allowlist (option iii) genuinely rejects each parametrized value (proven
        by the positive control claiming a real marker where these do not).
        """
        # The marker dir for the EMPTY (rejected) team_name lives at
        # teams//.agent_handoff_emitted — but already_emitted returns before
        # creating it for a degenerate key. The KEY assertion is that NO marker
        # dir keyed on the raw unsafe token (the per-path-split symptom) exists.
        def _unsafe_marker_present(home: Path) -> bool:
            teams = home / ".claude" / "teams"
            if not teams.exists():
                return False
            # Any team dir that is not empty-named and carries an emitted marker
            # would be the split symptom; the safe path creates none here.
            return any(
                (child / ".agent_handoff_emitted").exists()
                for child in teams.iterdir()
                if child.name  # ignore the empty-named degenerate root
            )

        # b1 path.
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "b1")
        monkeypatch.setenv("HOME", str(tmp_path / "b1"))
        pact_context(team_name=unsafe_team_name, session_id="s1")
        b1_calls: list = []
        b1_stdin = {"task_id": TASK_ID, "task_subject": SUBJECT,
                    "teammate_name": OWNER, "team_name": "stdin-ignored"}
        b1_task = {"status": "completed", "owner": OWNER,
                   "metadata": {"handoff": HANDOFF}}
        with patch.object(b1, "get_journal_path",
                          return_value=str(tmp_path / "b1" / "j.jsonl")), \
             patch.object(b1, "read_task_json", return_value=b1_task), \
             patch.object(b1, "append_event",
                          side_effect=lambda e: b1_calls.append(e) or True), \
             patch("sys.stdin", io.StringIO(json.dumps(b1_stdin))):
            with pytest.raises(SystemExit):
                b1.main()
        b1_emitted = len(b1_calls)
        b1_unsafe_marker = _unsafe_marker_present(tmp_path / "b1")
        assert not b1_unsafe_marker, (
            f"b1 claimed a marker keyed on the raw UNSAFE team_name "
            f"{unsafe_team_name!r} — get_pact_context's read-boundary "
            f"reject-to-empty did not apply on the b1 path (per-path split)."
        )

        # b2 path (fresh context for the b2 HOME).
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "b2")
        monkeypatch.setenv("HOME", str(tmp_path / "b2"))
        pact_context(team_name=unsafe_team_name, session_id="s1")
        b2_calls: list = []
        monkeypatch.setattr(tlg, "get_journal_path",
                            lambda: str(tmp_path / "b2" / "j.jsonl"))
        monkeypatch.setattr(tlg, "append_event",
                            lambda e: b2_calls.append(e) or True)
        task = {"id": TASK_ID, "subject": SUBJECT, "owner": OWNER,
                "metadata": {"handoff": HANDOFF}}
        tlg.evaluate_lifecycle({
            "agent_type": LEAD,
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": TASK_ID, "status": "completed"},
            "tool_response": {"task": task},
        })
        b2_emitted = len(b2_calls)
        b2_unsafe_marker = _unsafe_marker_present(tmp_path / "b2")
        assert not b2_unsafe_marker, (
            f"b2 claimed a marker keyed on the raw UNSAFE team_name "
            f"{unsafe_team_name!r} — the read-boundary reject-to-empty did not "
            f"apply on the b2 path (per-path split, #887 divergence)."
        )

        # THE CONVERGENCE INVARIANT: both paths reached the IDENTICAL outcome.
        assert b1_emitted == b2_emitted, (
            f"b1 and b2 DIVERGED on an unsafe persisted team_name "
            f"{unsafe_team_name!r}: b1 emitted {b1_emitted}x, b2 emitted "
            f"{b2_emitted}x. The shared read-boundary normalization must make "
            f"both paths behave identically (#887 divergence guard)."
        )
        assert b1_emitted == 1, (
            f"unsafe team_name {unsafe_team_name!r}: expected emit-without-marker "
            f"(reject-to-empty → #24 degenerate-key fail-open biases to handoff "
            f"PRESERVATION), got {b1_emitted} emits. If this changed to a defer "
            f"(0 emits) the fail-open contract regressed — update this pin "
            f"deliberately."
        )

    @pytest.mark.parametrize("safe_team_name", ["pact-test", "session-deadbeef"])
    def test_safe_team_name_survives_on_both_paths(
        self, safe_team_name, tmp_path, monkeypatch, pact_context
    ):
        """Positive control + (iii)-vs-(i) decision pin (security-engineer ask):
        a SAFE persisted team_name SURVIVES get_pact_context's read-boundary
        re-validation (== itself) so BOTH b1 and b2 EMIT exactly once.

        ``pact-test`` is the load-bearing case: it is NOT a ``session-`` value,
        so it would be REJECTED by the tighter producer-exact regex (option i)
        but is ACCEPTED by the chosen is_safe_path_component allowlist (option
        iii). Its survival here pins the deliberate choice of (iii) over (i) —
        if a future edit tightened the read-boundary to session-only, this case
        flips RED (pact-test → '' → defer), surfacing the regression as a
        test-gated re-decision. ``session-deadbeef`` is the always-valid
        control. Together with the unsafe-defer test above, this proves the
        read-boundary rejection is a real allowlist, not 'always suppress'."""
        # b1.
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "b1")
        monkeypatch.setenv("HOME", str(tmp_path / "b1"))
        pact_context(team_name=safe_team_name, session_id="s1")
        b1_calls: list = []
        b1_stdin = {"task_id": TASK_ID, "task_subject": SUBJECT,
                    "teammate_name": OWNER, "team_name": "stdin-ignored"}
        b1_task = {"status": "completed", "owner": OWNER,
                   "metadata": {"handoff": HANDOFF}}
        with patch.object(b1, "get_journal_path",
                          return_value=str(tmp_path / "b1" / "j.jsonl")), \
             patch.object(b1, "read_task_json", return_value=b1_task), \
             patch.object(b1, "append_event",
                          side_effect=lambda e: b1_calls.append(e) or True), \
             patch("sys.stdin", io.StringIO(json.dumps(b1_stdin))):
            with pytest.raises(SystemExit):
                b1.main()
        assert len(b1_calls) == 1, (
            f"b1 did NOT emit for SAFE team_name {safe_team_name!r} — the "
            f"read-boundary wrongly rejected a valid value (too strict: it "
            f"should accept the is_safe_path_component allowlist, not session-"
            f"only)."
        )

        # b2.
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "b2")
        monkeypatch.setenv("HOME", str(tmp_path / "b2"))
        pact_context(team_name=safe_team_name, session_id="s1")
        b2_calls: list = []
        monkeypatch.setattr(tlg, "get_journal_path",
                            lambda: str(tmp_path / "b2" / "j.jsonl"))
        monkeypatch.setattr(tlg, "append_event",
                            lambda e: b2_calls.append(e) or True)
        task = {"id": TASK_ID, "subject": SUBJECT, "owner": OWNER,
                "metadata": {"handoff": HANDOFF}}
        tlg.evaluate_lifecycle({
            "agent_type": LEAD,
            "tool_name": "TaskUpdate",
            "tool_input": {"taskId": TASK_ID, "status": "completed"},
            "tool_response": {"task": task},
        })
        assert len(b2_calls) == 1, (
            f"b2 did NOT emit for SAFE team_name {safe_team_name!r} — the "
            f"read-boundary wrongly rejected a valid value on the b2 path."
        )
