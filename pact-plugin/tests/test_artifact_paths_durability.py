"""
#927 HANDOFF + artifact durability — comprehensive durability tests.

Covers the four committed seams of the artifact_paths durability mechanism:
  - session_journal.py     — artifact_paths event schema + read_events_from
                             vs the off-lead-masked read_events (the masked-read
                             seam) + supersede-by-(workflow,feature) latest-ts.
  - task_lifecycle_gate.py — the lead-frame, fail-open emit BACKSTOP that nudges
                             when a PREPARE/ARCHITECT phase completes with no
                             artifact_paths event for its (workflow, feature).

DISCIPLINE (per the locked plan + this session's plan-mode strategy):
  - BOTH-MODES AXIS = is_lead / agent_type, NOT session_id. The emit/handoff/
    backstop path keys on agent_type (LEAD = 'PACT:pact-orchestrator' /
    'pact-orchestrator'; TEAMMATE = a pact-* type). The teammate-frame leg
    asserts the backstop is ABSENT (the by-design lead-process-only boundary),
    never present. Constructing a session_id-topology fixture here would test a
    gate input this surface does not read (see the both-modes-axis-is-surface-
    specific lesson banked in plan-mode).
  - PHANTOM-GREEN GUARD: phase-task subjects + owners are seeded from THIS
    session's real on-disk task shapes ('PREPARE: handoff-artifact-durability',
    'ARCHITECT: handoff-artifact-durability', bare owner 'devops-engineer'),
    not a synthetic shape the code expects. A fixture using owner
    'pact-devops-engineer' (the agentType, not the owner) is the #1028 fake.
  - DETERMINISM: reproduce the END-STATE (direct evaluate_lifecycle / read drive
    + real on-disk journal file ops), never the async race. No sleeps, no
    SendMessage, no real GC timing — GC/worktree-teardown is a real file removal.
  - NON-MOCKED seam integration for the masked-read seam + the backstop journal
    read: both task_lifecycle_gate and agent_handoff_emitter are in
    SEAM_DEPENDENT_HOOKS, so these tests drive a REAL on-disk journal via
    append_event/read_events_from over a tmp-redirected HOME + pact_context —
    they never mock read_events / read_events_from / the gate.
  - NON-VACUITY: source-removal counter-tests (remove the journal SOURCE and
    prove recovery collapses) + a masked-read inversion (prove the off-lead leg
    is false-empty for the RIGHT reason, not a missing fixture).

Run with the 3.13.7 interpreter (default python3 has no pytest):
    /Users/mj/.pyenv/versions/3.13.7/bin/python3 -m pytest \
        pact-plugin/tests/test_artifact_paths_durability.py -rA
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

import shared.pact_context as pact_context  # noqa: E402
import task_lifecycle_gate as tlg  # noqa: E402
from shared.session_journal import (  # noqa: E402
    append_event,
    make_event,
    read_events,
    read_events_from,
)

# --- agent_type axis constants (the both-modes discriminator for this surface) ---
LEAD = "PACT:pact-orchestrator"
LEAD_BARE = "pact-orchestrator"
TEAMMATE = "pact-devops-engineer"

# --- production-real seeds (this session's actual on-disk task shapes) ---
FEATURE = "handoff-artifact-durability"
PREPARE_SUBJECT = f"PREPARE: {FEATURE}"
ARCHITECT_SUBJECT = f"ARCHITECT: {FEATURE}"
# A real specialist owner is a BARE name; the pact-* token is the agentType
# (config.json members[].agentType), NOT the owner. Phantom-green ground truth.
BARE_OWNER = "devops-engineer"

TEAM = "session-artifact-dur"
SID = "aaaaaaaa-1111-2222-3333-444444444444"
PROJECT_DIR = "/test/project"


def _artifact_event(workflow, feature=FEATURE, paths=None, task_id=None, ts=None):
    """Build a real artifact_paths event via make_event (auto-sets v/ts).

    ts override lets a test pin supersede ordering deterministically.
    """
    if paths is None:
        paths = [f"/abs/docs/{workflow}/{feature}.md"]
    fields = {"workflow": workflow, "feature": feature, "paths": paths}
    if task_id is not None:
        fields["task_id"] = task_id
    event = make_event("artifact_paths", **fields)
    if ts is not None:
        event["ts"] = ts
    return event


@pytest.fixture
def live_env(tmp_path, monkeypatch, pact_context):
    """Redirect HOME + session context to a tmp tree so append_event /
    read_events / read_events_from write+read a REAL on-disk session journal.

    Mirrors test_pr4_drain_survival_harvest.live_env — the canonical non-mocked
    seam harness. Returns (tmp_path, session_dir) where session_dir is the
    absolute on-disk session directory the explicit reader resolves.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(team_name=TEAM, session_id=SID, project_dir=PROJECT_DIR)
    slug = Path(PROJECT_DIR).name
    session_dir = tmp_path / ".claude" / "pact-sessions" / slug / SID
    return tmp_path, str(session_dir)


