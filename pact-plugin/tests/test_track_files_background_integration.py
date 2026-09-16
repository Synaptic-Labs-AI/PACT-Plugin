"""L2 non-mocked seam test for track_files' background-work registry write.

Location: pact-plugin/tests/test_track_files_background_integration.py
Summary: exercises the REAL integration seam Layer 1 depends on — team-name
         resolution from a real session context, a real team config on disk, a
         real task store, and a real flock'd registry file. Nothing is
         monkeypatched except the config ROOT.
Used by: hook_infra_classifier's COVERED_L2 mapping for `track_files`.

WHY THIS EXISTS AT ALL. `track_files` joined SEAM_DEPENDENT_HOOKS when Layer 1
landed: it now resolves the task dir and reads team config. The seam
requirement is this project's substitute for a shipped runtime hook, and a
mocked test would re-prove the instrument rather than the seam — which is
exactly how the feature this replaces shipped inert.

REVERT-CARDINALITY NON-VACUITY GATE — MEASURED, not asserted. Source-revert
the Layer 1 call in `hooks/track_files.py` (replace the guarded
`record_background_launch(input_data)` block with `pass`) and this file
reports **6 failed, 8 passed**. The six kills are the arms that assert a
record IS written: `test_a_background_launch_lands_a_real_record_on_disk`,
`test_layer_1_records_the_harness_task_id`,
`test_a_launch_without_a_string_harness_id_records_none`,
`test_a_SHELL_backgrounded_launch_with_NO_FLAG_lands_a_record`,
`test_a_LONG_RUNNING_command_IS_recorded_now` and
`test_a_SUBAGENT_launched_shell_lands_a_subagent_owned_record`. The other eight
pin fail-open and negative behaviour and correctly survive a feature that does
nothing. If a future edit makes that ablation report **0 failed**, this file has
stopped measuring the seam and the number above is the tripwire.

A SECOND ABLATION IS ALREADY PINNED BY THE FIXTURE: omitting the
`pact-session-context.json` write also reports 6 failed, 8 passed, on the same
six positive-record arms, for a DIFFERENT reason: with no session context and
no registry entry, the frame's team cannot be resolved. Two distinct
single-point ablations, same cardinality, different cause; see the fixture
docstring.

BOTH NUMBERS ARE MEASURED, NEVER CARRIED. Each is re-run in an isolated copy of
the tree, with the mutation proved applied before the run, whenever an arm here
is added or parametrized. A positive-record arm adds to the failed count and a
negative arm to the passed count, and a parametrized arm counts once for each of
its cases. Restate both numbers in the same commit, or the tripwire stops
tripping.

NO module-level sys.path.insert: path setup is conftest-owned.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from clock_shift.clock_shift_env import carry_clock_shift
from shared.pact_context import LEAD_AGENT_TYPES

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "track_files.py"
TEAM = "session-seamtest"


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


SESSION_ID = "lead-sid"
PROJECT_DIR = "/seam/project"


@pytest.fixture
def seam(tmp_path):
    """A real config root: team config, task store, AND session context.

    THE SESSION CONTEXT IS PART OF THE SEAM, NOT SETUP NOISE. `get_team_name`
    reads the persisted team name from it and treats an EMPTY value as a
    deliberate fail-closed "team unknown -> refuse" signal. A fixture that
    creates the team config and the task store but omits this file reproduces
    the SHAPE of production without its CONFIGURATION, and every write-path
    assertion then passes for the wrong reason or fails for one.

    MEASURED: this test failed exactly that way before the context file was
    added — every stage of the bind succeeded and `get_team_name()` returned
    "", so no record was written.

    The path is built with the module's OWN `project_slug`, not hand-spelled,
    so the fixture cannot drift from the resolver it is exercising.
    """
    from shared.pact_context import project_slug

    _write(
        tmp_path / "teams" / TEAM / "config.json",
        {
            "leadSessionId": SESSION_ID,
            "members": [
                {
                    "name": "seam-coder",
                    "agentId": f"seam-coder@{TEAM}",
                    "agentType": "pact-backend-coder",
                    "backendType": "in-process",
                }
            ],
        },
    )
    _write(
        tmp_path / "tasks" / TEAM / "7.json",
        {"id": "7", "status": "in_progress", "owner": "seam-coder"},
    )
    _write(
        tmp_path
        / "pact-sessions"
        / project_slug(PROJECT_DIR)
        / SESSION_ID
        / "pact-session-context.json",
        {
            "session_id": SESSION_ID,
            "project_dir": PROJECT_DIR,
            "team_name": TEAM,
        },
    )
    return tmp_path


def _run(seam_root: Path, frame: dict) -> subprocess.CompletedProcess:
    """Fire the hook as a REAL subprocess, the way hooks.json invokes it."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(seam_root),
        "CLAUDE_CONFIG_DIR": str(seam_root),
        "CLAUDE_PROJECT_DIR": PROJECT_DIR,
    }
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(frame),
        capture_output=True,
        text=True,
        env=carry_clock_shift(env),
        timeout=30,
    )


