"""
Location: pact-plugin/tests/test_stop_background_gate.py
Summary: Behaviour of the Stop turn-end gate (hooks/stop_background_gate.py
         over hooks/shared/turn_end_gate.py), driven through the production
         entry point as a subprocess against a temporary config root.
Used by: the pact-plugin test suite.

Every arm runs the real hook with CLAUDE_CONFIG_DIR and CLAUDE_PROJECT_DIR
pointed at tmp_path. Nothing in the resolution chain is stubbed: the team
config, the task store, the background-work registry, the session registry and
the session context file are written where production writes them, and only
for the sessions production writes them for. No context file is ever written
for a separate-process teammate's session, because session_init writes that
file for lead frames only; a fixture that wrote one would test a topology no
teammate is in.

REVERT CARDINALITY — MEASURED, NOT CARRIED. Each row names the behaviour one
edit removes. That edit was applied to its own copy of the tree, proved
applied, and run against this whole file with a fresh bytecode cache; the
unmutated copy passes every arm:
  hook file removed ................................. 33 failed (all but the six
                                                      arms that call the gate
                                                      in-process)
  block never printed ............................... 10 failed
  told-once read ignored ............................ 3 failed
  stop_hook_active guard removed .................... 2 failed
  cron check removed ................................ 4 failed
  lead branch returns every running job ............. 3 failed
  lead's recorded job-id set left empty ............. 3 failed
  leadSessionId branch removed ...................... 1 failed
  registry teammate branch removed .................. 6 failed
  unresolved frame given a session folder ........... 1 failed
  fast path removed ................................. 3 failed here, none in
                                                      test_validate_handoff_turn_end.py
  job-list cap removed .............................. 1 failed
  any agent_id treated as a teammate ................ 2 failed
  Layer 1 writer stops recording the job id ......... 3 failed, at the writer checks
  teammate check ignores the joined record .......... 1 failed
Re-measure every row whenever an arm here is added or parametrized, and restate
the table in the same commit. A parametrized arm counts once per case.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
HOOK = HOOKS_DIR / "stop_background_gate.py"
TRACK_FILES = HOOKS_DIR / "track_files.py"

TEAM = "session-stopgate"
LEAD_SID = "aaaaaaaa-0000-4000-8000-000000000001"
MATE_SID = "bbbbbbbb-0000-4000-8000-000000000002"
PLAIN_SID = "cccccccc-0000-4000-8000-000000000003"
MATE = "mate"
LEAD_TYPE = "PACT:pact-orchestrator"
TOLD_FILENAME = "background-stop-told.json"
SUPPRESS = {"suppressOutput": True}


def _iso(minutes_ago: int = 0) -> str:
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return moment.isoformat(timespec="seconds")


def job(job_id: str, **extra) -> dict:
    entry = {
        "id": job_id,
        "type": "shell",
        "status": "running",
        "description": f"Sleep in the background ({job_id})",
        "command": "sleep 300",
    }
    entry.update(extra)
    return entry


def stop_frame(session_id: str = LEAD_SID, jobs=(), **fields) -> dict:
    """The captured Stop key set, with running jobs and role fields added."""
    frame = {
        "hook_event_name": "Stop",
        "session_id": session_id,
        "transcript_path": "<transcript_path>",
        "cwd": "<cwd>",
        "permission_mode": "bypassPermissions",
        "last_assistant_message": "done",
        "background_tasks": list(jobs),
        "session_crons": [],
        "stop_hook_active": False,
    }
    frame.update(fields)
    return frame


def valid_wait() -> dict:
    now = _iso()
    return {
        "reason": "awaiting_peer_response",
        "expected_resolver": "peer",
        "since": now,
        "covers_since": now,
    }


class World:
    """A temporary config root laid out the way a live PACT team leaves it."""

    def __init__(self, tmp_path: Path):
        self.config = tmp_path / "config"
        self.project = tmp_path / "proj"
        self.project.mkdir()
        team_dir = self.config / "teams" / TEAM
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(json.dumps({
            "leadSessionId": LEAD_SID,
            "members": [
                {"name": "team-lead", "agentType": "pact-orchestrator"},
                {"name": MATE, "agentType": "pact-backend-coder"},
            ],
        }))
        (self.config / "tasks" / TEAM).mkdir(parents=True)
        self.write_lead_context()

    def session_dir(self, session_id: str) -> Path:
        return self.config / "pact-sessions" / self.project.resolve().name / session_id

    def write_lead_context(self) -> None:
        directory = self.session_dir(LEAD_SID)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "pact-session-context.json").write_text(json.dumps({
            "team_name": TEAM,
            "session_id": LEAD_SID,
            "project_dir": str(self.project),
            "plugin_root": "",
            "started_at": "2026-09-13T00:00:00Z",
        }))

    def add_task(self, task_id: int, owner: str, status: str = "in_progress", wait=None) -> None:
        task = {
            "id": str(task_id), "subject": "work", "status": status, "owner": owner,
            "blocks": [], "blockedBy": [], "metadata": {},
        }
        if wait is not None:
            task["metadata"]["intentional_wait"] = wait
        (self.config / "tasks" / TEAM / f"{task_id}.json").write_text(json.dumps(task))

    def add_record(self, agent_name: str, job_id: str, task_ids: list, minutes_ago: int = 5) -> None:
        path = self.config / "teams" / TEAM / "background_work.json"
        data = json.loads(path.read_text()) if path.exists() else {"records": []}
        data["records"].append({
            "agent_name": agent_name,
            "session_id": MATE_SID,
            "task_ids": task_ids,
            "registered_at": _iso(minutes_ago),
            "harness_task_id": job_id,
        })
        path.write_text(json.dumps(data))

    def register_teammate(self, session_id: str, name: str) -> None:
        path = self.config / "pact-sessions" / ".teammate-registry.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as registry:
            registry.write(json.dumps({"session_id": session_id, "value": f"{name}@{TEAM}"}) + "\n")

    def env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if k != "PYTEST_CURRENT_TEST"}
        env["CLAUDE_CONFIG_DIR"] = str(self.config)
        env["CLAUDE_PROJECT_DIR"] = str(self.project)
        return env

    def records(self) -> list:
        path = self.config / "teams" / TEAM / "background_work.json"
        return json.loads(path.read_text())["records"] if path.exists() else []

    def run(self, frame: dict, script: Path = HOOK) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(frame), capture_output=True, text=True,
            env=self.env(), timeout=60, check=False,
        )

    def events(self, session_id: str) -> list:
        journal = self.session_dir(session_id) / "session-journal.jsonl"
        if not journal.exists():
            return []
        return [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]

    def traces(self, session_id: str) -> list:
        return [e for e in self.events(session_id) if e.get("type") == "background_stop_gate"]

    def told(self, session_id: str):
        path = self.session_dir(session_id) / TOLD_FILENAME
        return json.loads(path.read_text())["ids"] if path.exists() else None


def output(proc: subprocess.CompletedProcess):
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout) if proc.stdout.strip() else None


# --------------------------------------------------------------------------
# Lead
# --------------------------------------------------------------------------


def test_the_production_hook_blocks_a_lead_ending_its_turn_over_an_unrecorded_job(tmp_path):
    world = World(tmp_path)
    proc = world.run(stop_frame(jobs=[job("bjob1")], agent_type=LEAD_TYPE))

    out = output(proc)
    assert out["decision"] == "block"
    assert "`bjob1`" in out["reason"]
    assert "nothing is scheduled to wake you" in out["reason"]
    assert [(t["role"], t["verdict"], t["ids"]) for t in world.traces(LEAD_SID)] == [
        ("lead", "block", ["bjob1"])
    ]
    assert world.told(LEAD_SID) == ["bjob1"]
    assert "journal emit dropped" not in proc.stderr


def test_a_second_stop_is_not_blocked_for_the_same_job(tmp_path):
    """The livelock bound, behaviourally: one block per job id, then the stop lands."""
    world = World(tmp_path)
    frame = stop_frame(jobs=[job("bjob1")], agent_type=LEAD_TYPE)

    assert output(world.run(frame))["decision"] == "block"
    assert output(world.run(frame)) == SUPPRESS

    # A job that was never reported still is: the bound is per job, not per session.
    third = output(world.run(stop_frame(jobs=[job("bjob1"), job("bjob2")], agent_type=LEAD_TYPE)))
    assert third["decision"] == "block"
    assert "`bjob2`" in third["reason"] and "`bjob1`" not in third["reason"]
    assert [t["verdict"] for t in world.traces(LEAD_SID)] == [
        "block", "allow_already_told", "block"
    ]
    assert world.told(LEAD_SID) == ["bjob1", "bjob2"]


def test_stop_hook_active_allows(tmp_path):
    world = World(tmp_path)
    proc = world.run(stop_frame(jobs=[job("bjob1")], agent_type=LEAD_TYPE, stop_hook_active=True))

    assert output(proc) == SUPPRESS
    assert [t["verdict"] for t in world.traces(LEAD_SID)] == ["allow_loop_guard"]
    assert world.told(LEAD_SID) is None


def test_a_scheduled_cron_allows(tmp_path):
    world = World(tmp_path)
    crons = [{"id": "c1", "cron": "*/5 * * * *", "prompt": "check the job"}]
    proc = world.run(stop_frame(jobs=[job("bjob1")], agent_type=LEAD_TYPE, session_crons=crons))

    assert output(proc) == SUPPRESS
    assert [(t["verdict"], t.get("cause")) for t in world.traces(LEAD_SID)] == [
        ("allow_flagged", "session_cron")
    ]


def test_a_job_recorded_by_a_teammate_does_not_block_the_lead(tmp_path):
    world = World(tmp_path)
    world.add_task(7, MATE)
    world.add_record(MATE, "bjob1", ["7"])
    proc = world.run(stop_frame(jobs=[job("bjob1")], agent_type=LEAD_TYPE))

    assert output(proc) == SUPPRESS
    assert [(t["role"], t["verdict"]) for t in world.traces(LEAD_SID)] == [("lead", "allow_flagged")]


def test_a_stop_from_the_lead_session_is_the_lead_whatever_its_spelling(tmp_path):
    world = World(tmp_path)
    proc = world.run(stop_frame(jobs=[job("bjob1")], agent_type="some-other-orchestrator"))

    out = output(proc)
    assert out["decision"] == "block"
    assert "nothing is scheduled to wake you" in out["reason"]


# --------------------------------------------------------------------------
# Frames that must never block
# --------------------------------------------------------------------------


def test_a_plain_session_frame_never_blocks(tmp_path):
    """The captured plain Stop shape: no agent_type, no agent_id, no context."""
    world = World(tmp_path)
    proc = world.run(stop_frame(session_id=PLAIN_SID, jobs=[job("bjob1")]))

    assert output(proc) == SUPPRESS
    assert not world.session_dir(PLAIN_SID).exists(), (
        "an unidentified session must not get a session folder created for it"
    )


def test_an_agent_id_that_is_not_a_member_at_this_team_is_unresolved(tmp_path):
    world = World(tmp_path)
    frame = stop_frame(jobs=[job("bjob1")], agent_type="general-purpose", agent_id="ab997f00c7cd48288")
    proc = world.run(frame)

    assert output(proc) == SUPPRESS
    assert [t["verdict"] for t in world.traces(LEAD_SID)] == ["allow_role_unresolved"]


# --------------------------------------------------------------------------
# Separate-process teammate: real type, no agent_id, its own session, a
# session-registry entry, and NO session context file.
# --------------------------------------------------------------------------


def teammate_frame(jobs) -> dict:
    return stop_frame(session_id=MATE_SID, jobs=jobs, agent_type="pact-backend-coder")


def test_a_separate_process_teammate_without_a_wait_is_blocked(tmp_path):
    world = World(tmp_path)
    world.register_teammate(MATE_SID, MATE)
    world.add_task(7, MATE)
    proc = world.run(teammate_frame([job("bmate1")]))

    out = output(proc)
    assert out["decision"] == "block"
    assert not (world.session_dir(MATE_SID) / "pact-session-context.json").exists()
    assert world.told(MATE_SID) == ["bmate1"]
    assert [(t["role"], t["verdict"]) for t in world.traces(MATE_SID)] == [("teammate", "block")]


def test_a_teammate_with_a_covering_wait_is_allowed(tmp_path):
    world = World(tmp_path)
    world.register_teammate(MATE_SID, MATE)
    world.add_task(7, MATE, wait=valid_wait())
    proc = world.run(teammate_frame([job("bmate1")]))

    assert output(proc) == SUPPRESS
    assert [(t["role"], t["verdict"]) for t in world.traces(MATE_SID)] == [
        ("teammate", "allow_flagged")
    ]


# --------------------------------------------------------------------------
# The join with records the production Layer 1 writer produces. The records
# above are written by hand; these are written by track_files.py from a real
# PostToolUse launch frame, so the harness_task_id they carry is the writer's.
# --------------------------------------------------------------------------


def launch_frame(job_id: str) -> dict:
    """A separate-process teammate's background Bash launch, as PostToolUse sees it."""
    return {
        "hook_event_name": "PostToolUse",
        "session_id": MATE_SID,
        "agent_type": "pact-backend-coder",
        "transcript_path": "<transcript_path>",
        "cwd": "<cwd>",
        "tool_name": "Bash",
        "tool_input": {"command": "sleep 300", "description": "sleep", "run_in_background": True},
        "tool_response": {
            "backgroundTaskId": job_id, "interrupted": False, "isImage": False,
            "noOutputExpected": False, "stderr": "", "stdout": "",
        },
    }


