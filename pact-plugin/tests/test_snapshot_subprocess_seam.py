"""
Location: pact-plugin/tests/test_snapshot_subprocess_seam.py
Summary: REAL-PROCESS end-to-end coverage for the task_metadata_snapshot
         stdin-to-journal path — the hook binaries run as SUBPROCESSES with
         real stdin frames, a real env-isolated HOME (context file, task
         store, marker dirs), and the REAL session journal on disk. Closes
         the in-process-patching gap: the CODE-phase seam suites drive
         main()/evaluate_lifecycle in-process with append_event spied, so
         the real append_event schema validation, the real O_EXCL marker
         filesystem, and the real pact_context env resolution
         (CLAUDE_PROJECT_DIR + stdin session_id) are never exercised
         together on one code path. Here they are.

         Also closes the harvest read seam as a real CLI subprocess: the
         skill's documented command (session_journal.py read --session-dir
         --type task_metadata_snapshot) is run verbatim against the journal
         the hook subprocess produced — a full produce -> register ->
         consume round-trip through real entrypoints (a missed
         _REQUIRED_FIELDS_BY_TYPE registration cannot pass this green:
         append_event validates per-type only for registered types, and the
         CLI read returns the typed events only if the write landed).

================================ ANTI-MOCK INVARIANT ===========================
Nothing in this file monkeypatches ANY module under test — no Path.home
redirect, no pact_context fixture, no append_event spy. Isolation is
process-level only: HOME + CLAUDE_PROJECT_DIR env vars on the subprocess.
If a future edit converts these to in-process calls with patched seams, the
file's reason to exist is gone — revert that edit.

============================ NON-VACUITY ========================================
Same-fixture negative controls: (a) no task file on disk -> neither event
(proves the real read_task_json seam is load-bearing); (b) the CLI read of a
journal with no snapshot events returns an empty array (proves the typed
read is coupled to the write, not an always-true parse).

Counter-test-by-revert (source-only; production is committed, test files
stage separately): restoring the six production files to their pre-arc shape
(`git checkout main -- <hooks paths>`) must fail every test in this file that
asserts a task_metadata_snapshot event, with ZERO failures among the
pre-existing agent_handoff suites. The measured cardinality is documented in
the TEST-phase HANDOFF (task metadata + journal mirror).
================================================================================
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent / "hooks"

# For the occupant-join oracle only (computing the expected discriminator
# with the shared SSOT fn) — the modules under test run in SUBPROCESSES and
# are never imported, patched, or stubbed here.
sys.path.insert(0, str(HOOKS_DIR))

TEAM = "session-subproc"
SID = "bbbbbbbb-2222-3333-4444-555555555555"
SLUG = "subproc-project"

VALID_HANDOFF = {
    "produced": "did the thing",
    "decisions": "chose X",
    "reasoning_chain": "because",
    "uncertainty": "none",
    "integration": "n/a",
    "open_questions": "none",
}

SIBLINGS = {
    "teachback_submit": {"understanding": "u", "first_action": "f"},
    "variety": {"total": 8, "novelty": 2},
    "consultation_analysis": "full five-section analysis text",
}


def _seed_home(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Build the real on-disk world the hook resolves: session context file,
    task-store dir, and return (home, session_dir, tasks_dir)."""
    home = tmp_path / "home"
    session_dir = home / ".claude" / "pact-sessions" / SLUG / SID
    session_dir.mkdir(parents=True)
    (session_dir / "pact-session-context.json").write_text(
        json.dumps({
            "team_name": TEAM,
            "session_id": SID,
            "project_dir": f"/tmp/{SLUG}",
            "plugin_root": "",
            "started_at": "2026-01-01T00:00:00Z",
        }),
        encoding="utf-8",
    )
    tasks_dir = home / ".claude" / "tasks" / TEAM
    tasks_dir.mkdir(parents=True)
    return home, session_dir, tasks_dir


