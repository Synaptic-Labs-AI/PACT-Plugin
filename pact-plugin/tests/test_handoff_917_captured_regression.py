"""
Location: pact-plugin/tests/test_handoff_917_captured_regression.py
Summary: The #917 defer-then-emit SEQUENCE, grounded in REAL captured fixtures.
Used by: the pact-plugin test suite (standing merge gate).

WHAT THIS ADDS OVER THE EXISTING SUITE
--------------------------------------
test_handoff_writability_parity.py already pins, with INLINE SYNTHETIC frames
and a MONKEYPATCHED get_journal_path(), the ISOLATED properties: an unwritable
fire defers (no marker, no event), a writable fire emits exactly once, a re-fire
dedups, and the source-ordering parity of the gate. test_handoff_b1_b2_dedup.py
::TestDualModeMatrix pins the is_lead both-modes gate. This file does NOT
re-assert those. It closes the two genuine gaps the dispatch named:

1. THE SEQUENCE (the load-bearing #917 proof). The regression is not "a fire
   defers" in isolation — it is that a NON-WRITABLE b1 poison-attempt must NOT
   block the SUBSEQUENT writable b2, with BOTH contending for the SAME marker
   (aligned team / task_id / occupant). A single-path defer test is
   necessary-but-insufficient. Here a teammate-process b1 (captured frame,
   team_name PRESENT but session unpersisted) DEFERS, then the lead's writable
   b2 EMITS exactly one event for the same marker. Byte-faithful #917: pre-fix
   b1 claims the marker + b2 is suppressed = 0 events; post-fix b1 defers + b2
   emits = 1 event.

2. REAL CAPTURED FIXTURES + NATURAL WRITABILITY (#880, masking-de-risking).
   The frames are role_frames.captured_* (real stdin captured under tmux), and
   writability resolves NATURALLY through pact_context (an unpersisted teammate
   context yields get_journal_path() == "" with NO monkeypatch of the gate) —
   precisely because the open masking question is whether a monkeypatched gate
   faithfully models writability. The teammate fire's deferral is proved to be
   for the RIGHT reason (get_journal_path() == "" asserted directly + a
   same-frame writable POSITIVE CONTROL that emits), and the whole file's
   non-vacuity is backstopped by a source-only-revert counter-test (documented
   in the HANDOFF; reverting the C1/C2 gate flips the sequence to net 0).

The marker mechanism (shared.agent_handoff_marker.already_emitted) runs for
real against a tmp HOME, so marker-file assertions are against the actual
O_EXCL side-effect, not a mock. append_event is spied (the path is never
written) so "emits" means "the journal write was attempted exactly once".
"""
import copy
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import agent_handoff_emitter as b1  # noqa: E402
import task_lifecycle_gate as tlg  # noqa: E402
from shared.agent_handoff_marker import occupant_hash  # noqa: E402
from shared.pact_context import is_lead  # noqa: E402
from shared.session_journal import get_journal_path  # noqa: E402
from fixtures.emitter import VALID_HANDOFF  # noqa: E402
from fixtures.role_frames import (  # noqa: E402
    captured_lead_posttooluse_taskupdate_completed,
    captured_lead_taskcompleted,
    captured_teammate_taskcompleted,
)

# Identity shared by b1 and b2 so they contend for the SAME marker.
# team / task_id / owner come from the captured teammate frame (real values);
# the subject is read from that frame so the occupant key aligns byte-for-byte.
TEAM = "pact-e5e2be7d"          # captured_teammate_taskcompleted.team_name
TASK_ID = "10"                  # captured_teammate_taskcompleted.task_id
OWNER = "architect"            # captured_teammate_taskcompleted.teammate_name
LEAD_SESSION = "e5e2be7d-84fb-4eb8-a932-1ca4557b4a43"  # lead/team session (writable)
TEAMMATE_SESSION = "ce2de714-7b5c-48b5-a202-d6275bd5de47"  # foreign/unpersisted


def _strip_meta(frame: dict) -> dict:
    """Return a copy of a captured frame WITHOUT the ``_meta`` provenance key.

    The platform never delivers ``_meta`` to a hook; the fixtures carry it for
    auditability. Strip it before feeding stdin so the test exercises exactly
    the bytes the hook would see in production.
    """
    f = copy.deepcopy(frame)
    f.pop("_meta", None)
    return f