def launch_through_layer_1(world: World, job_id: str) -> None:
    proc = world.run(launch_frame(job_id), script=TRACK_FILES)
    assert proc.returncode == 0, proc.stderr
    # Positive control: the writer ran and recorded this job's id.
    assert [r.get("harness_task_id") for r in world.records()] == [job_id], world.records()


def test_a_job_layer_1_recorded_for_a_teammate_does_not_block_the_lead(tmp_path):
    world = World(tmp_path)
    world.register_teammate(MATE_SID, MATE)
    world.add_task(7, MATE)
    launch_through_layer_1(world, "bjoin1")

    out = output(world.run(stop_frame(jobs=[job("bjoin1"), job("blead1")], agent_type=LEAD_TYPE)))
    assert out["decision"] == "block"
    assert "`blead1`" in out["reason"] and "`bjoin1`" not in out["reason"]


def test_a_wait_older_than_a_layer_1_launch_does_not_cover_it(tmp_path):
    """Only the join can tell these apart: with no record, any valid wait covers."""
    world = World(tmp_path)
    world.register_teammate(MATE_SID, MATE)
    old = _iso(10)
    world.add_task(7, MATE, wait={
        "reason": "awaiting_peer_response", "expected_resolver": "peer",
        "since": old, "covers_since": old,
    })
    launch_through_layer_1(world, "bjoin1")

    first = output(world.run(teammate_frame([job("bjoin1")])))
    assert first is not None and first.get("decision") == "block", first
    world.add_task(7, MATE, wait=valid_wait())
    assert output(world.run(teammate_frame([job("bjoin1")]))) == SUPPRESS