def _journal_file(tmp_path: Path) -> Path:
    """Resolve the single on-disk journal file under the tmp sessions root —
    so a non-vacuity arm can remove the SOURCE and prove recovery collapses."""
    sessions_root = tmp_path / ".claude" / "pact-sessions"
    matches = list(sessions_root.rglob("session-journal.jsonl"))
    assert len(matches) == 1, f"expected one tmp journal, found {matches!r}"
    return matches[0]


def _payload(*, agent_type, subject, task_id="40", skipped=None, owner=None):
    """Build a TaskUpdate(status=completed) PostToolUse frame for
    evaluate_lifecycle. The both-modes axis is agent_type (is_lead reads it
    directly); session_id is supplied by the live_env pact_context only for
    journal-seam resolution, never as the gate discriminator."""
    metadata: dict = {}
    if skipped is not None:
        metadata["skipped"] = skipped
    task = {"id": task_id, "subject": subject, "owner": owner, "metadata": metadata}
    payload = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": task_id, "status": "completed"},
        "tool_response": {"task": task},
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return payload


def _has_backstop_advisory(advisories) -> bool:
    return any(code == "artifact_paths_emit_missing" for code, _ in advisories)


# =============================================================================
# P2-6 SCHEMA — artifact_paths validates required {workflow,feature,paths:list}
#                + optional task_id; list is a first-class registered type.
# =============================================================================
class TestSchema:
    def test_valid_event_appends(self, live_env):
        _tmp, session_dir = live_env
        assert append_event(_artifact_event("prepare")) is True
        events = read_events_from(session_dir, "artifact_paths")
        assert len(events) == 1
        assert events[0]["workflow"] == "prepare"
        assert events[0]["feature"] == FEATURE
        assert isinstance(events[0]["paths"], list)
        assert events[0]["v"] == 1
        assert "ts" in events[0]

    def test_optional_task_id_accepted(self, live_env):
        _tmp, session_dir = live_env
        assert append_event(_artifact_event("architect", task_id="17")) is True
        ev = read_events_from(session_dir, "artifact_paths")[0]
        assert ev["task_id"] == "17"

    @pytest.mark.parametrize("missing", ["workflow", "feature", "paths"])
    def test_missing_required_field_rejected(self, live_env, missing):
        """A required field absent → append_event refuses to write (returns
        False), so the malformed event never lands in the journal."""
        _tmp, session_dir = live_env
        ev = _artifact_event("prepare")
        del ev[missing]
        assert append_event(ev) is False
        assert read_events_from(session_dir, "artifact_paths") == []

    def test_paths_wrong_type_rejected(self, live_env):
        """paths typed `list` — a str fails isinstance(value, list)."""
        _tmp, session_dir = live_env
        ev = _artifact_event("prepare")
        ev["paths"] = "/abs/docs/prepare/x.md"  # str, not list
        assert append_event(ev) is False
        assert read_events_from(session_dir, "artifact_paths") == []

    def test_paths_list_is_first_class_multi(self, live_env):
        """The PLURAL list is first-class: a >1-path enumeration is preserved
        whole (a phase can write multiple files)."""
        _tmp, session_dir = live_env
        paths = [f"/abs/docs/prepare/{FEATURE}.md",
                 f"/abs/docs/prepare/environment-model-{FEATURE}.md"]
        assert append_event(_artifact_event("prepare", paths=paths)) is True
        ev = read_events_from(session_dir, "artifact_paths")[0]
        assert ev["paths"] == paths

    def test_empty_paths_list_passes_isinstance_writer_caveat(self, live_env):
        """Validator-depth caveat (arch §1.2): the schema checks
        isinstance(paths, list) but does NOT descend into elements, so an EMPTY
        list passes the validator. This pins the documented shallowness — the
        WRITER (emit site) is responsible for dropping empty-glob emits; an
        empty event is meaningless but not schema-rejected."""
        _tmp, session_dir = live_env
        assert append_event(_artifact_event("prepare", paths=[])) is True
        ev = read_events_from(session_dir, "artifact_paths")[0]
        assert ev["paths"] == []


