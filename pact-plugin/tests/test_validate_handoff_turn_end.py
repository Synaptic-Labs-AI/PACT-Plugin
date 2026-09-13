"""
Location: pact-plugin/tests/test_validate_handoff_turn_end.py
Summary: validate_handoff.py as the single SubagentStop decision: the
         in-process teammate background-work check (shared/turn_end_gate.py)
         composed with the prose HANDOFF check, and the teammate exclusion
         from that prose check. Driven through the production entry point as
         a subprocess against a temporary config root.
Used by: the pact-plugin test suite.

The world is test_stop_background_gate's: a team config, task store, registry
and the lead's session context, written where production writes them. An
in-process teammate shares the lead's session, so its SubagentStop resolves the
lead's context. The platform's subagent metadata file sits where the platform
writes it, under the config root's projects folder beside the agent
transcript.
"""

import json
import subprocess
import sys

from test_stop_background_gate import (
    HOOKS_DIR,
    LEAD_SID,
    MATE,
    TEAM,
    World,
    job,
    probe_env,
    valid_wait,
)

HOOK = HOOKS_DIR / "validate_handoff.py"
MATE_AGENT_ID = "amate-0123456789abcdef"
SUBAGENT_ID = "ab997f00c7cd48288"
TOLD_FILENAME = "background-stop-told.json"
SUPPRESS = {"suppressOutput": True}

GOOD_HANDOFF = (
    "HANDOFF:\n"
    "1. Produced: hooks/example.py, a module that implements the requested endpoint.\n"
    "2. Key decisions: chose the simpler approach because it keeps the diff small.\n"
    "3. Next steps: the test engineer should cover the error path.\n"
)
POOR_CLOSING = "x" * 100 + " Hello world, here is some random text without any of those words."


def write_metadata(world: World, agent_id: str, meta: dict) -> str:
    """Write the platform's metadata file and return the agent transcript path."""
    subagents = world.config / "projects" / "-proj" / LEAD_SID / "subagents"
    subagents.mkdir(parents=True, exist_ok=True)
    (subagents / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta))
    return str(subagents / f"agent-{agent_id}.jsonl")


def teammate_metadata(world: World) -> str:
    return write_metadata(world, MATE_AGENT_ID, {
        "agentType": MATE, "name": MATE, "taskKind": "in_process_teammate",
        "teamName": TEAM, "customAgentType": "pact-backend-coder",
    })


def subagent_frame(agent_type: str, agent_id: str, transcript_path: str, jobs=(), **fields) -> dict:
    frame = {
        "hook_event_name": "SubagentStop",
        "session_id": LEAD_SID,
        "agent_type": agent_type,
        "agent_id": agent_id,
        "agent_transcript_path": transcript_path,
        "transcript_path": "<transcript_path>",
        "last_assistant_message": GOOD_HANDOFF,
        "background_tasks": list(jobs),
        "session_crons": [],
        "stop_hook_active": False,
    }
    frame.update(fields)
    return frame


def run(world: World, frame: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(frame), capture_output=True, text=True,
        env=world.env(), timeout=60, check=False,
    )


def only_decision(proc: subprocess.CompletedProcess) -> dict:
    """The one JSON object the hook printed. Fails on zero or several."""
    assert proc.returncode == 0, proc.stderr
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected exactly one decision, got {lines}"
    return json.loads(lines[0])


def recorded_mate_job(world: World) -> None:
    world.add_task(7, MATE)
    world.add_record(MATE, "bmate1", ["7"])


# --------------------------------------------------------------------------
# In-process teammate background check
# --------------------------------------------------------------------------


def test_validate_handoff_blocks_an_in_process_teammate_ending_over_its_recorded_job(tmp_path):
    world = World(tmp_path)
    recorded_mate_job(world)
    transcript = teammate_metadata(world)
    # The lead's own job is in the same process list and must not be attributed.
    frame = subagent_frame(MATE, MATE_AGENT_ID, transcript, jobs=[job("blead1"), job("bmate1")])

    out = only_decision(run(world, frame))
    assert out["decision"] == "block"
    assert "background work you started" in out["reason"]
    assert "`bmate1`" in out["reason"] and "`blead1`" not in out["reason"]
    assert [(t["role"], t["verdict"], t["ids"]) for t in world.traces(LEAD_SID)] == [
        ("teammate", "block", ["bmate1"])
    ]
    assert world.told(LEAD_SID) == ["bmate1"]