def subagent_shell_launch_frame(job_id: str) -> dict:
    """A background shell launched INSIDE an Agent-tool subagent.

    The frame fires in the LEAD's process and carries the subagent's own
    `agent_id` ("a" + 16 hex), which is what separates it from the lead's own
    frame (no agent_id) and from an in-process teammate's (whose agent_type
    carries a member name).
    """
    return {
        "hook_event_name": "PostToolUse",
        "session_id": LEAD_SID,
        "agent_type": "general-purpose",
        "agent_id": "ad2b1261fdd77c958",
        "transcript_path": "<transcript_path>",
        "cwd": "<cwd>",
        "tool_name": "Bash",
        "tool_input": {
            "command": "sleep 300", "description": "sleep", "run_in_background": True,
        },
        "tool_response": {
            "backgroundTaskId": job_id, "interrupted": False, "isImage": False,
            "noOutputExpected": False, "stderr": "", "stdout": "",
        },
    }


def test_a_shell_launched_inside_a_subagent_is_not_charged_to_the_lead(tmp_path):
    """The lead must not be refused its turn end over a subagent's shell.

    A shell launched inside a subagent appears in the LEAD's background_tasks
    with no owner, and outlives the subagent that started it. The lead's
    candidate set is every running shell MINUS the recorded launches, so unless
    that launch is recorded, nothing marks it as someone else's: the lead is
    refused a turn end over a job it did not start and cannot flag. Refusing an
    honest turn end is the failure this gate must never produce.

    The record is written through the PRODUCTION writer, not by hand. A
    hand-written row would satisfy this arm whatever the writer does, which is
    the whole thing being fixed.
    """
    world = World(tmp_path)
    proc = world.run(subagent_shell_launch_frame("bsubshell1"), script=TRACK_FILES)
    assert proc.returncode == 0, proc.stderr
    assert [r.get("harness_task_id") for r in world.records()] == ["bsubshell1"], (
        "the subagent's shell launch was not recorded, so nothing marks it as "
        "someone else's and the lead is charged for it"
    )

    out = output(world.run(stop_frame(jobs=[job("bsubshell1")], agent_type=LEAD_TYPE)))
    assert out == SUPPRESS, (
        "the lead was refused its own turn end over a shell that a subagent "
        f"launched: {out}"
    )


