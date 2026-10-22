"""Frame-gate arms for the six sites that moved from is_lead to
is_canonical_journal_frame.

WHY THIS FILE EXISTS. The substitution shipped with a green suite, and the
suite was green BEFORE the change and AFTER it. The arms in this repository
were insensitive to the substitution in the two directions, so the green
proved that nothing broke and proved nothing about the substitution.

🔴 WHY THE OLD ARMS COULD NOT SEE IT, MEASURED RATHER THAN ASSUMED.
``is_canonical_journal_frame`` returns True IMMEDIATELY when ``is_lead`` is
True, and only then falls to the topology leg. So in a lead frame that
carries a lead ``agent_type``, THE TWO PREDICATES GIVE THE SAME ANSWER and a
revert is invisible. The ONLY frame class that separates them is
``is_lead`` False AND ``is_canonical_journal_frame`` True, which needs the
topology leg to resolve.

🔴 THE TOPOLOGY LEG NEEDS TWO HALVES, AND ONE HALF IS A TRAP. It needs a
frame ``session_id`` AND a team config carrying a ``leadSessionId`` that
agrees with it. WITH ONE HALF THE PREDICATE ANSWERS False, WHICH IS THE SAME
ANSWER ``is_lead`` GIVES. So an arm built on one half is green with the
substitution and green without it, AND IT READS AS TESTED because it does
drive the site. That is an equality between two outcomes rather than a
defect in the arm: no arm confined to that frame class can discriminate,
however carefully it is written. ``test_four_point_control`` pins all four
points so a later editor cannot seed one half and believe it is covered.

🔴 THREE OBSERVABLE CLASSES, NOT ONE, AND NOT TWO. The sites do not share
one channel, and an arm that watches the wrong one reports a driven site as
silent.

  1. THE JOURNAL RECORD. Five of the six sites append a record. They do NOT
     all append it through one function object: ``dispatch_variety`` and
     ``teachback_ack`` go through ``append_event_checked`` (which calls
     ``session_journal.append_event``), the lead-side handoff emit calls the
     ``append_event`` name IMPORTED INTO task_lifecycle_gate, and the
     snapshot emits call the name imported into task_metadata_snapshot.
     A rig that patches ONE of those three names sees a subset of the sites
     and reports the rest as silent. THAT IS AN INSTRUMENT DEFECT AND IT
     READS AS A FINDING ABOUT THE CODE. This file mocks NONE of them: it
     redirects ``Path.home`` and reads the REAL on-disk journal, so all
     three routes land in one place by construction and no future emit can
     escape the instrument by changing which name it calls.
  2. THE ADVISORY. The artifact-paths site appends to the RETURN VALUE of
     ``evaluate_lifecycle`` and writes no journal record at all.
  3. THE CALL. A site can be REACHED, CALLED, and still write nothing,
     because the emitter dedups on an O_EXCL marker or drops an ineligible
     payload. An arm that reads only the downstream artifact collapses three
     states into one empty list: not reached, reached but gate-rejected, and
     called but deduplicated. ``emit_calls`` records that layer so the three
     stay separable.

SIX SITES, ALL COVERED HERE, cited by symbol because line numbers move:
``dispatch_variety`` (TaskCreate branch), the lead-side ``agent_handoff``
emit at the acceptance-commit, the ``artifact_paths_emit_missing`` advisory,
``teachback_ack`` (TaskUpdate-completed branch), the post-completion handoff
backstop, and the post-completion ``task_metadata_snapshot`` backstop.

HOW THE TWO BACKSTOPS ARE SEPARATED, because they share one frame class and
a shared arm would kill two mutants at once and name neither. The handoff
backstop needs an incoming ``metadata.handoff`` DICT. The snapshot backstop
excludes ``handoff`` from its payload and emits nothing when that is the
only key. So a handoff-ONLY write drives the handoff backstop alone, and a
write carrying only a non-handoff key drives the snapshot backstop alone.

R5 IS OUT OF SCOPE AND IS NAMED SO A READER DOES NOT EXPECT IT.
``_journal_lifecycle_decision`` writes a ``lifecycle_decision`` record with
NO frame gate, in EVERY frame. It is called from ``main()`` rather than from
``evaluate_lifecycle``, so it does not reach the arms below. The arm-D
controls assert silence FOR THE RECORD KIND OF THEIR OWN SITE anyway, never
journal silence in general, so a future move of that call cannot turn them
red for a defect this commit does not own.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import shared.pact_context as pc  # noqa: E402
import task_lifecycle_gate as tlg  # noqa: E402

HOOKS_DIR = Path(__file__).parent.parent / "hooks"

LEAD_AGENT_TYPE = "PACT:pact-orchestrator"
TEAMMATE_AGENT_TYPE = "pact-devops-engineer"
SESSION_ID = "sess-canonical-frame-0001"
OTHER_SESSION_ID = "sess-tmux-teammate-0002"
TEAM = "session-abcd1234"
PROJECT_DIR = "/test/project"

HANDOFF = {
    "produced": "the arms",
    "decisions": "the rig reads disk",
    "reasoning_chain": "one channel is blind",
    "uncertainty": "dedup",
    "integration": "staged only",
    "open_questions": "none",
}


def _journal_records(home: Path) -> list[dict]:
    """Read EVERY journal below the redirected home.

    Reading the file rather than a patched function is what makes this rig
    blind to nothing: the three import routes of ``append_event`` all end at
    one file, so a site cannot escape the instrument by calling a different
    name.
    """
    root = home / ".claude" / "pact-sessions"
    records: list[dict] = []
    for journal in root.rglob("*.jsonl"):
        for line in journal.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


@pytest.fixture
def frame_rig(tmp_path, monkeypatch, pact_context):
    """Seed the two halves of the topology leg and capture ALL THREE channels.

    Returns a callable. Call it with a frame dict and it returns a
    ``Drive`` with ``records``, ``advisories`` and ``emit_calls``.

    NON-MOCKED SEAM. ``Path.home`` is redirected and ``CLAUDE_CONFIG_DIR`` is
    removed, so the team config read, the on-disk task read, the marker
    claim and the journal write all resolve through their REAL resolvers
    against a temp tree. No resolver is monkeypatched, so a regression in
    any of them turns these arms red rather than leaving them green against
    a stub.

    The team config is the half that the ordinary ``pact_context`` fixture
    does NOT write: that fixture writes the CONTEXT file, and the
    ``leadSessionId`` lives in the TEAM CONFIG. The two are different files,
    and an arm that seeds only the first carries one half.
    """
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pact_context(
        team_name=TEAM, session_id=SESSION_ID, project_dir=PROJECT_DIR
    )
    teams_dir = tmp_path / ".claude" / "teams" / TEAM
    teams_dir.mkdir(parents=True, exist_ok=True)
    teams_dir.joinpath("config.json").write_text(
        json.dumps({"leadSessionId": SESSION_ID}), encoding="utf-8"
    )
    tasks_dir = tmp_path / ".claude" / "tasks" / TEAM
    tasks_dir.mkdir(parents=True, exist_ok=True)

    emit_calls: list[str] = []
    real_handoff = tlg._emit_lead_side_agent_handoff
    real_snapshot = tlg.emit_task_metadata_snapshot
    real_per_write = tlg._emit_per_write_snapshot

    def spy_handoff(*args, **kwargs):
        emit_calls.append("_emit_lead_side_agent_handoff")
        return real_handoff(*args, **kwargs)

    def spy_snapshot(*args, **kwargs):
        emit_calls.append("emit_task_metadata_snapshot")
        return real_snapshot(*args, **kwargs)

    def spy_per_write(*args, **kwargs):
        # The OPEN-task per-write mirror is the only caller of this helper,
        # so its presence NAMES the site. The helper then reaches
        # emit_task_metadata_snapshot through the module global above, so
        # one mirror fire records TWO entries and a backstop fire records
        # ONE. That difference is what separates the two sites.
        emit_calls.append("_emit_per_write_snapshot")
        return real_per_write(*args, **kwargs)

    monkeypatch.setattr(tlg, "_emit_lead_side_agent_handoff", spy_handoff)
    monkeypatch.setattr(tlg, "emit_task_metadata_snapshot", spy_snapshot)
    monkeypatch.setattr(tlg, "_emit_per_write_snapshot", spy_per_write)

    class Drive:
        def __init__(self, records, advisories, calls):
            self.records = records
            self.advisories = advisories
            self.emit_calls = calls

        def typed(self, kind):
            return [r for r in self.records if r.get("type") == kind]

    def drive(frame, disk_task=None):
        if disk_task is not None:
            tasks_dir.joinpath(f"{disk_task['id']}.json").write_text(
                json.dumps(disk_task), encoding="utf-8"
            )
        before = len(emit_calls)
        advisories = tlg.evaluate_lifecycle(frame)
        return Drive(
            _journal_records(tmp_path),
            [name for name, _ in advisories],
            list(emit_calls[before:]),
        )

    drive.home = tmp_path
    return drive


def _arm_b(**extra):
    """Arm B: a lead launched with NO --agent flag. `agent_type` is ABSENT,
    so `is_lead` is False, and the topology leg admits the frame."""
    return {"session_id": SESSION_ID, **extra}


def _arm_c(**extra):
    """Arm C: an in-process teammate that shares the lead session."""
    return {
        "agent_type": TEAMMATE_AGENT_TYPE,
        "session_id": SESSION_ID,
        **extra,
    }


def _arm_d(**extra):
    """Arm D: a tmux teammate. Its session_id DISAGREES with the config."""
    return {
        "agent_type": TEAMMATE_AGENT_TYPE,
        "session_id": OTHER_SESSION_ID,
        **extra,
    }


def _arm_a(**extra):
    """Arm A: a lead carrying a lead `agent_type`. `is_lead` short-circuits."""
    return {"agent_type": LEAD_AGENT_TYPE, **extra}


def _completed_task(task_id="7", owner="devops", subject="devops: implement",
                    metadata=None):
    return {
        "id": task_id,
        "owner": owner,
        "subject": subject,
        "status": "completed",
        "metadata": metadata if metadata is not None else {},
    }


# =============================================================================
# The control. Read this before changing any arm below it.
# =============================================================================
class TestFramePredicateControl:
    def test_four_point_control(self, tmp_path, monkeypatch):
        """The topology leg needs BOTH halves, and one half is not a weaker
        version of two: it gives the SAME verdict as the reverted predicate.

        Four points, and a three-point control would miss the fourth. The
        fourth is the two halves present and DISAGREEING, which is what a
        stale team config produces in the field.
        """
        teams_dir = tmp_path / "teams" / TEAM
        teams_dir.mkdir(parents=True, exist_ok=True)
        cfg = teams_dir / "config.json"
        cfg.write_text(json.dumps({"leadSessionId": SESSION_ID}), encoding="utf-8")
        monkeypatch.setattr(pc, "get_claude_config_dir", lambda: tmp_path)
        # The topology leg reads the RAW persisted team (the store the emit
        # legs read). The recorded session_id is empty so the lead-context
        # leg stays out of this config-half control.
        monkeypatch.setattr(pc, "get_pact_context", lambda: {
            "team_name": TEAM, "session_id": "", "project_dir": "",
            "plugin_root": "", "started_at": "",
        })

        both = {"session_id": SESSION_ID}
        assert pc.is_canonical_journal_frame(both) is True, (
            "POINT 1: the two halves seeded and in agreement must ADMIT."
        )
        assert pc.is_lead(both) is False, (
            "POINT 1 NON-VACUITY: is_lead must answer False here, or this "
            "frame does not separate the two predicates and no arm built on "
            "it can detect a revert."
        )

        disagree = {"session_id": OTHER_SESSION_ID}
        assert pc.is_canonical_journal_frame(disagree) is False, (
            "POINT 2: the two halves present and DISAGREEING must EXCLUDE. "
            "This is the tmux teammate, and it is the state a stale team "
            "config produces."
        )

        cfg.unlink()
        assert pc.is_canonical_journal_frame(both) is False, (
            "POINT 3, THE TRAP: with the FRAME HALF ALONE the predicate "
            "answers False, which is the SAME answer is_lead gives. An arm "
            "built on this half is green with the substitution AND green "
            "without it, and it reads as tested because it drives the site."
        )

        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"leadSessionId": SESSION_ID}), encoding="utf-8")
        assert pc.is_canonical_journal_frame({}) is False, (
            "POINT 4: with the CONFIG HALF ALONE and no session_id the "
            "predicate answers False. The second half of the same trap."
        )

    def test_arm_a_cannot_discriminate_and_that_is_the_point(self, tmp_path,
                                                             monkeypatch):
        """A NEGATIVE CONTROL ON MY OWN REASONING, not on the code.

        In a lead frame carrying a lead `agent_type`, the two predicates
        AGREE, so a revert is invisible. This arm asserts that agreement.
        IT DETECTS NO DEFECT BY DESIGN. Its value is conditional: if it ever
        fails, the short-circuit model behind every other arm in this file is
        incorrect, and those arms must be re-derived rather than repaired.
        """
        monkeypatch.setattr(pc, "get_claude_config_dir", lambda: tmp_path)
        monkeypatch.setattr(pc, "get_team_name", lambda: TEAM)
        frame = _arm_a()
        assert pc.is_lead(frame) is True
        assert pc.is_canonical_journal_frame(frame) is True
        assert pc.is_lead(frame) == pc.is_canonical_journal_frame(frame), (
            "THE TWO PREDICATES MUST AGREE AT ARM A. If they disagree, the "
            "short-circuit is gone and every arm in this file needs a "
            "re-derived expectation."
        )

    def test_rig_reads_a_real_journal_file(self, frame_rig):
        """NON-VACUITY OF THE INSTRUMENT ITSELF.

        Every arm below reads records off disk. If the redirect were
        incomplete, the emits would land in the operator's REAL journal and
        this rig would report an empty list for every site, which reads as
        six correct gate rejections. This arm proves the reader is live and
        that the file it reads is inside the temp tree.
        """
        drive = frame_rig(_arm_b(**TestDispatchVarietySite.FRAME_EXTRA))
        assert drive.records, (
            "THE RIG READ NOTHING AT ALL. Do not read a downstream empty "
            "list as a gate rejection until this arm is green: an incomplete "
            "home redirect produces the same empty list at every site."
        )
        journals = list(
            (frame_rig.home / ".claude" / "pact-sessions").rglob("*.jsonl")
        )
        assert journals, "no journal file was written below the temp home"


# =============================================================================
# Site arms. Each drives a real site in a frame that separates the predicates.
# =============================================================================
class TestDispatchVarietySite:
    """`dispatch_variety`, in the TaskCreate branch."""

    FRAME_EXTRA = {
        "tool_name": "TaskCreate",
        "tool_input": {
            "subject": "devops: implement",
            "metadata": {"variety": {"total": 9}},
        },
        "tool_response": {"task": {"id": "42"}},
    }

    def test_arm_b_lead_without_agent_flag_emits(self, frame_rig):
        """A lead with no --agent flag writes the canonical journal, so the
        emit must run. A revert to is_lead makes this arm RED."""
        drive = frame_rig(_arm_b(**self.FRAME_EXTRA))
        assert len(drive.typed("dispatch_variety")) == 1, (
            "A lead launched with no --agent flag DOES write the canonical "
            "journal. If this arm is red, the gate answered on ROLE rather "
            "than on which journal the frame writes, and the record is lost."
        )

    def test_arm_c_in_process_teammate_emits(self, frame_rig):
        """The in-process teammate shares the lead session, so its writes
        land in the canonical journal and the emit must run."""
        drive = frame_rig(_arm_c(**self.FRAME_EXTRA))
        assert len(drive.typed("dispatch_variety")) == 1, (
            "An in-process teammate shares the lead session, so its emit "
            "lands in the canonical journal and must not be suppressed."
        )

    def test_arm_d_tmux_teammate_stays_silent(self, frame_rig):
        """Arm D control, SCOPED TO THIS RECORD KIND. It does NOT assert
        journal silence in general: R5 writes an ungated record in every
        frame, and a general assertion would be red at the base for a
        defect this commit does not own."""
        drive = frame_rig(_arm_d(**self.FRAME_EXTRA))
        assert drive.typed("dispatch_variety") == [], (
            "A tmux teammate writes a DIFFERENT journal. Emitting from it "
            "silos the record and poisons the shared marker namespace."
        )


class TestLeadSideAgentHandoffSite:
    """The lead-side `agent_handoff` emit at the acceptance-commit.

    THIS SITE WAS REPORTED AS NOT DRIVEN BY THE PRIOR PHASE. It is driven,
    and it needs no on-disk task at all: the completion branch takes its
    task from ``tool_response.task``. The prior silence was an instrument
    defect, because this emit calls the ``append_event`` name imported into
    task_lifecycle_gate and the prior rig patched the session_journal name.

    THE SAME GATE ALSO GUARDS the paired completion-time
    ``task_metadata_snapshot`` emit, so a revert of this ONE line suppresses
    two record kinds. The arms below assert the handoff record, which is
    enough to redden the revert and keeps the failure message pointed at one
    cause.
    """

    FRAME_EXTRA = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "7", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "7",
                "owner": "devops",
                "subject": "devops: implement the thing",
                "status": "completed",
                "metadata": {"handoff": HANDOFF},
            }
        },
    }

    def test_arm_b_lead_without_agent_flag_emits(self, frame_rig):
        drive = frame_rig(_arm_b(**self.FRAME_EXTRA))
        assert len(drive.typed("agent_handoff")) == 1, (
            "A lead launched with no --agent flag DOES write the canonical "
            "journal, so its acceptance-commit must emit the handoff. If "
            "this arm is red, the durable HANDOFF record is lost for every "
            "lead that runs without an --agent flag."
        )

    def test_arm_c_in_process_teammate_emits(self, frame_rig):
        drive = frame_rig(_arm_c(**self.FRAME_EXTRA))
        assert len(drive.typed("agent_handoff")) == 1

    def test_arm_d_tmux_teammate_stays_silent(self, frame_rig):
        """T7 control for this site, scoped to this record kind."""
        drive = frame_rig(_arm_d(**self.FRAME_EXTRA))
        assert drive.typed("agent_handoff") == [], (
            "A tmux teammate must not emit here: the event would silo into "
            "a journal the team does not read, and the marker claim would "
            "suppress a later canonical emit of the same handoff."
        )

    def test_arm_d_is_a_gate_rejection_and_not_a_dedup_no_op(self, frame_rig):
        """THE THIRD OBSERVABLE, and the reason it is in the rig.

        An empty record list has three causes: the site was not reached, the
        gate rejected the frame, or the emitter was CALLED and deduplicated.
        The arm above cannot separate them, so a dedup defect would read as
        a working gate. This arm pins the cause: at arm D the emitter is
        NEVER CALLED, so the silence is the gate.
        """
        drive = frame_rig(_arm_d(**self.FRAME_EXTRA))
        assert "_emit_lead_side_agent_handoff" not in drive.emit_calls, (
            "The emitter was CALLED in a tmux frame. The record list is "
            "empty for the wrong cause: the gate admitted the frame and the "
            "marker or the payload suppressed the write."
        )
        lead = frame_rig(_arm_b(**self.FRAME_EXTRA))
        assert "_emit_lead_side_agent_handoff" in lead.emit_calls, (
            "NON-VACUITY: the spy must record a call in the admitting frame, "
            "or its absence at arm D measures nothing."
        )


class TestArtifactPathsAdvisorySite:
    """The `artifact_paths_emit_missing` advisory.

    🔴 THIS SITE WRITES NO JOURNAL RECORD. It appends an advisory to the
    RETURN VALUE. An arm that watches the journal alone is blind here and
    reports the site as silent, which reads as a passing control. The
    fixture returns all three channels for that cause. Do not narrow these
    assertions to the journal channel.
    """

    FRAME_EXTRA = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "7", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "7",
                "owner": "architect",
                "subject": "ARCHITECT: the feature",
                "metadata": {},
            }
        },
    }

    def test_arm_b_lead_without_agent_flag_raises_the_advisory(self, frame_rig):
        drive = frame_rig(_arm_b(**self.FRAME_EXTRA))
        assert "artifact_paths_emit_missing" in drive.advisories, (
            "The artifact-paths check must run in a canonical-journal frame. "
            "If it is absent, the gate skipped the check on ROLE, and the "
            "missing durability pointer goes unreported."
        )
        assert drive.typed("artifact_paths") == [], (
            "RECORDED DELIBERATELY: this site writes NO journal record of "
            "its own. The advisory channel is the observable, and an arm "
            "that watches the journal here is blind AND green."
        )

    def test_arm_c_in_process_teammate_raises_the_advisory(self, frame_rig):
        drive = frame_rig(_arm_c(**self.FRAME_EXTRA))
        assert "artifact_paths_emit_missing" in drive.advisories

    def test_arm_d_tmux_teammate_stays_silent(self, frame_rig):
        drive = frame_rig(_arm_d(**self.FRAME_EXTRA))
        assert "artifact_paths_emit_missing" not in drive.advisories, (
            "A tmux teammate must not raise this advisory: it reads a "
            "journal it does not share, so its answer is not evidence."
        )


class TestTeachbackAckSite:
    """`teachback_ack`, in the TaskUpdate-completed branch."""

    FRAME_EXTRA = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "7", "status": "completed"},
        "tool_response": {
            "task": {
                "id": "7",
                "owner": "teammate",
                "subject": "teammate: TEACHBACK for the thing",
                "metadata": {
                    "teachback_submit": {
                        "variety_acknowledgment": {
                            "rationale_articulates_this_dispatch": "yes"
                        }
                    }
                },
            }
        },
    }

    def test_arm_b_lead_without_agent_flag_emits(self, frame_rig):
        drive = frame_rig(_arm_b(**self.FRAME_EXTRA))
        assert len(drive.typed("teachback_ack")) == 1, (
            "The teachback acknowledgement must reach the canonical journal "
            "from a lead frame that carries no --agent flag."
        )

    def test_arm_c_in_process_teammate_emits(self, frame_rig):
        drive = frame_rig(_arm_c(**self.FRAME_EXTRA))
        assert len(drive.typed("teachback_ack")) == 1

    def test_arm_d_tmux_teammate_stays_silent(self, frame_rig):
        drive = frame_rig(_arm_d(**self.FRAME_EXTRA))
        assert drive.typed("teachback_ack") == []


class TestHandoffBackstopSite:
    """The post-completion handoff backstop.

    ITS FRAME CLASS IS THE OPPOSITE OF THE ACCEPTANCE-COMMIT ONE, and that
    is why a completion-shaped fixture cannot drive it. The whole block is
    gated on ``tool_input.status != "completed"``, so it fires on a
    METADATA-ONLY TaskUpdate that lands on a task the disk already records
    as completed. A fixture that sets status="completed" routes to the
    completion branch and skips this site entirely.

    THE INCOMING WRITE CARRIES ONLY ``handoff``. That is what isolates this
    site from its sibling: the snapshot backstop excludes ``handoff`` from
    its payload and emits nothing when it is the only key.
    """

    DISK_TASK = _completed_task(metadata={})
    FRAME_EXTRA = {
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": "7", "metadata": {"handoff": HANDOFF}},
        "tool_response": {"task": {"id": "7"}},
    }

    def test_arm_b_lead_without_agent_flag_emits(self, frame_rig):
        drive = frame_rig(_arm_b(**self.FRAME_EXTRA), disk_task=self.DISK_TASK)
        assert len(drive.typed("agent_handoff")) == 1, (
            "The backstop is the GUARANTEE leg for a handoff written after "
            "completion. A lead with no --agent flag writes the canonical "
            "journal, so suppressing it here loses the record with no "
            "second chance: the completion-time seam has already passed."
        )

    def test_arm_c_in_process_teammate_emits(self, frame_rig):
        drive = frame_rig(_arm_c(**self.FRAME_EXTRA), disk_task=self.DISK_TASK)
        assert len(drive.typed("agent_handoff")) == 1

    def test_arm_d_tmux_teammate_stays_silent(self, frame_rig):
        """T7 control for this site."""
        drive = frame_rig(_arm_d(**self.FRAME_EXTRA), disk_task=self.DISK_TASK)
        assert drive.typed("agent_handoff") == []

    def test_the_snapshot_sibling_stays_silent_on_a_handoff_only_write(
            self, frame_rig):
        """SEPARATION PIN, and it is what lets each backstop name its own
        mutant. If this arm ever fails, a handoff-only write also drives the
        snapshot backstop, the two sites share one observable again, and a
        revert of either one reddens both arm sets."""
        drive = frame_rig(_arm_b(**self.FRAME_EXTRA), disk_task=self.DISK_TASK)
        assert drive.typed("task_metadata_snapshot") == [], (
            "A handoff-ONLY write must not produce a snapshot record: "
            "handoff is excluded from the snapshot payload, and an empty "
            "payload is ineligible."
        )


class TestSnapshotBackstopSite:
    """The post-completion `task_metadata_snapshot` backstop.

    THE INCOMING WRITE CARRIES NO ``handoff``, which is what isolates this
    site from the handoff backstop: that sibling requires an incoming
    handoff dict and does not fire here.

    IT IS ALSO DISJOINT FROM THE OPEN-TASK PER-WRITE MIRROR, which is a
    SEVENTH gate site that this commit did not change. The two legs split on
    the same on-disk status read: the mirror fires when the disk task is NOT
    completed, and this backstop fires when it is. The fixture seeds a
    completed task, so a snapshot record here can only come from this site.
    """

    DISK_TASK = _completed_task(metadata={})
    FRAME_EXTRA = {
        "tool_name": "TaskUpdate",
        "tool_input": {
            "taskId": "7",
            "metadata": {"audit_summary": {"verdict": "GREEN"}},
        },
        "tool_response": {"task": {"id": "7"}},
    }

    def test_arm_b_lead_without_agent_flag_emits(self, frame_rig):
        drive = frame_rig(_arm_b(**self.FRAME_EXTRA), disk_task=self.DISK_TASK)
        assert len(drive.typed("task_metadata_snapshot")) == 1, (
            "A late metadata write on a completed task is observed by "
            "neither completion-time seam, so this backstop is its only "
            "durable mirror. A lead with no --agent flag must reach it."
        )

    def test_arm_c_in_process_teammate_emits(self, frame_rig):
        drive = frame_rig(_arm_c(**self.FRAME_EXTRA), disk_task=self.DISK_TASK)
        assert len(drive.typed("task_metadata_snapshot")) == 1

    def test_arm_d_tmux_teammate_stays_silent(self, frame_rig):
        """T7 control for this site."""
        drive = frame_rig(_arm_d(**self.FRAME_EXTRA), disk_task=self.DISK_TASK)
        assert drive.typed("task_metadata_snapshot") == []

    def test_the_handoff_sibling_stays_silent_without_an_incoming_handoff(
            self, frame_rig):
        """SEPARATION PIN, the mirror image of the one on the sibling."""
        drive = frame_rig(_arm_b(**self.FRAME_EXTRA), disk_task=self.DISK_TASK)
        assert drive.typed("agent_handoff") == [], (
            "A write with no incoming handoff must not drive the handoff "
            "backstop, or the two backstops share one observable and no arm "
            "can name which site a revert broke."
        )

    def test_the_two_snapshot_sites_are_disjoint_on_the_disk_status(
            self, frame_rig):
        """This backstop and the OPEN-task per-write mirror are a disjoint
        pair, split on one on-disk status read. The backstop fires when the
        disk task reads completed. The mirror fires when it does not.

        🔴 READ THIS BEFORE YOU SIMPLIFY THE ASSERTIONS BELOW. THE JOURNAL
        RECORD CANNOT ANSWER THIS QUESTION. The two sites write the SAME
        record kind, the record carries NO FIELD NAMING ITS SITE, and the
        content-key dedup collapses a second write to one record. MEASURED:
        the record count is 1 in all four cells of (open, completed) by
        (base, both-sites-firing). So a record-count assertion looks
        equivalent to the calls below and carries NO INFORMATION. That
        equality is invisible from this arm, and it is why this pin lived
        as a docstring until it was measured.

        THE CALL LAYER IS THE ONLY CHANNEL THAT SEPARATES THEM. The mirror
        is the only caller of `_emit_per_write_snapshot`, so its presence
        names the site, and it reaches the substrate through the module
        global, so one mirror fire records two entries.

        MEASURED KILL, and the numbers here are read from a run and not
        from the source: force the `task_a.get("status") == "completed"`
        conjunct of the backstop to True. THE RECORD COUNT STAYS AT 1. The
        substrate call count on the OPEN task moves from 1 to 2, and the
        call order shows the backstop firing before the mirror. The
        COMPLETED cell holds at 1 in the two worlds, which is what makes
        the moved cell attributable to that conjunct.
        """
        open_task = {
            "id": "7", "owner": "devops", "subject": "devops: implement",
            "status": "in_progress", "metadata": {},
        }
        opened = frame_rig(_arm_b(**self.FRAME_EXTRA), disk_task=open_task)
        assert "_emit_per_write_snapshot" in opened.emit_calls, (
            "THE MIRROR MUST OWN AN OPEN TASK. If this is absent the "
            "open-task write reached neither site, and the counts below "
            "measure an unreached surface rather than a disjoint pair."
        )
        assert opened.emit_calls.count("emit_task_metadata_snapshot") == 1, (
            "TWO DIRECTIONS REDDEN THIS ASSERTION AND THEY HAVE DIFFERENT "
            "CAUSES. READ THE COUNT BEFORE YOU READ THE DIFF. A COUNT OF 2 "
            "IS THE PRODUCTION FAULT: the backstop fired on an open task, "
            "its status conjunct does not hold the split at this time, and "
            "the two sites double-fire on one write. The journal hides "
            "that, because the content-key dedup keeps the record count at "
            "1. A COUNT OF 0 IS A HARNESS FAULT AND THE PRODUCTION CODE IS "
            "CORRECT: somebody removed the spy on "
            "emit_task_metadata_snapshot, or the mirror did not fire."
        )

        completed = frame_rig(
            _arm_b(**self.FRAME_EXTRA), disk_task=_completed_task(metadata={})
        )
        assert "_emit_per_write_snapshot" not in completed.emit_calls, (
            "THE MIRROR FIRED ON A COMPLETED TASK. The pair is disjoint in "
            "one direction only, which is not disjoint."
        )
        assert completed.emit_calls.count("emit_task_metadata_snapshot") == 1, (
            "POSITIVE CONTROL, and it is what proves the arm above reads a "
            "live surface. The completed cell must stay at one substrate "
            "call in the two worlds, so a mutation that moves the open "
            "cell alone is attributable to the backstop status conjunct."
        )


# =============================================================================
# T6 — the additive shape, RECORDED rather than treated as a defect.
# =============================================================================
class TestAdditiveShapeAtTheTwoAppendOnlySites:
    """`dispatch_variety` and `teachback_ack` are APPEND-ONLY. They carry no
    O_EXCL marker and no content-hash dedup, unlike `agent_handoff` and
    `task_metadata_snapshot`. So two frames give two records.

    🔴 THIS ARM RECORDS THAT SHAPE. IT DOES NOT FAIL ON IT. A duplicate row
    at these two kinds is not evidence of a defect this commit introduced,
    because duplicate rows are present at the LEAD frame today, before any
    widening of the frame set.

    THE MEASURED BASELINE, with its parameter stated adjacent to the number
    because a count with an unstated parameter cannot be compared.
      POPULATION: 2274 `session-journal.jsonl` files below
      `~/.claude/pact-sessions`, 60346 lines, holding 981 `dispatch_variety`
      and 849 `teachback_ack` records.
      COUNTING RULE: two records with the same `type` AND the same `task_id`
      in the SAME file, content compared with the `ts` field REMOVED.
      RESULT: 19 duplicate pairs. 11 of them inside 600 seconds. Of those
      11, eight differ by one `agent` key the second record carries, which
      is a schema change rather than a repeat. THREE are content-equal. So
      the duplicate rate is about 3 in 1830 records, near 0.16 percent.

    THE BOUND ON THAT NUMBER, and no ruling rests on the stronger claim: it
    shows duplicate rows are PRESENT at the lead frame. It does NOT show
    that a repeat PostToolUse frame is reachable, because the records carry
    no session identifier and no invocation identifier, so a second genuine
    tool call and a repeat frame give the same shape.
    """

    def test_two_frames_give_two_dispatch_variety_records(self, frame_rig):
        frame = _arm_b(**TestDispatchVarietySite.FRAME_EXTRA)
        frame_rig(frame)
        drive = frame_rig(frame)
        assert len(drive.typed("dispatch_variety")) == 2, (
            "RECORDING THE SHAPE, not asserting a dedup that does not "
            "exist: this site is append-only by design, so a second frame "
            "gives a second record. A future editor who adds dedup here "
            "must change this arm deliberately rather than discover it."
        )

    def test_two_frames_give_two_teachback_ack_records(self, frame_rig):
        frame = _arm_b(**TestTeachbackAckSite.FRAME_EXTRA)
        frame_rig(frame)
        drive = frame_rig(frame)
        assert len(drive.typed("teachback_ack")) == 2

    def test_the_marker_bearing_sites_do_dedup_and_that_is_the_contrast(
            self, frame_rig):
        """THE CONTRAST THAT MAKES THE TWO ARMS ABOVE MEAN SOMETHING. If
        every site were append-only, "two frames give two records" would be
        a property of the journal rather than of these two sites. The
        handoff emit carries an occupant-and-content marker, so a repeat of
        the SAME content gives ONE record."""
        frame = _arm_b(**TestLeadSideAgentHandoffSite.FRAME_EXTRA)
        frame_rig(frame)
        drive = frame_rig(frame)
        assert len(drive.typed("agent_handoff")) == 1, (
            "The marker-bearing site must dedup an identical repeat. If it "
            "does not, the append-only arms above record nothing specific."
        )
        assert drive.emit_calls.count("_emit_lead_side_agent_handoff") == 1, (
            "NON-VACUITY: the emitter must be CALLED on the second frame. "
            "If it is not, the single record proves the gate rejected the "
            "second frame and says nothing about dedup."
        )


# =============================================================================
# T3 — the ordering pin, driven through a REAL subprocess with no seeded
#      context. Section 8.3 of the architecture document.
# =============================================================================
class TestSubprocessOrderingPin:
    """`main()` calls `pact_context.init(input_data)` BEFORE
    `evaluate_lifecycle(input_data)`. That order is load-bearing: the frame
    gates resolve the team through `get_team_name()`, and an unpopulated
    context makes the topology leg unresolvable. The gate then degrades
    SILENTLY to `is_lead`, and a canonical-frame emit is lost with no record.

    🔴 EVERY IN-PROCESS ARM IN THIS FILE IS TRUE ON EITHER SIDE OF THAT
    ORDER, because the fixture seeds the context before the drive. So none
    of them can see the ordering. This arm drives the hook as a real
    subprocess with an UNSEEDED context, which is the only place the order
    is observable.
    """

    def test_in_process_teammate_emits_through_a_real_hook_process(
            self, tmp_path):
        home = tmp_path / "home"
        slug = Path(PROJECT_DIR).name
        session_dir = home / ".claude" / "pact-sessions" / slug / SESSION_ID
        session_dir.mkdir(parents=True, exist_ok=True)
        # The on-disk context file is what `pact_context.init()` READS. The
        # in-process module globals stay unseeded, which is the state this
        # arm exists to exercise.
        session_dir.joinpath("pact-session-context.json").write_text(
            json.dumps({
                "team_name": TEAM,
                "session_id": SESSION_ID,
                "project_dir": PROJECT_DIR,
                "plugin_root": "",
                "started_at": "2026-01-01T00:00:00Z",
            }),
            encoding="utf-8",
        )
        teams = home / ".claude" / "teams" / TEAM
        teams.mkdir(parents=True, exist_ok=True)
        teams.joinpath("config.json").write_text(
            json.dumps({"leadSessionId": SESSION_ID}), encoding="utf-8"
        )
        tasks = home / ".claude" / "tasks" / TEAM
        tasks.mkdir(parents=True, exist_ok=True)
        tasks.joinpath("7.json").write_text(
            json.dumps(_completed_task(metadata={})), encoding="utf-8"
        )
        frame = dict(TestLeadSideAgentHandoffSite.FRAME_EXTRA)
        frame["agent_type"] = TEAMMATE_AGENT_TYPE
        frame["session_id"] = SESSION_ID
        frame["team_name"] = TEAM
        frame["cwd"] = str(home)

        env = dict(os.environ)
        env["HOME"] = str(home)
        env.pop("CLAUDE_CONFIG_DIR", None)
        # init() derives the session-dir slug from this basename. Without it
        # get_session_dir() is empty and every journal write defers silently,
        # which would make this arm red for an unrelated cause.
        env["CLAUDE_PROJECT_DIR"] = PROJECT_DIR
        result = subprocess.run(
            [sys.executable, str(HOOKS_DIR / "task_lifecycle_gate.py")],
            input=json.dumps(frame),
            capture_output=True,
            text=True,
            env=env,
            cwd=str(home),
            timeout=30,
        )
        assert result.returncode == 0, result.stderr

        records = _journal_records(home)
        assert [r for r in records if r.get("type") == "agent_handoff"], (
            "An in-process teammate frame emitted NOTHING through the real "
            "hook process. Two causes give this one empty result and each "
            "is a defect: the frame gate answered on ROLE, or "
            "pact_context.init() no longer runs before evaluate_lifecycle() "
            "so the topology leg could not resolve and the gate degraded "
            "silently to is_lead.\nstderr: " + result.stderr
        )
