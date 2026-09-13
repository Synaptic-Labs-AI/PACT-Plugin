"""Behaviour pins for adding a claimed task to its owner's launch records.

Location: pact-plugin/tests/test_claim_extends_launch_record.py
Summary: drives task_lifecycle_gate.main with a PostToolUse TaskUpdate claim and
         checks that the claimant's live background-work records gain the
         claimed task, so a wait flagged on that task silences and discharges
         them, while a wait anchored before the launch still does not.
Used by: the suite. Path setup is conftest-owned.

Every arm drives the production entry point, `task_lifecycle_gate.main`, on the
real clock: the gate passes no clock, so records are stamped relative to now.
"""

from __future__ import annotations

import io
import json
import sys
from datetime import timedelta
from unittest.mock import patch

import pytest

import task_lifecycle_gate
import teammate_idle
from shared.background_work import (
    classify_wait,
    load_unflagged_idle_counts,
    save_records,
    utc_now,
)

TEAM = "claim-team"
OWNER = "claim-coder"


@pytest.fixture
def config(tmp_path, monkeypatch, pact_context):
    root = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    pact_context(team_name=TEAM, session_id="claim-session")
    (root / "tasks" / TEAM).mkdir(parents=True)
    return root


def _write_task(config, task_id, owner=OWNER, wait=None):
    task = {"id": task_id, "subject": f"work {task_id}", "status": "in_progress", "owner": owner}
    if wait is not None:
        task["metadata"] = {"intentional_wait": wait}
    (config / "tasks" / TEAM / f"{task_id}.json").write_text(json.dumps(task), encoding="utf-8")
    return task


def _wait(anchor, now):
    """A valid wait, fresh by `since`, whose scope anchor is `anchor`."""
    return {
        "reason": "awaiting_blocker_resolution",
        "expected_resolver": "lead",
        "since": (now - timedelta(minutes=1)).isoformat(),
        "covers_since": anchor.isoformat(),
    }


def _record(registered_at, agent_name=OWNER, task_ids=("A",)):
    return {
        "agent_name": agent_name,
        "session_id": "sid",
        "task_ids": list(task_ids),
        "registered_at": registered_at.isoformat(),
    }


def _registry_path(config):
    return config / "teams" / TEAM / "background_work.json"


def _registry(config):
    path = _registry_path(config)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["records"]


def _claim(task_id, status: "str | None" = "in_progress", session_id="claim-session", **frame_fields):
    """Run the gate on a TaskUpdate of `task_id`; status None sends a metadata-only update."""
    tool_input = {"taskId": task_id}
    if status is None:
        tool_input["metadata"] = {"progress": "working"}
    else:
        tool_input["status"] = status
    frame = {
        "hook_event_name": "PostToolUse",
        "session_id": session_id,
        "tool_name": "TaskUpdate",
        "tool_input": tool_input,
        "tool_response": {"success": True},
        **frame_fields,
    }
    with patch.object(sys, "stdin", io.StringIO(json.dumps(frame))):
        with pytest.raises(SystemExit):
            task_lifecycle_gate.main()


def test_a_claim_adds_the_task_to_the_owners_live_record(config):
    now = utc_now()
    launched = now - timedelta(minutes=10)
    assert save_records([_record(launched)], team_name=TEAM) is True
    _write_task(config, "A")
    _write_task(config, "B")

    _claim("B")
    _claim("B")  # a second claim of the same task must not list it twice

    records = _registry(config)
    assert [r["task_ids"] for r in records] == [["A", "B"]], (
        "a task its owner claimed after launching was not added to the launch record"
    )
    assert records[0]["registered_at"] == launched.isoformat(), (
        "extending a record moved its launch time, which would widen what an older wait clears"
    )