def test_a_subagent_row_is_not_matched_by_a_member_of_the_same_name(tmp_path, monkeypatch):
    """SEMANTIC PIN — it proves the code does the right thing GIVEN the input,
    NOT that the input occurs.

    A subagent row is NOT a member's row, so matching it BY MEMBER NAME is
    wrong whatever any member happens to be called. That is the property this
    arm states. It is deliberately NOT a reachability demonstration: nothing in
    this codebase documents that a member could be named 'a' plus 16 hex, and
    no such collision has been shown to occur.

    TWO ASSERTIONS SUFFICE BECAUSE ONLY TWO READS COMPARE A ROW'S NAME WITH A
    MEMBER'S. `turn_end_gate._candidates`' SubagentStop branch builds `by_job`
    from rows whose `agent_name` equals the ending teammate's, and
    `extend_records_for_claim` appends a newly claimed task to rows whose
    `agent_name` equals the claiming owner. The store's other reads of
    `agent_name`, in missed_wake_scan, never see a subagent row, and not
    because of how they key: they read only rows that `outstanding_unflagged`
    passes, and its expiry gate (`has_live_listed_task`) refuses a row with no
    task ids unless `anchor_completed` is set, which the recorder never sets on
    a subagent row. The remaining readers match on the job id, indifferent to
    owner, or on task ids, which a subagent row does not carry.
    """
    from shared import background_work, turn_end_gate

    world = World(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(world.config))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(world.project))
    # A member name deliberately built in the subagent id shape.
    colliding = "a0123456789abcdef"
    (world.config / "teams" / TEAM / "config.json").write_text(json.dumps({
        "leadSessionId": LEAD_SID,
        "members": [
            {"name": "team-lead", "agentType": "pact-orchestrator"},
            {"name": colliding, "agentType": "pact-backend-coder"},
        ],
    }))
    world.add_task(7, colliding)
    (world.config / "teams" / TEAM / "background_work.json").write_text(json.dumps({
        "records": [{
            "agent_name": colliding,
            "session_id": LEAD_SID,
            "task_ids": [],
            "owner_role": "subagent",
            "registered_at": _iso(5),
            "harness_task_id": "bsub-collide",
        }]
    }))

    candidates = turn_end_gate._candidates(
        {"hook_event_name": "SubagentStop"},
        turn_end_gate.ROLE_TEAMMATE,
        colliding,
        TEAM,
        [job("bsub-collide")],
    )
    assert candidates == [], (
        "a subagent's row was matched to a member BY NAME, so that member's "
        "turn end counts a job the subagent started"
    )

    extended = background_work.extend_records_for_claim(colliding, "7", team_name=TEAM)
    assert extended == 0, (
        "a subagent's row was matched to a member BY NAME, so that member's "
        "claimed task was appended to it"
    )