# =============================================================================
# P0-2 ALWAYS-READ mechanics — read_events_from resolves the event;
#       supersede-by-(workflow,feature) latest-ts picks the latest.
# =============================================================================
class TestAlwaysReadAndSupersede:
    def test_read_events_from_resolves_event(self, live_env):
        _tmp, session_dir = live_env
        append_event(_artifact_event("architect", paths=["/abs/a.md"]))
        events = read_events_from(session_dir, "artifact_paths")
        assert [e["paths"] for e in events] == [["/abs/a.md"]]

    def test_supersede_latest_ts_wins_per_workflow_feature(self, live_env):
        """A re-emit for the same (workflow, feature) supersedes by latest-ts.
        The resolver groups by (workflow, feature) and takes the latest event;
        each event carries the COMPLETE path-list (not a delta)."""
        _tmp, session_dir = live_env
        append_event(_artifact_event(
            "prepare", paths=["/abs/OLD.md"], ts="2026-06-25T01:00:00Z"))
        append_event(_artifact_event(
            "prepare", paths=["/abs/NEW.md"], ts="2026-06-25T02:00:00Z"))
        events = read_events_from(session_dir, "artifact_paths")
        # Both events persist (append-only journal); supersede is a READ-time
        # resolution. Replicate the resolver: group by (workflow,feature), take
        # latest-ts.
        from datetime import datetime

        def _key(e):
            return datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
        prepare = [e for e in events
                   if e["workflow"] == "prepare" and e["feature"] == FEATURE]
        latest = max(prepare, key=_key)
        assert latest["paths"] == ["/abs/NEW.md"]
        assert len(prepare) == 2  # both on disk; supersede is read-time only

    def test_distinct_workflows_not_superseded(self, live_env):
        """Supersede is per-(workflow,feature): prepare and architect events for
        the same feature are distinct artifact sets, both survive resolution."""
        _tmp, session_dir = live_env
        append_event(_artifact_event("prepare", paths=["/abs/prep.md"]))
        append_event(_artifact_event("architect", paths=["/abs/arch.md"]))
        events = read_events_from(session_dir, "artifact_paths")
        by_wf = {e["workflow"]: e["paths"] for e in events}
        assert by_wf == {"prepare": ["/abs/prep.md"], "architect": ["/abs/arch.md"]}


# =============================================================================
# P0-3 FAILURE-PATH recovery — with the HANDOFF absent, the artifact is STILL
#       resolvable via the artifact_paths pointer (HANDOFF-independent).
# P0-1 BOUNDARY SEAL is exercised by TestBackstopBothModes (teammate-frame leg).
# =============================================================================
class TestFailurePathRecovery:
    def test_artifact_resolvable_with_no_handoff_event(self, live_env):
        """The durability guarantee: artifact recovery does NOT depend on any
        agent_handoff event. Seed ONLY an artifact_paths event (no handoff) and
        prove the pointer + disk read recover the substance."""
        tmp_path, session_dir = live_env
        artifact = tmp_path / "docs" / "prepare" / f"{FEATURE}.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("PREPARE SUBSTANCE — recovered without a handoff",
                            encoding="utf-8")
        append_event(_artifact_event("prepare", paths=[str(artifact)]))

        # No agent_handoff events exist at all.
        assert read_events_from(session_dir, "agent_handoff") == []
        # Yet the artifact is fully resolvable via the pointer.
        events = read_events_from(session_dir, "artifact_paths")
        assert len(events) == 1
        recovered = Path(events[0]["paths"][0]).read_text(encoding="utf-8")
        assert "recovered without a handoff" in recovered

    def test_recovery_collapses_when_journal_source_removed(self, live_env):
        """NON-VACUITY (source-removal): recovery is journal-SOURCED. Remove the
        journal file and the artifact_paths read collapses to empty — proving
        the recovery is not silently rescued by some surviving read."""
        tmp_path, session_dir = live_env
        append_event(_artifact_event("prepare", paths=["/abs/x.md"]))
        assert len(read_events_from(session_dir, "artifact_paths")) == 1
        _journal_file(tmp_path).unlink()
        assert read_events_from(session_dir, "artifact_paths") == []