def _registry(seam_root: Path):
    path = seam_root / "teams" / TEAM / "background_work.json"
    if not path.exists():
        return []
    return json.loads(path.read_text()).get("records", [])


def _frame(**over):
    frame = {
        "hook_event_name": "PostToolUse",
        "session_id": SESSION_ID,
        "tool_name": "Bash",
        "agent_type": "seam-coder",
        "agent_id": "0123456789abcdef",
        "tool_input": {"command": "echo seam", "run_in_background": True},
    }
    frame.update(over)
    return frame


# The `agent_type` of each frame sent with NO `agent_id`. The first is the
# synthetic subagent-shaped frame. The rest are every spelling the lead can
# carry, taken from the set the lead is recognised by rather than written out,
# so a spelling added there is covered here with no edit, and a writer that
# recognises only one spelling cannot slip past the others.
NO_AGENT_ID_AGENT_TYPES = [
    pytest.param("general-purpose", id="subagent-shaped"),
    *(
        pytest.param(spelling, id=f"lead:{spelling}")
        for spelling in sorted(LEAD_AGENT_TYPES)
    ),
]


class TestTrackFilesBackgroundSeam:
    def test_the_hook_exits_zero_and_stays_quiet(self, seam):
        result = _run(seam, _frame())
        assert result.returncode == 0, result.stderr
        assert "suppressOutput" in result.stdout

    def test_a_background_launch_lands_a_real_record_on_disk(self, seam):
        """The seam, end to end, through a real process.

        An empty registry here is the exact failure the predecessor shipped,
        so this asserts the POSITIVE — a file that stays empty is the alarm.
        """
        assert _registry(seam) == []
        assert _run(seam, _frame()).returncode == 0
        records = _registry(seam)
        assert len(records) == 1, (
            "no record written — the seam is broken, which is the shape the "
            "predecessor shipped green"
        )
        assert records[0]["agent_name"] == "seam-coder"
        assert records[0]["task_ids"] == ["7"]

    def test_layer_1_records_the_harness_task_id(self, seam):
        """The turn-end gate matches a running job to its launcher by this id, so
        the record carries the harness's `backgroundTaskId` from tool_response."""
        frame = _frame(tool_response={"backgroundTaskId": "bg-seam-1", "stdout": ""})
        assert _run(seam, frame).returncode == 0
        records = _registry(seam)
        assert len(records) == 1
        assert records[0].get("harness_task_id") == "bg-seam-1", (
            "the launch record carries no harness_task_id, so the turn-end gate "
            "cannot tell this teammate's job from anyone else's"
        )

    def test_a_launch_without_a_string_harness_id_records_none(self, seam):
        """Every tool_response key is optional: a shell `&` launch carries no
        `backgroundTaskId`, and a non-string value is not an id."""
        shell = _frame(tool_response={"stdout": ""})
        shell["tool_input"] = {"command": "nohup ./gate.sh &"}
        assert _run(seam, shell).returncode == 0
        assert _run(seam, _frame(tool_response={"backgroundTaskId": 17})).returncode == 0
        records = _registry(seam)
        assert len(records) == 2
        assert all("harness_task_id" not in r for r in records)

    def test_a_SHELL_backgrounded_launch_with_NO_FLAG_lands_a_record(self, seam):
        """ARM 5 — THE COMPOSITION ARM. It proves the gate CONSULTS the
        trailing-`&` predicate, which no predicate-level arm can show.

        🔴 WHY THIS ARM AND NOT A PREDICATE TEST. `is_shell_backgrounded_bash`
        can be tested seven ways and pass every time while
        `record_background_launch` never calls it — the widening would be
        entirely unwired and the suite fully green. Only an arm asserting a
        POSITIVE record THROUGH the gate can see that, because an unwired gate
        fails CLOSED and a silent refusal is observationally identical to a
        correct one. Every negative-asserting arm in this file is structurally
        incapable of catching it.

        THE FRAME CARRIES NO `run_in_background` KEY AT ALL — not False, absent
        — because that is the shape a foreground Bash call actually has, and it
        is the shape that was invisible to all three layers until the gate was
        widened. A live probe measured it: a teammate backgrounded a test sweep
        with `nohup … &` and nothing recorded it.

        MUTANT that reddens this arm (arm 6): revert the gate to
        `if not is_harness_background_bash(input_data)`. That is the PRIOR
        BEHAVIOUR rather than a broken function, which is this branch's
        acceptance standard.
        """
        frame = _frame()
        frame["tool_input"] = {"command": "nohup ./gate.sh &"}
        assert "run_in_background" not in frame["tool_input"]
        assert _registry(seam) == []
        assert _run(seam, frame).returncode == 0
        records = _registry(seam)
        assert len(records) == 1, (
            "a foreground Bash call whose command ends in a bare `&` was not "
            "recorded, so the trailing-`&` predicate is not wired into the "
            "gate — the widening is inert and every predicate-level arm would "
            "still be green"
        )
        assert records[0]["command"] == "nohup ./gate.sh &"

    def test_a_SUBAGENT_launched_shell_lands_a_subagent_owned_record(self, seam):
        """A shell launched INSIDE an Agent-tool subagent must be recorded.

        Such a launch appears in the LEAD's background_tasks with no owner and
        outlives the subagent that started it. The lead's candidate set is every
        running shell minus the RECORDED launches, so with no row here the lead
        is refused its own turn end over a job it did not start and cannot flag.

        The frame carries the subagent's own `agent_id` ("a" + 16 hex) and an
        agent_type that names no member, which is the combination the recorder
        refused before this arm existed.
        """
        frame = _frame(
            agent_type="general-purpose",
            agent_id="ad2b1261fdd77c958",
            tool_response={"backgroundTaskId": "bg-sub-1", "stdout": ""},
        )
        assert _registry(seam) == []
        assert _run(seam, frame).returncode == 0
        records = _registry(seam)
        assert len(records) == 1, (
            "a subagent's background shell was not recorded, so nothing marks "
            "it as someone else's and the lead is charged for it"
        )
        assert records[0]["owner_role"] == "subagent"
        assert records[0]["agent_name"] == "ad2b1261fdd77c958"
        assert records[0]["task_ids"] == []
        assert records[0]["harness_task_id"] == "bg-sub-1"
        # `anchor_completed` MUST NOT appear on a subagent row. It is the
        # CONSULTANT flag, and the consultant path is the nearest existing
        # shape for a task-less owner, so it is the thing a later edit reaches
        # for. `has_live_listed_task` returns True UNCONDITIONALLY for it, so
        # the row would never expire on task completion, would live the full
        # 24h TTL, could carry no wait to silence itself, and Layer 3 would
        # then tell the lead a teammate has unflagged work, naming a subagent
        # that no longer exists. Without the flag the row fails the liveness
        # gate naturally and every surfacing consumer drops it — which is
        # correct, because this row exists ONLY to be subtracted by id.
        assert "anchor_completed" not in records[0]

    @pytest.mark.parametrize("agent_type", NO_AGENT_ID_AGENT_TYPES)
    def test_a_launch_without_an_explicit_agent_id_writes_nothing(self, seam, agent_type):
        """No `agent_id` on the frame means no row, whichever shape the frame has.

        The two shapes guard different things. The SUBAGENT-SHAPED frame guards
        an inference: an absent `agent_id` is the lead's own signature on the
        lead's frames, and only INFERRED for an in-process teammate's spawn, so
        recording it as anyone would risk charging the lead for a teammate's
        launch. The LEAD-SHAPED frames — every lead spelling, in the lead's own
        session — are the defining positive of any row the lead would own:
        whatever a future writer uses to recognise the lead, it must write a row
        for at least one of them, so this arm reddens the day that writer lands.

        Neither shape writes a row today. With no `agent_id` there is no subagent
        id, so the frame reaches the teammate predicate, which refuses a lead
        spelling outright and refuses the subagent-shaped frame because it names
        no member and runs in the lead's session.
        """
        frame = _frame(agent_type=agent_type)
        frame.pop("agent_id")
        assert _run(seam, frame).returncode == 0
        assert _registry(seam) == [], (
            "a launch frame with no agent_id was recorded. If this is a new row "
            "the lead owns, note that the lead branch of turn_end_gate._candidates "
            "subtracts every recorded job id unconditionally, so it must filter "
            "these rows, or the lead is acquitted of its own running job."
        )

    def test_a_NON_background_bash_writes_nothing(self, seam):
        """The negative control, so the positive above is not vacuous."""
        frame = _frame()
        frame["tool_input"] = {"command": "echo seam"}
        assert _run(seam, frame).returncode == 0
        assert _registry(seam) == []

    def test_an_unresolvable_identity_writes_nothing(self, seam):
        """Fail-open, not mis-bind: refuse rather than guess an owner."""
        assert _run(seam, _frame(agent_type="pact-backend-coder")).returncode == 0
        assert _registry(seam) == []

    def test_a_LONG_RUNNING_command_IS_recorded_now(self, seam):
        """INVERTED. This arm asserted the opposite until the text predicate
        was deleted, and the inversion IS the fix's observable effect.

        `is_durable_command` suppressed the Layer 1 write whenever the command
        text contained `dev|start|serve|watch`. It was deleted because "is
        this command durable" is not answerable from the string: it caught
        `npm run dev` and equally silenced `pytest -k start` and
        `grep -rn watch hooks/`, which are ordinary one-shot work. The
        question IS answerable later — `intentional_wait` carries it, when the
        agent says what it is waiting for — so the launch is recorded here and
        judged there.

        WHY THIS ARM SURVIVED THE DELETION RATHER THAN GOING WITH IT. It never
        named the predicate; it reached it through the seam, by sending a
        command whose TEXT happened to match. A symbol census over
        `is_durable_command` returned a true zero tree-wide and could not see
        this, and a collection check passed because the failure is at RUN time.
        Kept and inverted so the seam still has an arm on what a long-running
        command does, which is now the same as any other command.
        """
        frame = _frame()
        frame["tool_input"] = {"command": "npm run dev", "run_in_background": True}
        assert _run(seam, frame).returncode == 0
        records = _registry(seam)
        assert len(records) == 1, (
            "a long-running command must now be RECORDED — the text-based "
            "suppressor was deleted deliberately; if this is empty the "
            "predicate has been re-added"
        )
        assert records[0]["task_ids"] == ["7"]

    def test_the_hosts_ORIGINAL_job_still_runs(self, seam):
        """The fold must not cost this hook its file tracking.

        An Edit frame exercises the pre-existing job; the background call is
        a third job bolted beside it and a fault there must not disturb this.
        """
        target = seam / "somefile.py"
        target.write_text("x = 1\n")
        frame = {
            "hook_event_name": "PostToolUse",
            "session_id": SESSION_ID,
            "tool_name": "Edit",
            "agent_type": "seam-coder",
            "tool_input": {"file_path": str(target)},
        }
        result = _run(seam, frame)
        assert result.returncode == 0, result.stderr
        tracked = list((seam / "pact-memory" / "session-tracking").glob("*.json"))
        assert tracked, "the host's own file-tracking job stopped running"
        assert str(target) in tracked[0].read_text()

    def test_a_broken_team_config_does_not_crash_the_hook(self, seam):
        """Fail-open on a corrupt seam — the host's jobs must survive it."""
        (seam / "teams" / TEAM / "config.json").write_text("{not json")
        result = _run(seam, _frame())
        assert result.returncode == 0
        assert _registry(seam) == []