def test_a_subagent_row_the_recorder_writes_is_never_surfaced_as_outstanding(
    tmp_path, monkeypatch
):
    """The lead-side reads of a row's name never see a subagent row.

    missed_wake_scan reads `agent_name` for its separate-process filter, its
    forensic event and its lead surface, and it reads only rows that
    `outstanding_unflagged` passes. That selector's expiry gate refuses a row
    with no task ids unless `anchor_completed` is set. The arm above rests on
    this to say two assertions suffice.

    The claim has two halves, so the row is written by the PRODUCTION recorder,
    not by hand: the gate refuses a row with no task ids, AND the recorder never
    sets `anchor_completed` on a subagent's row. A hand-written row would pin
    only the first.

    A team task is in progress and nothing is flagged, so a row that listed it
    would be surfaced. The control shows that with the recorded row itself,
    given that task id and stripped of the subagent marker.
    """
    from shared import background_work
    from shared.task_utils import iter_team_task_jsons

    world = World(tmp_path)
    world.add_task(7, MATE)
    proc = world.run(subagent_shell_launch_frame("bsubrow1"), script=TRACK_FILES)
    assert proc.returncode == 0, proc.stderr

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(world.config))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(world.project))
    rows = background_work.load_records_for_discharge(TEAM)
    assert [r.get("owner_role") for r in rows] == ["subagent"], rows
    tasks = list(iter_team_task_jsons(TEAM))
    assert [(t.get("id"), t.get("status")) for t in tasks] == [("7", "in_progress")], tasks

    assert background_work.outstanding_unflagged(tasks, TEAM) == [], (
        "a subagent's row was surfaced as outstanding, so the lead-side reads of "
        f"a row's name see it: {rows}"
    )

    listed = {k: v for k, v in rows[0].items() if k != "owner_role"}
    listed["task_ids"] = ["7"]
    assert background_work.outstanding_unflagged(tasks, TEAM, records=[listed]) == [listed], (
        "control: the recorded row, listing the in-progress task, was not "
        "surfaced either, so the assertion above holds without the gate"
    )