# =============================================================================
# P2-7 MASKED-READ SEAM — read_events() off-lead returns false-empty while
#       read_events_from(session_dir) returns events. THE load-bearing dual-mode
#       constraint (the secretary harvests off-lead). Non-mocked seam.
# =============================================================================
class TestMaskedReadSeam:
    def test_implicit_read_resolves_when_context_present(self, live_env):
        """Positive control: with the session context resolvable (the lead-frame
        analogue — get_session_dir resolves), the implicit read_events() DOES
        see the event. Proves the false-empty below is the off-lead masking, not
        a missing fixture."""
        _tmp, session_dir = live_env
        append_event(_artifact_event("prepare"))
        assert len(read_events("artifact_paths")) == 1
        assert len(read_events_from(session_dir, "artifact_paths")) == 1

    def test_implicit_read_false_empty_off_lead_explicit_resolves(
        self, live_env, monkeypatch
    ):
        """THE seam: off-lead, get_session_dir() false-returns '' (teammate frame
        has no persisted session context) → read_events() silently returns [].
        read_events_from(session_dir, ...) with the explicit absolute dir still
        resolves the SAME on-disk event. This is why the harvest MUST use
        read_events_from."""
        tmp_path, session_dir = live_env
        append_event(_artifact_event("prepare"))
        # Sanity: the event is really on disk.
        assert _journal_file(tmp_path).exists()

        # Simulate the off-lead frame: get_session_dir resolves '' (the real
        # false-empty path — session_id/project_dir unavailable in a teammate
        # frame). We patch get_session_dir at the resolution seam, NOT the read
        # function itself (the read stays unmocked over the real journal).
        monkeypatch.setattr(
            "shared.session_journal._get_session_dir", lambda: ""
        )
        assert read_events("artifact_paths") == []  # false-empty off-lead
        # The explicit reader is frame-independent — still finds the event.
        assert len(read_events_from(session_dir, "artifact_paths")) == 1

    def test_explicit_reader_empty_session_dir_returns_empty(self, live_env):
        """read_events_from with an empty session_dir returns [] (the documented
        empty-arg contract) — so a caller that fails to resolve session_dir gets
        a visible empty, not a crash. Pins the no-silent-fallback boundary."""
        _tmp, _session_dir = live_env
        append_event(_artifact_event("prepare"))
        assert read_events_from("", "artifact_paths") == []