def test_an_in_process_teammate_subagentstop_keeps_the_teammate_text(tmp_path):
    world = World(tmp_path)
    recorded_mate_job(world)
    frame = subagent_frame(MATE, MATE_AGENT_ID, teammate_metadata(world), jobs=[job("bmate1")])

    out = only_decision(run(world, frame))
    assert out["decision"] == "block"
    assert "will not wake you" in out["reason"]
    assert "can go undelivered" not in out["reason"]


def test_the_metadata_alone_identifies_the_teammate(tmp_path):
    """agent_type names neither a member nor a PACT type; only the metadata says teammate."""
    world = World(tmp_path)
    recorded_mate_job(world)
    transcript = teammate_metadata(world)
    frame = subagent_frame("some-role", MATE_AGENT_ID, transcript, jobs=[job("bmate1")])

    assert only_decision(run(world, frame))["decision"] == "block"


def test_stop_hook_active_degrades_the_background_reason_and_marks_nothing_told(tmp_path):
    world = World(tmp_path)
    recorded_mate_job(world)
    transcript = teammate_metadata(world)
    frame = subagent_frame(
        MATE, MATE_AGENT_ID, transcript, jobs=[job("bmate1")], stop_hook_active=True
    )

    out = only_decision(run(world, frame))
    assert "decision" not in out
    assert "background work you started" in out["systemMessage"]
    assert world.told(LEAD_SID) is None
    assert [t["verdict"] for t in world.traces(LEAD_SID)] == ["allow_loop_guard"]


def test_an_agent_tool_subagent_is_never_blocked_for_background_work(tmp_path):
    """The captured subagent shape: general-purpose, a hex id, metadata without taskKind."""
    world = World(tmp_path)
    transcript = write_metadata(world, SUBAGENT_ID, {
        "agentType": "general-purpose", "toolUseId": "toolu_x", "description": "probe",
    })
    frame = subagent_frame("general-purpose", SUBAGENT_ID, transcript, jobs=[job("bsub1")])

    assert only_decision(run(world, frame)) == SUPPRESS
    assert [t["verdict"] for t in world.traces(LEAD_SID)] == ["allow_role_unresolved"]


def test_an_unrecorded_job_does_not_block_the_teammate(tmp_path):
    """The stated under-block: a job Layer 1 did not record cannot be attributed."""
    world = World(tmp_path)
    world.add_task(7, MATE)
    transcript = teammate_metadata(world)
    frame = subagent_frame(MATE, MATE_AGENT_ID, transcript, jobs=[job("bunrecorded")])

    assert only_decision(run(world, frame)) == SUPPRESS
    assert [(t["role"], t["verdict"]) for t in world.traces(LEAD_SID)] == [
        ("teammate", "allow_flagged")
    ]


def test_a_covering_wait_allows_the_teammate(tmp_path):
    world = World(tmp_path)
    world.add_task(7, MATE, wait=valid_wait())
    world.add_record(MATE, "bmate1", ["7"])
    transcript = teammate_metadata(world)
    frame = subagent_frame(MATE, MATE_AGENT_ID, transcript, jobs=[job("bmate1")])

    assert only_decision(run(world, frame)) == SUPPRESS


# --------------------------------------------------------------------------
# One decision per turn end
# --------------------------------------------------------------------------


def test_one_turn_end_prints_exactly_one_decision(tmp_path):
    world = World(tmp_path)
    transcript = write_metadata(world, SUBAGENT_ID, {"agentType": "pact-preparer"})

    # A PACT subagent's prose refusal while a job runs: one block, and the
    # background verdict is traced rather than printed.
    refused = subagent_frame(
        "pact-preparer", SUBAGENT_ID, transcript,
        jobs=[job("bsub1")], last_assistant_message=POOR_CLOSING,
    )
    out = only_decision(run(world, refused))
    assert out["decision"] == "block"
    assert "PACT Handoff Refusal" in out["reason"]
    assert [t["verdict"] for t in world.traces(LEAD_SID)] == ["allow_role_unresolved"]

    # A teammate's background block alone: still one object.
    recorded_mate_job(world)
    mate = subagent_frame(MATE, MATE_AGENT_ID, teammate_metadata(world), jobs=[job("bmate1")])
    assert only_decision(run(world, mate))["decision"] == "block"