# --------------------------------------------------------------------------
# Which entries count as jobs, per role. background_tasks lists live
# teammates and subagents as running entries beside real work.
# --------------------------------------------------------------------------


def live_teammates(count: int = 14) -> list:
    return [job(f"bmate{i}", type="teammate", description=f"member {i}") for i in range(count)]


# A fresh lead's first Stop frame from the live pre-fix baseline, refused over
# exactly these two entries (fields as captured).
LIVE_BASELINE_ENTRIES = [
    {"id": "tbbgxyxjp", "status": "running", "type": "teammate"},
    {"id": "tztw4cwv5", "status": "running", "type": "teammate"},
]


@pytest.mark.parametrize(
    "entries", [live_teammates(), LIVE_BASELINE_ENTRIES], ids=["fourteen", "live-baseline"]
)
def test_a_lead_with_live_teammates_and_no_shell_is_not_blocked(tmp_path, entries):
    world = World(tmp_path)
    frame = stop_frame(jobs=entries, agent_type=LEAD_TYPE)

    stdout, loaded = _loaded_plugin_modules(world, frame)
    assert stdout == ""
    assert loaded == []
    assert world.traces(LEAD_SID) == []


def test_a_leads_live_subagent_passes_the_fast_path_and_does_not_block(tmp_path):
    """`subagent` counts for a separate-process teammate, so the role-blind
    fast path lets it through; the lead's own set then counts nothing."""
    world = World(tmp_path)
    frame = stop_frame(
        jobs=live_teammates() + [job("bsub1", type="subagent")], agent_type=LEAD_TYPE
    )

    assert output(world.run(frame)) == SUPPRESS
    assert [(t["verdict"], t["running"]) for t in world.traces(LEAD_SID)] == [("allow_no_job", 0)]
    assert world.told(LEAD_SID) is None


def test_a_lead_is_told_only_about_its_shell(tmp_path):
    world = World(tmp_path)
    frame = stop_frame(
        jobs=live_teammates() + [job("bsub1", type="subagent"), job("bshell1")],
        agent_type=LEAD_TYPE,
    )

    out = output(world.run(frame))
    assert out["decision"] == "block"
    assert "`bshell1`" in out["reason"]
    assert "`bmate" not in out["reason"] and "`bsub1`" not in out["reason"]
    assert [t["ids"] for t in world.traces(LEAD_SID)] == [["bshell1"]]


def test_the_lead_text_does_not_say_a_notice_cannot_start_a_turn(tmp_path):
    world = World(tmp_path)

    out = output(world.run(stop_frame(jobs=[job("bshell1")], agent_type=LEAD_TYPE)))
    assert out["decision"] == "block"
    assert "does not start one" not in out["reason"]
    assert "can go undelivered" in out["reason"]


def test_a_tmux_teammate_stop_uses_the_separate_process_text(tmp_path):
    world = World(tmp_path)
    world.register_teammate(MATE_SID, MATE)
    world.add_task(7, MATE)

    out = output(world.run(teammate_frame([job("bmate1")])))
    assert out["decision"] == "block"
    assert "Background work you started is still running" in out["reason"]
    assert "can go undelivered" in out["reason"]
    assert "will not wake you" not in out["reason"]