def _marker_files(home: Path) -> list[str]:
    """Names of marker files the REAL O_EXCL test-and-set created under the
    patched HOME, or [] if the marker dir was never created (the defer)."""
    marker_dir = home / ".claude" / "teams" / TEAM / ".agent_handoff_emitted"
    return sorted(p.name for p in marker_dir.iterdir()) if marker_dir.exists() else []


def _task_data(subject: str) -> dict:
    """Disk-shape task record both paths read (b1 via read_task_json, b2 via
    its disk-fallback). owner + subject define the occupant; handoff present so
    both paths reach the writability gate (not the handoff-presence gate)."""
    return {
        "id": TASK_ID,
        "owner": OWNER,
        "subject": subject,
        "status": "completed",
        "metadata": {"handoff": VALID_HANDOFF},
    }


def _count_agent_handoff_events() -> int:
    """Count agent_handoff events in the CURRENTLY-resolved canonical journal.

    Reads the file at get_journal_path() directly (rather than a spy) so the
    headline SEQUENCE asserts the REAL end-to-end outcome: pre-fix the poisoned
    marker yields 0 events; post-fix the lead's b2 write yields exactly 1.
    """
    path = get_journal_path()
    if not path or not Path(path).exists():
        return 0
    n = 0
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if json.loads(line).get("type") == "agent_handoff":
            n += 1
    return n


def _run_b1(frame: dict, task_data: dict, append_calls, monkeypatch) -> None:
    """Fire b1 (agent_handoff_emitter.main) with a CAPTURED frame as stdin.

    Does NOT patch get_journal_path — writability resolves NATURALLY via
    pact_context (the masking-de-risking model). read_task_json supplies the
    task. ``append_calls`` is a list to spy emissions, or None to use the REAL
    append_event end-to-end (byte-faithful journal write/loss). The REAL marker
    mechanism always runs.
    """
    monkeypatch.setattr(b1, "read_task_json", lambda tid, tn: task_data)
    if append_calls is not None:
        monkeypatch.setattr(b1, "append_event", lambda e: append_calls.append(e) or True)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_strip_meta(frame))))
    with pytest.raises(SystemExit):
        b1.main()


def _run_b2(frame: dict, task_data: dict, append_calls, monkeypatch) -> None:
    """Fire b2 (task_lifecycle_gate.evaluate_lifecycle) with a CAPTURED lead
    PostToolUse(TaskUpdate, completed) frame whose tool_response carries NO
    'task' (the #917 disk-fallback shape). get_journal_path is NOT patched
    (natural resolution); read_task_json supplies the disk-fallback task.
    ``append_calls`` is a spy list, or None to use the REAL append_event.
    """
    monkeypatch.setattr(tlg, "read_task_json", lambda tid, tn: task_data)
    if append_calls is not None:
        monkeypatch.setattr(tlg, "append_event", lambda e: append_calls.append(e) or True)
    tlg.evaluate_lifecycle(_strip_meta(frame))


