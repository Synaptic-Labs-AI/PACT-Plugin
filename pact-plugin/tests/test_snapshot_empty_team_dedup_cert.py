"""
Location: pact-plugin/tests/test_snapshot_empty_team_dedup_cert.py
Summary: Bidirectional certification of the snapshot family's empty-team
         dedup posture (session-dir marker-root fallback), at the seam
         level. Close direction (no durability regression): a targeted
         per-write TaskUpdate on an empty-team lead frame still mirrors
         (delta-only payload — the disk read is team-guarded) AND claims
         the content-key marker under the session dir; a symlinked
         fallback marker dir fails open (emit, never a marker through the
         symlink) and recovers to claiming once the fault clears; a
         changed payload supersedes with a second event + second marker.
         Open direction (bounded churn): an identical targeted per-write
         repeat is suppressed to exactly one event; a lead-completion
         emit and a per-write emit of identical content collapse to one
         event via the shared content-hash namespace under the session
         root; a failed journal append compensating-unclaims the
         session-root marker so a retry re-emits (claim/rollback root
         parity). Both-teammate-modes matrix (standing merge gate): the
         same targeted write under a lead frame emits + claims the
         session root, under an in-process teammate frame (session_id
         equal to the lead session, empty team) the topology leg
         fail-closes before any substrate call, and under a tmux
         teammate frame (distinct session_id, all-empty derived context)
         the writability precondition defers before any claim — frames
         are constructed from agent_type / session_id only, no mode flag
         anywhere. Plus a guard-order pin: a non-string team_name paired
         with a degenerate task_id fails open at the resolver (no raise,
         nothing created) because the key guards run before the team
         derivation. Runs against the REAL marker SSOT and a REAL
         on-disk journal (no mocking of agent_handoff_marker internals);
         the only patched binding is the module-under-test's
         append_event, and only in the rollback-parity rows where the
         journal-write failure IS the fault being injected.
Used by: pytest. Companion file test_snapshot_marker_root_fallback.py
         covers the substrate-surface rows (first-fire emits + claims,
         identical direct repeat suppressed, no-journal defer, the
         healthy-team two-root regression pin, and the falsy/relative
         root_dir resolver guards); this file certifies the seam-level
         and fault-path behavior on top of those.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import shared.pact_context as pact_context_module  # noqa: E402
import shared.session_journal as session_journal  # noqa: E402
import shared.task_metadata_snapshot as tms  # noqa: E402
import task_lifecycle_gate as tlg  # noqa: E402
from shared.agent_handoff_marker import already_emitted, unclaim  # noqa: E402
from shared.task_metadata_snapshot import (  # noqa: E402
    SNAPSHOT_MARKER_NAMESPACE,
    emit_task_metadata_snapshot,
)

LEAD = "PACT:pact-orchestrator"
TEAMMATE = "pact-devops-engineer"

SCOPE = {"files": ["hooks/shared/example.py"], "boundaries": "backend only"}
PAYLOAD_A = {"scope_contract": SCOPE}
PAYLOAD_B = {"scope_contract": {**SCOPE, "boundaries": "widened"}}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Test-scoped HOME: the journal, the session dir, and every marker
    root resolve under tmp_path."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return tmp_path


def _snapshot_events(session_dir: str) -> list:
    return session_journal.read_events_from(
        session_dir, "task_metadata_snapshot"
    )


def _marker_files(root: Path) -> list:
    marker_dir = root / SNAPSHOT_MARKER_NAMESPACE
    if not marker_dir.is_dir():
        return []
    return sorted(p.name for p in marker_dir.iterdir())


def _open_write_payload(task_id, metadata, agent_type=LEAD, session_id=None):
    """A metadata-only TaskUpdate (no status key) — the per-write mirror's
    fire surface. The frame carries ONLY structural signals (agent_type,
    session_id); there is no mode flag to carry."""
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": task_id, "metadata": metadata},
        "tool_response": {},
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
    if session_id is not None:
        payload["session_id"] = session_id
    return payload


def _completion_payload(task_id, subject, owner, metadata):
    """A lead TaskUpdate(status=completed) frame with post-state supplied
    via tool_response.task — drives the lead-completion seam without a
    team-scoped disk read (which an empty team cannot perform)."""
    return {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": task_id, "status": "completed"},
        "tool_response": {
            "task": {
                "id": task_id,
                "subject": subject,
                "owner": owner,
                "metadata": metadata,
            }
        },
        "agent_type": LEAD,
    }


# =============================================================================
# Close direction — no durability regression in the degenerate state
# =============================================================================
class TestEmptyTeamCloseDirection:
    def test_per_write_targeted_key_emits_delta_only_and_claims_session_root(
        self, home, pact_context
    ):
        """Empty team + resolvable journal, lead frame: a targeted
        metadata write through the REAL gate path appends exactly one
        event AND claims the content-key marker under the session dir.
        The payload is delta-only and the subject degrades to the
        sentinel: the team-scoped task-file read is guarded off on an
        empty team, so the write itself is all the seam can mirror."""
        pact_context(team_name="", session_id="s-pw-lead")
        session_dir = pact_context_module.get_session_dir()
        assert session_dir, "harness: session dir must resolve"

        tlg.evaluate_lifecycle(
            _open_write_payload("81", PAYLOAD_A, session_id="s-pw-lead")
        )

        events = _snapshot_events(session_dir)
        assert len(events) == 1
        assert events[0]["metadata"] == PAYLOAD_A
        assert events[0]["subject"] == "(no subject)"
        assert "owner" not in events[0]
        markers = _marker_files(Path(session_dir))
        assert len(markers) == 1
        assert markers[0].startswith("81-")

    def test_symlinked_marker_dir_fails_open_then_recovers(
        self, home, pact_context
    ):
        """Subsystem fault under the fallback root: with the session-dir
        marker dir pre-planted as a symlink, the emit still appends
        (fail-open — the marker-broken posture survives under the new
        root) and NO marker is written through the symlink. Clearing the
        fault proves the vector is live: the same payload re-emits (the
        faulted fire never claimed) and the marker now lands at exactly
        the path the symlink had occupied."""
        pact_context(team_name="", session_id="s-fault")
        session_dir = Path(pact_context_module.get_session_dir())
        session_dir.mkdir(parents=True, exist_ok=True)
        escape = home / "escape-target"
        escape.mkdir()
        planted = session_dir / SNAPSHOT_MARKER_NAMESPACE
        os.symlink(escape, planted)

        emit_task_metadata_snapshot("", "82", "subject", "owner", PAYLOAD_A)

        assert len(_snapshot_events(str(session_dir))) == 1
        assert list(escape.iterdir()) == [], (
            "a marker escaped through the symlinked dir"
        )

        # Fault cleared: the prior fire claimed nothing, so the same
        # payload fires again and claims at the real path — proving the
        # planted path IS the live marker target, not a lookalike.
        planted.unlink()
        emit_task_metadata_snapshot("", "82", "subject", "owner", PAYLOAD_A)

        assert len(_snapshot_events(str(session_dir))) == 2
        markers = _marker_files(session_dir)
        assert len(markers) == 1
        assert markers[0].startswith("82-")

    def test_changed_payload_supersedes_with_second_event_and_marker(
        self, home, pact_context
    ):
        """Supersession semantics intact in the degenerate state: two
        DIFFERENT payloads for the same task produce two events and two
        distinct content-key markers under the session root."""
        pact_context(team_name="", session_id="s-supersede")
        session_dir = pact_context_module.get_session_dir()

        emit_task_metadata_snapshot("", "83", "subject", "owner", PAYLOAD_A)
        emit_task_metadata_snapshot("", "83", "subject", "owner", PAYLOAD_B)

        assert len(_snapshot_events(session_dir)) == 2
        markers = _marker_files(Path(session_dir))
        assert len(markers) == 2
        assert markers[0] != markers[1]


# =============================================================================
# Open direction — churn is bounded to one claim per content key
# =============================================================================
class TestEmptyTeamBoundedChurn:
    def test_identical_per_write_repeat_suppressed_to_one_event(
        self, home, pact_context
    ):
        """The unbounded-duplicate case this posture exists to close: two
        identical targeted TaskUpdate writes through the REAL gate path
        collapse to exactly ONE event and ONE marker — the second fire
        claims nothing new and appends nothing."""
        pact_context(team_name="", session_id="s-repeat")
        session_dir = pact_context_module.get_session_dir()

        payload = _open_write_payload("84", PAYLOAD_A, session_id="s-repeat")
        tlg.evaluate_lifecycle(payload)
        tlg.evaluate_lifecycle(payload)

        assert len(_snapshot_events(session_dir)) == 1
        assert len(_marker_files(Path(session_dir))) == 1

    def test_cross_seam_dedup_completion_then_per_write(
        self, home, pact_context
    ):
        """Cross-seam dedup restored on the session root: a
        lead-completion emit of payload P then a per-write emit whose
        overlay resolves to the identical P dedup on the SHARED
        content-hash namespace — one event total, exactly as the team
        root dedups across seams for a healthy team."""
        pact_context(team_name="", session_id="s-xseam")
        session_dir = pact_context_module.get_session_dir()

        tlg.evaluate_lifecycle(
            _completion_payload("85", "subject", "owner", PAYLOAD_A)
        )
        assert len(_snapshot_events(session_dir)) == 1

        tlg.evaluate_lifecycle(
            _open_write_payload("85", PAYLOAD_A, session_id="s-xseam")
        )
        assert len(_snapshot_events(session_dir)) == 1
        assert len(_marker_files(Path(session_dir))) == 1

    @pytest.mark.parametrize("fail_mode", ["returns_false", "raises"])
    def test_failed_append_unclaims_session_marker_and_retry_reemits(
        self, home, pact_context, monkeypatch, fail_mode
    ):
        """Claim/rollback parity through the single root-derivation
        helper: when the journal append fails AFTER the session-root
        claim, the compensating unclaim removes the marker (resolved
        through the SAME root), so a later retry re-emits instead of
        being permanently suppressed. Both failure shapes are covered:
        append_event returning False and append_event raising. The
        patch target is the module-under-test's own append_event
        binding — the journal-write failure IS the injected fault; the
        marker claim, root resolution, and unclaim all stay real."""
        pact_context(team_name="", session_id=f"s-unclaim-{fail_mode}")
        session_dir = pact_context_module.get_session_dir()

        real_append = tms.append_event
        failing = {"active": True}

        def _flaky(event):
            if failing["active"]:
                if fail_mode == "raises":
                    raise OSError("journal write failed")
                return False
            return real_append(event)

        monkeypatch.setattr(tms, "append_event", _flaky)

        emit_task_metadata_snapshot("", "86", "subject", "owner", PAYLOAD_A)

        assert _snapshot_events(session_dir) == []
        assert _marker_files(Path(session_dir)) == [], (
            "a failed append left a poisoned session-root marker — the "
            "compensating unclaim did not resolve the fallback root"
        )

        failing["active"] = False
        emit_task_metadata_snapshot("", "86", "subject", "owner", PAYLOAD_A)

        assert len(_snapshot_events(session_dir)) == 1
        assert len(_marker_files(Path(session_dir))) == 1


# =============================================================================
# Both-teammate-modes matrix — standing merge gate. The SAME targeted
# write is driven under three frames distinguished ONLY by the runtime
# structural signals (agent_type; session_id vs the session context) —
# no mode flag exists in the inputs or the assertions.
# =============================================================================
class TestEmptyTeamBothModesMatrix:
    def test_lead_frame_emits_and_claims_session_root(
        self, home, pact_context
    ):
        """Lead frame (agent_type in the lead set), empty team: the
        per-write leg is admitted by the is_lead leg independently of
        team/config resolvability — emit + session-dir claim."""
        pact_context(team_name="", session_id="s-mode-lead")
        session_dir = pact_context_module.get_session_dir()

        tlg.evaluate_lifecycle(
            _open_write_payload(
                "87", PAYLOAD_A, agent_type=LEAD, session_id="s-mode-lead"
            )
        )

        assert len(_snapshot_events(session_dir)) == 1
        assert len(_marker_files(Path(session_dir))) == 1

    def test_in_process_teammate_frame_no_emit_no_claim(
        self, home, pact_context
    ):
        """Teammate frame whose session_id equals the session context's
        own id (the in-process shape), empty team: the topology leg
        fail-closes on the empty team BEFORE any config read or
        substrate call — no event lands anywhere, no marker dir is
        created anywhere. Unchanged fail-safe default behavior."""
        pact_context(team_name="", session_id="s-mode-inproc")

        tlg.evaluate_lifecycle(
            _open_write_payload(
                "87",
                PAYLOAD_A,
                agent_type=TEAMMATE,
                session_id="s-mode-inproc",
            )
        )

        assert list(home.rglob("*.jsonl")) == []
        assert list(home.rglob(SNAPSHOT_MARKER_NAMESPACE)) == []

    def test_tmux_teammate_frame_no_emit_no_claim(self, home, pact_context):
        """Teammate frame with a DISTINCT session_id and the all-empty
        derived context (the tmux teammate's context file is absent at
        its derived path): the writability precondition defers before
        any claim — the fallback-root code is never reached. No event
        anywhere, no marker anywhere."""
        pact_context(team_name="", session_id="", project_dir="")
        assert pact_context_module.get_session_dir() == ""

        tlg.evaluate_lifecycle(
            _open_write_payload(
                "87",
                PAYLOAD_A,
                agent_type=TEAMMATE,
                session_id="s-mode-tmux-other",
            )
        )

        assert list(home.rglob("*.jsonl")) == []
        assert list(home.rglob(SNAPSHOT_MARKER_NAMESPACE)) == []


# =============================================================================
# Resolver guard-order pin — the key guards run before the team derivation
# =============================================================================
class TestResolverGuardOrderFailOpen:
    @pytest.mark.parametrize("bad_team", [123, None, 3.5])
    @pytest.mark.parametrize("degenerate_task_id", ["", ".", ".."])
    def test_non_string_team_with_degenerate_task_id_fails_open(
        self, home, bad_team, degenerate_task_id
    ):
        """A non-string team_name paired with a degenerate task_id fails
        open at the resolver — no raise, never claims, nothing created —
        because the task_id/occupant key guards run BEFORE the team
        derivation touches team_name. (A non-string team with a VALID
        key still raises inside the hermetic emit wrapper as before;
        this pin covers only the guard-order surface that changed.)"""
        assert (
            already_emitted(bad_team, degenerate_task_id, "k1") is False
        )
        assert (
            already_emitted(bad_team, degenerate_task_id, "k1") is False
        )
        unclaim(bad_team, degenerate_task_id, "k1")  # must not raise
        assert list(home.rglob("*-k1")) == []


# =============================================================================
# Healing transition — context repaired mid-session flips the marker root
# =============================================================================
class TestHealingTransition:
    def test_heal_appends_exactly_one_duplicate_never_suppresses(
        self, home, pact_context
    ):
        """Context repaired mid-session (empty team heals to a real team,
        same session): the session-root marker claimed while the team was
        empty is INVISIBLE to the teams-namespace check, so the first
        healed emit of the identical payload APPENDS (bias-to-
        preservation — the canonical emit is never suppressed by the
        degenerate-state marker) and claims under the team root. The two
        marker populations coexist disjointly on disk, and a further
        identical healed emit is suppressed by the team-root marker — the
        transition costs EXACTLY one duplicate append, no more."""
        team = "pact-heal"
        pact_context(team_name="", session_id="s-heal")
        session_dir = pact_context_module.get_session_dir()
        assert session_dir, "harness: session dir must resolve"

        emit_task_metadata_snapshot("", "88", "subject", "owner", PAYLOAD_A)

        assert len(_snapshot_events(session_dir)) == 1
        assert len(_marker_files(Path(session_dir))) == 1

        # Heal: same session_id/project_dir (same session dir, same
        # journal), team_name now non-empty — as after a context repair.
        pact_context(team_name=team, session_id="s-heal")
        assert pact_context_module.get_session_dir() == session_dir

        emit_task_metadata_snapshot(team, "88", "subject", "owner", PAYLOAD_A)

        assert len(_snapshot_events(session_dir)) == 2, (
            "the healed canonical emit was suppressed by the "
            "degenerate-state session-root marker"
        )
        team_root = home / ".claude" / "teams" / team
        assert len(_marker_files(team_root)) == 1
        assert len(_marker_files(Path(session_dir))) == 1, (
            "the healed emit disturbed the session-root population"
        )

        # The duplicate bound is exactly one: the team-root marker now
        # suppresses further identical emits.
        emit_task_metadata_snapshot(team, "88", "subject", "owner", PAYLOAD_A)

        assert len(_snapshot_events(session_dir)) == 2
        assert len(_marker_files(team_root)) == 1