def _write_task(tasks_dir: Path, task_id: str, *, owner="architect",
                subject="design X", status="completed", metadata=None) -> None:
    task = {
        "id": task_id,
        "owner": owner,
        "subject": subject,
        "status": status,
        "metadata": metadata if metadata is not None else {},
    }
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps(task), encoding="utf-8"
    )


def _run_hook(hook_filename: str, stdin_obj: dict, home: Path,
              ) -> subprocess.CompletedProcess:
    hook_path = HOOKS_DIR / hook_filename
    assert hook_path.exists(), f"hook missing at {hook_path}"
    env = os.environ.copy()
    env["HOME"] = str(home)
    # pact_context.init computes the session dir slug from
    # CLAUDE_PROJECT_DIR's basename; without it get_session_dir() is ""
    # and every journal write silently defers.
    env["CLAUDE_PROJECT_DIR"] = f"/tmp/{SLUG}"
    return subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(stdin_obj),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(home),
        timeout=30,
    )


def _emitter_frame(task_id: str, *, subject="design X") -> dict:
    return {
        "hook_event_name": "TaskCompleted",
        "session_id": SID,
        "task_id": task_id,
        "task_subject": subject,
        "team_name": TEAM,
    }


def _gate_completion_frame(task_id: str, *, subject="design X",
                           owner="architect", metadata=None) -> dict:
    """A lead-frame PostToolUse TaskUpdate(status=completed) with post-state
    via tool_response.task — task_lifecycle_gate's preferred source."""
    return {
        "hook_event_name": "PostToolUse",
        "session_id": SID,
        "agent_type": "PACT:pact-orchestrator",
        "tool_name": "TaskUpdate",
        "tool_input": {"taskId": task_id, "status": "completed"},
        "tool_response": {"task": {
            "id": task_id,
            "subject": subject,
            "owner": owner,
            "metadata": metadata if metadata is not None else dict(SIBLINGS),
        }},
    }