class TestSequence917CapturedRegression:
    """The defer-then-emit SEQUENCE on REAL captured fixtures — the #917 proof."""

    def test_teammate_b1_unwritable_defers_then_lead_b2_emits_once(
        self, tmp_path, monkeypatch, pact_context
    ):
        """THE #917 repro. A teammate b1 (unpersisted context → journal
        unwritable) DEFERS (claims NO marker), then the lead's writable b2
        EMITS exactly one event for the SAME marker. Net = 1.

        Pre-fix this sequence netted 0 (b1 claimed the marker then lost the
        append = poison; b2 hit already_emitted()==True and was suppressed).
        Post-fix b1 defers and b2 emits → 1. Non-vacuity is proved by the
        source-only-revert counter-test in the HANDOFF (gate removed → net 0).
        """
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        # A real teammate process HAS a project dir + session_id, but its
        # session-context file was never persisted (persist is is_lead-gated,
        # #877) → get_journal_path() resolves to "" with no monkeypatch.
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))

        teammate = captured_teammate_taskcompleted()
        lead = captured_lead_posttooluse_taskupdate_completed()
        subject = teammate["task_subject"]
        occupant = occupant_hash(OWNER, subject)
        task_data = _task_data(subject)

        # --- b1: teammate, UNWRITABLE → must DEFER (no pact_context set up) ---
        # REAL append_event (append_calls=None): on an unwritable journal it
        # writes nothing and returns False — byte-faithful to production, where
        # pre-fix b1 claimed the marker and then LOST this append (the poison).
        _run_b1(teammate, task_data, None, monkeypatch)
        assert get_journal_path() == "", (
            "model check: an unpersisted teammate context must make the "
            "canonical journal UNWRITABLE (get_journal_path()=='') — natural "
            "resolution, no monkeypatch."
        )
        assert _marker_files(tmp_path) == [], (
            "b1 must DEFER on an unwritable journal — a claimed marker here is "
            "the #917 claim-without-write poison."
        )

        # --- b2: lead, WRITABLE → must EMIT exactly once for the same marker ---
        pact_context(team_name=TEAM, session_id=LEAD_SESSION)
        lead["tool_input"]["taskId"] = TASK_ID  # align marker to the poisoned task
        _run_b2(lead, task_data, None, monkeypatch)

        assert _marker_files(tmp_path) == [f"{TASK_ID}-{occupant}"], (
            "b2 must claim the now-free marker (b1 deferred, so it was never "
            "poisoned)."
        )
        # THE load-bearing #917 assertion (byte-faithful): exactly ONE event in
        # the canonical journal. Pre-fix this is 0 (b1 poisons the marker + its
        # append fails → b2 hits already_emitted()==True and is suppressed);
        # post-fix it is 1 (b1 defers → b2 emits).
        assert _count_agent_handoff_events() == 1, (
            "the canonical journal must hold EXACTLY ONE agent_handoff event "
            "after the defer-then-emit sequence (#917 0/7 closed)."
        )

    def test_captured_teammate_b1_emits_when_writable_positive_control(
        self, tmp_path, monkeypatch, pact_context
    ):
        """POSITIVE CONTROL for the deferral above: the SAME captured teammate
        frame, given a WRITABLE context, claims its marker and emits once.

        Proves the deferral in the sequence test is caused SOLELY by
        unwritability — not by the teammate frame tripping some unrelated
        earlier gate (handoff-presence, owner-empty, signal-task).
        """
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        # Persist a context for the teammate's own session → journal writable.
        pact_context(team_name=TEAM, session_id=TEAMMATE_SESSION)

        teammate = captured_teammate_taskcompleted()
        subject = teammate["task_subject"]
        occupant = occupant_hash(OWNER, subject)

        calls: list = []
        _run_b1(teammate, _task_data(subject), calls, monkeypatch)

        assert get_journal_path() != "", "model check: this context is writable"
        assert _marker_files(tmp_path) == [f"{TASK_ID}-{occupant}"], (
            "a WRITABLE teammate b1 fire must claim its marker."
        )
        assert len(calls) == 1, "a writable b1 fire emits exactly once."

    def test_lead_b2_emits_for_handoff_bearing_teammate_task(
        self, tmp_path, monkeypatch, pact_context
    ):
        """AGREEMENT VERIFICATION (L2): the fix fulfils #917's ORIGINAL purpose.

        Standalone (no prior b1): the lead completing a handoff-bearing teammate
        task via TaskUpdate(status=completed) — the real captured b2 frame whose
        tool_response has NO 'task' (disk-fallback always taken) — emits exactly
        one agent_handoff event. This is the behaviour #917 reported as broken
        (0/7); here it fires.
        """
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        pact_context(team_name=TEAM, session_id=LEAD_SESSION)

        lead = captured_lead_posttooluse_taskupdate_completed()
        lead["tool_input"]["taskId"] = TASK_ID
        subject = captured_teammate_taskcompleted()["task_subject"]

        calls: list = []
        _run_b2(lead, _task_data(subject), calls, monkeypatch)
        assert len(calls) == 1, (
            "the lead's b2 emit must fire for a lead completing a "
            "handoff-bearing teammate task (the #917 original purpose)."
        )