def test_subagentstop_has_exactly_one_registration():
    config = json.loads((HOOKS_DIR / "hooks.json").read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entry in config["hooks"].get("SubagentStop", [])
        for hook in entry.get("hooks", [])
    ]
    assert commands == ['python3 "${CLAUDE_PLUGIN_ROOT}/hooks/validate_handoff.py"']


_MODULE_PROBE = (
    "import json, runpy, sys\n"
    "script = sys.argv[1]\n"
    "sys.argv = [script]\n"
    "try:\n"
    "    runpy.run_path(script, run_name='__main__')\n"
    "except SystemExit:\n"
    "    pass\n"
    "sys.stderr.write('\\nGATE=' + json.dumps('shared.turn_end_gate' in sys.modules) + '\\n')\n"
)


def _gate_imported(world: World, frame: dict) -> bool:
    proc = subprocess.run(
        [sys.executable, "-c", _MODULE_PROBE, str(HOOK)],
        input=json.dumps(frame), capture_output=True, text=True,
        env=probe_env(world), timeout=60, check=False,
    )
    marker = [line for line in proc.stderr.splitlines() if line.startswith("GATE=")]
    assert marker, f"the probe did not report: {proc.stderr}"
    return json.loads(marker[-1][len("GATE="):])


def test_an_ordinary_subagentstop_does_not_import_the_gate(tmp_path):
    world = World(tmp_path)
    transcript = write_metadata(world, SUBAGENT_ID, {"agentType": "general-purpose"})

    assert _gate_imported(world, subagent_frame("general-purpose", SUBAGENT_ID, transcript)) is False
    # Positive control: a running job does import it.
    assert _gate_imported(
        world, subagent_frame("general-purpose", SUBAGENT_ID, transcript, jobs=[job("b1")])
    ) is True


# --------------------------------------------------------------------------
# The prose check's population
# --------------------------------------------------------------------------


def test_a_pact_typed_teammate_frame_is_not_refused_for_a_missing_prose_handoff(tmp_path):
    world = World(tmp_path)
    transcript = teammate_metadata(world)
    frame = subagent_frame(
        "pact-architect", MATE_AGENT_ID, transcript, last_assistant_message=POOR_CLOSING
    )

    assert only_decision(run(world, frame)) == SUPPRESS


def test_a_member_name_teammate_frame_is_not_refused(tmp_path):
    world = World(tmp_path)
    frame = subagent_frame(MATE, "0123456789abcdef", "", last_assistant_message=POOR_CLOSING)

    assert only_decision(run(world, frame)) == SUPPRESS


def test_the_teammate_exclusion_still_runs_the_background_arm(tmp_path):
    world = World(tmp_path)
    recorded_mate_job(world)
    transcript = teammate_metadata(world)
    frame = subagent_frame(
        "pact-architect", MATE_AGENT_ID, transcript,
        jobs=[job("bmate1")], last_assistant_message=POOR_CLOSING,
    )

    out = only_decision(run(world, frame))
    assert out["decision"] == "block"
    assert "background work you started" in out["reason"]
    assert "PACT Handoff Refusal" not in out["reason"]


def test_a_non_teammate_pact_subagent_is_still_refused_without_a_handoff(tmp_path):
    world = World(tmp_path)
    transcript = write_metadata(world, SUBAGENT_ID, {"agentType": "pact-preparer"})
    frame = subagent_frame(
        "pact-preparer", SUBAGENT_ID, transcript, last_assistant_message=POOR_CLOSING
    )

    out = only_decision(run(world, frame))
    assert out["decision"] == "block"
    assert "PACT Handoff Refusal" in out["reason"]


def test_lead_process_crons_do_not_silence_an_in_process_teammate(tmp_path):
    """A SubagentStop frame carries the lead process's crons. They wake the
    lead, not the teammate, so they are not a flag for the teammate's job."""
    world = World(tmp_path)
    recorded_mate_job(world)
    transcript = teammate_metadata(world)
    crons = [
        {"id": "c1", "cron": "*/5 * * * *", "prompt": "lead check"},
        {"id": "c2", "cron": "0 * * * *", "prompt": "lead hourly"},
    ]
    frame = subagent_frame(
        MATE, MATE_AGENT_ID, transcript, jobs=[job("bmate1")], session_crons=crons
    )

    out = only_decision(run(world, frame))
    assert out["decision"] == "block"
    assert [(t["role"], t["verdict"]) for t in world.traces(LEAD_SID)] == [("teammate", "block")]
