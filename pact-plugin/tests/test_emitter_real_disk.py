"""
Real-disk + platform-shape tests for agent_handoff_emitter.py.

Pairs the only real-disk-reading class (TestRealDiskRead — exercises
``read_task_json`` against an actual ``~/.claude/tasks/{team}/{id}.json``
file written under tmp_path) with TestStdinShapePin, which pins the
verbatim 9-field stdin shape captured during #551 PREPARE-phase probes.
Both tell future readers "if the platform changes its stdin or task.json
shape, these are the canaries."
"""
import io
import json
from unittest.mock import patch

import pytest

from fixtures.emitter import VALID_HANDOFF, _run_main


# Verbatim 9-field stdin shape captured by 3 real-platform probes during #551
# PREPARE phase (docs/preparation/551-emitter-regression-diagnostic.md
# § "Real-platform stdin shape"). Pinned as a fixture so future emitter
# changes are tested against what the platform actually delivers, not
# against a synthetic shape that test authors guessed.
PLATFORM_STDIN_SHAPE = {
    "session_id": "1fb6500d-25ba-48c6-af00-5f92024644d0",
    "transcript_path": (
        "/Users/example/.claude/projects/"
        "-Users-example-Sites-collab-PACT-Plugin/"
        "1fb6500d-25ba-48c6-af00-5f92024644d0.jsonl"
    ),
    "cwd": "/Users/example/Sites/collab/PACT-Plugin",
    "hook_event_name": "TaskCompleted",
    "task_id": "12",
    "task_subject": "PROBE: capture real TaskCompleted stdin shape",
    "task_description": "diagnostic probe payload",
    "teammate_name": "preparer",
    "team_name": "pact-1fb6500d",
}


def _write_task_json(tmp_path, team, task_id, payload):
    """Helper for TestRealDiskRead — write a task.json under the
    team-scoped path that read_task_json checks first.

    Returns the Path to the written file. Caller is responsible for
    setting `monkeypatch.setenv("HOME", str(tmp_path))` so HOME-relative
    resolution lands under tmp_path.
    """
    tasks_dir = tmp_path / ".claude" / "tasks" / team
    tasks_dir.mkdir(parents=True, exist_ok=True)
    task_json = tasks_dir / f"{task_id}.json"
    task_json.write_text(json.dumps(payload), encoding="utf-8")
    return task_json