def _read_journal(session_dir: Path) -> list[dict]:
    journal = session_dir / "session-journal.jsonl"
    if not journal.exists():
        return []
    return [
        json.loads(line)
        for line in journal.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _typed(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e.get("type") == event_type]


class TestSeamCSubprocess:
    """agent_handoff_emitter.py as a real process: stdin -> real journal."""

    def test_completion_emits_both_events_end_to_end(self, tmp_path):
        home, session_dir, tasks_dir = _seed_home(tmp_path)
        _write_task(tasks_dir, "7",
                    metadata={"handoff": VALID_HANDOFF, **SIBLINGS})

        result = _run_hook("agent_handoff_emitter.py",
                           _emitter_frame("7"), home)

        assert result.returncode == 0, (
            f"exit non-zero. stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        assert json.loads(result.stdout.strip())["suppressOutput"] is True

        events = _read_journal(session_dir)
        handoffs = _typed(events, "agent_handoff")
        snapshots = _typed(events, "task_metadata_snapshot")
        assert len(handoffs) == 1, "handoff emission must be unaffected"
        assert len(snapshots) == 1, (
            "exactly one snapshot event must land in the REAL journal when "
            "the real hook process resolves the real task store; got %d"
            % len(snapshots)
        )
        snap = snapshots[0]
        assert snap["task_id"] == "7"
        # The occupant discriminator must be computable by a reader holding
        # only the agent_handoff event's (agent, task_subject) — the §6
        # task-id-reuse join. Computed here with the same shared SSOT fn.
        from shared.agent_handoff_marker import occupant_hash
        assert snap["occupant"] == occupant_hash(
            handoffs[0]["agent"], handoffs[0]["task_subject"]
        )
        payload = snap["metadata"]
        assert "handoff" not in payload, "SNAPSHOT_EXCLUDE must hold end-to-end"
        assert payload["teachback_submit"] == SIBLINGS["teachback_submit"]
        assert payload["variety"] == SIBLINGS["variety"]
        assert (payload["consultation_analysis"]
                == SIBLINGS["consultation_analysis"])
        assert snap["subject"] == "design X"
        assert snap["owner"] == "architect"
        assert "truncated" not in snap, "no truncation on a small payload"

    def test_no_handoff_siblings_still_snapshot_no_handoff_event(
            self, tmp_path):
        # The seam-C position (after the transition gate, BEFORE the
        # handoff-presence exit) is only observable in the real exit ladder:
        # a task with siblings but no handoff must snapshot even though the
        # emitter exits before its own handoff emit.
        home, session_dir, tasks_dir = _seed_home(tmp_path)
        _write_task(tasks_dir, "8", metadata=dict(SIBLINGS))

        result = _run_hook("agent_handoff_emitter.py",
                           _emitter_frame("8"), home)

        assert result.returncode == 0
        events = _read_journal(session_dir)
        assert len(_typed(events, "task_metadata_snapshot")) == 1
        assert len(_typed(events, "agent_handoff")) == 0

    def test_signal_task_snapshot_included_handoff_suppressed(self, tmp_path):
        # D1 end-to-end in a real process: blocker tasks DO snapshot; the
        # agent_handoff suppression for signal tasks stays pinned.
        home, session_dir, tasks_dir = _seed_home(tmp_path)
        _write_task(tasks_dir, "9", metadata={
            "type": "blocker",
            "halt_context": "HALT: broken build",
            "handoff": VALID_HANDOFF,
        })

        result = _run_hook("agent_handoff_emitter.py",
                           _emitter_frame("9"), home)

        assert result.returncode == 0
        events = _read_journal(session_dir)
        snapshots = _typed(events, "task_metadata_snapshot")
        assert len(snapshots) == 1, "D1: signal tasks DO snapshot"
        assert snapshots[0]["task_type"] == "blocker"
        assert snapshots[0]["metadata"]["halt_context"] == "HALT: broken build"
        assert len(_typed(events, "agent_handoff")) == 0, (
            "unconditional leg: agent_handoff suppression for signal tasks"
        )

    def test_negative_control_no_task_file_neither_event(self, tmp_path):
        home, session_dir, _tasks_dir = _seed_home(tmp_path)

        result = _run_hook("agent_handoff_emitter.py",
                           _emitter_frame("7"), home)

        assert result.returncode == 0
        assert _read_journal(session_dir) == [], (
            "no task on disk -> the real read seam yields nothing; a green "
            "here with events present means the emit is decoupled from the "
            "real read (the inert-seam failure class)"
        )

    def test_rerun_same_content_dedups_via_real_marker(self, tmp_path):
        # Two full process runs: the second must be suppressed by the real
        # on-disk O_EXCL marker (fresh interpreter each run, so nothing
        # in-process can carry the dedup — only the filesystem can).
        home, session_dir, tasks_dir = _seed_home(tmp_path)
        _write_task(tasks_dir, "7",
                    metadata={"handoff": VALID_HANDOFF, **SIBLINGS})

        first = _run_hook("agent_handoff_emitter.py", _emitter_frame("7"), home)
        second = _run_hook("agent_handoff_emitter.py", _emitter_frame("7"), home)

        assert first.returncode == 0 and second.returncode == 0
        events = _read_journal(session_dir)
        assert len(_typed(events, "task_metadata_snapshot")) == 1, (
            "content-key marker must dedup across real process lifetimes"
        )
        marker_dir = (home / ".claude" / "teams" / TEAM
                      / ".task_metadata_snapshot_emitted")
        assert marker_dir.exists() and any(marker_dir.iterdir()), (
            "the snapshot marker namespace dir must exist on real disk"
        )

    def test_changed_content_supersedes_with_second_event(self, tmp_path):
        home, session_dir, tasks_dir = _seed_home(tmp_path)
        _write_task(tasks_dir, "7", metadata=dict(SIBLINGS))
        first = _run_hook("agent_handoff_emitter.py", _emitter_frame("7"), home)

        changed = dict(SIBLINGS)
        changed["r2_verification"] = {"verdict": "GO"}
        _write_task(tasks_dir, "7", metadata=changed)
        second = _run_hook("agent_handoff_emitter.py", _emitter_frame("7"), home)

        assert first.returncode == 0 and second.returncode == 0
        snapshots = _typed(_read_journal(session_dir),
                           "task_metadata_snapshot")
        assert len(snapshots) == 2, (
            "a changed payload is a new content key -> superseding event"
        )
        assert "r2_verification" in snapshots[-1]["metadata"]
        assert "r2_verification" not in snapshots[0]["metadata"]


class TestSeamASubprocess:
    """task_lifecycle_gate.py as a real process: lead completion frame ->
    real journal (the CODE-phase suite spies append_event; this leg proves
    the same frame lands a schema-valid event through the real writer)."""

    def test_lead_completion_lands_snapshot_in_real_journal(self, tmp_path):
        home, session_dir, tasks_dir = _seed_home(tmp_path)
        _write_task(tasks_dir, "42", status="completed",
                    metadata=dict(SIBLINGS))

        result = _run_hook("task_lifecycle_gate.py",
                           _gate_completion_frame("42"), home)

        assert result.returncode == 0, (
            f"exit non-zero. stderr={result.stderr!r} stdout={result.stdout!r}"
        )
        snapshots = _typed(_read_journal(session_dir),
                           "task_metadata_snapshot")
        assert len(snapshots) == 1
        assert snapshots[0]["task_id"] == "42"
        assert "handoff" not in snapshots[0]["metadata"]

    def test_teammate_frame_no_snapshot_in_real_journal(self, tmp_path):
        # Both-modes: the same frame under a teammate agent_type must not
        # fire the lead-completion seam (is_lead structural signal).
        home, session_dir, tasks_dir = _seed_home(tmp_path)
        _write_task(tasks_dir, "42", status="completed",
                    metadata=dict(SIBLINGS))

        frame = _gate_completion_frame("42")
        frame["agent_type"] = "pact-devops-engineer"
        result = _run_hook("task_lifecycle_gate.py", frame, home)

        assert result.returncode == 0
        assert _typed(_read_journal(session_dir),
                      "task_metadata_snapshot") == []


class TestHarvestCliReadSeam:
    """The harvest skill's documented consumer command, run verbatim as a
    real CLI subprocess against a journal a real hook subprocess wrote."""

    def _cli_read(self, session_dir: Path, home: Path) -> list[dict]:
        env = os.environ.copy()
        env["HOME"] = str(home)
        result = subprocess.run(
            [sys.executable,
             str(HOOKS_DIR / "shared" / "session_journal.py"),
             "read", "--session-dir", str(session_dir),
             "--type", "task_metadata_snapshot"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def test_cli_read_recovers_snapshot_after_task_drain(self, tmp_path):
        # Acceptance criterion end-to-end with REAL entrypoints on both
        # sides: hook subprocess writes; the task store is drained (real
        # unlink — the drain-shaped destruction model); the skill's CLI
        # read recovers the payload from the journal alone.
        home, session_dir, tasks_dir = _seed_home(tmp_path)
        _write_task(tasks_dir, "7", metadata=dict(SIBLINGS))
        assert _run_hook("agent_handoff_emitter.py",
                         _emitter_frame("7"), home).returncode == 0

        (tasks_dir / "7.json").unlink()  # the drain
        assert not (tasks_dir / "7.json").exists()

        recovered = self._cli_read(session_dir, home)
        assert len(recovered) == 1
        assert recovered[0]["metadata"]["teachback_submit"] == (
            SIBLINGS["teachback_submit"]
        )

    def test_cli_read_empty_when_no_snapshot_events(self, tmp_path):
        # Negative control for the typed read: a journal with only an
        # agent_handoff event yields an EMPTY snapshot array.
        home, session_dir, tasks_dir = _seed_home(tmp_path)
        _write_task(tasks_dir, "7", metadata={"handoff": VALID_HANDOFF})
        assert _run_hook("agent_handoff_emitter.py",
                         _emitter_frame("7"), home).returncode == 0

        events = _read_journal(session_dir)
        assert len(_typed(events, "agent_handoff")) == 1
        assert self._cli_read(session_dir, home) == []