@pytest.mark.parametrize("role, event, text", [
    ("ROLE_LEAD", "Stop", "LEAD_BLOCK_TEXT"),
    ("ROLE_TEAMMATE", "Stop", "SEPARATE_PROCESS_TEAMMATE_BLOCK_TEXT"),
    ("ROLE_TEAMMATE", "SubagentStop", "TEAMMATE_BLOCK_TEXT"),
])
def test_each_role_and_event_gets_its_block_text(role, event, text):
    from shared import turn_end_gate

    chosen = turn_end_gate._block_text_for(getattr(turn_end_gate, role), event)
    assert chosen is getattr(turn_end_gate, text)


def test_a_separate_process_teammate_is_told_about_its_own_monitor(tmp_path):
    world = World(tmp_path)
    world.register_teammate(MATE_SID, MATE)
    world.add_task(7, MATE)

    out = output(world.run(teammate_frame([job("bmon1", type="monitor")])))
    assert out["decision"] == "block"
    assert "`bmon1`" in out["reason"]


def test_a_leads_own_cron_still_allows_its_stop(tmp_path):
    world = World(tmp_path)
    frame = stop_frame(
        jobs=live_teammates(2) + [job("bshell1")], agent_type=LEAD_TYPE,
        session_crons=[{"id": "c1", "cron": "*/5 * * * *", "prompt": "check"}],
    )

    assert output(world.run(frame)) == SUPPRESS
    assert [(t["verdict"], t.get("cause")) for t in world.traces(LEAD_SID)] == [
        ("allow_flagged", "session_cron")
    ]


def test_a_separate_process_teammates_own_cron_still_allows(tmp_path):
    world = World(tmp_path)
    world.register_teammate(MATE_SID, MATE)
    world.add_task(7, MATE)
    frame = teammate_frame([job("bmate1")])
    frame["session_crons"] = [{"id": "c1", "cron": "*/5 * * * *", "prompt": "check"}]

    assert output(world.run(frame)) == SUPPRESS
    assert [(t["role"], t["verdict"], t.get("cause")) for t in world.traces(MATE_SID)] == [
        ("teammate", "allow_flagged", "session_cron")
    ]


def test_nothing_counted_for_the_role_traces_allow_no_job(tmp_path):
    world = World(tmp_path)
    proc = world.run(stop_frame(jobs=[job("bmon1", type="monitor")], agent_type=LEAD_TYPE))

    assert output(proc) == SUPPRESS
    assert [(t["role"], t["verdict"], t["running"]) for t in world.traces(LEAD_SID)] == [
        ("lead", "allow_no_job", 0)
    ]
    assert world.told(LEAD_SID) is None


def test_the_entry_scripts_share_one_job_filter():
    """The job-type sets live only in turn_end_jobs.py: neither entry script
    keeps its own copy of the running-job test or names a job type."""
    import ast

    labels = {"shell", "subagent", "monitor", "workflow", "MCP task", "teammate"}
    for script in ("stop_background_gate.py", "validate_handoff.py"):
        tree = ast.parse((HOOKS_DIR / script).read_text(encoding="utf-8"))
        functions = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert "_has_running_job" not in functions, script
        named = {
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in labels
        }
        assert named == set(), (script, named)
    source = (HOOKS_DIR / "shared" / "turn_end_jobs.py").read_text(encoding="utf-8")
    assert "LEAD_JOB_TYPES" in source and "OWN_PROCESS_JOB_TYPES" in source


# --------------------------------------------------------------------------
# Trace
# --------------------------------------------------------------------------


def _prepare_told(world: World, as_directory: bool = False) -> None:
    path = world.session_dir(LEAD_SID) / TOLD_FILENAME
    if as_directory:
        path.mkdir()
    else:
        path.write_text(json.dumps({"ids": ["bjob1"]}))