class TestRealDiskRead:
    """The suite's mocked-read tests patch read_task_json and never
    exercise the actual on-disk read path that ships in production. This
    class fires main() against a real ~/.claude/tasks/{team}/{id}.json
    file written under tmp_path — verifies path-join and JSON parse on
    the read path that mocked tests bypass. (Sanitization is unit-tested
    separately in TestPathSanitization; these tests use safe inputs.)

    Without this coverage, a regression in read_task_json's path
    construction (e.g., team-scoped vs base directory ordering) would
    not be caught by the unit suite — exactly the test-vs-production
    gap that masked #551.
    """

    def test_real_disk_read_completed_task_emits_event(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        # Write a real task.json at the team-scoped path that
        # read_task_json checks first (the team-scoped branch in
        # task_utils.read_task_json's `for task_dir in task_dirs:` loop).
        _write_task_json(
            tmp_path, "pact-test", "real-disk-1",
            {
                "id": "real-disk-1",
                "subject": "real disk read probe",
                "status": "completed",
                "owner": "probe-agent",
                "metadata": {"handoff": VALID_HANDOFF},
            },
        )

        # Patch the tasks_base_dir to point at our tmp tree. read_task_json
        # accepts the override; we go through main() so the full pipeline
        # (init, sanitize, status gate, marker, append) is exercised
        # except for the bare read_task_json call site, which we redirect
        # to use our tmp path.
        from agent_handoff_emitter import main
        from shared import task_utils

        original_read = task_utils.read_task_json

        # Belt-and-suspenders: explicit tasks_base_dir override + HOME
        # monkeypatch route to the same path; intentional defense-in-depth
        # against future fixture-isolation changes.
        def _read_with_tmp_base(task_id, team_name, tasks_base_dir=None):
            return original_read(
                task_id, team_name,
                tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
            )

        calls: list[dict] = []

        def _append_spy(event):
            calls.append(event)
            return True

        with patch(
            "agent_handoff_emitter.read_task_json",
            side_effect=_read_with_tmp_base,
        ), patch(
            "agent_handoff_emitter.append_event",
            side_effect=_append_spy,
        ), patch("sys.stdin", io.StringIO(json.dumps({
            "session_id": "test-session-1",
            "hook_event_name": "TaskCompleted",
            "task_id": "real-disk-1",
            "task_subject": "real disk read probe",
            "teammate_name": "probe-agent",
            "team_name": "pact-test",
        }))):
            with pytest.raises(SystemExit) as exc:
                main()

        assert exc.value.code == 0
        assert len(calls) == 1, (
            "real-disk-read path failed to emit event despite valid "
            "task.json on disk. Sanitization, path-join, or JSON parse "
            "regression — investigate read_task_json in shared/task_utils.py."
        )
        assert calls[0]["task_id"] == "real-disk-1"
        assert calls[0]["agent"] == "probe-agent"
        assert calls[0]["handoff"] == VALID_HANDOFF

    def test_real_disk_read_in_progress_with_hook_event_name_still_emits(
        self, tmp_path, monkeypatch
    ):
        """The #551 race shape, fully end-to-end with a real on-disk
        task.json showing status=in_progress. Under Option B,
        hook_event_name primary signal trumps disk-status, journal
        write succeeds. This is the most direct production-fidelity
        regression guard."""
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_task_json(
            tmp_path, "pact-test", "real-disk-race",
            {
                "id": "real-disk-race",
                "subject": "race shape on real disk",
                "status": "in_progress",  # THE #551 race
                "owner": "probe-agent",
                "metadata": {"handoff": VALID_HANDOFF},
            },
        )

        from agent_handoff_emitter import main
        from shared import task_utils

        original_read = task_utils.read_task_json

        # Belt-and-suspenders: explicit tasks_base_dir override + HOME
        # monkeypatch route to the same path; intentional defense-in-depth
        # against future fixture-isolation changes.
        def _read_with_tmp_base(task_id, team_name, tasks_base_dir=None):
            return original_read(
                task_id, team_name,
                tasks_base_dir=str(tmp_path / ".claude" / "tasks"),
            )

        calls: list[dict] = []

        with patch(
            "agent_handoff_emitter.read_task_json",
            side_effect=_read_with_tmp_base,
        ), patch(
            "agent_handoff_emitter.append_event",
            side_effect=lambda e: (calls.append(e), True)[1],
        ), patch("sys.stdin", io.StringIO(json.dumps({
            "session_id": "test-session-1",
            "hook_event_name": "TaskCompleted",
            "task_id": "real-disk-race",
            "task_subject": "race shape on real disk",
            "teammate_name": "probe-agent",
            "team_name": "pact-test",
        }))):
            with pytest.raises(SystemExit):
                main()

        assert len(calls) == 1, (
            "#551 race against REAL disk + hook_event_name primary signal "
            "must emit. If this fails, Option B is not actually wired up "
            "to the production read path."
        )

class TestStdinShapePin:
    """Pin the verbatim 9-field platform stdin shape captured during
    PREPARE-phase probes (3/3 fires identical structure). Future emitter
    changes are now tested against what the platform actually delivers,
    not against a synthetic shape that may drift.

    Diagnostic capture: docs/preparation/551-emitter-regression-diagnostic.md
    § "Real-platform stdin shape". Fields:
      session_id, transcript_path, cwd, hook_event_name, task_id,
      task_subject, task_description, teammate_name, team_name.

    Scope: assertions verify the emitter→append_event boundary (the
    emitter IS the boundary), not the journal-file-on-disk E2E path.
    """

    def test_platform_stdin_shape_emits_event_under_option_b(
        self, tmp_path, monkeypatch
    ):
        """The platform stdin always carries hook_event_name; under
        Option B the primary signal fires and the event lands. This is
        the realistic-shape equivalent of TestRaceShapeRegression's
        synthetic minimal payload — same Option B path, real fields."""
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        # Use the verbatim shape but ensure task_data shows in_progress
        # (the empirical race) so we prove the primary signal works on
        # the real shape, not just on the synthetic one.
        _run_main(
            stdin_payload=PLATFORM_STDIN_SHAPE,
            task_data={
                "status": "in_progress",
                "owner": "preparer",
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        assert len(calls) == 1
        assert calls[0]["agent"] == "preparer"
        assert calls[0]["task_id"] == "12"
        assert calls[0]["task_subject"] == (
            "PROBE: capture real TaskCompleted stdin shape"
        )

    def test_platform_stdin_shape_extra_fields_do_not_break_main(
        self, tmp_path, monkeypatch
    ):
        """The platform delivers `transcript_path`, `cwd`, and
        `task_description` — fields the emitter does not consume. Pin
        that their presence does NOT crash main() (e.g. via a stricter
        future schema check). If a regression makes the emitter strict
        about unknown stdin fields, this test catches it before
        production."""
        monkeypatch.setenv("HOME", str(tmp_path))
        calls: list[dict] = []
        # All 9 fields present, including the ones the emitter ignores.
        _run_main(
            stdin_payload=PLATFORM_STDIN_SHAPE,
            task_data={
                "status": "completed",
                "owner": "preparer",
                "metadata": {"handoff": VALID_HANDOFF},
            },
            append_calls=calls,
        )
        # Event emits cleanly — no exception, no extra fields leaked
        # into the journal entry beyond what the emitter explicitly
        # forwards (agent, task_id, task_subject, handoff).
        assert len(calls) == 1
        event = calls[0]
        assert set(event.keys()) >= {
            "type", "agent", "task_id", "task_subject", "handoff",
        }
        # transcript_path / cwd / task_description / session_id /
        # team_name / teammate_name / hook_event_name from stdin must
        # NOT leak into the journal event payload.
        for leaked_field in (
            "transcript_path", "cwd", "task_description",
            "session_id", "team_name", "teammate_name", "hook_event_name",
        ):
            assert leaked_field not in event, (
                f"stdin field {leaked_field!r} leaked into journal "
                f"event — emitter is forwarding too much data."
            )