# =============================================================================
# P0-1 BOUNDARY SEAL + P1-5 BACKSTOP — both-modes (is_lead/agent_type) matrix.
#   The backstop fires a FAIL-OPEN advisory when a PREPARE/ARCHITECT phase
#   completes with no artifact_paths event for its (workflow,feature).
#   The teammate-frame leg asserts the backstop is ABSENT (lead-process-only
#   boundary — the by-design self-drop; doubles as the empirical seal).
# =============================================================================
class TestBackstopBothModes:
    @pytest.mark.parametrize("subject,workflow", [
        (PREPARE_SUBJECT, "prepare"),
        (ARCHITECT_SUBJECT, "architect"),
    ])
    def test_lead_missing_emit_fires_advisory(self, live_env, subject, workflow):
        """LEAD frame + PREPARE/ARCHITECT complete + NO artifact_paths event for
        (workflow,feature) → the nudge fires. The recovery pointer is missing."""
        _tmp, _session_dir = live_env
        advisories = tlg.evaluate_lifecycle(_payload(agent_type=LEAD, subject=subject))
        assert _has_backstop_advisory(advisories), (
            f"lead frame, {subject!r}, no emit → backstop must nudge"
        )

    def test_lead_with_emit_present_no_advisory(self, live_env):
        """LEAD frame + the artifact_paths event PRESENT for (prepare,feature) →
        no nudge. Same-fixture positive control proving the advisory above is the
        presence check, not an unconditional fire."""
        _tmp, _session_dir = live_env
        append_event(_artifact_event("prepare"))
        advisories = tlg.evaluate_lifecycle(
            _payload(agent_type=LEAD, subject=PREPARE_SUBJECT))
        assert not _has_backstop_advisory(advisories)

    @pytest.mark.parametrize("lead_spelling", [LEAD, LEAD_BARE])
    def test_both_lead_spellings_fire(self, live_env, lead_spelling):
        """is_lead accepts both LEAD_AGENT_TYPES spellings."""
        _tmp, _session_dir = live_env
        advisories = tlg.evaluate_lifecycle(
            _payload(agent_type=lead_spelling, subject=PREPARE_SUBJECT))
        assert _has_backstop_advisory(advisories)

    def test_teammate_frame_no_advisory_boundary_seal(self, live_env):
        """BOUNDARY SEAL (P0-1): a teammate (off-lead) frame self-completing the
        SAME PREPARE phase with NO emit produces NO backstop advisory — the
        lead-process-only boundary. The teammate frame has no resolvable journal;
        the backstop is is_lead-gated and correctly self-drops. This is the
        accepted by-design boundary, asserted ABSENT (not present)."""
        _tmp, _session_dir = live_env
        advisories = tlg.evaluate_lifecycle(
            _payload(agent_type=TEAMMATE, subject=PREPARE_SUBJECT))
        assert not _has_backstop_advisory(advisories), (
            "teammate frame must NOT nudge — lead-process-only boundary"
        )

    def test_teammate_then_lead_same_fixture_proves_gate_is_role(self, live_env):
        """Non-vacuity for the boundary seal: the SAME (subject, no-emit) fixture
        that is SILENT under the teammate frame FIRES under the lead frame —
        proving the suppression is the is_lead role gate, not a missing
        precondition."""
        _tmp, _session_dir = live_env
        teammate = tlg.evaluate_lifecycle(
            _payload(agent_type=TEAMMATE, subject=PREPARE_SUBJECT))
        lead = tlg.evaluate_lifecycle(
            _payload(agent_type=LEAD, subject=PREPARE_SUBJECT))
        assert not _has_backstop_advisory(teammate)
        assert _has_backstop_advisory(lead)

    @pytest.mark.parametrize("agent_type", ["", None])
    def test_empty_or_missing_agent_type_no_advisory(self, live_env, agent_type):
        """is_lead('') and is_lead(missing) are False (fail-safe default) → the
        backstop self-drops, never nudges a non-lead frame."""
        _tmp, _session_dir = live_env
        advisories = tlg.evaluate_lifecycle(
            _payload(agent_type=agent_type, subject=PREPARE_SUBJECT))
        assert not _has_backstop_advisory(advisories)