def test_a_wait_on_the_claimed_task_discharges_and_silences_the_record(config):
    """The owner holds A (launched under, no wait) and B (claimed later, flagged).

    B is listed first so that Layer 2 judges A, the task the record was launched
    under: without the claim, B's wait is on no record, the record stays, and
    A's missing wait advances the unflagged counter.
    """
    now = utc_now()
    launched = now - timedelta(minutes=10)
    assert save_records([_record(launched)], team_name=TEAM) is True
    task_a = _write_task(config, "A")
    _write_task(config, "B")
    _claim("B")
    task_b = _write_task(config, "B", wait=_wait(now - timedelta(minutes=5), now))

    advisory = teammate_idle.check_unflagged_background([task_b, task_a], OWNER, TEAM)

    assert _registry(config) == [], "a wait on the claimed task did not discharge the launch record"
    assert advisory is None
    assert OWNER not in load_unflagged_idle_counts(TEAM), (
        "the unflagged counter advanced although the claimed task carries a covering wait"
    )


def test_a_wait_anchored_before_the_launch_still_does_not_clear_it(config):
    now = utc_now()
    launched = now - timedelta(minutes=10)
    assert save_records([_record(launched)], team_name=TEAM) is True
    task_a = _write_task(config, "A")
    _write_task(config, "B")
    _claim("B")
    task_b = _write_task(config, "B", wait=_wait(now - timedelta(minutes=20), now))
    # The wait itself is valid, so only its anchor can keep the record.
    assert classify_wait(task_b) is None

    teammate_idle.check_unflagged_background([task_b, task_a], OWNER, TEAM)

    assert [r["task_ids"] for r in _registry(config)] == [["A", "B"]], (
        "a wait anchored before the launch cleared it"
    )


def test_an_update_that_does_not_claim_adds_nothing(config):
    now = utc_now()
    assert save_records([_record(now - timedelta(minutes=10))], team_name=TEAM) is True
    _write_task(config, "A")
    _write_task(config, "B")

    _claim("B", status=None)

    assert [r["task_ids"] for r in _registry(config)] == [["A"]], (
        "a TaskUpdate that did not set in_progress added its task to a launch record"
    )


def test_a_claim_with_no_registry_creates_no_file(config):
    _write_task(config, "B")

    _claim("B")

    team_dir = config / "teams" / TEAM
    assert not _registry_path(config).exists()
    assert not (team_dir / "background_work.json.lock").exists()


def test_another_owners_record_is_untouched(config):
    now = utc_now()
    launched = now - timedelta(minutes=10)
    assert save_records(
        [_record(launched), _record(launched, agent_name="other-coder", task_ids=("C",))],
        team_name=TEAM,
    ) is True
    _write_task(config, "A")
    _write_task(config, "B")

    _claim("B")

    by_owner = {r["agent_name"]: r["task_ids"] for r in _registry(config)}
    assert by_owner == {OWNER: ["A", "B"], "other-coder": ["C"]}


def test_a_separate_process_teammate_claim_extends_its_record_with_no_context_file(
    tmp_path, monkeypatch
):
    """A separate-process teammate's own hook process has no session context, so
    its team comes from its session-registry entry. Its claim must still extend
    its launch record."""
    root = tmp_path / "config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/claim/project")
    session = "tmux-claim-session"
    (root / "teams" / TEAM).mkdir(parents=True)
    (root / "teams" / TEAM / "config.json").write_text(json.dumps({
        "leadSessionId": "lead-claim-session",
        "members": [{"name": OWNER, "agentId": f"{OWNER}@{TEAM}",
                     "agentType": "pact-backend-coder", "backendType": "tmux"}],
    }), encoding="utf-8")
    registry = root / "pact-sessions" / ".teammate-registry.jsonl"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps({"session_id": session, "value": f"{OWNER}@{TEAM}"}) + "\n", encoding="utf-8"
    )
    (root / "tasks" / TEAM).mkdir(parents=True)
    now = utc_now()
    assert save_records([_record(now - timedelta(minutes=10))], team_name=TEAM) is True
    _write_task(root, "A")
    _write_task(root, "B")
    assert not list((root / "pact-sessions").glob("*/" + session + "/pact-session-context.json"))

    _claim("B", session_id=session, agent_type="pact-backend-coder")

    assert [r["task_ids"] for r in _registry(root)] == [["A", "B"]], (
        "a separate-process teammate's claim did not extend its launch record: "
        "its team was not resolved without a session context"
    )