class TestBothModesCaptured:
    """Dual-mode pin grounded in the REAL captured frames (complements the
    synthetic TestDualModeMatrix)."""

    def test_is_lead_classifies_captured_frames(self):
        """The real #917 frames classify correctly: the teammate TaskCompleted
        is NOT lead (tmux teammate mode), both lead frames ARE lead. This is the
        role-classification ground truth the SEQUENCE relies on."""
        assert is_lead(captured_teammate_taskcompleted()) is False
        assert is_lead(captured_lead_posttooluse_taskupdate_completed()) is True
        assert is_lead(captured_lead_taskcompleted()) is True

    def test_b2_gate_suppresses_captured_teammate_spelling(
        self, tmp_path, monkeypatch, pact_context
    ):
        """The b2 emit is is_lead-gated: the REAL captured teammate agent_type
        spelling ('pact-architect') applied to a b2-shaped frame is SUPPRESSED,
        while the lead spelling on the same frame EMITS (positive control).

        Frame STRUCTURE is the captured lead PostToolUse(TaskUpdate, completed);
        only agent_type is varied — the captured-grounded analog of
        TestDualModeMatrix's synthetic suppress test.
        """
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        pact_context(team_name=TEAM, session_id=LEAD_SESSION)

        teammate_spelling = captured_teammate_taskcompleted()["agent_type"]
        subject = captured_teammate_taskcompleted()["task_subject"]

        # teammate spelling → is_lead False → b2 suppressed
        suppressed = captured_lead_posttooluse_taskupdate_completed()
        suppressed["tool_input"]["taskId"] = TASK_ID
        suppressed["agent_type"] = teammate_spelling
        sup_calls: list = []
        _run_b2(suppressed, _task_data(subject), sup_calls, monkeypatch)
        assert sup_calls == [], (
            "a teammate agent_type at the b2 call site must be suppressed by "
            "the is_lead gate."
        )

        # positive control: lead spelling on the same frame → emits
        lead = captured_lead_posttooluse_taskupdate_completed()
        lead["tool_input"]["taskId"] = TASK_ID
        lead_calls: list = []
        _run_b2(lead, _task_data(subject), lead_calls, monkeypatch)
        assert len(lead_calls) == 1, (
            "same frame, lead spelling → emits (proves the suppression is the "
            "is_lead gate, not a missing precondition)."
        )


class TestReverseOrderingBenign:
    """F1 completeness pins: the BENIGN reverse orderings — a LEGITIMATELY-claimed
    marker (a writable fire that actually wrote) followed by an unwritable fire —
    CANNOT poison.

    These are deliberately NOT gate-coupled: already_emitted() would dedup the
    second fire even without the writability gate, which is EXACTLY why the
    reverse direction is safe. #917's poison requires the UNWRITABLE fire to come
    FIRST and claim the marker before any successful write (the headline
    SEQUENCE). Here the writable fire claims first, so the marker honestly
    reflects a written event and the later unwritable fire (a) defers at its
    writability gate and (b) could not corrupt the marker even if it reached
    already_emitted(). These pin no-double-emit + marker-integrity for the two
    orderings the headline SEQUENCE does not cover.

    Writability is modeled NATURALLY (consistent with this file): a writable
    process sets a resolvable pact_context; the unwritable process sets a context
    whose team_name resolves (so the fire still reads the handoff and REACHES the
    gate) but whose session_id is empty, so get_journal_path()=='' — the same
    team-resolvable / journal-unresolvable split that defines the #917 hazard.
    """

    def test_writable_b1_then_unwritable_b2_cannot_poison(
        self, tmp_path, monkeypatch, pact_context
    ):
        """(a) A writable b1 legitimately claims the marker + emits; a SUBSEQUENT
        b2 in a journal-unwritable process DEFERS at the C2 gate without touching
        the existing marker. Net: exactly ONE event, marker intact, no poison."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        teammate = captured_teammate_taskcompleted()
        lead = captured_lead_posttooluse_taskupdate_completed()
        subject = teammate["task_subject"]
        occupant = occupant_hash(OWNER, subject)
        task_data = _task_data(subject)
        calls: list = []

        # b1 WRITABLE → legitimate claim + emit
        pact_context(team_name=TEAM, session_id=LEAD_SESSION)
        _run_b1(teammate, task_data, calls, monkeypatch)
        assert get_journal_path() != "", "b1 model check: writable"
        assert _marker_files(tmp_path) == [f"{TASK_ID}-{occupant}"], "b1 claims the marker"
        assert len(calls) == 1, "b1 emits once"

        # b2 UNWRITABLE (team_name resolves so it reads the handoff + reaches the
        # gate; session_id empty so the journal is unresolvable) → C2 defers
        pact_context(team_name=TEAM, session_id="")
        assert get_journal_path() == "", (
            "b2 model check: journal unwritable while team still resolvable "
            "(the #917 split)."
        )
        lead["tool_input"]["taskId"] = TASK_ID
        _run_b2(lead, task_data, calls, monkeypatch)

        assert len(calls) == 1, (
            "the unwritable b2 must add NO event (C2 defer) — cannot poison a "
            "legitimately-claimed marker."
        )
        assert _marker_files(tmp_path) == [f"{TASK_ID}-{occupant}"], (
            "the legitimately-claimed marker is untouched by the deferred b2."
        )

    def test_writable_b2_then_unwritable_b1_cannot_poison(
        self, tmp_path, monkeypatch, pact_context
    ):
        """(b) The realistic reverse of the #917 sequence: the lead's writable b2
        emits + claims first (legitimate), THEN the platform Stop-sweep dispatches
        an unwritable teammate b1 for the same task — it DEFERS at the C1 gate
        (and already_emitted would dedup it anyway). Net: exactly ONE event,
        marker intact, no poison."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        teammate = captured_teammate_taskcompleted()
        lead = captured_lead_posttooluse_taskupdate_completed()
        subject = teammate["task_subject"]
        occupant = occupant_hash(OWNER, subject)
        task_data = _task_data(subject)
        calls: list = []

        # b2 WRITABLE (lead) → legitimate claim + emit
        pact_context(team_name=TEAM, session_id=LEAD_SESSION)
        lead["tool_input"]["taskId"] = TASK_ID
        _run_b2(lead, task_data, calls, monkeypatch)
        assert _marker_files(tmp_path) == [f"{TASK_ID}-{occupant}"], "b2 claims the marker"
        assert len(calls) == 1, "b2 emits once"

        # b1 UNWRITABLE (teammate, unpersisted journal) for the SAME key → C1 defers
        pact_context(team_name=TEAM, session_id="")
        assert get_journal_path() == "", "b1 model check: journal unwritable"
        _run_b1(teammate, task_data, calls, monkeypatch)

        assert len(calls) == 1, (
            "the unwritable b1 must add NO event (C1 defer) — a Stop-sweep "
            "TaskCompleted arriving after the lead's legitimate emit cannot "
            "poison."
        )
        assert _marker_files(tmp_path) == [f"{TASK_ID}-{occupant}"], (
            "the legitimately-claimed marker is untouched by the deferred b1."
        )