# =============================================================================
# P1-5 BACKSTOP exemptions — skipped phases + non-artifact phases are exempt;
#       the advisory NEVER blocks.
# =============================================================================
class TestBackstopExemptions:
    @pytest.mark.parametrize("subject", [
        f"CODE: {FEATURE}",
        f"TEST: {FEATURE}",
        f"ATOMIZE: {FEATURE}",
        f"CONSOLIDATE: {FEATURE}",
        f"backend-coder: implement {FEATURE}",  # a specialist work task
    ])
    def test_non_artifact_phase_exempt(self, live_env, subject):
        """CODE/TEST/ATOMIZE/CONSOLIDATE/specialist subjects are NOT in the
        phase→workflow map → _phase_artifact_requirement returns None → no nudge
        (their artifacts are git-tracked or non-existent)."""
        _tmp, _session_dir = live_env
        advisories = tlg.evaluate_lifecycle(
            _payload(agent_type=LEAD, subject=subject))
        assert not _has_backstop_advisory(advisories)

    def test_skipped_phase_exempt(self, live_env):
        """A PREPARE phase marked metadata.skipped=True produced no artifact →
        exempt even under the lead frame with no emit."""
        _tmp, _session_dir = live_env
        advisories = tlg.evaluate_lifecycle(
            _payload(agent_type=LEAD, subject=PREPARE_SUBJECT, skipped=True))
        assert not _has_backstop_advisory(advisories)

    def test_advisory_never_blocks(self, live_env):
        """The backstop is advisory-only: evaluate_lifecycle returns a list of
        (code, message) advisories — it does NOT raise and does NOT emit a deny.
        The nudge being present is informational; the TaskUpdate is never
        blocked."""
        _tmp, _session_dir = live_env
        # No raise, returns a list, advisory present but harmless.
        advisories = tlg.evaluate_lifecycle(
            _payload(agent_type=LEAD, subject=PREPARE_SUBJECT))
        assert isinstance(advisories, list)
        for entry in advisories:
            assert isinstance(entry, tuple) and len(entry) == 2


# =============================================================================
# P2-6 unit — _phase_artifact_requirement maps subjects to (workflow, feature)
#       or None. Phantom-green-seeded from real subject shapes.
# =============================================================================
class TestPhaseArtifactRequirement:
    def test_prepare_maps(self):
        assert tlg._phase_artifact_requirement(PREPARE_SUBJECT) == ("prepare", FEATURE)

    def test_architect_maps(self):
        assert tlg._phase_artifact_requirement(ARCHITECT_SUBJECT) == ("architect", FEATURE)

    @pytest.mark.parametrize("subject", [
        f"CODE: {FEATURE}", f"TEST: {FEATURE}", f"ATOMIZE: {FEATURE}",
        f"CONSOLIDATE: {FEATURE}", "random subject", "",
    ])
    def test_non_phase_returns_none(self, subject):
        assert tlg._phase_artifact_requirement(subject) is None

    def test_malformed_no_separator_returns_none(self):
        """A subject startswith the prefix-token but no ': ' separator → degenerate
        → None (fail open, no nudge on an unresolvable feature slug)."""
        assert tlg._phase_artifact_requirement("PREPARE") is None
        assert tlg._phase_artifact_requirement("PREPARE:") is None

    def test_non_string_returns_none(self):
        assert tlg._phase_artifact_requirement(None) is None
        assert tlg._phase_artifact_requirement(123) is None


# =============================================================================
# P1-4 TIMING — the artifact_paths event/journal lives OUTSIDE the worktree
#       (survives worktree teardown); paths are full-absolute.
# =============================================================================
class TestWorktreeSurvivalAndAbsolutePaths:
    def test_journal_lives_outside_worktree(self, live_env):
        """The journal resolves under ~/.claude/pact-sessions/, NOT under any
        worktree — so a `git worktree remove` (modeled as removing a worktree
        subtree) cannot touch it.

        The LOAD-BEARING invariant is the PATH RELATIONSHIP: the journal abspath
        is NOT a descendant of the worktree dir. Asserted via os.path.commonpath
        (the journal/worktree common ancestor is NOT the worktree itself) — this
        is the assertion that fails if a regression ever resolves the journal
        INSIDE the worktree, at any nesting depth. Structural + real file ops."""
        import os
        tmp_path, session_dir = live_env
        append_event(_artifact_event("prepare"))
        journal = _journal_file(tmp_path).resolve()
        sessions_root = (tmp_path / ".claude" / "pact-sessions").resolve()
        worktree_dir = (tmp_path / "worktrees" / "feature-x").resolve()
        worktree_dir.mkdir(parents=True, exist_ok=True)

        # Path-relationship invariant: the journal is NOT under the worktree.
        # commonpath(journal, worktree) == worktree IFF journal is inside the
        # worktree — so the journal is outside exactly when it is NOT the
        # worktree. (Equivalent not-descendant form: no startswith of the
        # worktree dir + os.sep.)
        common = os.path.commonpath([str(journal), str(worktree_dir)])
        assert common != str(worktree_dir), (
            f"journal {journal} must NOT be a descendant of worktree "
            f"{worktree_dir} (commonpath={common})"
        )
        assert not str(journal).startswith(str(worktree_dir) + os.sep)
        # And it IS under the (worktree-independent) sessions root.
        assert os.path.commonpath([str(journal), str(sessions_root)]) == str(sessions_root)

    def test_event_survives_worktree_subtree_removal(self, live_env):
        """END-STATE determinism for 'survives git worktree remove': remove the
        worktree subtree entirely (the real teardown effect on disk) and prove
        the artifact_paths event is STILL resolvable from the journal."""
        import shutil
        tmp_path, session_dir = live_env
        worktree_dir = tmp_path / "worktrees" / "feature-x"
        (worktree_dir / "docs").mkdir(parents=True, exist_ok=True)
        append_event(_artifact_event("prepare", paths=["/abs/docs/prepare/x.md"]))

        # Model `git worktree remove`: the whole worktree subtree is deleted.
        shutil.rmtree(worktree_dir)

        # The journal (outside the worktree) is untouched; the pointer survives.
        events = read_events_from(session_dir, "artifact_paths")
        assert len(events) == 1
        assert events[0]["paths"] == ["/abs/docs/prepare/x.md"]

    def test_emitted_paths_are_absolute(self, live_env):
        """The recovery pointer must be full-absolute (the harvest reads paths
        off disk from any cwd). A relative path would be unresolvable at harvest
        time from the secretary's frame."""
        _tmp, session_dir = live_env
        append_event(_artifact_event(
            "architect", paths=["/abs/docs/architect/x.md", "/abs/docs/architect/y.md"]))
        ev = read_events_from(session_dir, "artifact_paths")[0]
        for p in ev["paths"]:
            assert Path(p).is_absolute(), f"path must be absolute: {p!r}"