_VERDICT_CASES = {
    "block": (lambda w: None, {"agent_type": LEAD_TYPE}),
    "allow_flagged": (lambda w: None, {"agent_type": LEAD_TYPE, "session_crons": [{"id": "c"}]}),
    "allow_already_told": (_prepare_told, {"agent_type": LEAD_TYPE}),
    "allow_loop_guard": (lambda w: None, {"agent_type": LEAD_TYPE, "stop_hook_active": True}),
    "allow_role_unresolved": (
        lambda w: None, {"agent_type": "general-purpose", "agent_id": "ab997f00c7cd48288"}
    ),
    # A directory where the told-once file belongs cannot be read as a file.
    "allow_error": (lambda w: _prepare_told(w, as_directory=True), {"agent_type": LEAD_TYPE}),
    # A monitor passes the fast path's union but is not a job the lead counts.
    "allow_no_job": (
        lambda w: None,
        {"agent_type": LEAD_TYPE, "background_tasks": [job("bjob1", type="monitor")]},
    ),
}


@pytest.mark.parametrize("verdict", sorted(_VERDICT_CASES))
def test_every_verdict_writes_exactly_one_valid_trace_event(tmp_path, verdict):
    world = World(tmp_path)
    prepare, fields = _VERDICT_CASES[verdict]
    prepare(world)
    proc = world.run(stop_frame(jobs=[job("bjob1")], **fields))

    assert proc.returncode == 0, proc.stderr
    assert [t["verdict"] for t in world.traces(LEAD_SID)] == [verdict]
    assert not [e for e in world.events(LEAD_SID) if e.get("type") == "journal_emit_skipped"]
    assert "journal emit dropped" not in proc.stderr


# --------------------------------------------------------------------------
# Cost path and registration
# --------------------------------------------------------------------------

_MODULE_PROBE = (
    "import json, runpy, sys\n"
    "script = sys.argv[1]\n"
    "sys.argv = [script]\n"
    "runpy.run_path(script, run_name='__main__')\n"
    "loaded = sorted(m for m in sys.modules if m == 'shared' or m.startswith('shared.'))\n"
    "sys.stderr.write('\\nLOADED=' + json.dumps(loaded) + '\\n')\n"
)


def probe_env(world: World) -> dict:
    """The world's environment with the hooks directory importable, as running
    a hook script makes it: runpy.run_path does not add the script's folder."""
    env = world.env()
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(HOOKS_DIR), env.get("PYTHONPATH", "")) if p
    )
    return env


def _loaded_plugin_modules(world: World, frame: dict) -> "tuple[str, list]":
    proc = subprocess.run(
        [sys.executable, "-c", _MODULE_PROBE, str(HOOK)],
        input=json.dumps(frame), capture_output=True, text=True,
        env=probe_env(world), timeout=60, check=False,
    )
    marker = [line for line in proc.stderr.splitlines() if line.startswith("LOADED=")]
    assert marker, f"the probe did not report its modules: {proc.stderr}"
    return proc.stdout, json.loads(marker[-1][len("LOADED="):])


def test_an_empty_array_exits_before_importing_the_plugin(tmp_path):
    world = World(tmp_path)

    stdout, loaded = _loaded_plugin_modules(world, stop_frame(agent_type=LEAD_TYPE))
    assert loaded == []
    assert stdout == ""
    assert world.traces(LEAD_SID) == []

    # Positive control: the same probe does see the plugin once a job is running.
    _stdout, loaded = _loaded_plugin_modules(
        world, stop_frame(jobs=[job("bjob1")], agent_type=LEAD_TYPE)
    )
    assert "shared.turn_end_gate" in loaded


def test_hooks_json_registers_the_gate_on_stop_and_nowhere_else():
    config = json.loads((HOOKS_DIR / "hooks.json").read_text(encoding="utf-8"))
    bindings = [
        (event, hook["command"], hook.get("async", False))
        for event, entries in config["hooks"].items()
        for entry in entries
        for hook in entry.get("hooks", [])
        if "stop_background_gate.py" in hook.get("command", "")
    ]
    assert bindings == [
        ("Stop", 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/stop_background_gate.py"', False)
    ]
    assert HOOK.exists()
    assert 'if __name__ == "__main__":\n    main()' in HOOK.read_text(encoding="utf-8")


def test_block_text_lists_at_most_five_jobs_each_capped():
    from shared import turn_end_gate

    entries = [job(f"b{i}", description="x" * 200 + "\nsecond line") for i in range(7)]
    text = turn_end_gate.describe_jobs(entries)

    labels = text.split(", ")
    assert labels[-1] == "and 2 more"
    assert len(labels) == 6
    assert all(len(label) <= turn_end_gate.JOB_LABEL_MAX_CHARS for label in labels[:-1])
    assert "\n" not in text