class TestFixtureProvenance:
    """T7 — the captured fixtures carry auditable provenance and the synthetic
    placeholder has been retired (#880 no-synthetic-stdin)."""

    def test_917_diagnostic_accessors_carry_capture_method(self):
        """Every #917-diagnostic captured accessor returns a frame carrying a
        non-empty _meta.capture_method (the provenance that makes the
        no-synthetic-stdin discipline auditable)."""
        for accessor in (
            captured_teammate_taskcompleted,
            captured_lead_posttooluse_taskupdate_completed,
            captured_lead_taskcompleted,
        ):
            frame = accessor()
            meta = frame.get("_meta", {})
            method = meta.get("capture_method", "")
            assert isinstance(method, str) and method, (
                f"{accessor.__name__} must carry a non-empty "
                f"_meta.capture_method; got {method!r}"
            )
            assert "synthetic" not in method.lower(), (
                f"{accessor.__name__} must be a REAL capture, not synthetic "
                f"(capture_method={method!r})"
            )

    def test_917_frames_encode_the_team_name_poisoning_asymmetry(self):
        """The poisoning asymmetry is in the captured data: the teammate
        TaskCompleted carries a stdin team_name (b1 can claim the marker dir),
        the lead frames do NOT (their writable b2 path is the emitter). This is
        the structural precondition for the #917 split."""
        assert captured_teammate_taskcompleted().get("team_name") == TEAM, (
            "the teammate frame must carry the stdin team_name that lets b1 "
            "claim the marker."
        )
        assert "team_name" not in captured_lead_taskcompleted(), (
            "the lead TaskCompleted must NOT carry a stdin team_name."
        )
        assert "team_name" not in captured_lead_posttooluse_taskupdate_completed(), (
            "the lead PostToolUse frame must NOT carry a stdin team_name."
        )

    def test_retired_synthetic_userpromptsubmit_fixture_is_real_capture(self):
        """The TODO #672 synthetic placeholder
        (userpromptsubmit_stdin_post_bootstrap.json) is retired: the file now
        holds a REAL captured lead UserPromptSubmit frame with provenance and
        no synthetic markers."""
        path = (
            Path(__file__).parent
            / "fixtures"
            / "userpromptsubmit_stdin_post_bootstrap.json"
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        method = data.get("_meta", {}).get("capture_method", "")
        assert isinstance(method, str) and method, (
            "the retired fixture must carry _meta.capture_method provenance."
        )
        assert "synthetic" not in method.lower(), (
            "the fixture must be a REAL capture (synthetic placeholder retired)."
        )
        assert data.get("agent_type") == "PACT:pact-orchestrator", (
            "the captured UserPromptSubmit frame is a qualified-lead capture."
        )
        # Real UserPromptSubmit frames carry NO 'source' (SessionStart-only).
        assert "source" not in data, (
            "real UserPromptSubmit frames carry no 'source' field."
        )