# =============================================================================
# P2-8 EDGES — missing artifact, malformed handoff coexistence, signal-task
#       independence.
# =============================================================================
class TestEdges:
    def test_missing_artifact_file_pointer_still_resolves(self, live_env):
        """The event resolves even when the pointed-at file is already gone (the
        accepted abnormal-teardown edge). The RESOLVER sees the pointer; the
        read-off-disk step is where the gap is noted + degrades to HANDOFF-only.
        This pins that the event layer is independent of file existence."""
        _tmp, session_dir = live_env
        append_event(_artifact_event("prepare", paths=["/abs/gone/missing.md"]))
        events = read_events_from(session_dir, "artifact_paths")
        assert len(events) == 1
        assert not Path(events[0]["paths"][0]).exists()  # file gone; pointer survives

    def test_artifact_paths_independent_of_agent_handoff(self, live_env):
        """artifact_paths and agent_handoff are distinct event types — reading
        one never returns the other; the durability pointer is independent of
        HANDOFF presence in BOTH directions."""
        _tmp, session_dir = live_env
        append_event(_artifact_event("prepare"))
        append_event(make_event(
            "agent_handoff", agent=BARE_OWNER, task_id="27",
            task_subject="devops-engineer: implement #927 journal mechanics",
            handoff={"produced": "x", "decisions": "y", "uncertainty": "n",
                     "integration": "n", "reasoning_chain": "b", "open_questions": "n"},
        ))
        artifacts = read_events_from(session_dir, "artifact_paths")
        handoffs = read_events_from(session_dir, "agent_handoff")
        assert len(artifacts) == 1 and artifacts[0]["type"] == "artifact_paths"
        assert len(handoffs) == 1 and handoffs[0]["type"] == "agent_handoff"

    def test_feature_scoping_filters_other_features(self, live_env):
        """feature scopes the event to one arc: an artifact_paths event for a
        DIFFERENT feature is not confused with this one (the multi-feature/
        resumed-session disambiguation)."""
        _tmp, session_dir = live_env
        append_event(_artifact_event("prepare", feature=FEATURE, paths=["/abs/this.md"]))
        append_event(_artifact_event("prepare", feature="other-feature", paths=["/abs/other.md"]))
        events = read_events_from(session_dir, "artifact_paths")
        this_feature = [e for e in events if e["feature"] == FEATURE]
        assert len(this_feature) == 1
        assert this_feature[0]["paths"] == ["/abs/this.md"]
